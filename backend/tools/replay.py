"""Publish a JSONL of MQTT frames to the broker, keeping the original spacing (plan Phase 0).

Line format: {"topic": str, "payload": obj, "retain"?: bool}. Spacing comes from payload["ts"];
lines without ts go out with the previous frame. By default each ts is shifted so the run starts
now (engine sees fresh data); --keep-ts sends the recorded timestamps unchanged.
"""

import argparse
import json
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from common.replay import retimed, schedule
from common.timeutil import now_utc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--speed", type=float, default=1.0, help="10 = ten times faster")
    ap.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    ap.add_argument("--keep-ts", action="store_true", help="do not shift ts to now")
    ap.add_argument("--loop", action="store_true", help="repeat forever")
    args = ap.parse_args()

    lines = [json.loads(s) for s in args.jsonl.read_text().splitlines() if s.strip()]
    plan = schedule(lines, args.speed)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"replay-{os.getpid()}")
    client.connect(args.host, args.port)
    client.loop_start()
    try:
        while True:
            start_wall, start_mono, info = now_utc(), time.monotonic(), None
            for offset, line in plan:
                delay = start_mono + offset - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                payload = (
                    line["payload"]
                    if args.keep_ts
                    else retimed(line["payload"], offset, start_wall)
                )
                info = client.publish(
                    line["topic"], json.dumps(payload), qos=0, retain=line.get("retain", False)
                )
            if info:
                info.wait_for_publish(timeout=5)
            print(f"replayed {len(plan)} frames in {time.monotonic() - start_mono:.1f} s")
            if not args.loop:
                break
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
