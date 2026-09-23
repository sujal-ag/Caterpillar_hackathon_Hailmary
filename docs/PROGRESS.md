# Build progress

One entry per phase (plan.md §0 rule 3): what was built, gate results, deviations.

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
