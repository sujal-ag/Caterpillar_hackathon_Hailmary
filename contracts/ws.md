# WebSocket bridge — v1

Agreed by: P3 ☐

Endpoints (edge-api):
- `/ws/live?machine=EXC001&token=<JWT>`: operator tablet, one machine.
- `/ws/site?token=<JWT>`: supervisor, all machines, `state` + `alert` only. Needs the supervisor role.

`AUTH_DISABLED=true` skips the token. It is for P3 development only and is logged loudly.

Every message uses the envelope `ws_envelope.v1`: `{ "type": "...", "ts": "...", "data": {...} }`.

| type | data | When |
|---|---|---|
| `snapshot` | `{state: state.v1, alerts: SafetyAlert[] (active), hazards: hazards.v1, sync: sync_status.v1, eta: {task_id, prediction} \| null, as_of}` | Once, on connect and on reconnect. Always the current truth. |
| `state` | `state.v1` | On change, ≤ 1 Hz |
| `telemetry` | `telemetry.v1` | ≤ 1 Hz (throttled) |
| `alert` | `alert.v1` (`{action: RAISED\|UPDATED\|CLEARED\|ACKED, alert}`) | On change |
| `nudge` | `nudge.v1` | On event |
| `event` | `event.v1` | On event |
| `hazards` | `hazards.v1` (ACTIVE pins only) | Every time the retained `cat/{site}/hazards` list changes, after the engines applied it. Resolved/expired/deleted pins are simply absent |
| `sync` | `sync_status.v1` | 10 s + on change |
| `lesson` | `{assignment_id, deliverable: bool}` | When deliverability changes |
| `eta` | `{task_id, prediction: EtaPrediction}` | When an ETA changes |
| `health` | `{sensor_health, models, as_of}` | On change |
| `ping` | `{}` | Every 10 s |

Order within one engine step (one raw frame, proximity/env/DTC message or tick): `telemetry`, `event`s, `alert`s, `nudge`s, then `state` (then `health` if sensor health changed). A nudge therefore arrives just before the `state` that carries the same step's `exit_checks`. Read the stream to the end; don't stop at the nudge.

UI rules that the backend relies on:
- The Exit Guard overlay opens on a `nudge` with `display: FULLSCREEN, rule_id: R03`. Checklist ticks come from `state.exit_checks`. A FULLSCREEN CRITICAL alert can be acknowledged, but it stays until its alert is `CLEARED`.
- Text comes from `contracts/i18n/en.json[message_key]`, with `slots` filled in. Voice comes from `contracts/audio_clips.yaml[audio_clip]`. The tone comes from `tone_pattern`.
- Show stale data as stale (I6). Use `state.data_stale` and every `ts` / `as_of`.

TypeScript types: `contracts/ts/*.d.ts` (generated, do not edit).
