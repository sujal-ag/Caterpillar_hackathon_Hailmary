"""Connect to /ws/live, optionally start a scenario, and wait for expected messages in order
(plan.md Phase 4 gate). Exit 0 when every expectation was seen before the timeout.

Expectation: `type[:token...]`. A token `field=value` must equal that field of the message
`data` (dotted paths reach nested fields: `alert.rule_id=R03`); a bare token must equal any
top-level scalar of `data`. Examples:
    --expect nudge:R03:FULLSCREEN   --expect state:exit_state=SAFE   --expect alert:action=RAISED
"""

import argparse
import asyncio
import json
import os
import sys
import time

import httpx
import websockets


def matches(expect: str, env: dict) -> bool:
    type_, *tokens = expect.split(":")
    if env.get("type") != type_:
        return False
    data = env.get("data") or {}
    scalars = {str(v) for v in data.values() if not isinstance(v, dict | list)}
    for tok in tokens:
        if "=" in tok:
            path, want = tok.split("=", 1)
            value = data
            for part in path.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if str(value) != want:
                return False
        elif tok not in scalars:
            return False
    return True


def login(base: str, badge: str, pin: str, machine: str | None) -> str:
    r = httpx.post(
        f"{base}/auth/login", json={"badge_id": badge, "pin": pin, "machine_id": machine}
    )
    r.raise_for_status()
    return r.json()["token"]


async def probe(args) -> int:
    base = args.base_url.rstrip("/")
    token = args.token or login(base, args.badge, args.pin, None)
    ws_url = base.replace("http", "ws", 1) + f"/ws/live?machine={args.machine}&token={token}"
    pending = list(args.expect)
    deadline = time.monotonic() + args.timeout
    async with websockets.connect(ws_url) as ws:
        first = json.loads(await ws.recv())
        print(f"connected: {first['type']} as_of={first['data'].get('as_of')}")
        if args.scenario:
            r = httpx.post(
                f"{base}/sim/scenario",
                json={"name": args.scenario, "machine_id": args.machine, "speed": args.speed},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            print(f"scenario: {r.json()}")
        while pending:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                env = json.loads(await asyncio.wait_for(ws.recv(), left))
            except TimeoutError:
                break
            if matches(pending[0], env):
                print(f"ok   {pending.pop(0)}  ({env['ts']})")
    for exp in pending:
        print(f"MISS {exp}")
    return 1 if pending else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=os.environ.get("EDGE_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--machine", required=True)
    ap.add_argument("--token", help="JWT; otherwise log in with --badge/--pin")
    ap.add_argument("--badge", default=os.environ.get("PROBE_BADGE"))
    ap.add_argument("--pin", default=os.environ.get("PROBE_PIN"))
    ap.add_argument("--scenario", help="POST /sim/scenario after connecting (needs admin)")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed for --scenario")
    ap.add_argument("--expect", action="append", default=[], help="repeatable, in order")
    ap.add_argument("--timeout", type=float, default=30)
    args = ap.parse_args()
    if not args.token and not (args.badge and args.pin):
        ap.error("give --token or --badge and --pin")
    sys.exit(asyncio.run(probe(args)))


if __name__ == "__main__":
    main()
