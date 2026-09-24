# REST API — v1 route table

Agreed by: P3 ☐ · P1 ☐ (sim + models rows)

Source: HLD §8.2 / §8.3 + plan.md §5.7 and Phase 10. Request and response shapes are published in `contracts/openapi.json` as each route lands (it is exported at every phase end). Schemas in `contracts/schemas/` are the shared payload types.

Conventions:
- JSON bodies. Timestamps are ISO-8601 with the site offset (D22).
- Every snapshot or list response carries `as_of` (I6). Fleet responses also carry `source: edge|cloud`.
- Auth: `Authorization: Bearer <JWT>` from `/auth/login`. Roles are operator / supervisor / admin. Cloud sync uses device tokens.
- Errors: `{"detail": str}` with standard HTTP codes. `409` = not allowed right now (e.g. a lesson while the machine is active).
- **No route commands or interlocks the machine (I2).**

## Edge (edge-api, :8000)

| Method | Path | Purpose | Role | Phase |
|---|---|---|---|---|
| GET | `/health` | Liveness `{status, service, ts}` | — | 0 ✅ |
| GET | `/system/status` | Sync, models (local/cloud, versions), sensor health, disabled rules, alerts/h | any | 4 |
| POST | `/system/network` | `{force_offline: bool}` demo kill switch (D16) | admin | 9 |
| POST | `/auth/login` | `{badge_id, pin, machine_id?}` → `{token, expires_at, operator, shift}`. badge_id = employee_code (D6). With `machine_id`: `shift` = the machine's ACTIVE shift, else today's row, else null; JWT `shift_id` claim; `exp = max(planned_end + 2 h, now + JWT_TTL_S)`. Unknown badge → 401 (guest mode cut, 24 h plan) | — | 5 ✅ |
| POST | `/shift/start` | `{machine_id?}` → `{shift, previous_handover_note, as_of}`. Activates today's `SH-YYYYMMDD-{machine}-D` (created if absent; window SHIFT_START/SHIFT_END). 409 if ACTIVE for someone else or already CLOSED today. Never refused for readiness (I10) | operator | 5 ✅ |
| POST | `/shift/end` | `{machine_id?, handover_note?}` (shift's operator or supervisor) | operator | 5 ✅ |
| GET | `/shift/current?machine=` | `{shift, previous_handover_note, as_of}` | operator | 5 ✅ |
| POST | `/readiness` | Submit a check → `readiness.v1` (`score`, `rating`, `reasons[]` = i18n keys `readiness.reason.*`). Never blocks (I10) | operator | 5 ✅ |
| POST | `/readiness/{id}/override` | Supervisor PIN override — **cut (24 h plan)** | supervisor | — |
| POST | `/walkaround` | Checklist — **cut (24 h plan)** | operator | — |
| GET | `/tasks?shift_id=&machine=` | `{as_of, tasks: task.v1[]}` for the machine's current shift (or `shift_id`), each with `prediction` | operator | 6 ✅ |
| POST | `/tasks/{id}/status` | `{status, delay_reason?, actual_quantity?}` → `task.v1`. IN_PROGRESS stamps `actual_start`, DONE `actual_end`; re-estimates the machine's ETAs | operator | 6 ✅ |
| GET | `/tasks/{id}/eta` | `EtaPrediction` + `task_id`, `as_of`; 404 when not predicted (done, time-boxed) | operator | 6 ✅ |
| GET | `/state/current` | Same content as the WS `snapshot` | operator | 4 |
| POST | `/alerts/{id}/ack` | Acknowledge (≠ clear) | operator | 4 |
| GET | `/alerts/{id}/why` | Rule id, inputs, thresholds | operator | 4 |
| POST | `/incidents` | Multipart: `incident` (JSON: type, category, severity_self, transcript?, machine_id?, `create_hazard {type, radius_m?}`?) + `voice` (≤ 1 audio) + `photos` (≤ 3 images) → `{incident: incident.v1, hazard, hazard_error}`. 415 bad type, 413 too big. Media paths are relative to MEDIA_DIR | operator | 5 ✅ |
| GET | `/hazards?bbox=minx,miny,maxx,maxy` | `hazards.v1` (ACTIVE pins) | any | 5 ✅ |
| POST | `/hazards` | Create a pin `{type, geometry, radius_m?, line_clearance_m?}` → pin (201) | operator | 5 ✅ |
| PATCH | `/hazards/{id}` | `{action: CONFIRM\|RESOLVE}`. CONFIRM any operator (extends expiry); RESOLVE the reporter or a supervisor | operator / supervisor | 5 ✅ |
| DELETE | `/hazards/{id}` | Tombstone (DB keeps it; dropped from the retained list). Edge addition, Phase 5 | supervisor | 5 ✅ |
| POST | `/env/ground` | `{ground_condition: DRY\|WET\|MUDDY}` → published `env.v1` (MANUAL) | operator | 5 ✅ |
| POST | `/env/manual` | `{temp_c, rh_pct}` → published `env.v1` (MANUAL) | operator | 5 ✅ |
| POST | `/idle/{episode_id}/reason` | `{reason_tag}` (R09 chip) — skipped: R09 is cut (24 h plan) | operator | — |
| GET | `/diagnostics/active` | Active DTC cards: what happened / why it matters / what to do + `action_class` (no LLM) | operator | 8 |
| POST | `/assistant/ask` | `{question, context_code?}` → `{answer, citations[], action_class, model: local\|cloud\|template, latency_ms}` | operator | 8 |
| GET | `/lessons/assigned?operator_id=` | `{as_of, assignments[]}` with `reason`, `deliverable`, lesson summary (`operator_id`: supervisor only) | operator | 6 ✅ |
| GET | `/lessons/{id}` | `{id}` = **assignment_id** → `{assignment, lesson, replay}`. `409` unless the machine is known OFF or the shift ended (I5) | operator | 6 ✅ |
| POST | `/lessons/{id}/complete` | `{score 0–100, answers[]}` (assignment_id). `409` while the machine may be active (I5) | operator | 6 ✅ |
| GET | `/scorecard/{operator_id}?period=day\|week` | Scorecard — **cut (24 h plan)** | operator (self) / supervisor | — |
| POST | `/fatigue/samples` | In-shift fatigue samples — **cut (24 h plan)** | operator | — |
| POST | `/sim/scenario` | `{name, machine_id?}` proxy to the sim, or replay (`sim_control.md`) | admin | 4 |
| GET | `/sim/scenarios` | Scenario names | admin | 4 |
| POST | `/sim/stop` | Replay mode: stop the running scenario and its hold frames → `{ok, stopped}`. Proxy mode → `409` (the sim control API has no stop). Edge addition, Phase 4 | admin | 4 |

## Supervisor / fleet (mounted on edge **and** cloud, Phase 10)

| Method | Path | Purpose |
|---|---|---|
| GET | `/fleet/overview` | Machines, state class, operator, current task, `last_seen`, `offline` |
| GET | `/fleet/alerts?since=&level=` | Alert feed |
| GET | `/fleet/exits?since=` | Exit events, unsafe-exit rate, belt-bypass flags |
| GET | `/fleet/machines/{id}` | Hourly windows (organizer schema), idle buckets, fuel split, DTC history |
| GET | `/fleet/operators` | Operator list |
| GET | `/fleet/operators/{id}/scorecard` | Scorecard + readiness history + lessons |
| GET / PATCH | `/fleet/incidents` | Review incidents (status, notes, media URLs) |
| GET | `/fleet/hazards` | Pins for approval |
| GET | `/fleet/tasks?date=` | Plan vs predicted vs actual, backlog, ETA MAPE |
| GET | `/fleet/anomalies?status=open` | Flagged windows |
| POST | `/fleet/anomalies/{id}/label` | `{label: TRUE_POSITIVE\|FALSE_POSITIVE}` |

## Cloud only (cloud-api, Phase 9)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness (Phase 0 ✅) |
| POST | `/devices/register` | Admin secret → device token |
| POST | `/sync/push` | `sync_push.v1` (gzip) → `sync_push_ack.v1`. Idempotent |
| GET | `/sync/pull?stream=&cursor=&site_id=` | `sync_pull.v1` |
| PUT | `/sync/media/{incident_id}/{filename}` | Incident media upload (D7) |
| GET | `/models/{name}/latest` | Version + sha256 |
| GET | `/models/{name}/{version}/file` | Model binary |
| POST | `/models/{name}` | Upload a new model version (admin, P1 retrain) |
