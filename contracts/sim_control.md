# Simulator control API — v1

Agreed by: P1 ☐

P1's simulator exposes:

| Method | Path | Body / response |
|---|---|---|
| POST | `{SIM_CONTROL_URL}/scenario` | `{name, machine_id?}` → `{ok, name, started_at}` |
| GET | `{SIM_CONTROL_URL}/scenarios` | `{scenarios: [name, ...]}` |

Edge `POST /sim/scenario` and `GET /sim/scenarios` proxy these calls (Phase 4). With `SIM_MODE=replay` the edge replays `backend/tests/fixtures/scenarios/{name}.jsonl` instead, so the demo never waits on the simulator.

Replay mode only (edge addition, not part of P1's API): after a scenario ends, its last raw frame keeps being re-sent at the fixture's frame rate (`SIM_REPLAY_HOLD`), so the machine stays in the end state. `POST /sim/stop` ends playback and the hold. Only one source may publish raw frames for a machine at a time: two publishers interleave, and the switch debounce and rules then see neither one's machine.

Required scenario names. Each one is deterministic and ends in a steady state:

| name | What it must produce (signals on `raw.v1` unless noted) |
|---|---|
| `reset` | Normal work, belted, no DTCs, no people nearby |
| `unsafe_exit` | Belt off → seat vacated (+2 s) → door open with bucket raised (~2.1 m) and hydraulics unlocked. Then the correction: bucket down, hydraulics locked, engine off, about 14 s after the seat is vacated. Wet steps (env RH ≥ 90%, dew spread ≤ 2 °C). HLD §3.2. |
| `safe_exit` | Bucket grounded, hydraulics locked, engine off, *then* belt off → seat vacated |
| `proximity_intrusion` | Person 2–6 m REAR on `sim/proximity` while swinging |
| `drive_into_zone` | TRAVEL into a pinned hazard zone (x/y move) |
| `dtc_1638_16` | `dtc.v1` SPN 1638 FMI 16 active, hydraulic oil temp ≈ 96 °C |
| `belt_bypass` | Belt FASTENED, seat_occupied false > 5 s, engine running |
| `long_idle_unbelted` | ≥ 9 min idle, belt off, engine running, seat occupied |
| `tilt_excursion` | Roll 11–17° for 20–120 s |
| `overheat_trend` | Hydraulic oil rising 1 °C / 3 min toward the DTC |

Edge replay-only addition (Phase 8 demo, not required from P1): `demo_e001` = `dtc.v1` SPN 520192 FMI 31 `cat_code: E001` on EXC001 with `hyd_lockout` missing from t=10 (catalogue `DEMO-E001`, a placeholder, not a real CAT code).

`sim_label` must be set on the frames that belong to the injected behaviour (HLD §6.14).
