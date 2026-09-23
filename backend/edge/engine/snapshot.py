"""Engine snapshot: persist/rehydrate a MachineEngine (plan.md Phase 3 item 7, D18).

What survives a restart: context signals + timers + injected fields, the open exit, active
alerts with their cooldown/once-per memory, the classifier, the event seq and disabled
rules. Proximity frames are kept too; their 1 s freshness check makes them UNKNOWN after any
real outage. What doesn't survive (deliberately): switch debounce history (the first frame
after a restart is a baseline, not a change) and an in-progress idle accumulator.
"""

# ponytail: an idle episode in progress at a crash restarts from the next frame; persist
# IdleTracker._accum too if episode-level fuel/rpm accuracy across restarts ever matters.

from dataclasses import fields
from datetime import datetime

_DT = "$dt"


def encode(obj):
    """JSON-safe copy; datetimes become {"$dt": iso}."""
    if isinstance(obj, datetime):
        return {_DT: obj.isoformat()}
    if isinstance(obj, dict):
        return {k: encode(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [encode(v) for v in obj]
    return obj


def decode(obj):
    if isinstance(obj, dict):
        if set(obj) == {_DT}:
            return datetime.fromisoformat(obj[_DT])
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    return obj


_CTX_SKIP = {"machine_id", "site_id", "model", "has_rear_camera"}


def dump(engine) -> dict:
    ctx = engine.ctx
    return encode(
        {
            "ctx": {f.name: getattr(ctx, f.name) for f in fields(ctx) if f.name not in _CTX_SKIP},
            "exits": {k: getattr(engine.exits, k) for k in engine.exits.PERSIST},
            "alerts": {k: getattr(engine.alerts, k) for k in engine.alerts.PERSIST},
            "classifier": {k: getattr(engine.classifier, k) for k in engine.classifier.PERSIST},
            "seq_next": engine.seq._next,
            "disabled_rules": engine.disabled_rules,
        }
    )


def load(engine, payload: dict) -> None:
    s = decode(payload)
    for k, v in s["ctx"].items():
        setattr(engine.ctx, k, v)
    for part in ("exits", "alerts", "classifier"):
        for k, v in s[part].items():
            setattr(getattr(engine, part), k, v)
    engine.seq._next = s["seq_next"]
    engine.disabled_rules = s["disabled_rules"]
