# ML feature dictionaries — v1

Agreed by: P1 ☐

The backend builds these dicts from SQLite at inference time. P1 must build **identical** dicts for training (D15). Names and units are frozen. Changing one needs a new version of this file.

## Anomaly — one hourly window (HLD §7.1)

| key | unit | notes |
|---|---|---|
| `idle_ratio` | 0–1 | idling_time_min / 60 |
| `fuel_per_productive_h` | L/h | fuel_work_l / productive hours |
| `fuel_per_load` | L/load | null when loads < 3. The model imputes its own median. |
| `loads_per_productive_h` | loads/h | |
| `passes_per_h` | passes/h | |
| `seatbelt_unfastened_running_min` | min | |
| `long_idle_count` | count | idle episodes ≥ 9 min |
| `elevated_rpm_idle_min` | min | idle minutes with mean_rpm > low_idle + 300 |
| `mean_engine_load_pct` | % | |
| `hyd_oil_temp_over_ambient_c` | °C | max_hyd_oil_temp_c − ambient temp_c |
| `alert_count` | count | |
| `task_type` | enum | one-hot inside the model; values from `task.v1` |
| `power_mode` | enum | ECO / POWER / SMART |
| `z_<metric>` | z | Robust z-score of each numeric metric above vs the operator's 14-day baseline (median / MAD) |

Excluded on purpose: raw engine hours and the binary seatbelt state.

`baseline` argument to `AnomalyModel.score`: `{metric: {"median": float, "mad": float, "n": int}}`, taken from the operator's windows in the last 14 days.

## ETA — one task (HLD §7.2)

| key | unit | notes |
|---|---|---|
| `task_type` | enum | `task.v1.task_type_id` |
| `planned_quantity` | per `unit` | |
| `unit` | enum | M3, M, M2, LIFTS, HOURS |
| `soil_type` | enum | or null |
| `fill_factor` | ratio | from soil_type (HLD §6.7 midpoint) |
| `material_density_t_m3` | t/m³ | |
| `haul_distance_m` | m | |
| `truck_capacity_total_t` | t | trucks_assigned × truck_capacity_t |
| `machine_model` | enum | CAT-320, CAT-950GC |
| `attachment_capacity_m3` | m³ | |
| `power_mode` | enum | |
| `operator_rate_median` | unit/h | Rolling median over the operator's last 10 tasks of this type. Cold start uses cohort, then task type. |
| `operator_rate_source` | enum | OPERATOR, COHORT, TASK_TYPE |
| `experience_years` | years | |
| `readiness_score` | 0–100 | null if no check |
| `rain_mm_h` | mm/h | |
| `temp_c` | °C | |
| `heat_index_c` | °C | |
| `start_hour` | 0–23 | site time |
| `hours_into_shift` | h | |
| `day_of_week` | 0–6 | Monday = 0 |

Target: `log(duration_min)`. Quantiles α = 0.5 and α = 0.9.
