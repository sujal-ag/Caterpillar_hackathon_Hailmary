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
| POST | `/auth/login` | `{badge_id, pin}` → `{token, operator, shift}`. badge_id = employee_code (D6). Unknown badge → guest, `verified=false` | — | 4 (min) / 5 |
| POST | `/shift/start` | Start a shift → `SH-YYYYMMDD-{machine}-D` | operator | 5 |
| POST | `/shift/end` | `{handover_note?}` | operator | 5 |
| GET | `/shift/current` | Current shift + previous handover note | operator | 5 |
| POST | `/readiness` | Submit a check → `readiness.v1` (`score`, `rating`, `reasons[]`). Never blocks (I10) | operator | 5 |
| POST | `/readiness/{id}/override` | Supervisor PIN override | supervisor | 5 |
| POST | `/walkaround` | Checklist `{items:[{item, status, photo?}]}` | operator | 5 |
| GET | `/tasks?shift_id=` | Today's tasks with predictions (`task.v1[]`) | operator | 6 |
| POST | `/tasks/{id}/status` | `{status, delay_reason?}` | operator | 6 |
| GET | `/tasks/{id}/eta` | `EtaPrediction` | operator | 6 |
| GET | `/state/current` | Same content as the WS `snapshot` | operator | 4 |
| POST | `/alerts/{id}/ack` | Acknowledge (≠ clear) | operator | 4 |
| GET | `/alerts/{id}/why` | Rule id, inputs, thresholds | operator | 4 |
| POST | `/incidents` | Multipart: JSON (`incident.v1` subset, optional `create_hazard {type, radius_m}`) + voice + ≤ 3 photos | operator | 5 |
| GET | `/hazards?bbox=` | `hazards.v1` | any | 5 |
| POST | `/hazards` | Create a pin | operator | 5 |
| PATCH | `/hazards/{id}` | `{action: CONFIRM\|RESOLVE}` | operator / supervisor | 5 |
| POST | `/env/ground` | `{ground_condition: DRY\|WET\|MUDDY}` | operator | 5 |
| POST | `/env/manual` | `{temp_c, rh_pct}` | operator | 5 |
| POST | `/idle/{episode_id}/reason` | `{reason_tag}` (R09 chip) | operator | 5 |
| GET | `/diagnostics/active` | Active DTC cards: what happened / why it matters / what to do + `action_class` (no LLM) | operator | 8 |
| POST | `/assistant/ask` | `{question, context_code?}` → `{answer, citations[], action_class, model: local\|cloud\|template, latency_ms}` | operator | 8 |
| GET | `/lessons/assigned` | Assignments + `deliverable` | operator | 6 |
| GET | `/lessons/{id}` | Lesson content. `409` while the machine is active (I5) | operator | 6 |
| POST | `/lessons/{id}/complete` | `{score, answers[]}` | operator | 6 |
| GET | `/scorecard/{operator_id}?period=day\|week` | Scorecard | operator (self) / supervisor | 6 |
| POST | `/fatigue/samples` | In-shift fatigue samples (stretch, D5) | operator | 6 |
| POST | `/sim/scenario` | `{name, machine_id?}` proxy to the sim, or replay (`sim_control.md`) | admin | 4 |
| GET | `/sim/scenarios` | Scenario names | admin | 4 |

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
