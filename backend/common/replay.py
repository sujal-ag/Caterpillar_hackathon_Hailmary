"""Frame scheduling shared by `tools/replay.py` and the edge's `SIM_MODE=replay`
(contracts/sim_control.md). Line format: {"topic", "payload", "retain"?}."""

from datetime import timedelta

from common.timeutil import parse_iso, to_site_iso


def schedule(lines: list[dict], speed: float) -> list[tuple[float, dict]]:
    """[(seconds after start, line)] from payload ts, divided by speed."""
    t0, last, out = None, 0.0, []
    for line in lines:
        ts = line["payload"].get("ts") if isinstance(line["payload"], dict) else None
        if ts:
            t = parse_iso(ts)
            t0 = t0 or t
            last = max(last, (t - t0).total_seconds() / speed)
        out.append((last, line))
    return out


def retimed(payload: dict, offset_s: float, start) -> dict:
    if not isinstance(payload, dict) or "ts" not in payload:
        return payload
    return {**payload, "ts": to_site_iso(start + timedelta(seconds=offset_s))}
