"""Message bus interface (plan.md §6 `bus.py`). `MqttBus` (aiomqtt, Mosquitto) is the real
one; `MemoryBus` is the in-process implementation unit tests use instead (plan.md §4
"Tests"). Both deliver `(topic, payload_dict)` tuples to subscriber queues."""

import asyncio
import json
import logging
from typing import Protocol

import aiomqtt

from common.log import jlog

log = logging.getLogger("edge.bus")


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT wildcard match: `+` = one level, `#` = this level and everything below."""
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class Bus(Protocol):
    async def publish(self, topic: str, payload: dict) -> None: ...

    def subscribe(self, pattern: str, queue: asyncio.Queue) -> None: ...


class MemoryBus:
    def __init__(self):
        self._subs: list[tuple[str, asyncio.Queue]] = []

    async def publish(self, topic: str, payload: dict) -> None:
        for pattern, queue in self._subs:
            if topic_matches(pattern, topic):
                queue.put_nowait((topic, payload))

    def subscribe(self, pattern: str, queue: asyncio.Queue) -> None:
        self._subs.append((pattern, queue))


class MqttBus:
    """Subscriptions are registered before `run()` and re-subscribed after every reconnect.
    Publishing while disconnected is dropped (and counted): MQTT output is best-effort,
    while persistence and the WebSocket bridge don't depend on the broker."""

    def __init__(self, host: str, port: int, client_id: str, reconnect_s: float):
        self.host, self.port, self.client_id, self.reconnect_s = host, port, client_id, reconnect_s
        self._subs: list[tuple[str, asyncio.Queue]] = []
        self._client: aiomqtt.Client | None = None
        self.connected = False
        self.dropped_in = 0  # non-JSON payloads received
        self.dropped_out = 0  # publishes while disconnected

    def subscribe(self, pattern: str, queue: asyncio.Queue) -> None:
        self._subs.append((pattern, queue))

    async def publish(self, topic: str, payload: dict) -> None:
        client = self._client
        if client is None:
            self.dropped_out += 1
            return
        try:
            await client.publish(topic, json.dumps(payload, default=str), qos=0)
        except aiomqtt.MqttError:
            self.dropped_out += 1

    async def run(self) -> None:
        while True:
            try:
                async with aiomqtt.Client(
                    self.host, self.port, identifier=self.client_id or None
                ) as client:
                    for pattern in dict.fromkeys(p for p, _ in self._subs):
                        await client.subscribe(pattern)
                    self._client, self.connected = client, True
                    jlog(log, logging.INFO, "mqtt_connected", host=self.host, port=self.port)
                    async for message in client.messages:
                        self._deliver(message.topic.value, message.payload)
            except aiomqtt.MqttError as exc:
                jlog(
                    log,
                    logging.WARNING,
                    "mqtt_disconnected",
                    error=str(exc),
                    retry_s=self.reconnect_s,
                )
            finally:
                self._client, self.connected = None, False
            await asyncio.sleep(self.reconnect_s)

    def _deliver(self, topic: str, raw) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            self.dropped_in += 1
            return
        for pattern, queue in self._subs:
            if topic_matches(pattern, topic):
                queue.put_nowait((topic, payload))
