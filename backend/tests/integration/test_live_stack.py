"""Phase 4 integration: real Mosquitto (compose). Skips itself when no broker is reachable,
like the Postgres gate test. Start it with:
    docker compose -f infra/docker-compose.yml up -d mosquitto
"""

import asyncio
import json
import os
import socket
import subprocess
import time

import paho.mqtt.client as mqtt
import pytest

from common.config import Settings
from edge.bus import MqttBus
from tools.latency_probe import MqttSide, ProbeError

S = Settings()


def _broker_up() -> bool:
    try:
        with socket.create_connection((S.mqtt_host, S.mqtt_port), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _broker_up(), reason=f"no MQTT broker at {S.mqtt_host}:{S.mqtt_port}"
)


def test_mqtt_bus_round_trip_and_bad_payload_counted():
    async def main():
        bus = MqttBus(S.mqtt_host, S.mqtt_port, f"it-bus-{time.time_ns()}", 0.5)
        q: asyncio.Queue = asyncio.Queue()
        topic = f"cat/IT-SITE/IT-{time.time_ns()}/raw"
        bus.subscribe(topic, q)
        task = asyncio.create_task(bus.run())
        for _ in range(200):
            if bus.connected:
                break
            await asyncio.sleep(0.02)
        assert bus.connected
        await asyncio.sleep(0.2)  # subscription settled
        await bus.publish(topic, {"schema": "raw.v1", "ts": "x"})
        got = await asyncio.wait_for(q.get(), 5)
        other = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        other.connect(S.mqtt_host, S.mqtt_port)
        other.publish(topic, b"not json").wait_for_publish(5)
        other.disconnect()
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return got, bus.dropped_in

    (topic, payload), dropped = asyncio.run(main())
    assert payload == {"schema": "raw.v1", "ts": "x"} and dropped == 1


def test_latency_probe_detects_a_foreign_publisher():
    machine = f"IT{time.time_ns()}"
    side = MqttSide(S.mqtt_host, S.mqtt_port, "IT-SITE", machine)
    try:
        time.sleep(0.3)
        side.publish({"schema": "raw.v1"})  # our own frame: never counts as foreign
        time.sleep(0.3)
        side.check_quiet()
        other = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        other.connect(S.mqtt_host, S.mqtt_port)
        other.publish(side.raw_topic, json.dumps({"ts": "someone-else"})).wait_for_publish(5)
        other.disconnect()
        time.sleep(0.3)
        with pytest.raises(ProbeError, match="another publisher"):
            side.check_quiet()
    finally:
        side.close()


BROKER_CONTAINER = os.environ.get("MQTT_BROKER_CONTAINER", "operator-companion-mosquitto-1")


def _container_running(name: str) -> bool:
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.stdout.strip() == "true"


@pytest.mark.skipif(
    os.environ.get("RUN_BROKER_RESTART") != "1",
    reason="restarts the broker container (disrupts anything else on it): "
    "run with RUN_BROKER_RESTART=1",
)
@pytest.mark.skipif(
    not _container_running(BROKER_CONTAINER),
    reason=f"broker container {BROKER_CONTAINER} not running (set MQTT_BROKER_CONTAINER)",
)
def test_mqtt_bus_reconnects_and_resubscribes_after_broker_restart():
    """Restart the real broker under a connected MqttBus: it must notice, reconnect,
    re-subscribe, and deliver again; publishes while down are counted, not raised."""
    topic = f"cat/IT-SITE/IT-{time.time_ns()}/raw"

    def publish_from_outside(payload: dict) -> None:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        c.connect(S.mqtt_host, S.mqtt_port)
        c.loop_start()
        c.publish(topic, json.dumps(payload)).wait_for_publish(5)
        c.loop_stop()
        c.disconnect()

    async def wait(cond, seconds: float, what: str) -> None:
        deadline = time.monotonic() + seconds
        while not cond():
            assert time.monotonic() < deadline, f"timed out: {what}"
            await asyncio.sleep(0.05)

    async def main():
        bus = MqttBus(S.mqtt_host, S.mqtt_port, f"it-reconnect-{time.time_ns()}", 0.5)
        q: asyncio.Queue = asyncio.Queue()
        bus.subscribe(topic, q)
        task = asyncio.create_task(bus.run())
        try:
            await wait(lambda: bus.connected, 10, "first connect")
            await asyncio.sleep(0.3)
            await asyncio.to_thread(publish_from_outside, {"n": 1})
            assert (await asyncio.wait_for(q.get(), 5))[1] == {"n": 1}

            restart = asyncio.create_task(
                asyncio.to_thread(
                    subprocess.run,
                    ["docker", "restart", "-t", "1", BROKER_CONTAINER],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            )
            await wait(lambda: not bus.connected, 30, "disconnect to be noticed")
            await bus.publish(topic, {"while": "down"})  # dropped + counted, never raises
            assert bus.dropped_out >= 1
            await restart
            await wait(lambda: bus.connected, 60, "reconnect after broker restart")
            await asyncio.sleep(0.3)
            await asyncio.to_thread(publish_from_outside, {"n": 2})
            assert (await asyncio.wait_for(q.get(), 10))[1] == {"n": 2}  # re-subscribed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())
