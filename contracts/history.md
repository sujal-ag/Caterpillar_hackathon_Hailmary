# Historical data export (P1 → backend) — v1

Agreed by: P1 ☐

P1 drops these files in `data/history/`. `backend/tools/load_history.py` imports them (Phase 1 stub; the real import comes later). `backend/tools/eval_rules.py` uses the labelled traces (Phase 3).

| File | Format | Rows | Columns |
|---|---|---|---|
| `windows.parquet` | Parquet | hourly windows | Every `telemetry_window` field in HLD §6.10 (organizer schema + extensions), plus `window_id` (UUIDv7), `machine_id`, `operator_id`, `site_id`, `sim_labels` (list, eval only) |
| `tasks.parquet` | Parquet | completed tasks | `task.v1` fields + `actual_start`, `actual_end`, `actual_quantity`, `duration_min` |
| `operators.parquet` | Parquet | operators | HLD §6.5 fields incl. hidden `persona` (eval only) |
| `traces/{machine_id}_{date}.jsonl` | JSONL | 1 Hz | Lines in `replay.py` format (`raw.v1`, `dtc.v1`, `env.v1`, `proximity.v1`) with `sim_label` set |
| `anchors/EXC001_2025-05-0{1,2}.jsonl` | JSONL | 1 Hz | Traces whose hourly rollup must reproduce the four organizer rows (HLD §6.13, D14) |

Rules:
- All timestamps are ISO-8601 with offset. The backend stores UTC.
- The rollup definition is `backend/edge/ingest/rollup.py` (Phase 2, D14). P1 validates the anchors against it.
- The anomaly review labels flow back to P1. The export query is documented here in Phase 10.
