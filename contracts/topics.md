# MQTT topics — v1

Agreed by: P1 ☐ · P3 ☐

Broker: Mosquitto on the edge (port 1883, anonymous on the demo LAN only). `{site}` = `SITE-PUN-01`, `{machine}` = `EXC001`, `WL001`, `EXC002`.
Every payload is JSON with a `schema` field and a `ts` in ISO-8601 **with offset** (site time, `+05:30`). The UI does not speak MQTT. It uses the WebSocket bridge (`ws.md`).

| Topic | Direction | Rate | Payload (schema) |
|---|---|---|---|
| `cat/{site}/{machine}/raw` | sim → ingest | 1 Hz + on switch change | `raw.v1` |
| `cat/{site}/{machine}/dtc` | sim → ingest | on change | `dtc.v1` |
| `cat/{site}/{machine}/cv/proximity` | cv-worker → engine | 5 Hz | `proximity.v1` (`source: "cv"`) |
| `cat/{site}/{machine}/sim/proximity` | sim → engine | 1–5 Hz | `proximity.v1` (`source: "sim"`, fallback channel) |
| `cat/{site}/env` | sim / env source → ingest | 10 min | `env.v1` |
| `cat/{site}/{machine}/telemetry` | ingest → all | 1 Hz | `telemetry.v1` |
| `cat/{site}/{machine}/event` | ingest / engine → all | on event | `event.v1` |
| `cat/{site}/{machine}/state` | engine → all | on change, ≤ 1 Hz | `state.v1` |
| `cat/{site}/{machine}/alert` | engine → all | on change | `alert.v1` |
| `cat/{site}/{machine}/nudge` | engine → all | on event | `nudge.v1` |
| `cat/{site}/hazards` **retained** | edge ↔ devices | on change | `hazards.v1` (full list with versions) |
| `cat/{site}/{machine}/sync/status` | sync → UI | 10 s + on change | `sync_status.v1` |

Rules:
- **Unknown, never safe (I1):** a missing key or `null` in `raw.v1` means UNKNOWN. Send `null`. Never send a default like `false` or `0` for a sensor you don't have.
- `sim_label` in `raw.v1` is used only for evaluation. The engine never reads it (I9).
- Proximity: `frame_ok: false` means the camera is unusable (UNKNOWN). `min_dist_m: null` with `frame_ok: true` means no person was seen.
- Hazards always travel over the retained topic, even inside one edge process. This is what makes multi-edge "site memory" work (plan.md §2).
- Raw 1 Hz topics stay on the site LAN. Only rollups sync to the cloud (I8).

Tools: `backend/tools/replay.py` publishes a JSONL file. `backend/tools/record.py` captures topics to JSONL. Line format: `{"topic", "payload", "retain"?}`.
