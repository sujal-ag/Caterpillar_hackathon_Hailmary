# Runbook — edge stack

How to run the edge for development and the demo. Every setting is an env var documented in `infra/.env.example`.

## First start

```sh
cp infra/.env.example infra/.env
# set EDGE_JWT_SECRET to a long random string (edge-api refuses to start without it
# unless AUTH_DISABLED=true), and CORS_ORIGINS (see below)
docker compose -f infra/docker-compose.yml up -d --build
curl -s localhost:8000/health
```

Compose defaults for edge-api: `SIM_MODE=replay` (fixture scenarios, no P1 sim needed), `EDGE_SEED_ON_START=true` (idempotent seed into the `edge-data` volume), `AUTH_DISABLED=false`.

If an older DB fails with `DB schema vN is older than vM`, delete it and let the seed recreate it: `docker compose -f infra/docker-compose.yml down -v` (drops the volumes), or delete `backend/data/edge.db` for a host run.

## Tablet on the LAN

- edge-api binds `0.0.0.0:8000`, so any device on the travel-router LAN can reach it.
- Tablet / P3 base URL: **`http://<laptop-LAN-IP>:8000`**. Get the IP with `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux). WebSocket: `ws://<laptop-LAN-IP>:8000/ws/live?machine=EXC001&token=<JWT>`.
- `CORS_ORIGINS` must include every browser origin P3 serves from: the dev server (e.g. `http://localhost:5173`, `http://<P3-laptop-IP>:5173`) and the PWA origin. Comma-separated, exact scheme + host + port. A missing origin shows up in the browser console as a CORS error and nothing else, so check it first.
- The broker (1883) accepts anonymous clients on purpose (isolated demo LAN only, `infra/mosquitto.conf`). Never expose it on an untrusted network.

## Auth

- Login: `POST /auth/login {"badge_id": "EMP1001", "pin": "1234", "machine_id": "EXC001"}` → `{token, ...}`. Badge = `employee_code` (D6). Demo users and roles are in `data/seed/operators.yaml` (`SUP001` is admin, which includes supervisor).
- `/sim/*` needs admin; `/ws/site` needs supervisor.
- `AUTH_DISABLED=true` makes every request admin. P3 development only. It is logged at WARNING on start and reported by `/system/status` (`auth_disabled: true`).

## Shift, readiness, hazards (Phase 5)

- Shifts: one per machine per site-local date, `SH-YYYYMMDD-{machine}-D`, window `SHIFT_START`/`SHIFT_END` (site-local HH:MM; END ≤ START = overnight). The seed plans today's EXC001 (OP1001, "Operator A") and EXC002 (OP1002 `EMP1002`/`5678`, "Operator B") shifts, insert-only, so a container restart never resets an ACTIVE shift. `/shift/start` creates the row when none exists for today (e.g. after midnight).
- Tokens last `max(shift planned_end + 2 h, now + JWT_TTL_S)`.
- Demo flow: login → `POST /readiness` → `POST /shift/start` (R20 fires here if RED) → work → `POST /incidents` with `create_hazard {"type":"WORKER_ZONE"}` → the pin is on the retained topic → drive EXC002 in: `ws_probe.py --machine EXC002 --badge SUP001 --pin 9999 --scenario drive_into_zone --speed 4 --expect nudge:R14` (only if a pin lies on the drive path).
- Incident media: `MEDIA_DIR` (compose: `/data/media` in the `edge-data` volume). Upload to the cloud is Phase 9.
- Schema v3 (Phase 5): an older DB is refused at start. Delete it and let the seed recreate it: `docker compose -f infra/docker-compose.yml run --rm --no-deps --entrypoint rm edge-api -f /data/edge.db /data/edge.db-wal /data/edge.db-shm`.
- Retained hazards: `docker exec operator-companion-mosquitto-1 mosquitto_sub -t 'cat/SITE-PUN-01/hazards' -C 1 -v`.

## Models, ETA, lessons (Phase 6)

- P1 hand-off: install P1's `ml_runtime` in the image, then `tools/register_model.py <eta|anomaly_EXCAVATOR|anomaly_WHEEL_LOADER> <version> <file>` against the edge DB (`MODELS_DIR`, compose `/data/models`), and restart edge-api. `/system/status` → `models` + `model_detail` (loaded versions or the rejection reason). Without it: ETAs are "Estimate (generic)" and anomaly scoring is off (R23 UNKNOWN). Nothing blocks.
- Demo data is seeded relative to now: today's tasks sit on today's shift (offsets from `SHIFT_START`), and OP1001 has one earlier unsafe exit 2 days back, so the live one reads "R03 ×2 in 7 days".
- Lessons: `data/lessons/lessons.json` (a stub until P3 delivers). They are served only with the engine OFF or after shift end (`409` otherwise, I5).
- Schema v4: delete the old DB as in Phase 5.

## Scenarios

- `GET /sim/scenarios`, `POST /sim/scenario {"name": "unsafe_exit"}` (admin token).
- Replay mode plays `backend/tests/fixtures/scenarios/{name}.jsonl`. The list is whatever is in that directory. With `SIM_REPLAY_HOLD=true` the last raw frame keeps being re-sent so the machine stays in the scenario's end state; start `reset` to get back to normal work.
- `SIM_MODE=proxy` + `SIM_CONTROL_URL` forwards the same calls to P1's simulator.

## Checks

```sh
cd backend
.venv/bin/python tools/ws_probe.py --machine EXC001 --badge SUP001 --pin 9999 \
  --scenario unsafe_exit --speed 4 --expect nudge:R03:FULLSCREEN --expect state:exit_state=SAFE --timeout 30
.venv/bin/python tools/latency_probe.py --runs 50 --badge SUP001 --pin 9999 --stop-replay
curl -s localhost:8000/system/status -H "Authorization: Bearer $TOKEN"
docker exec operator-companion-mosquitto-1 mosquitto_sub -t 'cat/#' -v
RUN_BROKER_RESTART=1 .venv/bin/pytest -q tests/integration   # restarts the broker: reconnect check
```

The latency probe must be the only publisher of raw frames for its machine. It refuses to start while a replay scenario is running (`--stop-replay` calls `POST /sim/stop` first). It checks the raw topic is quiet for `data_stale_s` before starting, and it aborts with a clear message if a foreign frame appears (P1's sim, `tools/replay.py`, another probe). With P1's simulator driving every machine, run it against a machine the sim doesn't drive (`--machine`), or pause the sim.

The WebSocket JWT travels in the URL (`?token=`). edge-api masks `token=` values in uvicorn's access and error logs, so tokens don't end up in container logs.
