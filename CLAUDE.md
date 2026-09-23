# Operator Companion — Backend (Edge + Cloud)

In-cab AI safety companion for CAT 320 excavator / 950 GC wheel loader (hackathon, ~48 h).
Loop: DETECT (telemetry + camera + env) → NUDGE (one spoken instruction) → LEARN (micro-lesson) → PREDICT (ETA, anomaly).
Hero feature: **Exit Guard** (unsafe-exit detection). Safety-critical paths run fully offline on the edge.
This repo is **backend only (P2)**.

## Source of truth
- `docs/hld.md`: WHAT the product does (rules R01–R24 §4.4, data model §6, ML §7, APIs §8, demo §12).
- `docs/plan.md`: HOW the backend is built (phases, contracts, gates). **If the two disagree, plan.md wins.** Deviations D1–D24 are in plan.md §3.
- `docs/PROGRESS.md`: phase log. Append to it after every phase.
- Read the relevant plan.md phase section and its HLD refs before you start a phase.

## Workflow (plan.md §0)
1. Build phases 0 → 11 strictly in order. Only start a phase when the user asks for it.
2. A phase is done only when every item in its **Verification gate** passes. If a gate fails, fix it, or stop and report what blocks it. Never skip to the next phase.
3. After each phase:
   - Run the full test suite.
   - Append to `docs/PROGRESS.md`: phase, what was built, gate results with a short output summary, deviations.
   - Export `contracts/openapi.json` and regenerate the TS types (from Phase 4 on).
   - Commit as `phase-N: <summary>`.
4. If the plan is ambiguous or contradicts itself, **stop and ask**. Do not guess.
5. Open teammate questions Q1–Q9 (plan.md §10): use the named stub/fixture behind the same interface. Do not block on them.

## Do NOT build (teammates own)
- **Simulator (P1).** Only `tools/fake_machine.py` (a deterministic fixture scripter) is allowed. It must never grow into a simulator.
- **ML training, datasets, eval numbers (P1).** Only load models through the `ml_runtime` interface (plan.md §5.6) with fallbacks.
- **Any UI (P3):** PWA, supervisor dashboard, MediaPipe, Piper audio, map UI, lesson content. The backend ships REST, WS, TS types, `contracts/i18n/en.json` and `contracts/audio_clips.yaml`.

## Safety invariants: tests must enforce every one (plan.md §0.1)
- I1: A missing, stale or faulted signal = **UNKNOWN, never SAFE**. UNKNOWN on an Exit Guard check counts as FAIL. An UNKNOWN safety sensor means the class is never PRODUCTIVE.
- I2: Advisory only. No code path commands or interlocks the machine.
- I3: The LLM is never on the safety path. `action_class` is always copied from the catalogue.
- I4: CRITICAL audio is never rate-limited, suppressed or downgraded.
- I5: A lesson is never deliverable while the machine is `active` (checked server-side).
- I6: Stale data is never shown as live. Every payload has `ts`; every snapshot/list has `as_of`.
- I7: A cloud-bound write inserts its `sync_queue` row in the **same SQLite transaction**.
- I8: Raw 1 Hz telemetry never leaves the edge.
- I9: `sim_label` is read only by `tools/eval_*.py`.
- I10: Readiness and fatigue never block operation.

## Hard rules
- Never invent safety numbers, machine specs or CAT documentation. Thresholds live in `contracts/rules.yaml` or `data/seed/*.yaml` with an `hld_ref`. Tag assumptions with `assumption: true`.
- Keep it simple: one edge-api process with asyncio tasks. No Kafka, Celery, Redis, Kubernetes or migration framework. Use `create_all` + a `schema_version` table.
- Rules are deterministic (YAML + Python predicates). ML scores are only *inputs* to rules.

## Stack (plan.md §4)
Python 3.11, FastAPI + uvicorn, pydantic v2 + pydantic-settings, SQLModel, aiomqtt 2.x (Mosquitto), SQLite WAL (+ FTS5, sqlite-vec), Postgres (cloud, psycopg), uuid-utils (UUIDv7 via `common/ids.py`), PyJWT, bcrypt, shapely 2, fastembed, onnxruntime, opencv-python-headless, httpx, pytest + pytest-asyncio, ruff. Pin versions in `requirements.lock`. Pin sklearn/lightgbm/joblib to P1's versions (D24). Docker images: `python:3.11-slim`. Run the stack with `infra/docker-compose.yml`.

## Conventions
- Config via env vars (`pydantic-settings`). Document every var in `infra/.env.example`. No hard-coded URLs or secrets.
- IDs: UUIDv7 strings for event-like rows. Human IDs for master data (`EXC001`, `WL001`, `OP1001`, `SITE-PUN-01`).
- Time: UTC in the DB. On the wire, ISO-8601 with the site offset (`Asia/Kolkata`, +05:30). Use a monotonic clock for engine timers.
- Units: SI, °C, kPa, L, m. Field names exactly as HLD §6 unless plan.md §3 changes them.
- Every MQTT/WS payload has a `schema` field (`raw.v1`, `telemetry.v1`, `state.v1`, …).
- SQLite: WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`. All writes go through the single `DbWriter` task. Never block the event loop.
- Logs are structured JSON. Every alert raise/clear is logged with rule_id and inputs.
- A failing rule is disabled and logged. The other rules keep running.
- Tests: unit tests use the in-memory `MemoryBus`. Integration tests use real Mosquitto via compose.

## Layout (plan.md §6)
`backend/{common,edge,cloud,cv_worker,tools,tests}` · `contracts/` (schemas, rules.yaml, i18n, API docs, TS types) · `data/{seed,catalogue,manuals,lessons}` · `infra/` · `docs/`.

## Commands
Python commands run from `backend/` using `backend/.venv` (Python 3.11). Compose runs from the repo root.
- Setup: `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.lock && .venv/bin/pip install --no-deps -e .`
- Tests: `.venv/bin/pytest -q` · Lint: `.venv/bin/ruff check . && .venv/bin/ruff format --check .`
- Lock after adding a dep to `pyproject.toml`: `.venv/bin/pip install -e '.[dev]' && .venv/bin/pip freeze --exclude-editable > requirements.lock`
- Stack: `docker compose -f infra/docker-compose.yml up -d --build` (profiles: `sim`, `llm`, `cloud`)
- MQTT sub (not installed on the host): `docker exec operator-companion-mosquitto-1 mosquitto_sub -t 'cat/#' -v`
- Replay: `.venv/bin/python tools/replay.py tests/fixtures/replay_10min.jsonl --speed 10`
- Fixture: `.venv/bin/python tools/fake_machine.py tests/fixtures/specs/<name>.yaml -o tests/fixtures/<name>.jsonl`
- Seed DB: `EDGE_DB_PATH=data/edge.db .venv/bin/python tools/seed.py` (idempotent; loads `data/seed/*.yaml` + `data/catalogue/diagnostic_codes.yaml`)
- Postgres gate test: `docker compose -f infra/docker-compose.yml --profile cloud up -d postgres`, then `.venv/bin/pytest -k postgres` (skips itself if unreachable)
- Phase end: `./tools/gen_ts.sh` (TS types + `tsc`) and `.venv/bin/python tools/export_openapi.py`

## Gotchas
- YAML 1.1 reads bare `ON`/`OFF` as booleans. Quote them in fixture specs and seeds.
- Contracts are JSON Schema first (`contracts/schemas/`). Never hand-edit `contracts/ts/` or `contracts/openapi.json`.
- Phase 0 deviations and contract decisions are in `docs/PROGRESS.md`.
