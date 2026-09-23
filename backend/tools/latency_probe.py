"""Measure trigger-frame-to-nudge latency end to end: MQTT publish -> edge ingest -> rules ->
nudge -> WebSocket (plan.md Phase 4 gate: p95 < 300 ms on the demo laptop).

Frames come from a fixture, not from code: the "arm" frame is the fixture's first raw frame
with the engine running; the trigger is that frame with `--trigger` overrides applied
(default: the HLD §4.4 strong exit intent, belt unfastened + door open). Each run publishes
arm, waits until the edge has processed it and no exit is open, publishes the trigger and
times the first `--rule` nudge that arrives after it. Frames are spaced by the switch
debounce window from rules.yaml so no change of ours is suppressed.

The probe must be the only publisher of raw frames for the machine: anything else (a
replay scenario's hold frames, tools/replay.py, P1's simulator) interleaves with the probe's
frames, and the debounce/rules then see a different machine than the probe thinks it sent.
So it refuses to run while a replay scenario is active (`--stop-replay` stops it), checks the
raw topic is quiet before starting, and aborts the moment a foreign frame shows up. Every
wait has a hard deadline: a run that can't complete fails loudly instead of waiting forever.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt
import websockets
import yaml

from common.config import Settings
from common.spn import FIELD_SPNS
from common.timeutil import now_utc, to_site_iso
from tools.ws_probe import login


class ProbeError(RuntimeError):
    pass


def parse_value(v: str):
    return yaml.safe_load(v)  # "true" -> True, "2.1" -> 2.1, "UNFASTENED" -> str


def with_overrides(frame: dict, overrides: dict) -> dict:
    frame = json.loads(json.dumps(frame))
    for k, v in overrides.items():
        if k in FIELD_SPNS:
            frame["can"][FIELD_SPNS[k]] = v
        else:
            frame["sens"][k] = v
    return frame


class MqttSide:
    """Publishes our raw frames and watches the machine's raw + telemetry topics: a raw
    frame we didn't send is a foreign publisher; a telemetry frame carrying one of our
    timestamps proves the edge has processed that frame."""

    def __init__(self, host: str, port: int, site: str, machine: str):
        self.raw_topic = f"cat/{site}/{machine}/raw"
        self.tel_topic = f"cat/{site}/{machine}/telemetry"
        self._lock = threading.Lock()
        self.sent: set[str] = set()
        self.processed: set[str] = set()
        self.foreign: list[str] = []
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"latency-probe-{os.getpid()}"
        )
        self.client.on_message = self._on_message
        self.client.connect(host, port)
        self.client.subscribe([(self.raw_topic, 0), (self.tel_topic, 0)])
        self.client.loop_start()

    def _on_message(self, _client, _userdata, msg) -> None:
        try:
            ts = json.loads(msg.payload).get("ts")
        except ValueError:
            ts = None
        with self._lock:
            if msg.topic == self.raw_topic and ts not in self.sent:
                self.foreign.append(str(ts))
            elif msg.topic == self.tel_topic and ts in self.sent:
                self.processed.add(ts)

    def publish(self, frame: dict) -> str:
        ts = to_site_iso(now_utc())
        with self._lock:
            self.sent.add(ts)
        self.client.publish(self.raw_topic, json.dumps({**frame, "ts": ts}), qos=0)
        return ts

    def check_quiet(self) -> None:
        with self._lock:
            foreign = list(self.foreign)
        if foreign:
            raise ProbeError(
                f"another publisher is sending raw frames on {self.raw_topic} "
                f"({len(foreign)} frames, last ts {foreign[-1]}). Stop it (POST /sim/stop, "
                "tools/replay.py, P1's sim) or probe a machine nothing else drives."
            )

    def was_processed(self, ts: str) -> bool:
        with self._lock:
            return ts in self.processed

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class WsSide:
    """Reads the socket continuously, so `state` is always the latest the edge sent and
    every nudge is stamped with its arrival time (no message is ever left unread)."""

    def __init__(self, ws):
        self.ws = ws
        self.state: dict | None = None
        self.nudges: list[tuple[float, dict]] = []
        self.task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for raw in self.ws:
            env = json.loads(raw)
            arrived = time.perf_counter()
            if env["type"] == "snapshot":
                self.state = env["data"]["state"]
            elif env["type"] == "state":
                self.state = env["data"]
            elif env["type"] == "nudge":
                self.nudges.append((arrived, env["data"]))

    def check_alive(self) -> None:
        if self.task.done():
            exc = None if self.task.cancelled() else self.task.exception()
            raise ProbeError(f"WebSocket closed: {exc or 'by the server'}")


async def wait_until(cond, deadline: float, what: str, checks) -> None:
    while not cond():
        for check in checks:
            check()
        if time.monotonic() > deadline:
            raise ProbeError(f"timed out waiting for {what}")
        await asyncio.sleep(0.002)


async def run(args) -> list[float]:
    s = Settings()
    base = args.base_url.rstrip("/")
    token = args.token or login(base, args.badge, args.pin, None)
    headers = {"Authorization": f"Bearer {token}"}
    status = httpx.get(f"{base}/system/status", headers=headers).raise_for_status().json()
    if args.machine not in status["machines"]:
        raise ProbeError(f"{args.machine} is not served by this edge: {list(status['machines'])}")
    running = status["sim"]["running"]
    if running:
        if not args.stop_replay:
            raise ProbeError(
                f"replay scenario '{running}' is running (its hold frames would fight the "
                "probe); rerun with --stop-replay or POST /sim/stop"
            )
        httpx.post(f"{base}/sim/stop", headers=headers).raise_for_status()

    rules = yaml.safe_load((s.contracts_dir / "rules.yaml").read_text())
    gap_s = args.gap_s or rules["policy"]["switch_flap_suppress_ms"] / 1000 + 0.2
    quiet_s = args.quiet_s or rules["policy"]["data_stale_s"]

    lines = [json.loads(x) for x in Path(args.fixture).read_text().splitlines() if x.strip()]
    arm = next(
        ln["payload"]
        for ln in lines
        if ln["topic"].endswith("/raw") and ln["payload"]["sens"].get("engine_state") == "RUNNING"
    )
    arm = {**arm, "site_id": status["site_id"], "machine_id": args.machine}
    overrides = {k: parse_value(v) for k, v in (t.split("=", 1) for t in args.trigger)}
    trigger = with_overrides(arm, overrides)

    bus = MqttSide(args.mqtt_host, args.mqtt_port, status["site_id"], args.machine)
    samples: list[float] = []
    ws_url = base.replace("http", "ws", 1) + f"/ws/live?machine={args.machine}&token={token}"
    try:
        await asyncio.sleep(quiet_s)  # nothing else may be publishing for this machine
        bus.check_quiet()
        async with websockets.connect(ws_url, max_size=None) as ws:
            live = WsSide(ws)
            checks = (bus.check_quiet, live.check_alive)
            for i in range(args.runs):
                # 1. arm (seated, belted, door closed): processed by the edge, no exit open
                ts = bus.publish(arm)
                deadline = time.monotonic() + args.wait_s
                await wait_until(
                    lambda ts=ts: bus.was_processed(ts),
                    deadline,
                    f"run {i + 1}: edge to process the arm frame",
                    checks,
                )
                await wait_until(
                    lambda: live.state is not None and live.state["exit_state"] == "NONE",
                    deadline,
                    f"run {i + 1}: exit to close after arm",
                    checks,
                )
                await asyncio.sleep(gap_s)
                # 2. trigger: time to the first matching nudge that arrives after it
                t0 = time.perf_counter()
                bus.publish(trigger)

                def first_after(t0=t0):
                    return next(
                        (t for t, n in live.nudges if t >= t0 and n["rule_id"] == args.rule),
                        None,
                    )

                await wait_until(
                    lambda: first_after() is not None,
                    time.monotonic() + args.wait_s,
                    f"run {i + 1}: {args.rule} nudge after the trigger",
                    checks,
                )
                samples.append((first_after() - t0) * 1000)
                print(f"run {i + 1:>3}: {samples[-1]:6.1f} ms", flush=True)
                await asyncio.sleep(gap_s)
            live.task.cancel()
    finally:
        bus.close()
    return samples


def main() -> None:
    s = Settings()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=os.environ.get("EDGE_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--mqtt-host", default=s.mqtt_host)
    ap.add_argument("--mqtt-port", type=int, default=s.mqtt_port)
    ap.add_argument("--machine", default="EXC001")
    ap.add_argument("--token")
    ap.add_argument("--badge", default=os.environ.get("PROBE_BADGE"))
    ap.add_argument("--pin", default=os.environ.get("PROBE_PIN"))
    ap.add_argument("--fixture", default=str(Path(s.sim_scenarios_dir) / "unsafe_exit.jsonl"))
    ap.add_argument(
        "--trigger",
        action="append",
        default=[],
        help="signal=value override for the trigger frame (repeatable)",
    )
    ap.add_argument("--rule", default="R03")
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--budget-ms", type=float, default=300)
    ap.add_argument("--gap-s", type=float, default=None, help="default: debounce window + 0.2 s")
    ap.add_argument("--wait-s", type=float, default=10, help="hard deadline for each wait")
    ap.add_argument(
        "--quiet-s",
        type=float,
        default=None,
        help="silence required on the raw topic before starting (default: data_stale_s)",
    )
    ap.add_argument(
        "--stop-replay",
        action="store_true",
        help="POST /sim/stop if a replay scenario is running (needs admin)",
    )
    args = ap.parse_args()
    args.trigger = args.trigger or ["seatbelt=UNFASTENED", "door_open=true"]  # HLD §4.4 intent
    if not args.token and not (args.badge and args.pin):
        ap.error("give --token or --badge and --pin")
    try:
        samples = asyncio.run(run(args))
    except ProbeError as exc:
        print(f"latency_probe: {exc}", file=sys.stderr)
        sys.exit(2)
    ordered = sorted(samples)
    p95 = ordered[max(0, round(0.95 * len(ordered)) - 1)]
    print(
        f"\n{args.rule} nudge latency over {len(samples)} runs: "
        f"p50 {statistics.median(samples):.1f} ms · p95 {p95:.1f} ms · "
        f"max {max(samples):.1f} ms (budget {args.budget_ms} ms)"
    )
    sys.exit(0 if p95 < args.budget_ms else 1)


if __name__ == "__main__":
    main()
