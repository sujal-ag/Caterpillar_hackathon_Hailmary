"""Subscribe to MQTT and write every message as a JSONL line replay.py can read (plan Phase 0)."""

import argparse
import json
import os
import sys
import threading
import time

import paho.mqtt.client as mqtt


def to_line(topic: str, raw: bytes, retain: bool) -> str:
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = raw.decode("utf-8", "replace")
    return json.dumps({"topic": topic, "payload": payload, "retain": retain}, ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", help="output JSONL, or - for stdout")
    ap.add_argument("-t", "--topic", action="append", help="repeatable; default cat/#")
    ap.add_argument("--duration", type=float, help="stop after N seconds")
    ap.add_argument("--count", type=int, help="stop after N messages")
    ap.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    args = ap.parse_args()

    out = sys.stdout if args.out == "-" else open(args.out, "w")
    done, n = threading.Event(), 0

    def on_connect(client, _userdata, _flags, _rc, _props):
        for t in args.topic or ["cat/#"]:
            client.subscribe(t)

    def on_message(_client, _userdata, msg):
        nonlocal n
        out.write(to_line(msg.topic, msg.payload, msg.retain) + "\n")
        out.flush()
        n += 1
        if args.count and n >= args.count:
            done.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"record-{os.getpid()}")
    client.on_connect, client.on_message = on_connect, on_message
    client.connect(args.host, args.port)
    client.loop_start()
    t0 = time.monotonic()
    try:
        done.wait(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        print(f"recorded {n} messages in {time.monotonic() - t0:.1f} s", file=sys.stderr)
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
