"""Deterministic test-fixture scripter: small YAML scenario -> JSONL of MQTT frames.

NOT a simulator (plan.md §1.2). No randomness, no physics beyond counters. The real
simulator is P1's. Only grow this when a fixture needs it.

Output line: {"topic": str, "payload": obj}  (same format tools/replay.py reads).

Spec:
  site_id, machine_id, start (ISO with offset), duration_s
  initial: {signal: value}      # flat names: normalised CAN names (common.spn) + raw.v1 sens keys
  timeline:                     # applied in order at `at` seconds (float ok -> extra raw frame)
    - at: 0
      mode: WORK                # OFF | IDLE | WORK | TRAVEL, animates engine/motion signals
      set: {signal: value}      # applied after mode
      label: UNSAFE_EXIT        # sim_label from here on (null clears); eval only (I9)
      env: {...}                # env.v1 fields -> cat/{site}/env
      dtc: {...}                # dtc.v1 fields -> cat/{site}/{machine}/dtc
      proximity: {...}          # proximity.v1 fields -> .../{source}/proximity (default sim)
"""

import argparse
import json
import math
import sys
from datetime import timedelta
from pathlib import Path

import yaml

from common.spn import FIELD_SPNS
from common.timeutil import parse_iso, to_site_iso

# ponytail: CAT-320-ish values only (HLD §6.0 ranges); add CAT-950GC presets when needed.
IDLE = dict(
    engine_state="RUNNING",
    engine_rpm=1000,
    engine_load_pct=12,
    fuel_rate_lph=2.1,
    engine_oil_press_kpa=290,
    hyd_pump_press_kpa=3100,
    swing_rate_deg_s=0.0,
    travel_speed_kmh=0.0,
    joystick_active=False,
    payload_kg=0,
    activity_hint="IDLE",
)
OFF = dict(
    IDLE,
    engine_state="OFF",
    engine_rpm=0,
    engine_load_pct=0,
    fuel_rate_lph=0.0,
    engine_oil_press_kpa=0,
    hyd_pump_press_kpa=0,
    activity_hint="OFF",
)
TRAVEL = dict(
    IDLE,
    engine_rpm=1700,
    engine_load_pct=40,
    fuel_rate_lph=10.0,
    hyd_pump_press_kpa=8000,
    travel_speed_kmh=3.0,
    joystick_active=True,
    activity_hint="TRAVELLING",
)
# 18 s truck-loading pass (HLD §6.0: 15–22 s); mean fuel 11.5 L/h (medium work).
WORK_PHASES = [  # (hint, seconds, overrides)
    (
        "DIGGING",
        5,
        dict(
            swing_rate_deg_s=0.0,
            bucket_height_m=-1.5,
            hyd_pump_press_kpa=24000,
            engine_load_pct=75,
            fuel_rate_lph=15.0,
            payload_kg=0,
        ),
    ),
    (
        "SWING_LOADED",
        4,
        dict(
            swing_rate_deg_s=30.0,
            bucket_height_m=3.0,
            hyd_pump_press_kpa=18000,
            engine_load_pct=60,
            fuel_rate_lph=12.0,
            payload_kg=2000,
        ),
    ),
    (
        "DUMPING",
        3,
        dict(
            swing_rate_deg_s=0.0,
            bucket_height_m=3.0,
            hyd_pump_press_kpa=14000,
            engine_load_pct=45,
            fuel_rate_lph=10.0,
            payload_kg=0,
        ),
    ),
    (
        "SWING_EMPTY",
        6,
        dict(
            swing_rate_deg_s=-20.0,
            bucket_height_m=1.0,
            hyd_pump_press_kpa=12000,
            engine_load_pct=40,
            fuel_rate_lph=9.0,
            payload_kg=0,
        ),
    ),
]
PRESETS = {"OFF": OFF, "IDLE": IDLE, "TRAVEL": TRAVEL, "WORK": IDLE}  # WORK animates on top
CYCLE_S = sum(s for _, s, _ in WORK_PHASES)
PASSES_PER_LOAD = 6


def work_phase(t_in_mode: float) -> tuple[str, dict]:
    x = t_in_mode % CYCLE_S
    for hint, secs, over in WORK_PHASES:
        if x < secs:
            return hint, over
        x -= secs
    raise AssertionError("unreachable")


