# Build progress

One entry per phase (plan.md §0 rule 3): what was built, gate results, deviations.

---

## Phase 6 — Prediction and learning: ML adapter, ETA + tasks, anomaly + R23, lessons + Replay ✅ (24 h scope) (2026-09-24)

### Scope
- **Cut (24 h plan):** scorecard, model hot-swap, fatigue samples/R19, fuel baseline R24.
- **Kept after all (user, mid-build):** anomaly detection. `"the anomaly detection is there - keep it"`, so the anomaly job and R23 are built and R23 is `enabled: true` again.
- **P1 (ML) owns the models.** The backend loads them only through `ml_runtime` (`contracts/ml_runtime.md`). P1's package and model files aren't delivered yet, so the gates use a test stub with the same interface.

### Built
- **Demo data relative to now (user request).** `tools/seed.run(engine, now, settings)` uses the runtime's clock.
  - Demo tasks attach to the shift for `now`'s date, scheduled as offsets from the shift's planned start (`tasks_demo.yaml` `start/end_after_shift_min`). Ids carry the date. Shifts and tasks are insert-only, so a restart never resets them.
  - OP1001 gets one earlier unsafe exit (R03 alert, cleared, plus an UNSAFE exit_event, corrected) dated `now − 2 days`, with UUIDv7 ids from that instant (`common/ids.uuid7_at`). It is inserted only if OP1001 has no R03 in the last 7 days. It is demo master data like the rest of the seed, so no outbox rows.
  - Lessons are seeded from `lessons.json`.
- **ML adapter** (`edge/ml/adapter.py`):
  - Imports `ml_runtime`. For `eta` / `anomaly_EXCAVATOR` / `anomaly_WHEEL_LOADER` it takes the active `model_registry` row, checks the sha256 of `MODELS_DIR/{name}/{version}`, then calls `load()`.
  - Any failure (package missing, no row, missing file, sha mismatch, load raising) → that model is UNAVAILABLE, with the reason in `/system/status.model_detail`.
  - Predictions that raise or are invalid (e.g. p50 ≤ 0) are refused → fallback.
  - `tools/register_model.py` is the P1 hand-off (hot-swap is cut: register, then restart).
  - The stub `tests/stubs/ml_runtime/` is test-only.
- **ETA** (`edge/ml/features.py`, `edge/ml/eta_service.py`):
  - Exact `ml_features.md` keys. The operator rate follows the cold-start ladder: operator (≥ 3 done tasks) → experience cohort → task-type midpoint.
  - Generic fallback per plan.md §5.6, using the new `data/seed/soil_types.yaml` (HLD §6.7 fill factors; clay mapping `assumption: true`; wet clay +10 % cycle time). p90 = 1.4 × p50. CLEANUP gets no prediction.
  - Chain: IN_PROGRESS → actual_start + p50 + unplanned-stop minutes (union of engine-off and operator-out intervals); next tasks → max(scheduled_start, previous ETA) + p50; a DONE task's actual_end feeds the chain.
  - Predictions are stored on the task (+ `pred_label`, `pred_rate_source`; schema v4) with outbox P1. WS `eta` is pushed for every change. The snapshot `eta` = the current or next task.
  - Triggers: shift start, readiness, task status, IGNITION_OFF/ON, EXIT_COMPLETED/MOUNT, and a rain flip.
- **Tasks REST**: `GET /tasks`, `POST /tasks/{id}/status`, `GET /tasks/{id}/eta` (task.v1-validated).
- **Hourly windows live + anomaly** (`edge/ml/anomaly_job.py`):
  - The engine now owns the Phase 2 `RollupAccumulator`. Closed minutes go out as `telemetry_minute` (P3); closed hours as `telemetry_window` (P2), with `alert_count`, `critical_count` and `safety_alert_triggered` counted by the engine.
  - A closed window → runner `on_window` → anomaly queue → the frozen feature dict + robust z vs the operator's 14-day windows (median/MAD) → the family model → `anomaly_score`, `anomaly_flagged`, `anomaly_top_features` (schema v4) + outbox.
  - Then `set_anomaly` → **R23 INFO, visual only**. `review_queue()` = flagged, unlabelled windows.
- **Lessons + Replay** (`edge/lessons/engine.py`, `edge/api/lessons.py`):
  - A RAISED `lesson_trigger` rule for an operator → assign if CRITICAL or ≥ 2× in 7 days (reason `"R03 ×2 in 7 days"` / `"R03 critical"`). One open assignment per (operator, lesson). Lesson pick: rule → family, else the subject's generic lesson.
  - R03 creates a Replay `scenario` from that alert's exact inputs, filling P3's template placeholders only.
  - Deliverable only when the operator's machine is known OFF, or no shift is ACTIVE. `GET /lessons/{assignment_id}` and `/complete` return **409 otherwise (I5, server-side)**. WS `lesson` is pushed when deliverability flips.
  - `data/lessons/lessons.json` is a **lesson.v1 stub** (all text marked STUB) until P3 delivers (Q7).
- **LEARN/PREDICT triggers run off an in-process Broadcaster listener, not MQTT**, so they keep working with the broker down. On queue overflow they resync (recompute everything), rather than skip.

