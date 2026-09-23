# Operator Companion — Backend Build Plan (Edge + Cloud)

*For Claude Code. Owner: backend (P2). Companion doc to `docs/hld.md` (the HLD, "cat-operator-companion-hld.md").*

The HLD is the source of truth for **what** the product does. This plan is the source of truth for **how the backend is built**. Where they disagree, this plan wins; every deviation is listed in §3 with the reason.

---

## 0. Working rules for Claude Code (read first)

1. Read this entire file and `docs/hld.md` before writing any code. Copy the HLD into the repo at `docs/hld.md`.
2. Build phases strictly in order (§7). A phase is done only when every item in its **Verification gate** passes. Never start the next phase with a failing gate — fix it, or stop and report what is blocking.
3. After each phase: run the full test suite (not just the phase's tests), append to `docs/PROGRESS.md` (phase, what was built, gate results with a short summary of command output, deviations from this plan), and commit as `phase-N: <summary>`.
4. Do not build anything teammates own (§1.2). When you need their output before it exists, use the stub/fixture this plan names, behind the same interface, so swapping in the real thing needs zero backend changes.
5. Never invent safety numbers, machine specs, or CAT documentation. Every threshold lives in `contracts/rules.yaml` or `data/seed/*.yaml` with an `hld_ref`. Anything this plan marks **ASSUMPTION** stays tagged `assumption: true` in config so the team can review it.
6. If this plan is ambiguous or contradicts itself, stop and ask. Do not guess.
7. Keep it simple: single process for edge-api, asyncio tasks, no Kafka/Celery/Redis/Kubernetes/ORM migrations framework. `create_all` + a `schema_version` table is enough.

### 0.1 Safety invariants (tests must enforce every one)

| ID | Invariant |
|---|---|
| I1 | A missing, faulted or stale signal evaluates as **UNKNOWN**, never SAFE. UNKNOWN on any Exit Guard check counts as a failed check. A machine with an UNKNOWN safety-critical sensor can never be classed PRODUCTIVE. |
| I2 | Advisory only. No endpoint, topic or code path may command or interlock the machine. |
| I3 | The LLM is never on the safety path. Rules, alerts, nudges and `action_class` never depend on LLM output; `action_class` is always copied from the catalogue. |
| I4 | CRITICAL audio is never rate-limited, suppressed or downgraded. |
| I5 | A lesson is never deliverable while the machine is `active` (server-side check, not just UI). |
| I6 | Stale data is never presented as live. Every payload carries `ts`; every snapshot/list response carries `as_of`. |
| I7 | Any write that must reach the cloud inserts its `sync_queue` row in the **same SQLite transaction**. |
| I8 | Raw 1 Hz telemetry never leaves the edge (only 1-min rollups and above sync). |
| I9 | `sim_label` is never read by rules, engine or inference code — only by `tools/eval_*.py`. |
| I10 | Readiness and fatigue never block starting or operating the machine. |

---

## 1. Scope

### 1.1 Backend owns

Mosquitto config and bus client · ingestion/normalisation · SQLite schema and repositories · Operator State Engine · Context/Risk (rule) Engine · alert-fatigue manager · Exit Guard/exit tracking · nudge selection · WebSocket bridge · all edge REST · auth (offline JWT) · readiness scoring endpoint · hazard pins + geofencing · incident/near-miss intake · micro-lesson assignment/delivery + Replay scenario generation · scorecard computation · ML inference serving (loading P1's models, with fallbacks) · proximity CV worker · alarm catalogue + RAG + Ollama/cloud LLM · outbox/sync agent · cloud API + Postgres · supervisor/fleet API (edge site-local + cloud) · docker compose · demo tooling (replay, reset, latency probe).

### 1.2 Not backend — do not build

- **Simulator** (P1). Backend consumes `raw.v1` over MQTT (§5.2) and proxies scenario commands (§5.5). Backend may write a tiny deterministic *test-fixture scripter* (`tools/fake_machine.py`) — it is not a simulator and must not grow into one.
- **ML training, datasets, evaluation numbers** (P1). Backend only loads model files through the `ml_runtime` interface (§5.6) and serves inference with fallbacks.
- **All UI** (P3): tablet PWA, supervisor dashboard, MediaPipe face processing, Piper audio rendering, site-plan image, lesson/Replay content, map UI. Backend provides REST, WS, generated TypeScript types, `contracts/i18n/en.json` and `contracts/audio_clips.yaml`.
- **Readiness formula tuning** (P1). Backend implements HLD §7.4 exactly as a pure function; P1 may replace the body behind the same signature.

### 1.3 Interfaces to teammates (frozen in Phase 0)

| Contract | Producer | Consumer | File |
|---|---|---|---|
| `raw.v1` machine frames, `dtc`, `env`, sim proximity | P1 sim | backend | `contracts/schemas/raw.v1.json` etc. |
| Scenario control API + scenario names | P1 sim | backend proxy | `contracts/sim_control.md` |
| `ml_runtime` package + feature names | P1 | backend | `contracts/ml_runtime.md`, `contracts/ml_features.md` |
| Historical data export (Parquet windows, tasks, labelled 1 Hz traces) | P1 | backend `tools/load_history.py`, `tools/eval_rules.py` | `contracts/history.md` |
| REST + WS messages, TS types | backend | P3 | `contracts/rest.md`, `contracts/ws.md`, `contracts/ts/*.d.ts` |
| i18n message keys + audio clip ids | backend (text from HLD rule table) | P3 renders | `contracts/i18n/en.json`, `contracts/audio_clips.yaml` |
| Lesson catalogue + Replay templates | P3 | backend | `data/lessons/lessons.json` (schema `lesson.v1`) |
| Site plan scale/origin | P3 | backend | `data/seed/site.yaml` |

---

## 2. Target architecture (backend view)

**Processes (docker compose, `infra/docker-compose.yml`):**

| Service | What | Notes |
|---|---|---|
| `mosquitto` | MQTT broker | LAN-exposed 1883; anonymous allowed on demo LAN (document it). |
| `edge-api` | FastAPI single process | Bus client, ingestion, per-machine engines, nudges, WS, REST, geofence, lessons, readiness, ML adapter, RAG, outbox agent, site-local fleet API, background jobs. Configurable `EDGE_MACHINE_IDS` + `EDGE_DB_PATH` so it can run as one instance (both machines) or one instance per machine ("multi-edge mode"). |
| `cv-worker` | YOLO11n ONNX person detector | Publishes `cv/proximity`. |
| `ollama` | Local LLM | Model pre-pulled into a volume. |
| `simulator` | P1's image | Until it exists: runs `tools/replay.py` on a fixture. |
| `cloud-api` + `postgres` | Cloud tier | **Must run on a different host than edge for the unplug demo** (see D16). Compose profile `cloud` for local dev. |

**Data flow inside edge-api:**
MQTT `raw` → normaliser → per-machine `MachineContext` (state vector + timers) → risk engine → alert manager (cooldown/suppression/budget) → nudge generator → in-process broadcaster → WebSocket clients **and** MQTT `state/alert/nudge/event`. A single `DbWriter` task persists everything; outbox rows are written in the same unit of work.

**Hazards always travel via the retained MQTT topic `cat/{site}/hazards`**, even inside one process. That is what makes multi-edge mode (two edge-api instances, one broker) work with no extra code — and it's the honest implementation of "site memory over LAN".

---

## 3. HLD review — issues found and decisions

These were found by cross-checking HLD sections against each other. Items marked **TEAM** need a teammate's confirmation before the relevant phase; the rest are backend-internal and decided here.

| # | Where | Problem | Decision |
|---|---|---|---|
| D1 | §4.4 R03 vs §6.10 `failed_checks` | R03's condition checks only hyd/grounded/park brake, but `failed_checks` also lists MOVING and SLOPE. Exiting a moving or tilted machine is unsafe. | Exit check set = ENGINE_RUNNING, HYD_UNLOCKED, IMPLEMENT_RAISED, MOVING, PARK_BRAKE_OFF (loader only), SLOPE (pitch/roll > model caution). `UNSAFE` if any except ENGINE_RUNNING fails or is UNKNOWN; `SAFE_ENGINE_ON` if only ENGINE_RUNNING fails; else `SAFE`. R03 fires on UNSAFE, R04 on SAFE_ENGINE_ON. |
| D2 | §4.2 debounce 500 ms vs §3.2 nudge < 300 ms | A classic 500 ms debounce delays every switch change past the latency target. | Leading-edge debounce: accept a change immediately, then ignore further changes of that switch for 500 ms (flap suppression). Zero added latency. |
| D3 | §12 demo "5 h sleep → Yellow" vs §7.4 | Under §7.4, 5 h/24 h triggers only "< 6 h" (−15) → score 85 = GREEN. `< 5 h` is strict. **TEAM** | Implement §7.4 exactly. Demo input must be 5 h/24 h **and** < 12 h/48 h (−30 → 70 = YELLOW), or P1 changes the threshold to ≤ 5 h. A test pins the current behaviour either way. |
| D4 | §6 IDs "UUIDv7" | Python 3.11 stdlib has no `uuid7`. | Use `uuid-utils` (`uuid_utils.uuid7()`), wrapped in `common/ids.py`. |
| D5 | R19 / §6.12 `fatigue_sample` | No REST/WS path for the browser to send in-shift fatigue samples. | Add `POST /fatigue/samples` (stretch, Phase 6). |
| D6 | §6.5 `operator` / §8.2 `/auth/login {badge_id}` | Operator has no badge field. | Badge QR encodes `employee_code`; `badge_id` = `employee_code`. |
| D7 | §4.13 sync | Incident voice notes/photos have no sync path. | Incident JSON syncs as P0; media uploads afterwards via `PUT /sync/media/{incident_id}/{filename}` (P2). |
| D8 | §4.15 cloud fleet overview | Needs live-ish machine state, but state isn't a synced entity. | Add coalesced `machine_state_snapshot` entity: at most one PENDING outbox row per machine; newer state replaces its payload. |
| D9 | R12/R13 | Proximity zones defined only for CAT 320 (tail swing 2.83 m + 1 m = 3.8 m). **TEAM** | Per-model `proximity_critical_m` / `proximity_warning_m` in `machine_models.yaml`. 320: 3.8 / 8.0 (HLD). 950 GC: 5.0 / 12.0 **ASSUMPTION** until the team confirms. |
| D10 | §6.11 `safety_alert` | No way to know if an alert is still active. | Add `active` (bool) and `cleared_at`. |
| D11 | §8.3 `/fleet/anomalies/{id}/label` | `telemetry_window` has no ID. | Add `window_id` (UUIDv7) and `review_label` (TRUE_POSITIVE/FALSE_POSITIVE/null). |
| D12 | §6.8 `bucket_height_m` "DER (kinematics)" | Excavator kinematics need link lengths we'd be guessing. | Treat `bucket_height_m` and `boom_tip_height_m` as signals from the Grade/implement system (sim provides them). Backend derives only `implement_grounded` (≤ 0.3 m). |
| D13 | §7.5 "retrieval score below threshold" | Hybrid retrieval uses RRF, whose scores aren't calibrated. | Gate on (exact code match) OR (top vector cosine ≥ τ). τ default 0.55 **ASSUMPTION**, tuned on the golden set in Phase 8. |
| D14 | §6.13 organizer anchors | The rollup definition must match what P1 used to scale traces, or anchors won't reproduce. **TEAM** | Backend owns `edge/ingest/rollup.py` as the single definition; P1 validates anchors against it. |
| D15 | §7.1/§7.2 features | Train/serve skew if backend and P1 build features differently. **TEAM** | Feature names/units frozen in `contracts/ml_features.md`; P1 ships `ml_runtime` that consumes those dicts. |
| D16 | §12 unplug demo | If cloud-api runs on the same laptop, pulling the cable doesn't cut edge↔cloud. | Host cloud remotely (Neon/Supabase Postgres + a small hosted API) or on a second laptop reached through the unplugged uplink. Backup: `POST /system/network {force_offline:true}` kill switch, shown in System Status as "Forced offline (demo)" — honest label. |
| D17 | §6.10 idle | Unclear whether sub-3-min pauses count in `idling_time_min`. **TEAM** | `idling_time_min` = all idle time (running, not active ≥ 30 s) — matches organizer "idle minutes". Bucket counts (`idle_short/medium/long_count`) exclude PAUSE (< 3 min). |
| D18 | §4.4 watchdog "< 2 s" | Achievable for an in-process engine task restart, not a container restart. | Engine task supervised in-process (< 2 s, state rehydrated from `engine_snapshot`); container `restart: unless-stopped` as second layer, with measured time documented. |
| D19 | §6.10 `exit_event.trigger` WEAK | No path produces a WEAK exit. | v1: exit_event always created on STRONG intent; weak hints only raise R05. Enum kept. |
| D20 | §4.3 vs rule table "State" column | §4.3 says any CAUTION → Attention, but R06/R21 (CAUTION) have State "—". | Rule table's State column is authoritative: each rule has explicit `state_effect`. |
| D21 | Stretch rules | R19 (PERCLOS) and R22 (Coupler Confirm) are stretch per §2. | Present in `rules.yaml` with `enabled: false`. |
| D22 | Timezones | Samples use `+05:30`. | Store UTC in DB; API/WS emit ISO-8601 with the site's offset (`Asia/Kolkata`). |
| D23 | Windows dev | aiomqtt needs a selector event loop on Windows. | Dev inside Docker or WSL2; if native Windows, set `WindowsSelectorEventLoopPolicy`. |
| D24 | ML model files | joblib-pickled sklearn/LightGBM models break or warn across library versions. **TEAM** | Backend pins **the same** scikit-learn, lightgbm and joblib versions as P1's training env. |

---

## 4. Stack and conventions

**Python 3.11**, FastAPI + uvicorn, pydantic v2, SQLModel (SQLAlchemy 2), aiomqtt 2.x, httpx, uuid-utils, PyJWT, bcrypt, shapely 2, sqlite-vec, fastembed (`BAAI/bge-small-en-v1.5`), onnxruntime, opencv-python-headless, numpy, python-multipart, psycopg (cloud), pytest + pytest-asyncio. scikit-learn/lightgbm/joblib pinned to P1's versions (D24). Pin everything in `requirements.lock`. Use `python:3.11-slim` images (its `sqlite3` supports extension loading, needed by sqlite-vec).

Conventions:
- **Config:** `pydantic-settings`, env vars, `.env.example` documents every var. No hard-coded URLs/secrets.
- **IDs:** UUIDv7 strings for all event-like rows; human IDs (`EXC001`, `OP1001`, `SITE-PUN-01`) for master data.
- **Time:** UTC in DB, site offset on the wire (D22). Monotonic clock for durations/timers inside the engine.
- **Units:** as in HLD §6 (SI, °C, kPa, L, m). Field names exactly as HLD §6 unless listed in §3.
- **Schemas:** every MQTT/WS payload has a `schema` field (`telemetry.v1`, `event.v1`, …).
- **SQLite:** `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`. All writes go through one `DbWriter` asyncio task (queue of units of work, executed in a thread, telemetry batched per second). Reads use a separate connection via threadpool. Never block the event loop.
- **Logging:** structured JSON logs; every alert raise/clear logged with rule_id and inputs.
- **Errors:** a failing rule is disabled and logged; other rules keep running (HLD §4.4).
- **Tests:** unit tests bypass MQTT via an in-memory `Bus` implementation; integration tests use real Mosquitto via compose.

---

## 5. Contracts (frozen in Phase 0)

### 5.1 MQTT topics

HLD §8.1, plus:

| Topic | Direction | Rate | Payload |
|---|---|---|---|
| `cat/{site}/{machine}/raw` | sim → ingest | 1 Hz + on switch change | `raw.v1` (§5.2) |
| `cat/{site}/{machine}/dtc` | sim → ingest | on change | `{spn, fmi, oc, active, freeze_frame}` |
| `cat/{site}/{machine}/cv/proximity` | cv → engine | 5 Hz | `{ts, min_dist_m, sector, conf, frame_ok, source:"cv"}` |
| `cat/{site}/{machine}/sim/proximity` | sim → engine | 1–5 Hz | same shape, `source:"sim"` (fallback channel) |
| `cat/{site}/env` | sim/env source → ingest | 10 min | `environment_obs` raw fields |
| `cat/{site}/{machine}/telemetry` | ingest → all | 1 Hz | `telemetry.v1` (HLD §6.15) |
| `cat/{site}/{machine}/event` | engine → all | on event | `event.v1` |
| `cat/{site}/{machine}/state` | engine → all | on change ≤ 1 Hz | `state.v1` (§5.3) |
| `cat/{site}/{machine}/alert` | engine → all | on change | `alert.v1` |
| `cat/{site}/{machine}/nudge` | engine → all | on event | `nudge.v1` |
| `cat/{site}/hazards` **retained** | edge ↔ devices | on change | `{schema:"hazards.v1", site_id, as_of, pins:[…with version]}` |
| `cat/{site}/{machine}/sync/status` | sync → UI | 10 s | `{online, forced_offline, queue_depth, last_success_at}` |

### 5.2 `raw.v1` (agree with P1)

```json
{
  "schema": "raw.v1", "ts": "2026-10-14T10:42:17.000+05:30",
  "site_id": "SITE-PUN-01", "machine_id": "EXC001",
  "can":  { "190": 1010, "92": 12, "183": 2.1, "250": 15240.6, "96": 58.4, "1761": 71.0,
            "247": 1524.62, "110": 86, "100": 290, "1638": 62, "168": 27.1, "84": 0.0,
            "1856": "UNFASTENED", "70": "ON", "523": 0 },
  "sens": { "engine_state": "RUNNING", "hyd_pump_press_kpa": 3100, "swing_rate_deg_s": 0.0,
            "boom_angle_deg": 38.5, "stick_angle_deg": -62.0, "bucket_angle_deg": -15.0,
            "bucket_height_m": 2.1, "boom_tip_height_m": 6.2, "payload_kg": 0,
            "pass_count": 1843, "load_count": 212, "idle_hours_total": 581.3,
            "pitch_deg": 3.2, "roll_deg": 1.1, "x_m": 184.2, "y_m": 92.7, "heading_deg": 211,
            "seat_occupied": true, "door_open": false, "hyd_lockout": "UNLOCKED",
            "joystick_active": false, "lift_mode": false, "power_mode": "SMART",
            "attachment_id": "ATT-GP-119", "coupler_lock": "LOCKED",
            "cab_temp_c": 27.5, "cab_ac_on": true, "articulation_deg": null,
            "activity_hint": "IDLE" },
  "sim_label": null
}
```

Normaliser mapping (HLD §6.8): 190→engine_rpm, 92→engine_load_pct, 183→fuel_rate_lph, 250→fuel_used_total_l, 96→fuel_level_pct, 1761→def_level_pct, 247→engine_hours, 110→coolant_temp_c, 100→engine_oil_press_kpa, 1638→hyd_oil_temp_c, 168→battery_v, 84→travel_speed_kmh, 1856→seatbelt, 70→parking_brake, 523→transmission_gear. Any key absent or `null` → UNKNOWN (I1). `sim_label` is stripped before the engine and stored only in `sim_label_log` for eval (I9). `activity_hint` (fine phase DIGGING/SWING_LOADED/…) is optional; backend always derives coarse activity itself (OFF/IDLE/WORKING/TRAVELLING/LIFTING) and uses the hint only for display/snapshots.

### 5.3 `state.v1`

```json
{ "schema": "state.v1", "ts": "…", "machine_id": "EXC001", "operator_id": "OP1001", "shift_id": "…",
  "class": "UNSAFE",                         // PRODUCTIVE | ATTENTION | UNSAFE | PARKED | OFF
  "active_rules": [{"rule_id": "R03", "level": "CRITICAL"}],
  "exit_state": "UNSAFE",                    // NONE | SAFE | SAFE_ENGINE_ON | UNSAFE
  "exit_checks": {"ENGINE_RUNNING": "FAIL", "HYD_UNLOCKED": "FAIL", "IMPLEMENT_RAISED": "FAIL",
                  "MOVING": "PASS", "SLOPE": "PASS"},   // PASS | FAIL | UNKNOWN; PARK_BRAKE_OFF for loaders
  "readiness": "YELLOW",
  "sensor_health": {"seat": "OK", "seatbelt": "OK", "position": "OK", "proximity": "UNAVAILABLE", "data": "OK"},
  "data_stale": false }
```

Class order: any CRITICAL `state_effect` → UNSAFE; any ATTENTION `state_effect` or UNKNOWN safety sensor or data stale → ATTENTION; engine OFF → OFF; running AND hyd LOCKED AND not active → PARKED; else PRODUCTIVE. Step up immediately; step down only after 10 s clear (hysteresis).

### 5.4 WebSocket `/ws/live?machine=EXC001&token=…`

Envelope `{ "type": "...", "ts": "...", "data": {...} }`. On connect send `snapshot` (state, active alerts, hazards, sync status, current task ETA). Then stream types: `state`, `telemetry` (≤ 1 Hz, UI subset), `alert` (`{action: RAISED|UPDATED|CLEARED|ACKED, alert}`), `nudge`, `event`, `hazards`, `sync`, `lesson` (`{assignment_id, deliverable}`), `eta` (`{task_id, prediction}`), `health`, `ping` (10 s). Supervisor sockets: `/ws/site` (all machines, state + alert only).

`nudge.v1`: `{nudge_id, alert_id, rule_id, level, display: CHIP|BANNER|FULLSCREEN, message_key, slots, audio_clip, tone_pattern, channels, checklist?}`. Exit Guard checklist ticks arrive via `state.exit_checks`.

### 5.5 Sim control (agree with P1)

P1's sim exposes `POST {SIM_CONTROL_URL}/scenario {name, machine_id?}` and `GET /scenarios`. Edge `POST /sim/scenario` proxies it. Required names: `reset`, `unsafe_exit`, `safe_exit`, `proximity_intrusion`, `drive_into_zone`, `dtc_1638_16`, `belt_bypass`, `long_idle_unbelted`, `tilt_excursion`, `overheat_trend`. If `SIM_MODE=replay`, the proxy instead replays `tests/fixtures/scenarios/{name}.jsonl` — so CP1 never waits on the sim.

### 5.6 `ml_runtime` (agree with P1)

```python
class EtaModel:
    version: str
    @classmethod
    def load(cls, path: str) -> "EtaModel": ...
    def predict(self, feats: dict) -> dict:
        # -> {"p50_min": float, "p90_min": float,
        #     "drivers": [{"feature": str, "effect_pct": float}]}   # top 3

class AnomalyModel:
    version: str
    family: str                       # EXCAVATOR | WHEEL_LOADER
    threshold_top3pct: float          # score at/above which a window is "top 3%"
    @classmethod
    def load(cls, path: str) -> "AnomalyModel": ...
    def score(self, window: dict, baseline: dict) -> dict:
        # -> {"score": 0..1, "top_features": [{"feature","value","baseline","z"}]}
```

Feature dict keys: exactly the lists in HLD §7.1 and §7.2, written out with units in `contracts/ml_features.md`. Backend builds them from SQLite (operator rolling median rate over last 10 tasks of the type, 14-day baselines, readiness, env, hours into shift…). Backend also sets `operator_rate_source ∈ {OPERATOR, COHORT, TASK_TYPE}` (cold start per §7.2).

**Fallbacks (backend-side, always available):** ETA → `generic_eta()` from task-type base rate midpoint × soil fill factor (HLD §6.7), p90 = 1.4 × p50, labelled `"Estimate (generic)"`, `model_version:"generic"`. Anomaly → no score, `health.models.anomaly = "UNAVAILABLE"`; rules keep running.

### 5.7 REST

Everything in HLD §8.2 and §8.3, plus:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health`, `/system/status` | Liveness; sync, models (local/cloud, versions), sensor health, versions |
| POST | `/system/network` | `{force_offline: bool}` demo kill switch (D16) |
| GET | `/shift/current` | Current shift + handover note from previous shift |
| POST | `/shift/end` | Accepts optional `handover_note` (HLD §10) |
| POST | `/readiness/{id}/override` | Supervisor PIN override |
| POST | `/env/ground` | One-tap ground condition DRY/WET/MUDDY |
| POST | `/env/manual` | Manual temp/RH when no source |
| POST | `/idle/{episode_id}/reason` | R09 reason chip |
| POST | `/fatigue/samples` | In-shift fatigue samples (stretch, D5) |
| PATCH | `/hazards/{id}` | `{action: CONFIRM|RESOLVE}` |
| PUT | `/sync/media/{incident_id}/{filename}` | Cloud: media upload (D7) |
| POST | `/models/{name}` | Cloud, admin: upload new model version (P1 retrain) |
| GET | `/models/{name}/{version}/file` | Cloud: model binary |

Export the OpenAPI JSON to `contracts/openapi.json` at every phase end; generate TS types from it for P3.

---

## 6. Repo layout

```
backend/
  common/            config.py, ids.py, timeutil.py, levels.py
    db/              models.py (SQLModel tables, SQLite+Postgres), session.py, writer.py
    schemas/         pydantic models (generated from contracts/schemas + hand-written)
    fleet/           router.py (shared supervisor API, mounted on edge + cloud), queries.py
  edge/
    main.py          FastAPI app; lifespan starts bus, engines, jobs, outbox agent
    bus.py           Bus interface; MqttBus (aiomqtt) + MemoryBus (tests)
    broadcast.py     in-process pub/sub → WS + MQTT
    ingest/          normalise.py, switches.py (debounce), events.py, stale.py, env.py, dtc.py,
                     rollup.py, idle.py, retention.py
    engine/          context.py (MachineContext, timers), predicates.py, rules.py (YAML loader),
                     registry.py (predicate functions), alerts.py (cooldown/suppression/budget),
                     exits.py (exit tracker), classify.py, snapshot.py (persist/rehydrate), supervisor.py
    nudge/           generator.py
    ws/              live.py
    api/             auth.py shift.py readiness.py walkaround.py tasks.py alerts.py incidents.py hazards.py
                     diagnostics.py assistant.py lessons.py scorecard.py sim.py system.py env.py fatigue.py
    geo/             geofence.py
    readiness/       score.py (HLD §7.4, pure)
    ml/              adapter.py (loads ml_runtime, hot reload, fallbacks), features.py, anomaly_job.py, eta_service.py
    lessons/         engine.py, replay.py
    scorecard/       compute.py
    rag/             catalogue.py, chunk.py, index.py, retrieve.py, llm.py, guard.py
    sync/            outbox.py, agent.py, inbox.py, connectivity.py, media.py
  cloud/
    main.py, api/sync.py, api/models.py, api/auth.py, weather.py (Open-Meteo forecast job), change_log.py
  cv_worker/         main.py, capture.py, detector.py, distance.py, quality.py, calibrate.py
  tools/             replay.py, record.py, fake_machine.py, ws_probe.py, latency_probe.py, seed.py,
                     load_history.py, eval_rules.py, eval_rag.py, build_rag_index.py, reset_demo.sh
  tests/             unit/, integration/, fixtures/scenarios/*.jsonl, rag_golden.yaml
contracts/           schemas/*.json, examples/*.json, topics.md, rules.yaml, rest.md, ws.md, sim_control.md,
                     ml_runtime.md, ml_features.md, history.md, i18n/en.json, audio_clips.yaml, ts/, openapi.json
data/
  seed/              site.yaml, machine_models.yaml, machines.yaml, attachments.yaml, operators.yaml,
                     task_types.yaml, tasks_demo.yaml
  catalogue/         diagnostic_codes.yaml
  manuals/           *.md (demo corpus)
  lessons/           lessons.json (from P3; stub until then)
infra/               docker-compose.yml, mosquitto.conf, Dockerfile.edge, Dockerfile.cloud, Dockerfile.cv, .env.example
docs/                hld.md, PROGRESS.md, RUNBOOK.md
```

---

## 7. Phases

Time boxes follow the HLD's 48 h assumption (§11) and its checkpoints: **CP1 H14**, **CP2 H28**, **CP3 H38**. If the hackathon is longer, stretch the boxes; don't add features.

### Dependency check (verified)

Each phase depends only on earlier phases or on a named stub: rules needing later data (hazard zones R14/R15, readiness R20, anomaly R23, fuel baseline R24) are implemented in Phase 3 against `MachineContext` fields that are empty until Phases 5–6, and tested in Phase 3 by injecting those fields. The DTC catalogue is seeded in Phase 1 so R16/R17 work before RAG exists. The outbox exists from Phase 1 (I7), so Phase 9 only adds the agent. Proximity rules are tested with `sim/proximity` fixtures before the CV worker exists.

---

### Phase 0 — Foundations and contracts (H0–3)

**Goal:** repo, compose skeleton, frozen contracts, replay tooling, so P1 and P3 can start against contracts immediately.

**Build**
1. Repo layout (§6), `pyproject.toml`, `requirements.lock`, ruff, pytest config, `docs/PROGRESS.md`.
2. `infra/docker-compose.yml` with mosquitto, edge-api (`/health` only), postgres + cloud-api (`/health` only) under profile `cloud`, ollama, simulator placeholder.
3. JSON Schemas in `contracts/schemas/` for: raw.v1, telemetry.v1, event.v1, state.v1, alert.v1, nudge.v1, hazards.v1, task.v1, incident.v1, readiness.v1, lesson.v1, sync push/pull. Put HLD §6.15 samples (and one example per other schema) in `contracts/examples/`.
4. `contracts/rules.yaml` v1 — all R01–R24 from HLD §4.4 in this format (thresholds as `params`, not code):
   ```yaml
   - id: R03
     name: exit_guard_unsafe
     subject: EXIT            # SEATBELT|EXIT|TILT|PROXIMITY|GEOFENCE|POWERLINE|DTC|TEMP|FATIGUE|READINESS|HEAT|COUPLER|ANOMALY|EFFICIENCY|IDLE
     level: CRITICAL
     state_effect: UNSAFE     # UNSAFE|ATTENTION|NONE  (D20)
     predicate: exit_guard_unsafe
     params: {}
     display: FULLSCREEN
     channels: [VISUAL, AUDIO, VIBRATION]
     message_key: nudge.exit_guard.before_exiting
     audio_clip: exit_guard_before_exiting
     cooldown_s: 0
     repeat_while_true_s: null
     escalate_after_s: null   # R02: 300 → CAUTION
     notify_supervisor: false # R07, R20: true
     lesson_trigger: true
     enabled: true
     hld_ref: "§4.4 R03"
   ```
5. `contracts/i18n/en.json` and `contracts/audio_clips.yaml` with message text copied from the HLD rule table (slots as `{minutes}` etc.).
6. `contracts/topics.md`, `rest.md` (route table from §5.7 + HLD §8), `ws.md`, `sim_control.md`, `ml_runtime.md`, `ml_features.md`, `history.md` — each with an "Agreed by: P1/P3 ☐" line.
7. `tools/replay.py` (publish a JSONL of `raw.v1`/`dtc`/`env`/proximity frames to MQTT at `--speed` with original spacing), `tools/record.py` (subscribe → JSONL), `tools/fake_machine.py` (deterministic scripter producing fixture JSONLs from a small YAML scenario description).
8. Record `tests/fixtures/replay_10min.jsonl` (normal work + one unsafe exit) for P3's frontend dev.

**Verification gate**
- [ ] `docker compose up -d` → mosquitto, edge-api, ollama healthy; `curl :8000/health` → 200.
- [ ] `pytest tests/unit/test_contracts.py`: every file in `contracts/examples/` validates against its schema; `rules.yaml` loads, has R01–R24, every rule has all required keys, every `message_key` exists in `en.json`, every `audio_clip` exists in `audio_clips.yaml`.
- [ ] `python tools/replay.py tests/fixtures/replay_10min.jsonl --speed 10` while `mosquitto_sub -t 'cat/#' -v` shows frames on `cat/SITE-PUN-01/EXC001/raw`.
- [ ] TS types generated into `contracts/ts/` compile with `tsc --noEmit`.

**Done when:** contracts committed and sent to P1/P3; replay JSONL handed to P3.

---

### Phase 1 — Storage layer (H3–5)

**Goal:** SQLite schema, write path with outbox-in-transaction, seed data.

**Build**
1. SQLModel tables for every entity in HLD §6 plus the additions in §3: site, machine_model, machine, attachment, operator, shift, task_type, task, telemetry_sample, telemetry_minute, telemetry_window (+window_id, review_label), machine_event, idle_episode, exit_event, diagnostic_code, dtc_occurrence, safety_alert (+active, cleared_at), incident, hazard_pin, environment_obs, readiness_check, fatigue_sample, walkaround, lesson, scenario, lesson_assignment, operator_scorecard, sync_queue, sync_state, model_registry, engine_snapshot, machine_state_snapshot, device, sim_label_log, schema_version. JSON columns via `sa.JSON` so the same models work on Postgres. Indexes: `(machine_id, ts)` on time-series tables; `(status, priority, seq)` on sync_queue.
2. `common/db/writer.py`: `DbWriter` (single writer task, units of work, telemetry batching). Repository functions for every write; any entity marked `syncable` enqueues its outbox row inside the same unit of work (I7). Priority table:
   P0 incident, hazard_pin, safety_alert (CRITICAL or escalated), readiness_check (RED), exit_event (UNSAFE) · P1 other safety_alert, machine_event, exit_event, dtc_occurrence, task (status), shift, readiness_check, machine_state_snapshot (coalesced, D8) · P2 telemetry_window, idle_episode, operator_scorecard, lesson_assignment, walkaround · P3 telemetry_minute. telemetry_sample is **not** syncable (I8).
3. `tools/seed.py` loads `data/seed/*.yaml`: SITE-PUN-01; CAT-320 and CAT-950GC with HLD §6.0 specs and thresholds (+ D9 proximity values); machines EXC001 (engine hours start ≈ 1524, per §6.13), WL001, plus EXC002 for Operator B; attachments; operators OP1001 (demo) + a guest profile + a supervisor, bcrypt PIN hashes; task types §6.7; demo tasks for today; `diagnostic_codes.yaml` seeded with the HLD §6.11 examples (fields `source_doc: "DEMO corpus — verify against OMM"`).
4. `tools/load_history.py` stub: imports P1's Parquet windows/tasks per `contracts/history.md` (no-op with a clear message if files absent).

**Verification gate**
- [ ] `pytest tests/unit/test_storage.py`: create_all on fresh SQLite and on Postgres (compose `cloud` profile) succeeds with the same models.
- [ ] Outbox atomicity test: a unit of work that inserts an incident and then raises → neither incident nor sync_queue row exists. Success path → both exist, same entity_id, priority 0.
- [ ] Coalescing test: three state snapshots for EXC001 while PENDING → exactly one PENDING row with the latest payload.
- [ ] telemetry_sample insert never creates a sync_queue row (I8).
- [ ] Concurrency test: 5 concurrent API-style writes + 1 Hz telemetry for 60 s simulated → no `database is locked`.
- [ ] `python tools/seed.py` is idempotent (run twice, same row counts).

---

### Phase 2 — Ingestion, events, environment, rollups (H5–8)

**Goal:** raw frames become normalised telemetry, discrete events, env observations, DTC occurrences, idle episodes and rollups in the organizer's schema.

**Build**
1. `ingest/normalise.py`: validate `raw.v1` (malformed → drop + counter), map SPNs (§5.2), derive `implement_grounded` (D12), coarse `machine_activity`, `data_quality` bitmask; publish `telemetry.v1`; store `telemetry_sample`.
2. `ingest/switches.py`: leading-edge debounce (D2) for seatbelt, seat, door, hyd_lockout, parking_brake, coupler_lock, engine_state.
3. `ingest/events.py`: edge detection → `machine_event` types from HLD §6.9 (IGNITION_ON/OFF, SEATBELT_*, SEAT_*, DOOR_*, HYD_*, PARK_BRAKE_*, COUPLER_*), with per-machine monotonic `seq`.
4. `ingest/stale.py`: no raw frame for > 3 s → `DATA_STALE` event, context marks all signals UNKNOWN; recovery clears.
5. `ingest/env.py`: store `environment_obs`; derive dew point (Magnus: γ = ln(RH/100) + 17.62·T/(243.12+T), Td = 243.12·γ/(17.62−γ)), heat index (NOAA Rothfusz in °F with the standard low-HI simple formula below 80 °F, converted back to °C), WBGT_est = 0.567·T + 0.393·e + 3.94 with e = (RH/100)·6.105·exp(17.27·T/(237.7+T)) hPa, heat level (NOAA HI bands: <27 NONE, 27–32 CAUTION, 32–39 EXTREME_CAUTION, ≥39 DANGER). Source priority MACHINE_SENSOR/MANUAL > sim env > FORECAST_CACHE. Keep `current_env[site]` in memory.
6. `ingest/dtc.py`: upsert `dtc_occurrence` (freeze_frame = current telemetry), `DTC_ACTIVE/CLEARED` events, attach catalogue `action_class` (UNDOCUMENTED if code unknown).
7. `ingest/idle.py`: idle = running AND NOT active for ≥ 30 s (predicates from HLD §4.4); episode segmentation with bucket, fuel_l, mean_rpm, elevated_rpm, seatbelt_off_min; `IDLE_START/END` events.
8. `ingest/rollup.py`: 1-min `telemetry_minute` and hourly `telemetry_window` exactly per HLD §6.10 + D17: fuel_used_l = Δ SPN 250; load_cycles = Δ load_count; productive_min; idle split; fuel_idle_l/fuel_work_l; fuel_per_load = fuel_work_l / loads, **null if loads < 3**; enforce idle_min ≤ 60 − productive_min. Window closes on the hour (site time) and on shift end.
9. `ingest/retention.py`: daily purge of telemetry_sample older than 7 days.

**Verification gate**
- [ ] Unit: SPN mapping for every §5.2 key; missing key → UNKNOWN; malformed frame dropped and counted.
- [ ] Debounce: belt flapping FASTENED/UNFASTENED every 100 ms for 1 s → exactly one change event, emitted at the first change (no delay).
- [ ] Stale: stop replay → `DATA_STALE` within 3–4 s; resume → cleared.
- [ ] Env: dew point, heat index and WBGT match hand-computed values for 3 fixed inputs (put the numbers in the test).
- [ ] Idle: fixture with pauses of 2, 4, 7 and 12 min → buckets PAUSE/SHORT/MEDIUM/LONG; `idle_short/medium/long_count` = 1/1/1; `idling_time_min` includes the 2-min pause.
- [ ] Rollup: fixture hour at 2 L/h idle for 60 min → fuel_used_l ≈ 2.0 (±0.05), fuel_per_load null. Fixture with 2 loads and 55 idle min → fuel_per_load null (the "1.9 L/cycle artefact" guard from HLD §6.13).
- [ ] **Organizer anchors (TEAM, D14):** if P1 has delivered anchor traces, rollup reproduces the four HLD §6.13 rows (fuel ±0.1 L, loads exact, idle ±1 min). If not yet delivered, the test is skipped with a visible reason — not deleted.

---

### Phase 3 — Operator State Engine + Risk Engine (H8–12) — the core

**Goal:** per-machine state vector, all rules, alert-fatigue controls, Exit Guard lifecycle, crash-safe persistence.

**Build**
1. `engine/context.py` — `MachineContext` per machine: latest normalised signals with per-signal freshness, timers (seat_vacated_since, belt_off_since, last_belt_change, last_active_ts, idle_since, last_joystick_ts, coupler_unlock_ts, last_position_ts), current env, proximity (prefer `cv` if frame_ok and < 1 s old, else `sim` if < 1 s old, else UNKNOWN), active DTCs, zones currently inside (Phase 5), readiness (Phase 5), anomaly/fuel baseline flags (Phase 6), model thresholds from `machine_models.yaml`.
2. `engine/predicates.py` — tri-state (True/False/None) implementations of HLD §4.4 predicates: running, active, idle, grounded, exit_intent (strong), exit_hint (weak), wet, lift_mode. `None` propagates (Kleene logic).
3. `engine/registry.py` — one predicate function per rule R01–R24, reading thresholds from `params`/model config. R19 and R22 present but `enabled: false` (D21).
4. `engine/exits.py` — exit tracker (D1, D19):
   - exit_hint rising → R05 chip.
   - exit_intent rising → create `exit_event` (trigger STRONG, state_at_intent, failed_checks), `EXIT_ATTEMPT` event.
   - Each tick while intent: recompute `exit_checks` (PASS/FAIL/UNKNOWN); when state leaves UNSAFE → `corrected=true`, `time_to_correct_s`.
   - First moment not UNSAFE (or at completion if never corrected) with `wet` → R06 once per exit, `three_point_prompted=true`.
   - Seat vacated ≥ 10 s → `EXIT_COMPLETED`; seat re-occupied → `MOUNT`, `time_outside_s`, close exit. Re-occupied < 10 s with door never opened → cancel (operator shifted in seat).
5. `engine/alerts.py` — alert manager:
   - Raise when rule turns true and cooldown elapsed; `UPDATED` on repeat (`repeat_while_true_s`, e.g. R01 every 20 s); `CLEARED` when false (set `active=false`, `cleared_at`).
   - Escalation (R02 INFO → CAUTION after 300 s).
   - Suppression: a higher-level active alert on the same `subject` marks lower ones `suppressed=true, suppress_reason=HIGHER_ACTIVE` (still stored, no nudge).
   - Audio budget: max one non-CRITICAL audio per 30 s per machine; over budget → deliver VISUAL (+VIBRATION) only. CRITICAL bypasses (I4).
   - `state_snapshot` = the exact inputs + thresholds that fired (for `/alerts/{id}/why`).
   - `notify_supervisor` → `escalated=true` → P0 outbox.
   - Metric: alerts per operating hour (non-critical), exposed in `/system/status`.
6. `engine/classify.py` — class rules and 10 s hysteresis (§5.3).
7. `engine/snapshot.py` + `engine/supervisor.py` — persist MachineContext timers/open exit/active alerts to `engine_snapshot` every 1 s and on change; engine task supervised: on exception, log, restart, rehydrate (< 2 s, D18). A single rule raising disables only that rule (logged, visible in `/system/status`).
8. Evaluation cadence: on every raw frame, proximity message, env change, DTC change, zone change, and a 1 s timer tick (for duration-based rules).
9. `tools/eval_rules.py` — replays P1's labelled traces; confusion matrix vs `sim_label`.

**Verification gate**
- [ ] **One unit test per rule R01–R24** (disabled ones: test that they never fire while disabled, and fire correctly when enabled in-test), each with a true case, a false case and an UNKNOWN-input case.
- [ ] I1 tests: seat switch missing + belt off + engine running → exit check UNKNOWN counted as FAIL; state class not PRODUCTIVE; `sensor_health.seat = UNKNOWN`.
- [ ] Scenario tests via MemoryBus + fixtures:
  - `unsafe_exit_corrected.jsonl` → R03 raised within the frame that makes intent true; checklist transitions FAIL→PASS in order; `exit_event` UNSAFE → corrected, `time_to_correct_s` ≈ fixture value (±1 s); R06 once (wet fixture); state class UNSAFE → (≥ 10 s later) lower.
  - `safe_exit.jsonl` → no R03; R04 if engine running.
  - `belt_off_parked.jsonl` (hyd locked, stationary) → R02 INFO silent, escalates to CAUTION at 300 s; no R01.
  - `belt_off_moving.jsonl` → R01 CRITICAL, repeats every 20 s.
  - `belt_bypass.jsonl` → R07 WARNING, escalated=true, P0 outbox row.
  - `tilt.jsonl` → R10 then R11; R11 suppresses R10 (same subject).
  - `proximity_sim.jsonl` → R12 at < 3.8 m while active; nothing while not active; `frame_ok:false` with no sim channel → proximity UNKNOWN (never "no person").
  - `dtc_stop.jsonl` → R16 with catalogue action_class STOP.
- [ ] Audio budget test: three non-critical rules in 10 s → only first has AUDIO; a CRITICAL in the same window still has AUDIO.
- [ ] Crash test: inject an exception mid-unsafe-exit → engine restarts < 2 s, open exit_event and active alerts restored, no duplicate EXIT_ATTEMPT.
- [ ] Rule isolation: make R10's predicate raise → R10 disabled + logged; R03 still fires in the same run.
- [ ] Hysteresis: UNSAFE clears at t → class stays UNSAFE until t+10 s.
- [ ] If P1's labelled traces exist: `tools/eval_rules.py` → **100% recall on UNSAFE_EXIT and BELT_BYPASS** (HLD §7.3); output saved to `docs/eval_rules.md`. Otherwise skipped with reason.

---

### Phase 4 — Nudges, WebSocket bridge, core REST → **CP1 (H14)**

**Goal:** sim → rules → tablet unsafe exit live.

**Build**
1. `nudge/generator.py` — alert → `nudge.v1` (template per rule from rules.yaml + slots, display mode, audio_clip, tone_pattern per level, channels after budget). Publish to broadcaster.
2. `broadcast.py` + `ws/live.py` — `/ws/live` and `/ws/site` per §5.4; snapshot on connect; telemetry throttled to 1 Hz; 10 s ping; JWT via query param (skippable with `AUTH_DISABLED=true` for P3 dev only, logged loudly).
3. MQTT publishing of state/alert/nudge/event/telemetry.
4. REST: `/auth/login` (Phase 5 finishes auth; here a minimal version is fine), `/state/current`, `/alerts/{id}/ack`, `/alerts/{id}/why`, `/sim/scenario` + `GET /sim/scenarios` (proxy or `SIM_MODE=replay`, §5.5), `/health`, `/system/status`.
5. `tools/ws_probe.py` (connect, assert expected messages with timeout) and `tools/latency_probe.py` (publish the triggering frame, measure time to WS `nudge`).

**Verification gate**
- [ ] `python tools/ws_probe.py --machine EXC001 --expect nudge:R03:FULLSCREEN --expect state:exit_state=SAFE --timeout 30` passes while `POST /sim/scenario {"name":"unsafe_exit"}` runs (replay mode acceptable if P1's sim isn't ready).
- [ ] Latency: `tools/latency_probe.py --runs 50` → p95 from triggering frame ingest to WS nudge **< 300 ms** on the demo laptop. Record numbers in PROGRESS.md.
- [ ] `/alerts/{id}/why` returns rule id, inputs, thresholds for the R03 alert.
- [ ] Ack sets `acknowledged_at`; a FULLSCREEN CRITICAL remains active until its condition clears (ack ≠ clear).
- [ ] WS reconnect: kill the socket mid-scenario, reconnect → `snapshot` reflects current truth.
- [ ] `contracts/openapi.json` + TS types regenerated.
- [ ] **CP1 demo with P3:** unsafe exit shows live on the real tablet UI over the travel-router LAN.

---

### Phase 5 — Operator services: auth, shift, readiness, walkaround, incidents, hazards + geofencing (H14–18)

**Build**
1. **Auth** (HLD §4.14, D6): `/auth/login {badge_id, pin}` → bcrypt check against cached roster → JWT HS256 signed with `EDGE_JWT_SECRET` (claims: sub, role, machine_id, shift_id, exp = planned_end + 2 h). Unknown badge → guest operator, `verified=false` tag on all their data. Roles operator/supervisor/admin enforced by dependency.
2. **Shift**: `/shift/start` (creates `SH-YYYYMMDD-{machine}-D`, engine_hours_start), `/shift/end` (+handover_note), `/shift/current`.
3. **Readiness** (HLD §7.4, `readiness/score.py`, pure function `score(inputs, baseline, heat_level) -> {score, rating, reasons[]}`): start 100; RT bracket penalty by % over baseline (+10% −10, +20% −25, +30% −40, highest bracket only); −10 per lapse > 500 ms; sleep: < 5 h/24 or < 12 h/48 → −30, else < 6 h/24 → −15 (highest only); feel ≤ 2 → −15; heat EXTREME_CAUTION −5, DANGER −10; camera ≥ 2 long blinks → −15; floor 0. GREEN ≥ 75, YELLOW 50–74, RED < 50. Baseline = operator's median shift RT after 5 shifts, else population default 300 ms labelled "no personal baseline yet". RED → R20 WARNING + supervisor notify (P0). Never blocks (I10). Override endpoint (supervisor PIN).
4. **Walkaround**: store; P2 outbox.
5. **Incidents** (HLD §6.11): multipart JSON + voice + ≤ 3 photos → `media/`; auto-fill ts, x/y, `state_snapshot` (last 60 s summary), `linked_alert_ids` (alerts in last 5 min). Optional `create_hazard {type, radius_m}` creates and links a pin (the demo's "Report → Worker zone"). P0 outbox.
6. **Hazards + geofencing** (HLD §4.12): CRUD + `PATCH {CONFIRM|RESOLVE}` (confirm extends expiry, increments confirmations); version++ on every change; tombstone on delete; auto-expire WORKER_ZONE/SOFT_GROUND after 24 h (job every minute); publish full retained list on every change. Every edge instance **consumes** the retained topic to build its zone set (so multi-edge works). `geo/geofence.py` with shapely: enter when inside, exit when > radius + 2 m (hysteresis) → `GEOFENCE_ENTER/EXIT` events → R14; OVERHEAD_LINE zone + boom_tip_height > line_clearance − 3 m → R15. Position stale > 10 s → `sensor_health.position = UNKNOWN`, geofence alerts paused, visible in state.
7. **Env one-taps**: `/env/ground`, `/env/manual`; `/idle/{id}/reason`.

**Verification gate**
- [ ] Auth: right PIN → token; wrong PIN → 401; unknown badge → guest with `verified=false`; operator token rejected on supervisor routes. Works with network disabled.
- [ ] Readiness table test (all brackets) including D3: `sleep24=5, sleep48=14` → 85 GREEN; `sleep24=5, sleep48=11` → 70 YELLOW. RED creates R20 alert + P0 outbox; no endpoint refuses a RED operator.
- [ ] Incident: multipart with voice + 2 photos stored; snapshot and links filled; P0 outbox row; `create_hazard` makes a linked pin.
- [ ] Hazard retained message: new subscriber immediately receives the full list with versions.
- [ ] **Multi-edge test:** two edge-api instances (EXC001, EXC002) on one broker, separate DB files. Pin created on instance A → instance B's fake machine drives into it → R14 on B within 2 s.
- [ ] Geofence hysteresis: machine oscillating ±1 m at zone edge → one ENTER only.
- [ ] Power line: inside OVERHEAD_LINE (clearance 8 m), boom tip 5.5 m → R15 CRITICAL; 4.5 m → none.
- [ ] Expiry: WORKER_ZONE with created_at − 25 h → expired by the job, removed from retained list, tombstoned.
- [ ] Position stale 11 s → geofence paused and surfaced in `sensor_health`.

---

### Phase 6 — Prediction and learning: ETA, anomaly, lessons, Replay, scorecard (H18–21)

**Build**
1. **ML adapter** (`ml/adapter.py`): load `ml_runtime` models from `MODELS_DIR` via `model_registry` (sha256 check), atomic hot-swap on new version, fallbacks (§5.6). Until P1 delivers, a stub `ml_runtime` in `tests/stubs/` implements the interface.
2. **ETA service**: build features (§5.6) and predict on: shift start, task status change, rain start/stop, readiness update, unplanned stop (exit or engine-off during an IN_PROGRESS task not tagged BREAK), truck count change. No live progress (HLD decision). ETA for in-progress task = actual_start + p50 + accumulated unplanned-stop minutes; next tasks chain: `eta_i = max(scheduled_start_i, eta_{i−1}) + p50_i`. Cold start: `operator_rate_source` ≠ OPERATOR → label "Estimate (cohort)" / "Estimate (generic)". Store pred fields on task; push `eta` WS message on change. `GET /tasks`, `POST /tasks/{id}/status`, `GET /tasks/{id}/eta`.
3. **Anomaly job**: on each hourly window close → features + 14-day operator baseline → score → store on window; if ≥ `threshold_top3pct` → R23 INFO (no audio) + review queue. **Fuel baseline** for R24: fuel_per_productive_h vs operator 14-day median.
4. **Lesson engine** (HLD §4.11): assign on 1 CRITICAL event or same rule ≥ 2× in 7 days (reason string e.g. "R03 ×2 in 7 days"); `deliverable` computed live = engine OFF OR on break OR shift ended, and never while `active` (I5). `GET /lessons/assigned`, `GET /lessons/{id}` (409 if not deliverable), `POST /lessons/{id}/complete`. Missing content → generic lesson for the rule family.
5. **Replay**: on an UNSAFE exit, create `scenario` from the exit's state snapshot using P3's `replay_templates[rule_id]` (placeholder fill only — prompt/options/explanation text comes from P3's content, never generated).
6. **Scorecard** (HLD §6.12): daily/weekly per operator: loads_per_productive_h, passes_per_h, fuel_per_m3, idle_ratio, long_idle_count, seatbelt_compliance_pct, unsafe_exit_rate (per 100 exits), exits_count, alerts_per_h, critical_count, near_misses_reported, lessons_completed, mean_quiz_score, eta_accuracy_mape, plus idle fuel and CO₂ (2.68 kg/L, HLD §10). Site percentile/veteran gap left null on edge (cloud stretch).
7. **Fatigue samples** (stretch, D5): `POST /fatigue/samples` → context → R19 (still `enabled:false` unless the team turns it on).

**Verification gate**
- [ ] ETA with stub model: returns p50/p90/drivers/model_version; with model file deleted → generic estimate labelled, no 500.
- [ ] Re-estimation: unplanned stop of 12 min on an in-progress task moves ETA by ≈ 12 min (the demo's 3:40 → 3:52); a status change on task 1 shifts task 2's ETA.
- [ ] Model hot-swap: drop a new version into registry → next prediction uses it, no restart; corrupt sha → rejected, old model stays.
- [ ] Anomaly: window crafted at top of stub score → R23 INFO with no AUDIO channel; review queue item present.
- [ ] Lessons: second R03 within 7 days → assignment created; `GET /lessons/{id}` while machine active → 409; engine off → 200 and WS `lesson {deliverable:true}`.
- [ ] Replay scenario from the Phase 3 unsafe-exit fixture contains the real bucket height and failed checks.
- [ ] Scorecard on a fixture day: unsafe_exit_rate and seatbelt_compliance_pct match hand-computed values.

---

### Phase 7 — Proximity CV worker (H21–23)

**Build**
1. One-time export on a dev machine (document in RUNBOOK): `pip install ultralytics` → `yolo export model=yolo11n.pt format=onnx imgsz=416`. Runtime image uses only onnxruntime + opencv-python-headless + numpy (no torch, no ultralytics).
2. `capture.py`: read MJPEG from the phone (IP Webcam app URL, configurable `CAM_URL`) in a thread, keep only the latest frame.
3. `detector.py`: letterbox to 416, RGB, /255, NCHW; ONNX output `(1, 84, N)` → transpose → boxes (cx,cy,w,h) + class 0 (person) score; conf ≥ `CV_CONF` (0.45 default, 0.35 demo, HLD §13); NMS IoU 0.5 (`cv2.dnn.NMSBoxes`). Verify the output shape at startup and fail loudly if it differs.
4. `distance.py`: d = f_px × 1.7 m / box_height_px; `calibrate.py` computes f_px from one photo of a person at a known distance. Sector from config (REAR). Expect ±30% (HLD), zones are wide on purpose.
5. `quality.py`: frame_ok false if no frame for 2 s, mean brightness < 40, or Laplacian variance < 50 (thresholds configurable; tune at venue).
6. 3-of-5 frame persistence; publish `cv/proximity` at 5 Hz, `min_dist_m: null` when no person and frame_ok.
7. Optional debug MJPEG with boxes at `:8081/debug` (stretch, for the pitch camera thumbnail).

**Verification gate**
- [ ] Unit: on Ultralytics' public `bus.jpg`/`zidane.jpg` sample images → ≥ 1 person; on a blank/sky image → none.
- [ ] Distance: synthetic box of known height with calibrated f → expected distance ±1%.
- [ ] Quality: black frame and heavily blurred frame → frame_ok false; engine reports proximity UNAVAILABLE and falls back to sim channel if present.
- [ ] Persistence: detections in 2 of 5 frames → nothing published; 3 of 5 → published.
- [ ] Throughput on demo laptop ≥ 10 inference FPS at 416 (record measured FPS).
- [ ] End-to-end live: person walks behind phone camera while sim swings → R12 audio nudge on tablet.

---

### Phase 8 — Alarm explainer + RAG + LLM (H23–25)

**Build**
1. **Catalogue** (`rag/catalogue.py`): `diagnostic_codes.yaml` → table. Target 30–50 codes (HLD §7.5). Write entries in plain, generic, conservative language; every entry `source_doc: "DEMO corpus — verify against OMM"`, `action_class` only STOP/MONITOR/CONTINUE where the HLD seed list states it, else UNDOCUMENTED. A human on the team must review this file before the demo — list that as an open item.
2. **Manual corpus** `data/manuals/*.md`: daily walkaround, safe shutdown, mounting/dismounting (three points of contact), coupler checks, overheating response — generic public safety practice, clearly labelled demo corpus. Do not attribute text to CAT manuals.
3. **Index** (`tools/build_rag_index.py`, run at image build): one chunk per code; manual sections 300–500 tokens with headers; FTS5 table + `vec0` table `float[384]` via sqlite-vec; embeddings with fastembed `BAAI/bge-small-en-v1.5`, model cached inside the image (offline at runtime).
4. **Retrieval**: regex exact match (`SPN\s*(\d+)\s*FMI\s*(\d+)`, `E\d{3}`) → SQL first; else FTS5 BM25 top-10 + vector top-10 → RRF (k = 60) → top-4. Gate per D13.
5. **LLM** (`rag/llm.py`): online (connectivity check) → OpenAI-compatible endpoint from env (`CLOUD_LLM_BASE_URL`, `CLOUD_LLM_API_KEY`, `CLOUD_LLM_MODEL` — Groq/OpenRouter); offline → Ollama `/api/chat`, `stream:false`, low temperature, short `num_predict`, `keep_alive` long, model from `OLLAMA_MODEL` (default `qwen3:4b-instruct-2507-q4_K_M`, fallback `llama3.2:3b` — **verify both tags exist in the Ollama library before pulling**). Warm-up call at boot. System prompt: answer only from context, ≤ 3 short sentences, plain language, cite section.
6. **Guard** (`rag/guard.py`): `action_class` inserted from catalogue (I3); if class is STOP and answer contains continue-type phrasing ("continue", "keep working/operating/running", "safe to operate", "no need to stop") → discard, return templated card. LLM timeout/error → templated card from catalogue fields.
7. Endpoints: `GET /diagnostics/active` (cards, no LLM), `POST /assistant/ask` → `{answer, citations[], action_class, model: local|cloud|template, latency_ms}`.
8. `tests/rag_golden.yaml` — 30 questions with expected code/section; `tools/eval_rag.py`.

**Verification gate**
- [ ] `/diagnostics/active` for SPN 1638 FMI 16 returns What happened / Why it matters / What to do + action_class MONITOR with Ollama stopped (no LLM needed).
- [ ] "Can I keep working?" with context SPN 1638 FMI 16 → answer + citation, `model: local`, network off; p50 latency < 5 s on demo laptop (record).
- [ ] Guard test: mocked LLM returns "You can continue operating" for a STOP code → response is the template, action_class STOP.
- [ ] Off-corpus question ("what's the cricket score") → fixed refusal, `action_class: UNDOCUMENTED`.
- [ ] `tools/eval_rag.py`: retrieval hit@3 ≥ 90% on the golden set; action-class accuracy 100%. Save to `docs/eval_rag.md`.
- [ ] Container starts and answers with the network fully disabled (embedding model and Ollama model already local).

---

### Phase 9 — Sync agent + cloud API → **CP2 (H28)**

**Goal:** full DETECT → NUDGE → LEARN → PREDICT + unplug test.

**Build**
1. **Connectivity** (`sync/connectivity.py`): `GET {CLOUD_URL}/health`, 2 s timeout, every 5 s; honour `force_offline` (D16).
2. **Outbox agent** (HLD §4.13): when online, take PENDING rows ordered by (priority, seq), batch ≤ 500, gzip JSON, `POST /sync/push` with device token; mark SENT for `acked_seqs`; exponential backoff 5 s → 300 s; queue > 50k → delete oldest PENDING P3 only (never P0/P1). After an incident is acked, upload its media (D7). Publish `sync/status` every 10 s and on change ("Offline · 213 items waiting").
3. **Inbox** (`sync/inbox.py`): pull streams hazards, tasks, lessons, roster, models, forecast by cursor; apply: hazards LWW by `version` (tie → `updated_at`, then device id) + tombstones; tasks cloud-authoritative except `status`; roster replaces operator hashes; models → download, sha256 verify, register, hot-swap (Phase 6); forecast → env cache.
4. **Cloud API** (`cloud/`): Postgres with the shared models + `device`, `change_log` (bigserial id = pull cursor). `/sync/push` idempotent upserts by entity_id (immutable entities: insert-or-ignore; mutable: LWW); always returns every processed seq including duplicates. `/sync/pull?stream=&cursor=&site_id=`. `/models/{name}/latest`, `/models/{name}/{version}/file`, `POST /models/{name}` (admin). Device tokens issued by `POST /devices/register` (admin secret). Weather job: Open-Meteo hourly forecast for the site lat/lon (temperature, RH, precipitation, wind, gusts, visibility) every hour → `forecast` stream.
5. Deploy cloud per D16 (remote host) and document in RUNBOOK.

**Verification gate**
- [ ] Idempotency: push the same batch twice → Postgres row counts unchanged; both responses ack all seqs.
- [ ] Priority: 1,000 P3 rows + 1 P0 incident queued → incident is in the first batch.
- [ ] Overflow: 50,001 rows → oldest P3 dropped; zero P0/P1 dropped.
- [ ] Crash safety: kill edge-api mid-push → restart → no lost rows, no duplicates in cloud.
- [ ] Hazard conflict: same pin edited on edge (v3) and cloud (v4) → both converge to v4; delete → tombstone propagates both ways.
- [ ] Model push: P1 uploads a model to cloud → edge pulls, verifies, hot-swaps (visible in `/system/status`).
- [ ] **Unplug test (CP2):** run the full demo script §12 steps 1:30–4:50 with the uplink physically disconnected → every safety feature works; status shows Offline + growing queue; reconnect → queue drains to 0 and cloud shows the unsafe exit (corrected in N s), near miss, hazard pin, Yellow readiness, updated ETA. Repeat with the kill switch as the backup path.
- [ ] 1 Hz telemetry never present in cloud tables (I8) — query check.

---

### Phase 10 — Supervisor / fleet API → **CP3 (H38, feature freeze)**

**Build** — shared router `common/fleet/router.py` mounted on **edge** (SQLite, site-local over LAN) and **cloud** (Postgres). Every response has `as_of` and `source: edge|cloud`; every machine has `last_seen` and `offline` flag (I6). Supervisor role required. Polling-friendly (P3 polls every 10 s) plus `/ws/site` on edge.

| Screen (HLD §9.2) | Endpoints |
|---|---|
| Fleet overview | `GET /fleet/overview` (machines, state class, operator, current task, last_seen, offline badge) |
| Live alerts & exits | `GET /fleet/alerts?since=&level=`, `GET /fleet/exits?since=`, unsafe-exit rate, belt-bypass flags; `POST /alerts/{id}/ack` |
| Machine detail | `GET /fleet/machines/{id}` (hourly windows incl. organizer schema, idle buckets, fuel idle/work split, DTC history) |
| Operators | `GET /fleet/operators`, `GET /fleet/operators/{id}/scorecard` (+ readiness history, lessons) |
| Incidents | `GET/PATCH /fleet/incidents` (status, notes; media URLs) |
| Hazard map | `GET /fleet/hazards`, `PATCH /hazards/{id}` (approve/resolve) |
| Tasks & ETA | `GET /fleet/tasks?date=` (plan vs predicted vs actual, backlog, ETA MAPE) |
| Anomaly review | `GET /fleet/anomalies?status=open`, `POST /fleet/anomalies/{id}/label` |

Supervisor writes on edge (ack, hazard approve, anomaly label, task assignment) go through the outbox; on cloud they write `change_log` so edges pull them.

**Verification gate**
- [ ] Same test suite runs against both mounts (SQLite and Postgres) and passes.
- [ ] Offline machine shows `offline: true` and a `last_seen` in the past — never presented as live.
- [ ] Anomaly label on cloud → stored and exportable for P1 retraining (document query in `contracts/history.md`).
- [ ] Hazard approved on cloud → pulled by edge → retained topic updated.
- [ ] `/fleet/machines/EXC001` shows the organizer anchor windows (after `load_history.py`).
- [ ] **CP3:** P3's supervisor dashboard runs against both edge and cloud with no backend changes. Feature freeze after this gate.

---

### Phase 11 — Hardening and demo resilience (H38–44)

**Build**
1. `tools/reset_demo.sh` — restore demo DB snapshot, clear queue, reset sim, re-publish hazards; target < 5 s (HLD §12).
2. Boot sequence: Ollama warm-up, model load checks, RAG index present, CV camera check → `/system/status` all green or clearly red.
3. Compose `restart: unless-stopped`, healthchecks, and resource limits; measure container restart time (D18).
4. Load test: 3 machines × 1 Hz for 30 min + CV 5 Hz + 2 WS clients → no dropped frames, p95 nudge latency still < 300 ms, SQLite size and CPU recorded.
5. `docs/RUNBOOK.md`: network setup (travel router, uplink), start order, reset, calibration, kill switch, known failure → action table (HLD §13.1).
6. Final OpenAPI + TS types; tag release.

**Verification gate**
- [ ] Full demo script (HLD §12) run end-to-end 3× in a row without manual DB edits; `reset_demo.sh` between runs.
- [ ] `docker kill edge-api` during the demo → back automatically; open exit and alerts restored.
- [ ] Load test numbers recorded and within targets.
- [ ] All invariant tests I1–I10 green; full suite green.

---

## 8. Traceability — HLD → phase (coverage verified)

| HLD item | Phase |
|---|---|
| §4.2 Ingestion + event bus | 2 |
| §4.3 Operator State Engine | 3 |
| §4.4 Risk Engine, rules R01–R24, alert-fatigue controls | 3 (R14/R15 zones 5, R20 5, R23/R24 6, R19/R22 stretch) |
| §4.5 Anomaly inference | 6 |
| §4.6 Task-time predictor serving | 6 |
| §4.7 Readiness scoring + endpoint | 5 (fatigue samples 6, stretch) |
| §4.8 Proximity CV | 7 |
| §4.9 Alarm explainer + RAG | 8 (catalogue seeded 1, R16/R17 3) |
| §4.10 Nudge generator | 4 |
| §4.11 Lesson engine + Replay | 6 |
| §4.12 Hazard map + geofencing | 5 |
| §4.13 Sync | 1 (outbox), 9 (agent + cloud) |
| §4.14 Auth | 4 (minimal), 5 |
| §4.15 Supervisor backend | 10 |
| §5 Stack / docker compose | 0, 11 |
| §6 Data model (all tables) | 1 |
| §6.13 Organizer anchors | 2 (rollup), 10 (display) |
| §7.3 Rules evaluation (100% recall) | 3 |
| §7.5 RAG evaluation | 8 |
| §8.1 MQTT, §8.2 edge REST, §8.3 sync/cloud | 0 (contracts), 4–10 |
| §10 additions: belt bypass, walkaround, 3-tap report, "why", alert budget, CO₂ ticker, lessons at safe moments, sensor honesty, handover note, power-line check | 3, 5, 6 |
| §12 Demo script | CP1 (4), CP2 (9), 11 |
| §11 Contract-first + replay JSONL | 0 |

Not backend (confirmed out of scope): simulator, model training/eval, all UI screens (§9), MediaPipe, Piper rendering, lesson content, site-plan art.

## 9. Cut order if behind

Cut from the bottom up, never touching Phases 0–4 or the unplug test:
1. Debug MJPEG thumbnail (7.7) · 2. Fatigue samples / R19 (6.7) · 3. Cloud weather job (use sim env only) · 4. Model push/hot-swap from cloud (ship models baked in) · 5. `/ws/site` (supervisor polls REST) · 6. Multi-edge mode test (single instance still uses retained topic) · 7. Cloud LLM (local only) · 8. Scorecard weekly (daily only).

## 10. Open questions for teammates (resolve before the phase that needs them)

| # | Question | Who | Needed by |
|---|---|---|---|
| Q1 | Agree `raw.v1` (§5.2), including sim providing `bucket_height_m`/`boom_tip_height_m` (D12) | P1 | Phase 0 |
| Q2 | Sim control endpoint + scenario names (§5.5) | P1 | Phase 0 |
| Q3 | `ml_runtime` interface, feature names, library versions (D15, D24) | P1 | Phase 0 / 6 |
| Q4 | Rollup definition + anchor traces (D14, D17) | P1 | Phase 2 |
| Q5 | Demo readiness input or threshold for "Yellow at 5 h" (D3) | P1 + P3 | Phase 5 |
| Q6 | 950 GC proximity distances (D9) | Team | Phase 3 |
| Q7 | Lesson catalogue + Replay template format (`lesson.v1`) | P3 | Phase 6 |
| Q8 | Where the cloud is hosted for the unplug demo (D16) | Team | Phase 9 |
| Q9 | Human review of `diagnostic_codes.yaml` and manual corpus wording | Team | Before demo |