def generate(spec: dict) -> list[dict]:
    site, machine = spec["site_id"], spec["machine_id"]
    t0 = parse_iso(spec["start"])
    events = sorted(spec.get("timeline", []), key=lambda e: e["at"])  # stable: keeps file order
    times = sorted(
        {float(t) for t in range(int(spec["duration_s"]))} | {float(e["at"]) for e in events}
    )

    state = dict(spec.get("initial", {}))
    mode, mode_start, label, prev_t = "OFF", 0.0, None, None
    passes_done = 0
    out: list[dict] = []

    def ts(t: float) -> str:
        return to_site_iso(t0 + timedelta(seconds=t))

    def emit(topic: str, payload: dict) -> None:
        out.append({"topic": topic, "payload": payload})

    for t in times:
        if prev_t is not None:  # integrate counters over the elapsed interval at the old rates
            dt = t - prev_t
            running = state.get("engine_state") == "RUNNING"
            state["fuel_used_total_l"] = round(
                state.get("fuel_used_total_l", 0.0) + state.get("fuel_rate_lph", 0.0) * dt / 3600, 4
            )
            if running:
                state["engine_hours"] = round(state.get("engine_hours", 0.0) + dt / 3600, 4)
            if running and mode == "IDLE":
                state["idle_hours_total"] = round(state.get("idle_hours_total", 0.0) + dt / 3600, 4)
            v = state.get("travel_speed_kmh") or 0.0
            if v:
                h = math.radians(state.get("heading_deg") or 0.0)
                state["x_m"] = round(state["x_m"] + v / 3.6 * math.sin(h) * dt, 2)
                state["y_m"] = round(state["y_m"] + v / 3.6 * math.cos(h) * dt, 2)
        prev_t = t

        for e in (e for e in events if float(e["at"]) == t):
            if "mode" in e:
                if e["mode"] not in PRESETS:
                    raise ValueError(f'at={e["at"]}: bad mode {e["mode"]!r} (quote "OFF" in YAML)')
                mode, mode_start, passes_done = e["mode"], t, 0
                state.update(PRESETS[mode])
            state.update(e.get("set", {}))
            if "label" in e:
                label = e["label"]
            if "env" in e:
                emit(
                    f"cat/{site}/env",
                    {"schema": "env.v1", "ts": ts(t), "site_id": site, "source": "SIM", **e["env"]},
                )
            if "dtc" in e:
                emit(
                    f"cat/{site}/{machine}/dtc",
                    {
                        "schema": "dtc.v1",
                        "ts": ts(t),
                        "site_id": site,
                        "machine_id": machine,
                        "oc": 1,
                        "cat_code": None,
                        "freeze_frame": None,
                        **e["dtc"],
                    },
                )
            if "proximity" in e:
                p = {
                    "schema": "proximity.v1",
                    "ts": ts(t),
                    "machine_id": machine,
                    "sector": None,
                    "conf": None,
                    "frame_ok": True,
                    "source": "sim",
                    **e["proximity"],
                }
                emit(f"cat/{site}/{machine}/{p['source']}/proximity", p)

        if mode == "WORK":
            hint, over = work_phase(t - mode_start)
            state.update(over, engine_rpm=1800, joystick_active=True, activity_hint=hint)
            done = int((t - mode_start) // CYCLE_S)  # a pass completes at the end of each cycle
            while passes_done < done:
                passes_done += 1
                state["pass_count"] = state.get("pass_count", 0) + 1
                if state["pass_count"] % PASSES_PER_LOAD == 0:
                    state["load_count"] = state.get("load_count", 0) + 1

        can = {FIELD_SPNS[k]: v for k, v in state.items() if k in FIELD_SPNS}
        sens = {k: v for k, v in state.items() if k not in FIELD_SPNS}
        emit(
            f"cat/{site}/{machine}/raw",
            {
                "schema": "raw.v1",
                "ts": ts(t),
                "site_id": site,
                "machine_id": machine,
                "can": can,
                "sens": sens,
                "sim_label": label,
            },
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="output JSONL (default stdout)")
    args = ap.parse_args()
    lines = generate(yaml.safe_load(args.spec.read_text()))
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    if args.out:
        args.out.write_text(text)
        print(f"{len(lines)} frames -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