### Gate results
| Gate | Result |
|---|---|
| ETA with the stub model: p50/p90/drivers/model_version | ✅ `test_eta_with_stub_model` (model version, 3 drivers, `/system/status` LOCAL + version); with 3 done tasks → `OPERATOR` source, no label, p50 = 141.2 min as hand-computed |
| Model missing / corrupt → generic, labelled, no 500 | ✅ file deleted, sha tampered, prediction invalid → "Estimate (generic)", `model_version generic`, p50 155.3; `model_detail.errors` says why |
| Generic maths | ✅ `test_eta.py`: TRUCK_LOAD 420 m³ CLAY_WET → p50 155.3 / p90 217.4 (hand-computed in the test); CLEANUP → none |
| Unplanned stop 12 min moves the ETA ≈ 12 min | ✅ engine OFF 07:01 → ON 07:13 on an IN_PROGRESS task → ETA +12 min, WS `eta` pushed |
| A status change on task 1 shifts task 2 | ✅ task 1 DONE early → task 2 = its 07:30 slot + 140.5 min |
| Model hot-swap | ✂️ cut (24 h); a sha mismatch at load is still rejected |
| Anomaly: window at the top of the stub score → R23 INFO, no AUDIO, review queue | ✅ 13 L/h vs a 7.6–8.4 L/h baseline → flagged, top feature `fuel_per_productive_h`, R23 INFO `[VISUAL]`, in `review_queue`, outbox P2. Live hour boundary → window persisted with alert counts and scored automatically. No model → no score, R23 UNKNOWN, `anomaly UNAVAILABLE` |
| Lessons: second R03 in 7 days → assignment; active → 409; engine off → 200 + WS `lesson {deliverable:true}` | ✅ `test_seed_at_0030_finds_tasks_and_second_offence` (**user request**: seeded at 00:30 → both tasks on today's shift with ETAs; the live unsafe exit → "R03 ×2 in 7 days"; GET/complete 409 while running; engine OFF → WS lesson true; complete 200 + P2) |
| Replay from the unsafe-exit fixture has the real bucket height and failed checks | ✅ prompt: "…bucket at 2.1 m and these checks failing: ENGINE_RUNNING, HYD_UNLOCKED, IMPLEMENT_RAISED…"; `state_vector` = the R03 inputs |
| Scorecard | ✂️ cut (24 h) |
| Live compose smoke | ✅ tasks show ETAs (09:50 / 15:35, generic, since `ml_runtime` isn't installed yet); `ws_probe --scenario unsafe_exit --expect nudge:R03:FULLSCREEN --expect lesson:deliverable=True` passes; lesson reason "R03 ×2 in 7 days"; 0 errors in the logs |
| Full suite | ✅ **243 passed, 2 skipped**; ruff clean; OpenAPI + TS regenerated (20 files) |

### Decisions / deviations
1. The **PAUSED exclusion** in unplanned-stop minutes isn't built: task status history isn't stored, so every engine-off / operator-out stop after `actual_start` counts.
2. **Truck-count trigger** skipped: tasks are cloud-authoritative and the inbox is cut, so the edge has no source for truck changes.
3. Hourly windows keep the operator of their first sample and aren't closed on shift change. The open hour isn't snapshotted (`ponytail:` note in the engine).
4. The demo task 1 now starts 07:15 (it was 06:15, before the 07:00 shift).
5. `tests/unit/test_engine_scenarios.py` now filters its DB checks to the fixture day, since the seed adds demo history. The Phase 5 00:30 test moved its "no shift yet" path to WL001, because the seed now plans the shift for the clock's date.
6. `/system/status` gains `model_detail`. WS `eta` and `lesson` payloads carry `ts` (I6).

### Open items
- **P1:** deliver `ml_runtime` + the eta / anomaly model files + pinned library versions (D24), then register them (RUNBOOK).
- **P3:** the real `lessons.json` (Q7) replaces the stub.
- Not committed (asked before committing).

---

## Phase 5 — Operator services: auth, shift, readiness, incidents, hazards + geofencing ✅ (24 h scope) (2026-09-24)

### Scope cut (user decision, applies to the rest of the build)
With 24 h left, the user cut: walkaround, guest mode (an unknown badge stays a 401), readiness override, the multi-edge test, rules **R08/R09/R15/R19/R22/R23/R24** (now `enabled: false` in `rules.yaml`), the anomaly job, scorecard, model hot-swap, fatigue samples, the CV worker (stretch), embeddings/sqlite-vec, the cloud LLM, sync pull streams, media upload, hazard LWW, the weather job, the edge-side fleet API and the load test. Expired or deleted pins are **removed** from the retained list; the tombstone stays in the DB only. Also skipped: `/idle/{id}/reason`, because it is the R09 chip and R09 is cut.

### Built
- **Auth** (`edge/api/auth.py`): a login with `machine_id` binds that machine's current shift (ACTIVE, else today's row). The JWT carries a `shift_id` claim. **`exp = max(planned_end + 2 h, now + JWT_TTL_S)`** (user request), so a token is never born expired, whether you log in late or past midnight. `exp` is checked against the runtime clock that issued it. `LoginResponse.shift` is filled.
- **Shift** (`common/shifts.py`, `edge/api/shift.py`): `SH-YYYYMMDD-{machine}-D` per site-local date. The window comes from the new **`SHIFT_START`/`SHIFT_END`** settings (user request; END ≤ START = overnight). `/shift/start` creates today's row if none exists (this covers crossing midnight), marks it ACTIVE, records `engine_hours_start` (None when stale), links today's latest readiness check, and tells the engine the operator, shift and rating. It is idempotent for the same operator, and returns 409 if the shift is ACTIVE for someone else or already CLOSED today. `/shift/end` takes `handover_note`; `/shift/current` returns the shift and the last handover note left on the machine. Shift writes go through the outbox (P1).
- **Seed**: new `data/seed/shifts_demo.yaml`. Today's PLANNED shifts are EXC001/OP1001 and **EXC002/OP1002 ("Operator B", user request)**. Shifts are insert-if-absent, so `EDGE_SEED_ON_START` never resets an ACTIVE shift on a container restart.
- **Readiness** (`edge/readiness/score.py` pure + `edge/api/readiness.py`): HLD §7.4 exactly. All numbers are in the new `rules.yaml` `readiness:` block (hld_ref §7.4; the population default of 300 ms is tagged `assumption: true`). The personal baseline is the median of the last checks once there are ≥ 5. Reasons are i18n keys (`readiness.reason.*`, added to `en.json`). The check goes to the outbox (RED → P0). The rating reaches the engine at shift start, or immediately if the operator's shift is already ACTIVE, and **R20 fires on RED at shift start** (escalated → P0). No route reads the rating (I10).
- **Hazards** (`edge/hazards.py`, `edge/api/hazards.py`): create, `PATCH {CONFIRM|RESOLVE}` and `DELETE` (tombstone, supervisor). Every change is one unit of work with version+1, `updated_at` and a P0 outbox row (`DELETE` op for tombstones), then the full ACTIVE list is republished **retained**. Validation uses the HLD §6.11 ranges from the new `rules.yaml` `hazards:` block (radius 5–50, default 10; `line_clearance_m` 4–15, required for OVERHEAD_LINE). `GET /hazards?bbox=` is supported. WORKER_ZONE and SOFT_GROUND expire after 24 h; CONFIRM restarts the clock; a job runs every `HAZARD_EXPIRY_CHECK_S`.
- **Retained bus**: `Bus.publish(..., retain=False)`. `MemoryBus` delivers retained messages to later subscribers (broker semantics). `MqttBus` remembers the last retained payload per topic and republishes it on every (re)connect, before subscribing, so a change made while the broker was down still lands.
- **Engines learn pins only from the retained topic** (`Runtime._consume_hazards` → `MachineEngine.set_hazards` + WS `hazards`), including within one process, per plan.md §2. Pins live in `ctx.hazard_pins`, which is snapshotted, so a crash-restart keeps them.
- **Geofence** (`edge/geo/geofence.py`, shapely, pure): point zones use distance ≤ radius, polygons use `covers`. A zone is left only once the machine is more than R14 `exit_hysteresis_m` (2 m) outside. It runs in `on_raw` and in `set_hazards` (a pin dropped on the machine is entered at once; a removed pin is left at once, which clears R14). It is **paused while `sensor_health.position` isn't OK**: zones hold, R14 evaluates UNKNOWN and holds, and the state shows position UNKNOWN. It emits `GEOFENCE_ENTER`/`GEOFENCE_EXIT` events.
- **Incidents** (`edge/api/incidents.py`): multipart with `incident` JSON, ≤ 1 `voice` and ≤ 3 `photos`. There is a content-type whitelist (415), a `MEDIA_MAX_BYTES` cap (413) and server-made names under `MEDIA_DIR/{incident_id}/`; files are removed if the DB write fails. The server fills ts, x/y (from a live fix only), `state_snapshot` (engine state + a last-60 s telemetry summary) and `linked_alert_ids` (the last 5 min). `create_hazard` puts the pin in the **same** unit of work (both P0) and republishes. With no position, the incident is still saved, with no pin and `hazard_error` saying why.
- **Env one-taps** (`edge/api/env.py`): `/env/ground` and `/env/manual` publish `env.v1` (MANUAL) on the normal env path. `CurrentEnv` now resolves `ground_condition` on its own, so a ground tap doesn't freeze the SIM weather, and a MANUAL ground outranks SIM's.
- **Config/infra**: `SHIFT_START`, `SHIFT_END`, `MEDIA_DIR`, `MEDIA_MAX_BYTES`, `HAZARD_EXPIRY_CHECK_S` in `Settings` and `.env.example`; compose `MEDIA_DIR=/data/media`. New deps: `shapely`, `python-multipart` (re-locked). `SCHEMA_VERSION` 3 (`shift.handover_note`, `readiness_check.reasons`). Contracts updated: `rest.md` (Phase 5 rows, cuts, the `DELETE /hazards/{id}` addition), `ws.md`, `topics.md` and `en.json`; OpenAPI + TS regenerated. RUNBOOK has a Phase 5 section.

### Gate results
| Gate | Result |
|---|---|
| Auth: right PIN → token; wrong PIN → 401; unknown badge → 401 (guest cut); operator token rejected on supervisor routes | ✅ `test_auth_roles_and_unknown_badge` (DELETE /hazards 403, `/ws/site` 4403). Login touches only SQLite + bcrypt (no network) |
| Login at 21:00 and 00:30 (user request) | ✅ 21:00 with TTL 60 s → exp = now + 60 s (planned_end + 2 h already past), later calls work, `/shift/start` 200. 00:30 on a new date with an overnight window → no shift on login, token valid; `/shift/start` creates `SH-{new date}-EXC001-D` 19:00 → 07:00 ACTIVE, and a re-login binds it. 08:00 → exp = 20:00 (TTL wins over 19:00) |
| Readiness table incl. D3 | ✅ `test_readiness.py` (15 cases, every bracket, highest-only, floor, bands). `5/14` → 85 GREEN; `5/11` → 70 YELLOW (also via the API, schema-validated) |
| RED → R20 + P0; no endpoint refuses a RED operator | ✅ RED check → P0 outbox; `/shift/start` 200 → R20 WARNING escalated, P0 alert row, state readiness RED; `/shift/current` + `/hazards` 200; shift end clears R20 |
| Incident: voice + 2 photos, snapshot, links, P0, `create_hazard` | ✅ files on disk, snapshot state + last_60s, the active alerts linked, P0 incident + P0 pin, pin on the retained list; 4 photos → 422, PDF → 415, bad severity → 422 with nothing left on disk; no position → saved, no pin, reason given |
| Hazard retained: a new subscriber gets the full list with versions | ✅ MemoryBus (unit) + **real Mosquitto** (`test_hazards_retained_reach_a_late_subscriber`, including a list published before the bus connected) + live stack (`mosquitto_sub` on the running edge showed the pin) |
| Multi-edge test | ✂️ cut (24 h). Its demo beat is covered on one instance: pin created on the API → EXC002 (Operator B) drives in → **R14 on EXC002 within 2 s** of the first frame inside the zone (`test_operator_b_drives_into_operator_a_zone`); live: `ws_probe --machine EXC002 --scenario drive_into_zone --expect nudge:R14` → ok at +8.5 s (speed 4) |
| Geofence hysteresis: ±1 m at the edge → one ENTER | ✅ one ENTER, one EXIT only at 3 m outside (> 2 m) |
| Power line R15 | ✂️ R15 cut (disabled). Predicate correctness stays pinned by `test_rules.py` (5.5 m → True, 4.5 m → False at clearance 8 m) |
| Expiry: WORKER_ZONE after 24 h → expired, off the retained list, tombstoned | ✅ +25 h → tombstoned (deleted, v2), off the list, outbox UPSERT → DELETE; the OVERHEAD_LINE pin (no expiry) stays |
| Position stale 11 s → geofence paused, visible in sensor_health | ✅ 12 s without x/y while driving out → no EXIT, R14 held (not cleared), `sensor_health.position = UNKNOWN`, class not PRODUCTIVE |
| Full suite | ✅ **224 passed, 2 skipped** (broker restart opt-in; P1 traces absent); ruff check + format clean; `gen_ts.sh` → TS types OK, 20 files |

### Bugs found and fixed
1. **The DbWriter thread died when a caller stopped waiting (Phase 1 bug, found by the Phase 5 tests).** A cancelled caller (request aborted, lifespan shutdown) cancels its future. `set_result` then raised `InvalidStateError`, and so did the fallback `set_exception`, killing the writer thread: **every later write, including safety alerts and outbox rows, would silently stop.** Now the unit still commits and the result is dropped if nobody is waiting. Regression test `test_writer_survives_a_cancelled_caller` fails on the old code and passes on the new.
2. `MqttBus` dropped retained publishes made while disconnected. It now republishes them on connect (see Built).

### Decisions / deviations
1. **R20 with no readiness check = False, not UNKNOWN.** Readiness is advisory, not a safety sensor (I10); an UNKNOWN would have held R20 active forever after shift end. `test_rules.py` has a dedicated case for it.
2. Unknown heat (no env) → `heat_level NONE`, no penalty (advisory, I10).
3. `/incidents` `operator_id` = the operator on shift on that machine (None if none); `reporter_id` = the token's subject. Paths are relative to `MEDIA_DIR`.
4. `DELETE /hazards/{id}` (supervisor) added for the plan's "CRUD … tombstone on delete". RESOLVE is limited to the reporter or a supervisor.
5. The DB has one day shift per machine per date. A CLOSED shift can't be restarted the same day (409); delete the DB to reset the demo.
6. The token's `exp` is verified against the runtime clock, not the host clock (same clock that issued it; tests drive it).
7. `/env/manual` MANUAL weather outranks SIM with no expiry (`ponytail:` note in `CurrentEnv`).
8. The live container's dev DB (v2, seeded gate data only) was deleted so the v3 schema could start; the seed recreated it.

### Open items
- Teammate review: `readiness.reason.*` wording, the hazard validation ranges, `DELETE /hazards/{id}`, and the incident response shape (`{incident, hazard, hazard_error}`).
- P3: `/shift/*`, `/readiness`, `/incidents`, `/hazards` and `/env/*` are in `openapi.json` / `contracts/ts`.
- Not committed (the user asked not to commit without asking).

---

## Phase 4 — Nudges, WebSocket bridge, core REST → CP1 ✅ except the tablet demo (2026-09-24)

### Built
- **Config** `common/config.py` (`Settings`, pydantic-settings). Every host, port, secret, path and interval comes from env; each one is documented in `infra/.env.example`. The site and machines come from the DB (`EDGE_SITE_ID` / `EDGE_MACHINE_IDS` narrow it), thresholds from `machine_model` rows, the DTC catalogue from `diagnostic_code` rows, rules/tones/checklist order from `rules.yaml`, and the scenario list from a directory listing. The seed, rules and eval tools read `DATA_DIR`/`CONTRACTS_DIR`.
- **Runtime** `edge/runtime.py` + `create_app()` in `edge/main.py`. The lifespan does: DB (`create_all` or idempotent seed) → `DbWriter` → one `EngineRunner` per machine (rehydrated from `engine_snapshot`) → `MqttBus` → site-level env recorder (stores `environment_obs` once per site; deferred from Phase 3) → sync-status publisher.
- **Bus/broadcast**: `MqttBus` (aiomqtt) with a reconnect loop, re-subscribe on reconnect, and counters for dropped messages in and out. `edge/broadcast.py` fans each engine step out to WS clients (per-client filter; a bounded queue closes the socket on overflow so the client re-snapshots, I6) and to MQTT per `topics.md`.
- **Nudges** `edge/nudge/generator.py`: alert → `nudge.v1`. Display is per rule (or the per-raise override, e.g. R05's degraded-seat BANNER). The tone comes from `policy.tone_patterns`. No voice clip or tone when the budget dropped AUDIO. The Exit Guard `checklist` follows the new `exit_checks.checklist_order`, filtered to the machine's own checks (the loader adds PARK_BRAKE_OFF). `AlertManager.nudge_due` covers raises, repeats, escalations and un-suppression, never clears, acks or suppressed alerts.
- **Audio priority** (user decision): optional `audio_priority` per rule; raises on the same tick are budgeted in priority order. R06 = 1, so "steps may be slippery" now wins the voice slot over R04 (Phase 3 open item closed).
- **Auth** `edge/api/auth.py`: `/auth/login` (badge = employee_code, bcrypt PIN, HS256 JWT with `sub`/`role`/`machine_id`/`exp`). New `operator.role` column; `SCHEMA_VERSION` 2, and an older DB is refused with a clear message. `SUP001` = admin (user decision). An operator token bound to a machine sees only that machine. `AUTH_DISABLED` (default false in compose) makes every request admin, is logged at WARNING and is reported in `/system/status`.
- **REST**: `/state/current`, `/alerts/{id}/ack` (ack ≠ clear; 404 unknown / 409 inactive; goes through `EngineRunner.call()` so the engine keeps a single owner), `/alerts/{id}/why`, `/sim/scenarios`, `/sim/scenario`, `/sim/stop`, `/system/status`, `/health`.
- **WS** `edge/ws/live.py`: `/ws/live`, `/ws/site` (supervisor+). Snapshot on connect (subscribe first, so there are no gaps), a ping task every `WS_PING_S`, and a per-client telemetry throttle. Close codes 4401/4403/4404/1013.
- **Replay mode** `edge/sim.py`: plays `SIM_SCENARIOS_DIR/{name}.jsonl` onto the bus (the real ingest path), re-timed to now, re-targeted to this site and optionally another machine. A hold at the fixture's own frame rate keeps the end state; `/sim/stop` ends it.
- **Fixtures** (user decisions): the contract names in `tests/fixtures/scenarios/` are `reset`, `unsafe_exit` (the old `unsafe_exit_corrected`), `proximity_intrusion`, `drive_into_zone`, `dtc_1638_16` (new), plus `safe_exit`, `belt_bypass` and `tilt_excursion`. The last was rewritten to the contract: roll 11° → 17° for 40 s. Phase-3-only fixtures moved to `tests/fixtures/engine/`.
- **Tools**: `tools/ws_probe.py`, `tools/latency_probe.py`, `common/replay.py` (scheduling shared with `tools/replay.py`), and `gen_ts.sh` now also emits `ts/openapi.d.ts` (openapi-typescript 7.13.0).
- **Infra/docs**: compose passes the edge env (`AUTH_DISABLED` default false, `SIM_MODE` default replay, seed on start, `edge-data` volume). `Dockerfile.edge` copies `data/` and binds 0.0.0.0. The new `docs/RUNBOOK.md` covers the tablet URL `http://<laptop-LAN-IP>:8000`, `CORS_ORIGINS` for P3's dev/PWA origin, auth, scenarios and the probes. `ws.md` documents the message order within one engine step.

### Gate results
| Gate | Result |
|---|---|
| `ws_probe --scenario unsafe_exit --expect nudge:R03:FULLSCREEN --expect state:exit_state=SAFE --timeout 30` (replay mode, compose stack) | ✅ R03 FULLSCREEN at +15.8 s, exit_state SAFE at +19.3 s. Needs `--speed 4`: the fixture's intent is at t=63 s, so at 1× it can't fit in 30 s |
| Latency p95 < 300 ms, 50 runs | ✅ `latency_probe --runs 50`: **p50 13.1 ms · p95 15.7 ms · max 21.1 ms** (MQTT publish → WS nudge, compose on this laptop; not yet on the demo laptop) |
| `/alerts/{id}/why` for R03 | ✅ rule id, name, hld_ref, inputs (exit_state UNSAFE, hyd UNLOCKED …), thresholds (`test_api.py`) |
| Ack sets `acknowledged_at`; FULLSCREEN CRITICAL stays until its condition clears | ✅ ack → acknowledged_at + ack_by, still active; the reconnect snapshot shows it acked + active; it clears on correction; acking again → 409 |
| WS reconnect mid-scenario → snapshot = current truth | ✅ exit_state UNSAFE, class UNSAFE, R03 active + acked |
| openapi.json + TS types | ✅ exported; `gen_ts.sh` → `TS types OK: 20 files` (`tsc --strict`) |
| CP1 demo on the real tablet with P3 | ⏳ team step, not done yet (RUNBOOK has the LAN/CORS setup) |
| MQTT reconnect (added after the gate review) | ✅ `RUN_BROKER_RESTART=1 pytest tests/integration -k reconnect`: broker container restarted under a connected `MqttBus` → disconnect noticed, publishes while down counted in `dropped_out` (no exception), reconnect + re-subscribe, delivery resumes. The live edge-api went through the same restart: `mqtt_disconnected` → `mqtt_connected` in 2 s, `/system/status` mqtt.connected true, and `ws_probe` unsafe_exit passed again afterwards |
| Full suite | ✅ 187 passed, 1 skipped (eval_rules: no P1 traces). Includes 2 integration tests against the real Mosquitto. ruff check + format clean |

### Bugs found and fixed during the gate
1. **Latency probe ran ~50 min without finishing (tool bug, found live).** Every wait reset its timeout on each incoming message, and there was no overall deadline, so a run that couldn't complete looped forever. It couldn't complete because the `reset` scenario's hold frames were publishing raw frames for EXC001 at 1 Hz at the same time. Interleaved with the probe's frames, the 500 ms leading-edge debounce suppressed or reverted the probe's switch changes, so no exit intent rose and no R03 was raised. Reproduced offline with the real engine: hold interleaved at 6 phase offsets → 1 of 6 triggers produced no R03 each time; no other publisher → 50/50. The probe also stopped reading at the nudge, and the edge emits the step's `state` right after it, so its "exit closed" check used stale state. **Fix:** hard deadline on every wait (exit 2 with a reason); a reader task consumes the socket continuously; the edge's `telemetry` echo (same ts) confirms each probe frame was processed; the probe refuses to start while a replay scenario runs (`--stop-replay`); the raw topic must be quiet for `data_stale_s` first; it aborts the moment a foreign raw frame appears (verified live: a scenario started mid-run → exit 2 within 1 s, naming the topic). New `POST /sim/stop`.
2. **JWT leaked into container logs.** uvicorn logs the WS URL, including `?token=`. Now `common/log.RedactSecrets` on `uvicorn.access`/`uvicorn.error` masks it. Verified live: 0 tokens in the logs after the rebuild.
3. **The edge couldn't shut down under load (Python 3.11).** `asyncio.wait_for(queue.get(), t)` can swallow a cancellation that races a completing `get()`. A runner fed continuously then ignored cancel and hung the lifespan shutdown (found via a hanging test). `asyncio.timeout` on 3.11 has the mirror bug (a stray pending cancel). **Fix:** there are no timeouts on queue reads any more: ticks and pings are queued by small ticker tasks instead. The WS handler cancels its child tasks without awaiting them inside a cancellation, so it doesn't re-deliver the cancel past the server's scope.
4. Replay hold at `speed` > 1 was flooding the bus (hold spacing divided by speed). The hold now runs at the fixture's real frame rate.

### Decisions / deviations
1. Roles, scenario set, `tilt_excursion` content, R06 audio priority, LAN/CORS/AUTH defaults: user decisions (above).
2. `POST /sim/stop` is an edge addition, replay mode only (proxy → 409, since P1's control API has no stop). Recorded in `rest.md` and `sim_control.md`.
3. `sync_status` reports `online:false`, `last_success_at:null` and the real `queue_depth` until the Phase 9 agent exists; `models` report `UNAVAILABLE` until Phase 6 (`edge/ml/adapter.py`); `eta` in the snapshot is null. All reported as-is rather than faked.
4. Access log redaction covers `token=` / `access_token=` query values only.
5. The dev `infra/.env` (gitignored) was created with a random `EDGE_JWT_SECRET` for the gate run.

### How much the probe hang affected the application
- **Product path: no impact.** Ingest, rules, nudges, WS, REST and persistence were correct throughout. Every probe run that did complete measured about 10–21 ms. The hang was in the measuring tool.
- **Real constraint it exposed:** one publisher per machine. If P1's simulator and a replay (or `tools/replay.py`) drive the same machine, their frames interleave and the engine sees neither machine correctly. Now documented in `sim_control.md` and the RUNBOOK, and prevented for the probe. The edge itself doesn't detect a second publisher (MQTT carries no publisher identity). For the demo: only one of `SIM_MODE=replay` or P1's sim per machine.
- **Real bugs it surfaced:** the token leak (security) and the 3.11 cancellation hang (shutdown/restart reliability, and the D18 container-restart path). Both fixed.

### Open items
- CP1 on the real tablet with P3 over the travel-router LAN.
- Rerun `latency_probe` on the demo laptop and record the numbers here.
- Not automated yet: the `ws_probe` gate as a test against a running edge; the WS telemetry throttle, ping and overflow-close paths (no dedicated tests).
- Teammate review: `/sim/stop`, the message order note in `ws.md`, the new contract-named fixtures (P1 may replace them with sim recordings).
- Not committed (user asked not to commit or push).

---

## Phase 3 — Operator State Engine + Risk Engine ✅ (2026-09-23)

### Built
`backend/edge/engine/` (plan.md Phase 3 items 1–9), plus the minimum runtime the gate needs.

- `rules.py`: loads `contracts/rules.yaml` into frozen `Rule`/`RuleSet` dataclasses, plus machine-model thresholds, machines and the DTC catalogue from `data/`. No threshold lives in engine code.
- `context.py`: `MachineContext`. Latest debounced signals, read only through `sig()`, which returns `None` when the stream is stale (I1, I6). Also holds the timers from item 1, env, proximity per source (prefer fresh `cv` with `frame_ok`, else fresh `sim`, else UNKNOWN/UNAVAILABLE), active DTCs, and injectable later-phase fields (`zones_inside`, `readiness`, `anomaly`, `fuel_ratio`, `fatigue`, `task_type`). `sensor_health()` covers seat, seatbelt, position, proximity and data.
- `predicates.py`: Kleene tri-state `k_and`/`k_or`/`k_not` plus the HLD §4.4 predicates (running, active, idle, grounded, moving, exit_intent, exit_hint, wet, lift_mode). Thresholds come from rules.yaml `predicates`.
- `registry.py`: one function per rule R01–R24 returning `Eval(value, key, inputs, thresholds, slots, display)`. `key` scopes the multi-instance and once-per rules (DTC occurrence, zone entry, power-line pin, exit, idle episode, shift, hint event, anomaly window). R19/R22 are implemented; `enabled: false` is respected.
- `exits.py`: D1 check set (PARK_BRAKE_OFF for loaders only; SLOPE uses the model's caution thresholds) and `exit_state`. `ExitTracker` covers intent → `exit_event` (STRONG, D19) + `EXIT_ATTEMPT`, corrected/`time_to_correct_s`, R06 once per exit, `EXIT_COMPLETED` after the seat has been vacated ≥ 10 s, `MOUNT` + `time_outside_s`, and cancel.
- `alerts.py`: `AlertManager`. Handles raise/repeat/escalate/clear, cooldowns, once-per keys, same-subject suppression (`HIGHER_ACTIVE`, stored, not nudged, and it doesn't spend the audio budget), the 30 s non-CRITICAL audio budget, CRITICAL exemption (I4), `state_snapshot = {inputs, thresholds}`, `notify_supervisor → escalated`, and the non-critical alerts-per-operating-hour metric. Every raise/clear/escalate is logged as JSON with rule_id and inputs.
- `classify.py`: §5.3 class order + 10 s step-down hysteresis. An UNKNOWN sensor, stale data or UNKNOWN engine state gives ATTENTION, never PRODUCTIVE.
- `snapshot.py`: JSON-safe dump/restore of context, open exit, alert manager, classifier, event seq and disabled rules.
- `machine.py`: `MachineEngine`, the synchronous, clock-injected pipeline. raw → validate (drop + count) → strip `sim_label` (I9) → normalise → debounce + switch events → stale → context → idle → evaluate. It reuses all the Phase 2 ingest modules unchanged. Evaluation runs on every raw frame, DTC, env change, proximity message and 1 s tick. A rule that raises is disabled and logged, and the other rules keep running.
- `supervisor.py`: `EngineRunner` (asyncio). Subscribes on the bus, ticks every 1 s, persists each step as one `DbWriter` unit of work (entity + outbox, I7), publishes `state`/`alert`/`event`, and snapshots to `engine_snapshot` every 1 s and on change. On any fault it rebuilds the engine from the last snapshot in-process (D18).
- `edge/bus.py`: `Bus` protocol + `MemoryBus` (MQTT wildcards). `MqttBus` comes in Phase 4.
- `common/levels.py`, `common/log.py` (`jlog`: one JSON object per log line), `common/timeutil.MonotonicClock`. `common/db/repo.py` gains engine write helpers (machine_event, safety_alert, exit_event, dtc_occurrence, idle_episode, engine_snapshot).
- `tools/eval_rules.py`: replays P1's labelled traces through `MachineEngine` and reports per-episode recall for UNSAFE_EXIT/BELT_BYPASS plus a frame-level confusion matrix vs `sim_label` → `docs/eval_rules.md`. It is the only engine-side reader of `sim_label`.
- Fixtures: 8 specs in `tests/fixtures/specs/` → `tests/fixtures/scenarios/*.jsonl` (generated by `fake_machine.py`, unchanged; a test checks each committed JSONL equals a fresh generation).

### Gate results
| Gate | Result |
|---|---|
| One test per rule R01–R24 (true / false / UNKNOWN) | ✅ `test_rules.py`. 21 parametrised triples, plus dedicated tests for R06 (tracker flag), R16/R17 (STOP/MONITOR/other/UNDOCUMENTED). R19/R22 never fire while disabled and fire when enabled in-test (`test_engine.py`) |
| I1: seat missing + belt off + running | ✅ `sensor_health.seat=UNKNOWN`, class ATTENTION (never PRODUCTIVE), no R03, R05 as BANNER with failed checks. A missing `hyd_lockout` → HYD_UNLOCKED UNKNOWN counted as FAIL → UNSAFE → R03 |
| `unsafe_exit_corrected` | ✅ R03 on the intent frame (t=63); checklist FAIL→PASS in order IMPLEMENT_RAISED → HYD_UNLOCKED → ENGINE_RUNNING; exit_event UNSAFE → corrected, `time_to_correct_s` 9 (±1); R06 once; class UNSAFE until 81, lower at 82 (≥ 10 s after R03 cleared at 72); exit_event outbox rows P0 |
| `safe_exit` | ✅ no R03; R04 at 33; no R06 (dry) |
| `belt_off_parked` | ✅ R02 INFO, VISUAL only, at 10; UPDATED to CAUTION with AUDIO at 310; no R01 |
| `belt_off_moving` | ✅ R01 CRITICAL at 10, repeats at 30, 50, AUDIO every time |
| `belt_bypass` | ✅ R07 WARNING at 16, `escalated=true`, P0 outbox row |
| `tilt` | ✅ R10 at 10, R11 at 20, R10 marked suppressed `HIGHER_ACTIVE` at 20 |
| `proximity_sim` | ✅ R13 at 5, R12 at 7 (3.0 m < 3.8 m, working), cleared at 11; nothing while idle; `frame_ok:false` with no fresh sim frame → proximity UNKNOWN |
| `dtc_stop` | ✅ R16 CRITICAL with catalogue `action_class: STOP` at 10, cleared at 40 |
| Audio budget | ✅ 3 non-critical in 10 s → only the first has AUDIO; a CRITICAL in the same window keeps AUDIO |
| Crash test | ✅ injected fault 3 s into the unsafe exit → 1 restart, rehydrate took 0.0002 s (< 2 s); open exit + active R03 restored; exactly one EXIT_ATTEMPT in the DB; the same R03 alert clears once corrected |
| Rule isolation | ✅ R10's predicate raising → R10 disabled + logged once (`rule_disabled` JSON); R03 still fires |
| Hysteresis | ✅ UNSAFE clears at t → still UNSAFE at t+9.9 s, lower at t+10 s |
| `eval_rules.py` 100 % recall | Skipped, visibly: P1 hasn't delivered `data/history/traces/`. The tool itself ran on the two labelled scenario fixtures: UNSAFE_EXIT 1/1, BELT_BYPASS 1/1 |
| Bus payloads | ✅ everything the runner publishes validates against `state.v1`, `alert.v1`, `event.v1` |
| Full suite | ✅ 164 passed, 1 skipped (eval_rules); `ruff check` + `ruff format --check` clean |

### Decisions / deviations
1. **UNKNOWN exit_intent (user decision, I1).** An UNKNOWN intent does not raise R03. `exit_checks` are still computed (UNKNOWN = FAIL), `sensor_health.seat = UNKNOWN`, class ≥ ATTENTION. Seat UNKNOWN + belt OFF + running + any check FAIL/UNKNOWN → R05 shown as a **BANNER** with `slots.failed_checks`. Seat **and** door UNKNOWN + belt OFF + running → intent counts as TRUE, so R03 fires. Config: R05 `params: {degraded_seat_display: BANNER}` in rules.yaml.
2. **UNKNOWN never clears.** A rule evaluating `None` neither raises nor clears. An active alert holds until its inputs are definitively False (tested with R11 and pitch going missing).
3. **CRITICAL ignores cooldown (I4).** A CRITICAL rule re-raises each time its condition becomes true again, even inside `cooldown_s` (R11 20 s, R12/R15 5 s). Cooldowns still apply to non-critical rules. A flapping CRITICAL condition therefore re-alerts every time; that is the I4 trade-off.
4. **Minimal runtime built now, not in Phase 4.** The gate calls for MemoryBus scenario tests and a crash/restart test, so `edge/bus.py` (MemoryBus only) and `EngineRunner` exist now. `MqttBus`, MQTT publishing, nudges and the lifespan wiring stay in Phase 4.
5. **Engine clock.** It is injected. Live runs use `MonotonicClock` (a UTC anchor plus `time.monotonic()`, so timers are monotonic but still serialisable). Tests and `eval_rules.py` use frame `ts`.
6. **Exit cancel.** Seat re-occupied before the exit completes (< 10 s): the tracker closes the exit with no EXIT_COMPLETED/MOUNT. The `exit_event` row written at intent stays (it was a real strong intent and may already be queued as P0), with `time_outside_s = null`.
7. **`replay_10min.jsonl` timing.** Under D1 its exit is corrected **9 s** after intent (intent at t=303 when belt OFF + door open; it leaves UNSAFE at 312 once the hydraulics are locked with the bucket down → SAFE_ENGINE_ON). The spec comment's "14 s" counts seat vacated → engine off. The fixture is unchanged (P3 uses it); `test_replay_10min_fixture_for_p3` pins 9 s.
8. **Sensor health.** `UNAVAILABLE` = not fitted (no rear camera and no proximity channel ever seen). `UNKNOWN` = expected but missing, stale or `frame_ok:false`. Any UNKNOWN → ATTENTION. So EXC001 (`has_rear_camera: true`) is ATTENTION until the CV worker (Phase 7) or a sim proximity channel feeds it. That is honest per I1, but P3 will see ATTENTION, not PRODUCTIVE, during normal work in replay.
9. **R21 "outside cab"** = seat not occupied or an exit open. R10 with lift mode UNKNOWN on an excavator uses the stricter lift-mode roll threshold (I1).
10. **`escalated`** = `notify_supervisor` (R07, R20 → P0). R02's 300 s escalation changes level/channels via `escalate_to` and does not set `escalated`.
11. **Env obs are not persisted by the engine.** They are site-level, and one engine per machine would duplicate rows. Phase 4's ingest wiring stores them once per site. The engine still uses them for `wet`/R21.
12. **Real bug found by the bus contract test:** `repo.insert_machine_event` added the caller's object to the writer session. After commit, SQLAlchemy expires it, so the event published afterwards serialised as `{}`. It now adds a copy.

### Open items / for the team
- **Audio budget vs the demo beat (HLD §3.2).** In the unsafe-exit demo, R04 ("machine secured, engine running") and R06 ("steps may be slippery") are raised on the same tick. R04 is evaluated first and takes the 30 s audio slot, so R06 is visual-only. That is the budget working as specified, but the demo script wants the three-point-contact voice line. Options: evaluate R06 before R04, give R06 priority, or accept it. Needs a team call.
- **UNKNOWN `wet`** (no env source) never prompts R06. R06 is advisory (state_effect NONE), and no env source is a normal state, so prompting on every exit would mostly add audio fatigue. Flagging because it treats UNKNOWN as "no prompt".
- **UNDOCUMENTED DTCs** fire neither R16 nor R17 (I3: never guessed). They are still logged as a WARNING `DTC_ACTIVE` event. Consider a dedicated rule if the team wants a visible prompt.
- R06 stays active until the exit closes (MOUNT/cancel). It carries no state effect, but it does sit in `active_rules`.
- `eval_rules.py` 100 % recall gate: rerun when P1 delivers `data/history/traces/`.
- `IdleTracker` state in progress is not snapshotted. An idle episode spanning a crash restarts from the next frame (`ponytail:` note in `snapshot.py`).
- OpenAPI/TS regeneration starts in Phase 4 (no API surface changed here).
- Not committed (user asked not to commit or push).

---

## Phase 2 — Ingestion, events, environment, rollups ✅ (2026-09-23)

### Built
`backend/edge/ingest/`, all 9 modules from plan.md Phase 2, as pure functions/classes — no live bus wiring yet (that's Phase 4's `bus.py`/`main.py` lifespan; Phase 2 is about the logic being correct and independently testable, per the plan's own dependency-check note).

- `normalise.py`: `validate_raw` (malformed → drop, caller counts), `normalise` (raw.v1 → telemetry_sample dict). Missing/null signal stays `None` (I1). Derives `implement_grounded` (D12), coarse `machine_activity`, a small `data_quality` bitmask.
- `switches.py`: `Debouncer` — leading-edge debounce (D2). The window is refreshed on every suppressed flap, not fixed from the original accept, so a burst of flapping faster than 500 ms never lets a second change through no matter how long it continues.
- `events.py`: switch→event type map (HLD §6.9), `Seq` (monotonic per-machine), `make_event`.
- `stale.py`: `StaleTracker` — `DATA_STALE` after 3 s of silence, `DATA_RESTORED` on resume, one event per stale period.
- `env.py`: dew point (Magnus), heat index (NOAA Rothfusz + NWS simple-formula fallback), WBGT_est, heat level bands, source-priority `CurrentEnv`.
- `dtc.py`: `DtcTracker` — active/repeat/cleared lifecycle, catalogue lookup for `action_class` (UNDOCUMENTED if unknown, never guessed — I3).
- `idle.py`: `IdleTracker` — episodes confirmed only after 30 s of inactivity (HLD §4.4), then backdated to when inactivity actually started so duration matches reality, not the confirmation delay. Buckets PAUSE/SHORT/MEDIUM/LONG.
- `rollup.py`: `RollupAccumulator` — 1-min and hourly rollups, fuel/load counters as deltas of the cumulative meters, fuel split into idle/work, `fuel_per_load` null under 3 loads (§6.13 guard), idle bucket counts fed from finished `idle.py` episodes (D17: PAUSE counts toward `idling_time_min` only). Fields Phase 3+ owns (`alert_count`, `state_class_mode`, `anomaly_score`, ...) are explicit `None`, not a fake `0`.
- `retention.py`: `purge_old_samples` — 7-day cutoff delete, callable now, scheduled later (Phase 11).

### Gate results
| Gate | Result |
|---|---|
| SPN mapping for every §5.2 key; missing → UNKNOWN; malformed dropped | ✅ every key round-trips; empty `can` → every mapped field `None`; also validated end-to-end against the real `replay_10min.jsonl` fixture (activity classification matches the known unsafe-exit stretch) |
| Debounce: flap every 100 ms for 1 s → exactly one change, no delay | ✅ (see deviation below — first version of the window logic didn't survive this) |
| Stale: `DATA_STALE` within 3-4 s, resume clears it | ✅ |
| Env formulas match hand-computed values for 3 fixed inputs | ✅ dew point/heat index/WBGT all within 0.01; heat index cross-checked against the published NOAA reference (90°F/70% RH → ~106°F, got 105.9°F) |
| Idle: pauses of 2/4/7/12 min → PAUSE/SHORT/MEDIUM/LONG; counts 1/1/1; `idling_time_min` includes the 2-min pause | ✅ |
| Rollup: 2 L/h idle for 60 min → `fuel_used_l` ≈ 2.0; loads<3 → `fuel_per_load` null (§6.13 artefact guard); loads≥3 → populated | ✅ |
| Organizer anchors (D14) | Skipped, visibly: P1 hasn't delivered anchor traces yet (`data/history/` absent, per `load_history.py`'s own check) |
| Full suite | ✅ 96 passed; ruff clean |

### Deviations / decisions (flagged, not asked in advance)
1. **No pipeline/wiring module this phase.** Considered adding one to run the full `replay_10min.jsonl` fixture end-to-end, but the plan's repo layout puts bus wiring under Phase 4 (`bus.py`, `main.py` lifespan). Building it now would mean building it twice. Each module is unit-tested directly instead, including one test that runs `normalise` over every real fixture frame.
2. **Found and fixed a real debounce bug via the gate test itself, not just a design read.** A "fixed window from the original accept" implementation passes a shallow reading of D2 but fails the plan's own worked example (100 ms flapping for a full second must yield exactly one change). Fixed by refreshing the window on every suppressed attempt instead of only measuring from the last accepted change.
3. **India's UTC+5:30 offset has a half-hour component** — a UTC-midnight-based test time is *not* hour-aligned in site-local time, which silently broke two rollup window-boundary tests before I noticed. Fixed the tests (site-local `ZoneInfo("Asia/Kolkata")` starting points) and left a comment on it in `test_ingest.py` since the next person writing a rollup test will hit the same trap.
4. **`idle.py`'s per-episode dt-based accumulators (`seatbelt_off_min`, `mean_rpm`) undercount by about one sample period per episode** — the first tick of a new inactive run has `dt=0` since there's nothing to measure from yet. `duration_min` itself is exact (computed from the actual start/end timestamps, not by summing dt). Documented in the module; not worth the extra state to fix for a sub-1-second-per-episode error.
5. **Rollup attributes an entire inter-sample interval to the window containing the later sample's timestamp**, and caps any gap at 2 s before accumulating it. At 1 Hz this is at most a fraction of a second of misattribution at each minute/hour boundary. Documented as the upgrade path in `rollup.py` if a slower feed ever makes it matter.
6. **`safety_alert_triggered`, `alert_count`, `critical_count`, `state_class_mode`, `anomaly_score` are explicit `None`** in the hourly window Phase 2 produces, not `0`/`False` — those are Phase 3/6/10's fields, and a fake zero would be indistinguishable from "confirmed zero" later.

### Open items
- Wiring these modules to a live MQTT feed is Phase 4 (`bus.py`, `main.py`).
- Organizer anchor reproduction (D14) waits on P1's `data/history/` export.

### Post-review fixes (2026-09-23, same day — requested check of Phase 2)
Re-read every module against the plan and ran `normalise`/`rollup` over the real
`replay_10min.jsonl` fixture end-to-end (not just synthetic unit inputs). Found two real
bugs neither the gate tests nor my own synthetic tests had caught:
1. **`normalise._activity` read `travel_speed_kmh` from the wrong dict.** `raw.v1` puts it
   under `can` (SPN 84), but `_activity` only looked in `sens`. Every wheel-loader travel
   segment silently classified as IDLE (or WORKING, if swing/hyd happened to be nonzero)
   instead of TRAVELLING — this would have corrupted the fuel/productive split for every
   950GC LOAD_CARRY task once rollup consumed it. Fixed: `_activity` now takes the
   correctly-sourced value directly instead of guessing which dict holds it.
2. **`rollup.py`'s `payload_t` read this sample's `payload_kg` at the moment `load_count`
   increments**, but the real simulator's cycle resets `payload_kg` to 0 on the exact same
   tick it increments `load_count` for the next cycle — confirmed against the real fixture,
   which computed `payload_t: 0.0` despite 3 real completed loads. Fixed with a
   watermark (max `payload_kg` seen since the last increment); same fixture now gives
   `payload_t: 6.0` (3 × 2000 kg, matching `fake_machine.py`'s per-load weight).
3. Also fixed a stale docstring on `Debouncer` (still described the old 2-tuple state
   shape from before the leading-edge-refresh fix) and a `0.0`-is-falsy landmine in the old
   `sens.get(x) or sens.get(y)` fallback pattern the `_activity` fix also removed.

Added regression tests for both (`test_normalise_detects_travelling_from_can_not_sens`,
`test_rollup_payload_t_survives_load_count_and_payload_reset_on_the_same_tick`, and a
fixture-level check for each). Full suite: 99 passed. Not committed — left for the user.

### Known-but-not-fixed: per-machine tracker scoping isn't type-enforced
`Debouncer`, `IdleTracker` and `DtcTracker` are documented "one instance per machine" but
don't enforce it internally (unlike `StaleTracker`, which is explicitly multi-machine,
keyed by `machine_id`). If Phase 4's wiring code accidentally shares one instance across
machines, DTCs/idle episodes/switch state from different machines with the same key would
collide. Not a bug today (nothing in Phase 2 wires multiple machines together), but worth
a look when Phase 4 actually instantiates these per-machine.

---

## Phase 1 — Storage layer ✅ (2026-09-23)

### Built
- `common/ids.py`: `uuid7()` via `uuid-utils` (D4).
- `common/db/models.py`: all 35 SQLModel tables from plan.md Phase 1 item 1 (every HLD §6 entity + the plan.md §3 additions: `window_id`/`review_label` on `telemetry_window`, `active`/`cleared_at` on `safety_alert`, `engine_snapshot`, `machine_state_snapshot`, `device`, `sim_label_log`, `schema_version`). Composite indexes on every time-series table and on `sync_queue (status, priority, seq)`.
- `common/db/session.py`: engine factory with SQLite WAL/synchronous/busy_timeout/foreign_keys pragmas; same models work unchanged against Postgres.
- `common/db/writer.py`: `DbWriter` — single writer thread, one queue. A unit of work runs in one transaction (commit on return, rollback on raise = I7). `submit_telemetry` batches 1 Hz rows and never touches the outbox (I8).
- `common/db/repo.py`: the priority table from plan.md Phase 1 item 2, `enqueue_sync`, `insert_incident`, and `upsert_machine_state_snapshot` (D8 coalescing — one current row per machine, at most one PENDING outbox row per machine).
- `tools/seed.py`: loads `data/seed/*.yaml` + `data/catalogue/diagnostic_codes.yaml`, `session.merge()` per row (idempotent by construction). Seeds site, both machine models (with D9 assumptions flagged), 3 machines (EXC001, WL001, EXC002), 2 attachments, 4 operators (bcrypt PIN hashes from a plaintext `seed_pin`, never a precomputed hash), 8 task types, 9 diagnostic codes, and today's demo shift + 2 tasks for EXC001.
- `tools/load_history.py`: no-op stub, prints which files are missing under `data/history/`.
- `data/seed/*.yaml`, `data/catalogue/diagnostic_codes.yaml`: every value has an HLD/plan reference in a comment; `assumptions: [...]` flags fields the team hasn't confirmed (site lat/lon, machine serials, the D9 950 GC proximity distances, cert expiry dates).

### Gate results
| Gate | Result |
|---|---|
| `create_all` on fresh SQLite | ✅ 35 tables |
| `create_all` on Postgres (`--profile cloud up -d postgres`) | ✅ same models, same table set, round-tripped an `Incident` insert |
| Outbox atomicity | ✅ failing unit of work persists neither the incident nor a sync_queue row; success path persists both with matching `entity_id` and `priority: 0` |
| Coalescing | ✅ 3 `machine_state_snapshot` writes for EXC001 while PENDING → exactly 1 PENDING row, payload from the last write |
| `telemetry_sample` never enqueues outbox | ✅ |
| Concurrency (5 concurrent writes racing 60 simulated 1 Hz telemetry rows) | ✅ no `database is locked`; all 60 telemetry rows + all 5 events land |
| `python tools/seed.py` idempotent | ✅ identical row counts across two runs (site 1, machine_model 2, attachment 2, machine 3, operator 4, task_type 8, diagnostic_code 9, shift 1, task 2) |
| Full suite | ✅ 72 passed; ruff clean |

### Deviations from plan.md (flagged, not asked in advance — noting here per rule 4)
1. **Timestamps are ISO-8601 strings, not native `DateTime` columns.** SQLite drops tzinfo on `DateTime`; Postgres needs `timezone=True` to keep it. Since every timestamp on the wire is already this exact string, storing the same string sidesteps a real cross-dialect bug for no cost. String columns still sort correctly (fixed-width, zero-padded).
2. **`infra/docker-compose.yml`: added `ports: ["5432:5432"]` to the `postgres` service** so the cross-DB gate test (and any dev tooling) can reach it from the host. Dev/test only.
3. **`shift.walkaround_id` ↔ `walkaround.shift_id` is a genuine circular FK** (a shift has a walkaround; a walkaround belongs to a shift — both directions are in the HLD). Postgres refuses to `DROP`/topologically sort a true cycle, so `shift.walkaround_id`'s FK is declared `use_alter=True` with an explicit name (`fk_shift_walkaround_id`), deferring it to an `ALTER TABLE`. Anywhere code inserts a `Shift` and a `Walkaround` that reference each other in one flush, commit the `Shift` first (without `walkaround_id`), then the `Walkaround`, then update `shift.walkaround_id` — don't rely on a single flush to order it. Same caution applies to `shift.readiness_check_id` (no cycle there since `readiness_check.shift_id` has no FK constraint, but the sequencing is the same in practice: readiness check happens before shift start).
4. **Enum-shaped columns (`class`, `level`, `status`, ...) are plain `str`,** not a DB enum type. The allowed values are already the single source of truth in `contracts/schemas/*.json`; a DB-level CHECK per dialect would be a second copy to keep in sync for no benefit at this stage.
5. **`assumptions` is a real JSON column only on `MachineModel` and `TaskType`.** Other seed tables (`Site`, `Attachment`, `Machine`, `Operator`) carry `assumptions:` in their YAML as reviewer metadata, but `tools/seed.py` drops it before constructing the row — there's no current reader for "this machine's serial number is a guess" at runtime, unlike the D9 proximity thresholds a rule actually evaluates.

### Open items
- `data/history/` doesn't exist yet — `load_history.py` correctly no-ops. Wire the real import once P1 delivers (`contracts/history.md`).
- Postgres/cloud containers were left running locally after the gate check (`docker compose --profile cloud up -d postgres`); harmless, stop with `docker compose -f infra/docker-compose.yml --profile cloud down` if not needed.

---

## Phase 0 — Foundations and contracts ✅ (2026-09-23)

### Built
- Repo: `backend/` (pyproject, `requirements.lock`, ruff, pytest), `contracts/`, `infra/`, `docs/`, git initialised.
- `infra/docker-compose.yml`: mosquitto + edge-api by default. Profiles: `sim` (replay loop placeholder), `llm` (ollama), `cloud` (postgres + cloud-api). One image, `infra/Dockerfile.edge`.
- `edge-api` and `cloud-api`: `/health` only (`{status, service, ts}` with site offset).
- 19 JSON Schemas in `contracts/schemas/`: raw, dtc, proximity, env, telemetry, event, state, alert, nudge, hazards, task, incident, readiness, lesson, sync_push, sync_push_ack, sync_pull, sync_status, ws_envelope. There are 21 examples in `contracts/examples/`, and the HLD §6.15 samples plus the plan §5.2 raw frame are verbatim.
- `contracts/rules.yaml`: R01–R24, plus predicate thresholds, the Exit Guard check set (D1) and the alert-fatigue policy. R19 and R22 are `enabled: false` (D21).
- `contracts/i18n/en.json` (30 keys) and `contracts/audio_clips.yaml` (23 voice clips, 5 tones).
- Contract docs, each with an "Agreed by" line: `topics.md`, `rest.md`, `ws.md`, `sim_control.md`, `ml_runtime.md`, `ml_features.md`, `history.md`.
- `contracts/ts/*.d.ts` generated by `backend/tools/gen_ts.sh`. `contracts/openapi.json` generated by `backend/tools/export_openapi.py`.
- Tools: `replay.py` (spacing from `ts`, shifts ts to now unless `--keep-ts`, `--loop`), `record.py`, `fake_machine.py` (deterministic YAML → JSONL fixture scripter).
- `backend/tests/fixtures/replay_10min.jsonl` for P3: 10 min of EXC001 truck loading, one unsafe exit corrected in 14 s (HLD §3.2), wet steps, remount. It is generated from `tests/fixtures/specs/replay_10min.yaml`.

### Gate results
| Gate | Result |
|---|---|
| `docker compose up -d` → mosquitto, edge-api healthy; `curl :8000/health` → 200 | ✅ both healthy; `{"status":"ok","service":"edge-api","ts":"2026-09-23T19:04:45.992+05:30"}` HTTP 200. Also checked: the `cloud` profile (postgres + cloud-api healthy, `:8001/health` 200) and the `sim` profile (raw frames flowing). |
| `pytest tests/unit/test_contracts.py` | ✅ 45 passed. Every example validates; every schema is valid 2020-12 and has an example; rules.yaml has R01–R24 with all keys; every message_key is in en.json; every audio_clip is in audio_clips.yaml; CRITICAL rules carry AUDIO (I4); R19/R22 are disabled. |
| `replay.py replay_10min.jsonl --speed 10` + `mosquitto_sub -t 'cat/#'` | ✅ `replayed 601 frames in 59.9 s`; the subscriber got 600 × `cat/SITE-PUN-01/EXC001/raw` + 1 × `cat/SITE-PUN-01/env`. `mosquitto_sub` ran inside the broker container (not installed on the host). Record → replay round trip also verified. |
| TS types compile with `tsc --noEmit` | ✅ `TS types OK: 19 files` (`--strict`) |
| Full suite | ✅ 53 passed; `ruff check` and `ruff format --check` clean |

### Deviations from plan.md (agreed with user before building)
1. **Ollama is behind the compose profile `llm`.** Its health gate moves to Phase 8, where it is first used.
2. **Deps are locked per phase.** `requirements.lock` holds only what Phase 0 imports. Each phase adds its own pins (sklearn/lightgbm still follow D24).
3. **Pydantic generation from the JSON Schemas is deferred** to Phase 2, where `raw.v1` is first validated in code. JSON Schema stays the source of truth (user decision).
4. **One Dockerfile** (`Dockerfile.edge`) for edge, cloud and the replay placeholder. `Dockerfile.cloud` gets split out in Phase 9 when psycopg arrives. `Dockerfile.cv` comes in Phase 7.
5. The replay fixture is **generated** by `fake_machine.py`, not recorded from P1's sim, because the sim does not exist yet. A test checks the committed JSONL equals a fresh generation.

### Contract decisions made inside Phase 0 (additive; flagged for teammate review)
- `rules.yaml` is a mapping (`predicates`, `exit_checks`, `policy`, `rules`), not a bare list. This keeps the predicate thresholds (§4.4) and the alert policy in the same file with `hld_ref`s.
- Two keys were added to each rule: `once_per` (for HLD "per exit / per code / …" cooldowns) and `escalate_to` (R02 → CAUTION + soft chime). `display_proposed: true` marks display modes the HLD does not specify.
- R14 uses per-hazard-type messages: `nudge.geofence.enter.{hazard_type}`. The test expands the placeholder over all 7 hazard types.
- Additions to `event.v1`: a `DATA_RESTORED` type (stale recovery needs an event). Additions to `env.v1`: a `SIM` source. Additions to `dtc.v1`: an optional `cat_code` (for E360/E361).
- `telemetry.v1`: the HLD sample is kept verbatim. Missing HLD §6.8 fields (boom_tip_height_m, parking_brake, gear, articulation, joystick, lift_mode, power_mode, battery_v, activity_hint) are optional. `motion.activity` is the coarse backend enum; the sim's fine phase goes in `activity_hint`.
- `alert.v1` = `{schema, ts, action, alert}`. The same shape goes on MQTT and on the WS `alert` data.
- `proximity.v1` also carries `schema` + `machine_id`, so every payload is self-describing.
- Text marked `source: proposed` in `audio_clips.yaml` is backend wording where the HLD gives none (12 of 30 keys). P3/team should review it. The tone descriptions are proposals too.
- `sim_control.md`: the response body `{ok, name, started_at}` is a proposal.

### Open items
- Teammate sign-off on every contract doc (Q1, Q2, Q3 in plan.md §10).
- `ml_runtime.md`: P1 fills in the pinned library versions (D24).
- The starlette TestClient prints a deprecation warning about httpx. It is harmless; revisit when bumping FastAPI.
