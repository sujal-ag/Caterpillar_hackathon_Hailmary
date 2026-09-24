"""SQLModel tables for every entity in HLD §6, plus the plan.md §3 additions.

Cross-DB (SQLite + Postgres): JSON columns via sa.JSON; no dialect-specific types.
No migrations framework (plan.md §0 rule 7) — `SQLModel.metadata.create_all()` +
`schema_version` is enough.

Timestamps are stored as ISO-8601 strings with offset (`common.timeutil.to_site_iso`),
not native DateTime columns. SQLite silently drops tzinfo on a DateTime column and
Postgres needs `timezone=True` to keep it — two behaviours to keep in sync for zero
benefit, since every timestamp on the wire is already this exact string (HLD §6, D22).
String columns sort correctly since the format is fixed-width and zero-padded.

Enum-shaped fields (e.g. `class`, `level`, `status`) are plain `str` here, not a DB
enum type — the allowed values are already the single source of truth in
contracts/schemas/*.json. A DB-level CHECK per dialect would just be a second copy
of the same list to keep in sync.

`assumption: true` (plan.md §0 rule 5) is a JSON list of field names on the row it
applies to (`MachineModel.assumptions`), e.g. the D9 950 GC proximity distances.
"""

from sqlalchemy import JSON, Column, ForeignKey, Index, String
from sqlmodel import Field, SQLModel


def _json_col():
    return Field(default=None, sa_column=Column(JSON))


# ---------------------------------------------------------------------------
# Config / master data (CFG) — written by tools/seed.py, read everywhere.
# ---------------------------------------------------------------------------


class Site(SQLModel, table=True):
    __tablename__ = "site"
    site_id: str = Field(primary_key=True)
    name: str
    timezone: str
    origin_lat: float | None = None
    origin_lon: float | None = None
    site_plan_image: str | None = None
    plan_scale: float | None = None
    boundary: dict | None = _json_col()
    lan_available: bool = True


class MachineModel(SQLModel, table=True):
    """HLD §6.0 spec sheet + §6.13 calibration, per model_id. Thresholds rules.yaml refers
    to as `thresholds: machine_model` live here."""

    __tablename__ = "machine_model"
    model_id: str = Field(primary_key=True)
    family: str  # EXCAVATOR | WHEEL_LOADER
    engine_model: str
    net_power_kw: float
    operating_weight_kg: int
    bucket_capacity_m3: float
    fuel_tank_l: int
    def_tank_l: int
    max_implement_press_kpa: int
    low_idle_rpm: int
    rated_rpm: int
    high_idle_rpm: int
    idle_fuel_lph: float
    fuel_lph_light: float
    fuel_lph_med: float
    fuel_lph_heavy: float
    nominal_cycle_s: float
    tail_swing_radius_m: float | None = None  # excavator only
    pitch_caution_deg: float
    pitch_critical_deg: float
    roll_caution_deg: float
    roll_critical_deg: float
    roll_caution_lift_deg: float | None = None  # excavator lift_mode only (HLD §4.4 R10)
    coolant_caution_c: float
    coolant_critical_c: float
    hyd_oil_caution_c: float
    hyd_oil_critical_c: float
    proximity_critical_m: float  # D9
    proximity_warning_m: float  # D9
    assumptions: list | None = _json_col()  # field names flagged `assumption: true`


class Machine(SQLModel, table=True):
    __tablename__ = "machine"
    machine_id: str = Field(primary_key=True)
    model_id: str = Field(foreign_key="machine_model.model_id")
    oem_name: str = "CAT"
    serial_number: str | None = None
    year: int | None = None
    site_id: str = Field(foreign_key="site.site_id")
    current_attachment_id: str | None = Field(default=None, foreign_key="attachment.attachment_id")
    telematics_device_id: str | None = None
    has_rear_camera: bool = False
    has_seat_switch: bool = False
    has_coupler_sensor: bool = False
    status: str = "ACTIVE"  # ACTIVE | DOWN | SERVICE


class Attachment(SQLModel, table=True):
    __tablename__ = "attachment"
    attachment_id: str = Field(primary_key=True)
    type: str  # GP_BUCKET | HD_BUCKET | DITCH_BUCKET | HAMMER | GRAPPLE | COMPACTOR | FORKS
    capacity_m3: float | None = None
    weight_kg: int | None = None
    coupler_type: str | None = None  # PIN_ON | PIN_GRABBER | CENTER_LOCK | FUSION
    compatible_models: list | None = _json_col()


class Operator(SQLModel, table=True):
    __tablename__ = "operator"
    operator_id: str = Field(primary_key=True)
    name: str
    employee_code: str = Field(index=True, unique=True)  # badge QR payload (D6)
    experience_years: float | None = None
    experience_level: str | None = None  # NOVICE | INTERMEDIATE | VETERAN, derived at write time
    certified_families: list | None = _json_col()
    cert_expiry: str | None = None  # YYYY-MM-DD
    languages: list | None = _json_col()
    pin_hash: str  # bcrypt
    role: str = "operator"  # operator | supervisor | admin (HLD §4.14; admin ⊇ supervisor)
    camera_consent: bool = False
    baseline_rt_ms: float | None = None
    baseline_blink_rate_pm: float | None = None
    baseline_rate_by_task: dict | None = _json_col()
    # Eval-only, same rule as sim_label (I9): rules/engine code must never read this.
    persona: str | None = None  # VETERAN | AVERAGE | NOVICE | CARELESS


class Shift(SQLModel, table=True):
    __tablename__ = "shift"
    shift_id: str = Field(primary_key=True)  # "SH-YYYYMMDD-{machine}-D"
    operator_id: str = Field(foreign_key="operator.operator_id")
    machine_id: str = Field(foreign_key="machine.machine_id")
    site_id: str = Field(foreign_key="site.site_id")
    planned_start: str
    planned_end: str
    actual_start: str | None = None
    actual_end: str | None = None
    readiness_check_id: str | None = Field(default=None, foreign_key="readiness_check.check_id")
    # walkaround.shift_id points back here, making shift<->walkaround a real cycle (a shift
    # gets a walkaround, a walkaround belongs to a shift). use_alter defers this FK to an
    # ALTER TABLE after both tables exist, so CREATE/DROP can still be ordered on Postgres
    # (SQLite doesn't enforce FK-based DROP ordering, so this only bites there).
    walkaround_id: str | None = Field(
        default=None,
        sa_column=Column(
            String,
            ForeignKey("walkaround.walkaround_id", use_alter=True, name="fk_shift_walkaround_id"),
        ),
    )
    break_minutes: int | None = None
    engine_hours_start: float | None = None
    engine_hours_end: float | None = None
    status: str = "PLANNED"  # PLANNED | ACTIVE | CLOSED
    handover_note: str | None = None  # HLD §10 shift handover (plan.md §5.7 /shift/end)


class TaskType(SQLModel, table=True):
    """HLD §6.7 task category table (base rates are simulator assumptions)."""

    __tablename__ = "task_type"
    task_type_id: str = Field(primary_key=True)
    name: str
    unit: str  # M3 | M | M2 | LIFTS | HOURS
    machines: list | None = _json_col()  # model_ids this task type applies to
    base_rate_low: float | None = None
    base_rate_high: float | None = None
    assumptions: list | None = _json_col()


# ---------------------------------------------------------------------------
# Tasks and predictions
# ---------------------------------------------------------------------------


class Task(SQLModel, table=True):
    __tablename__ = "task"
    task_id: str = Field(primary_key=True)  # UUIDv7
    shift_id: str = Field(foreign_key="shift.shift_id", index=True)
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str = Field(foreign_key="operator.operator_id")
    task_type_id: str = Field(foreign_key="task_type.task_type_id")
    zone_id: str | None = None
    planned_quantity: float
    unit: str
    soil_type: str | None = None
    material_density_t_m3: float | None = None
    haul_distance_m: float | None = None
    trucks_assigned: int | None = None
    truck_capacity_t: float | None = None
    priority: int = 3
    depends_on: str | None = Field(default=None, foreign_key="task.task_id")
    scheduled_start: str
    scheduled_end: str
    status: str = "SCHEDULED"
    actual_start: str | None = None
    actual_end: str | None = None
    actual_quantity: float | None = None
    delay_minutes: int | None = None
    delay_reason: str | None = None
    pred_duration_p50_min: float | None = None
    pred_duration_p90_min: float | None = None
    pred_eta: str | None = None
    pred_drivers: list | None = _json_col()
    model_version: str | None = None


# ---------------------------------------------------------------------------
# Time series (HLD §6.8, §6.10) — telemetry_sample is never synced (I8).
# ---------------------------------------------------------------------------


class TelemetrySample(SQLModel, table=True):
    """1 Hz normalised telemetry (HLD §6.8). 7-day retention (Phase 2 `ingest/retention.py`).
    Not syncable: raw 1 Hz never leaves the edge (I8)."""

    __tablename__ = "telemetry_sample"
    __table_args__ = (Index("ix_telemetry_sample_machine_ts", "machine_id", "ts"),)

    id: int | None = Field(default=None, primary_key=True)
    ts: str
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str | None = None
    shift_id: str | None = None
    engine_state: str | None = None
    engine_rpm: float | None = None
    engine_load_pct: float | None = None
    fuel_rate_lph: float | None = None
    fuel_used_total_l: float | None = None
    fuel_level_pct: float | None = None
    def_level_pct: float | None = None
    engine_hours: float | None = None
    idle_hours_total: float | None = None
    coolant_temp_c: float | None = None
    engine_oil_press_kpa: float | None = None
    hyd_oil_temp_c: float | None = None
    hyd_pump_press_kpa: float | None = None
    battery_v: float | None = None
    travel_speed_kmh: float | None = None
    swing_rate_deg_s: float | None = None
    boom_angle_deg: float | None = None
    stick_angle_deg: float | None = None
    bucket_angle_deg: float | None = None
    bucket_height_m: float | None = None
    boom_tip_height_m: float | None = None
    implement_grounded: bool | None = None
    payload_kg: float | None = None
    pass_count: int | None = None
    load_count: int | None = None
    machine_activity: str | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    x_m: float | None = None
    y_m: float | None = None
    heading_deg: float | None = None
    seatbelt: str | None = None
    seat_occupied: bool | None = None
    door_open: bool | None = None
    hyd_lockout: str | None = None
    parking_brake: str | None = None
    transmission_gear: int | None = None
    articulation_deg: float | None = None
    joystick_active: bool | None = None
    lift_mode: bool | None = None
    power_mode: str | None = None
    attachment_id: str | None = None
    coupler_lock: str | None = None
    cab_temp_c: float | None = None
    cab_ac_on: bool | None = None
    proximity_min_dist_m: float | None = None
    proximity_sector: str | None = None
    data_quality: int = 0


class TelemetryMinute(SQLModel, table=True):
    """1-min rollup. Exact field set is Phase 2's (D14, `edge/ingest/rollup.py` is the single
    definition) — this is the placeholder shape needed for create_all in Phase 1."""

    __tablename__ = "telemetry_minute"
    __table_args__ = (Index("ix_telemetry_minute_machine_start", "machine_id", "window_start"),)

    id: int | None = Field(default=None, primary_key=True)
    window_start: str
    window_end: str
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str | None = None
    fuel_used_l: float | None = None
    load_cycles: int | None = None
    productive_min: float | None = None
    idle_min: float | None = None
    mean_engine_load_pct: float | None = None
    state_class_mode: str | None = None


class TelemetryWindow(SQLModel, table=True):
    """Hourly rollup, organizer schema + extensions (HLD §6.10, §6.13). window_id and
    review_label added by D11 for the anomaly review queue."""

    __tablename__ = "telemetry_window"
    __table_args__ = (Index("ix_telemetry_window_machine_start", "machine_id", "window_start"),)

    window_id: str = Field(primary_key=True)  # UUIDv7 (D11)
    window_start: str
    window_end: str
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str | None = None
    engine_hours: float | None = None
    fuel_used_l: float | None = None
    load_cycles: int | None = None
    pass_count: int | None = None
    idling_time_min: float | None = None
    idle_short_count: int | None = None
    idle_medium_count: int | None = None
    idle_long_count: int | None = None
    seatbelt_status: str | None = None
    seatbelt_unfastened_running_min: float | None = None
    safety_alert_triggered: bool | None = None
    productive_min: float | None = None
    idle_ratio: float | None = None
    fuel_idle_l: float | None = None
    fuel_work_l: float | None = None
    fuel_per_productive_h: float | None = None
    fuel_per_load: float | None = None  # null if loads < 3 (§6.13 guard)
    loads_per_productive_h: float | None = None
    payload_t: float | None = None
    mean_engine_load_pct: float | None = None
    max_hyd_oil_temp_c: float | None = None
    alert_count: int | None = None
    critical_count: int | None = None
    anomaly_score: float | None = None
    state_class_mode: str | None = None
    review_label: str | None = None  # TRUE_POSITIVE | FALSE_POSITIVE | null (D11)


# ---------------------------------------------------------------------------
# Discrete events and derived episodes (HLD §6.9, §6.10)
# ---------------------------------------------------------------------------


class MachineEvent(SQLModel, table=True):
    __tablename__ = "machine_event"
    __table_args__ = (Index("ix_machine_event_machine_ts", "machine_id", "ts"),)

    event_id: str = Field(primary_key=True)  # UUIDv7
    ts: str
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str | None = None
    shift_id: str | None = None
    type: str
    severity: str
    rule_id: str | None = None
    payload: dict | None = _json_col()
    seq: int  # monotonic per machine
    source: str  # ingest | risk-engine | cv | operator


class IdleEpisode(SQLModel, table=True):
    __tablename__ = "idle_episode"
    __table_args__ = (Index("ix_idle_episode_machine_start", "machine_id", "start_ts"),)

    episode_id: str = Field(primary_key=True)  # UUIDv7
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str | None = None
    start_ts: str
    end_ts: str | None = None
    duration_min: float | None = None
    bucket: str | None = None  # PAUSE | SHORT | MEDIUM | LONG
    fuel_l: float | None = None
    mean_rpm: float | None = None
    elevated_rpm: bool | None = None
    seatbelt_off_min: float | None = None
    reason_tag: str | None = None


class ExitEvent(SQLModel, table=True):
    __tablename__ = "exit_event"
    __table_args__ = (Index("ix_exit_event_machine_ts", "machine_id", "ts"),)

    exit_id: str = Field(primary_key=True)  # UUIDv7
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str | None = None
    ts: str
    trigger: str  # STRONG | WEAK (D19: v1 always STRONG)
    state_at_intent: str  # SAFE | SAFE_ENGINE_ON | UNSAFE
    failed_checks: list | None = _json_col()
    corrected: bool = False
    time_to_correct_s: float | None = None
    time_outside_s: float | None = None
    wet_conditions: bool = False
    three_point_prompted: bool = False


# ---------------------------------------------------------------------------
# Diagnostics (HLD §6.11) — catalogue is CFG, occurrence is derived from CAN DM1.
# ---------------------------------------------------------------------------


class DiagnosticCode(SQLModel, table=True):
    __tablename__ = "diagnostic_code"
    code_id: str = Field(primary_key=True)  # "J1939-110-0", "CAT-E361"
    spn: int | None = None
    fmi: int | None = None
    cat_code: str | None = None
    component: str | None = None
    description: str | None = None
    severity: int | None = None
    action_class: str = "UNDOCUMENTED"  # CONTINUE | MONITOR | STOP | UNDOCUMENTED
    what_happened: str | None = None
    why_it_matters: str | None = None
    what_to_do: str | None = None
    source_doc: str = "DEMO corpus — verify against OMM"
    source_section: str | None = None
    doc_version: str | None = None


class DtcOccurrence(SQLModel, table=True):
    __tablename__ = "dtc_occurrence"
    __table_args__ = (Index("ix_dtc_occurrence_machine_last_seen", "machine_id", "last_seen"),)

    occurrence_id: str = Field(primary_key=True)  # UUIDv7
    machine_id: str = Field(foreign_key="machine.machine_id")
    code_id: str | None = Field(default=None, foreign_key="diagnostic_code.code_id")
    spn: int
    fmi: int
    occurrence_count: int | None = None
    first_seen: str
    last_seen: str
    active: bool = True
    freeze_frame: dict | None = _json_col()


# ---------------------------------------------------------------------------
# Alerts, incidents, hazards (HLD §6.11) — the safety record.
# ---------------------------------------------------------------------------


class SafetyAlert(SQLModel, table=True):
    __tablename__ = "safety_alert"
    __table_args__ = (Index("ix_safety_alert_machine_ts", "machine_id", "ts"),)

    alert_id: str = Field(primary_key=True)  # UUIDv7
    ts: str
    machine_id: str = Field(foreign_key="machine.machine_id")
    operator_id: str | None = None
    shift_id: str | None = None
    rule_id: str
    level: str
    subject: str
    state_snapshot: dict | None = _json_col()
    message_key: str
    slots: dict | None = _json_col()
    channels: list | None = _json_col()
    acknowledged_at: str | None = None
    ack_by: str | None = None
    suppressed: bool = False
    suppress_reason: str | None = None
    escalated: bool = False
    active: bool = True  # D10
    cleared_at: str | None = None  # D10


class Incident(SQLModel, table=True):
    __tablename__ = "incident"
    incident_id: str = Field(primary_key=True)  # UUIDv7
    type: str  # NEAR_MISS | INCIDENT | INJURY | EQUIPMENT_DAMAGE | HAZARD_OBSERVATION
    category: str
    ts: str
    x_m: float | None = None
    y_m: float | None = None
    machine_id: str | None = Field(default=None, foreign_key="machine.machine_id")
    operator_id: str | None = Field(default=None, foreign_key="operator.operator_id")
    reporter_id: str
    voice_note_path: str | None = None
    transcript: str | None = None
    photo_paths: list | None = _json_col()
    severity_self: int | None = None
    state_snapshot: dict | None = _json_col()
    linked_alert_ids: list | None = _json_col()
    hazard_pin_id: str | None = Field(default=None, foreign_key="hazard_pin.pin_id")
    status: str = "OPEN"
    supervisor_notes: str | None = None


class HazardPin(SQLModel, table=True):
    __tablename__ = "hazard_pin"
    pin_id: str = Field(primary_key=True)  # UUIDv7
    site_id: str = Field(foreign_key="site.site_id")
    type: str
    geometry: dict | None = _json_col()
    radius_m: float | None = None
    line_clearance_m: float | None = None
    created_by: str
    created_at: str
    confirmations: int = 0
    expires_at: str | None = None
    status: str = "ACTIVE"
    version: int = 1
    updated_at: str
    deleted: bool = False


# ---------------------------------------------------------------------------
# Environment, readiness, training (HLD §6.12)
# ---------------------------------------------------------------------------


class EnvironmentObs(SQLModel, table=True):
    __tablename__ = "environment_obs"
    __table_args__ = (Index("ix_environment_obs_site_ts", "site_id", "ts"),)

    id: int | None = Field(default=None, primary_key=True)
    ts: str
    site_id: str = Field(foreign_key="site.site_id")
    source: str  # API | FORECAST_CACHE | MACHINE_SENSOR | MANUAL | SIM
    temp_c: float | None = None
    rh_pct: float | None = None
    dew_point_c: float | None = None
    heat_index_c: float | None = None
    wbgt_est_c: float | None = None
    precip_mm_h: float | None = None
    rain_flag: bool | None = None
    wind_kmh: float | None = None
    gust_kmh: float | None = None
    visibility_m: float | None = None
    daylight: bool | None = None
    ground_condition: str | None = None


class ReadinessCheck(SQLModel, table=True):
    __tablename__ = "readiness_check"
    check_id: str = Field(primary_key=True)  # UUIDv7
    operator_id: str = Field(foreign_key="operator.operator_id")
    shift_id: str | None = None
    ts: str
    sleep_last_24h_h: float
    sleep_last_48h_h: float
    feel_score: int
    rt_mean_ms: float
    rt_sd_ms: float | None = None
    rt_lapses: int = 0
    rt_delta_vs_baseline_pct: float | None = None
    baseline_source: str | None = None  # PERSONAL | POPULATION_DEFAULT
    camera_used: bool = False
    long_blinks_20s: int | None = None
    heat_level: str = "NONE"
    score: int
    rating: str  # GREEN | YELLOW | RED
    reasons: list | None = _json_col()  # i18n keys (readiness.v1 `reasons`)
    supervisor_override: str | None = None


class FatigueSample(SQLModel, table=True):
    """In-shift, stretch (R19, D21)."""

    __tablename__ = "fatigue_sample"
    __table_args__ = (Index("ix_fatigue_sample_operator_ts", "operator_id", "ts"),)

    id: int | None = Field(default=None, primary_key=True)
    ts: str
    operator_id: str = Field(foreign_key="operator.operator_id")
    perclos_60s: float | None = None
    blink_rate_pm: float | None = None
    long_blinks_pm: float | None = None
    yawns_5min: int | None = None
    head_nod_count: int | None = None
    level: str | None = None  # NONE | MILD | HIGH


class Walkaround(SQLModel, table=True):
    __tablename__ = "walkaround"
    walkaround_id: str = Field(primary_key=True)  # UUIDv7
    shift_id: str = Field(foreign_key="shift.shift_id")
    items: list | None = _json_col()
    completed_at: str | None = None
    issues_count: int = 0


class Lesson(SQLModel, table=True):
    __tablename__ = "lesson"
    lesson_id: str = Field(primary_key=True)
    title_key: str
    format: str  # CARD | VIDEO | MCQ | REPLAY
    duration_s: int
    machine_family: str  # EXCAVATOR | WHEEL_LOADER | ANY
    trigger_rule_ids: list | None = _json_col()
    tags: list | None = _json_col()
    content_json: dict | None = _json_col()
    media_path: str | None = None
    language: str = "en"
    version: int = 1


class Scenario(SQLModel, table=True):
    """Replay MCQ (HLD §6.12)."""

    __tablename__ = "scenario"
    scenario_id: str = Field(primary_key=True)  # UUIDv7
    source_incident_id: str | None = None  # anonymised, nullable
    state_vector: dict | None = _json_col()
    prompt: str | None = None
    options: list | None = _json_col()
    correct_option: int | None = None
    explanation: str | None = None
    doc_reference: str | None = None


class LessonAssignment(SQLModel, table=True):
    __tablename__ = "lesson_assignment"
    assignment_id: str = Field(primary_key=True)  # UUIDv7
    operator_id: str = Field(foreign_key="operator.operator_id")
    lesson_id: str = Field(foreign_key="lesson.lesson_id")
    trigger_event_id: str | None = None
    reason: str | None = None  # e.g. "R03 x2 in 7 days"
    assigned_at: str
    deliver_after: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    score: int | None = None
    attempts: int = 0


class OperatorScorecard(SQLModel, table=True):
    __tablename__ = "operator_scorecard"
    __table_args__ = (Index("ix_scorecard_operator_period", "operator_id", "period_start"),)

    scorecard_id: str = Field(primary_key=True)  # UUIDv7
    operator_id: str = Field(foreign_key="operator.operator_id")
    period_start: str
    period_end: str
    loads_per_productive_h: float | None = None
    passes_per_h: float | None = None
    fuel_per_m3: float | None = None
    idle_ratio: float | None = None
    long_idle_count: int | None = None
    seatbelt_compliance_pct: float | None = None
    unsafe_exit_rate: float | None = None
    exits_count: int | None = None
    alerts_per_h: float | None = None
    critical_count: int | None = None
    near_misses_reported: int | None = None
    lessons_completed: int | None = None
    mean_quiz_score: float | None = None
    eta_accuracy_mape: float | None = None
    percentile_site: float | None = None
    gap_to_veteran_pct: float | None = None


# ---------------------------------------------------------------------------
# Sync (HLD §6.13, plan.md §4.13) and plan.md additions (D8, D18)
# ---------------------------------------------------------------------------


class SyncQueue(SQLModel, table=True):
    """Outbox. Every syncable write inserts its row here in the same transaction as the
    entity write (I7, plan.md Phase 1 item 2). See common/db/repo.py for the priority table."""

    __tablename__ = "sync_queue"
    __table_args__ = (Index("ix_sync_queue_dispatch", "status", "priority", "seq"),)

    seq: int | None = Field(default=None, primary_key=True)
    entity: str
    entity_id: str
    op: str  # UPSERT | DELETE
    payload: dict | None = _json_col()
    priority: int  # 0 (incidents, critical) .. 3 (telemetry)
    created_at: str
    attempts: int = 0
    last_error: str | None = None
    status: str = "PENDING"  # PENDING | SENT | FAILED


class SyncState(SQLModel, table=True):
    __tablename__ = "sync_state"
    stream: str = Field(primary_key=True)
    last_pushed_seq: int | None = None
    last_pull_cursor: int | None = None
    last_success_at: str | None = None


class ModelRegistry(SQLModel, table=True):
    __tablename__ = "model_registry"
    model_name: str = Field(primary_key=True)
    version: str = Field(primary_key=True)
    sha256: str
    trained_at: str | None = None
    metrics: dict | None = _json_col()
    active: bool = False


class EngineSnapshot(SQLModel, table=True):
    """Crash-safe rehydration of MachineContext (Phase 3, D18). Not syncable."""

    __tablename__ = "engine_snapshot"
    machine_id: str = Field(primary_key=True, foreign_key="machine.machine_id")
    payload: dict | None = _json_col()
    updated_at: str


class MachineStateSnapshot(SQLModel, table=True):
    """D8: one current-state row per machine, so the cloud fleet overview has something
    live-ish to show. Coalesced in the outbox (see repo.py) — a new write replaces the
    existing PENDING row's payload instead of queuing another one."""

    __tablename__ = "machine_state_snapshot"
    machine_id: str = Field(primary_key=True, foreign_key="machine.machine_id")
    payload: dict | None = _json_col()
    updated_at: str


class Device(SQLModel, table=True):
    __tablename__ = "device"
    device_id: str = Field(primary_key=True)
    site_id: str | None = Field(default=None, foreign_key="site.site_id")
    token_hash: str
    created_at: str
    last_seen_at: str | None = None


class SimLabelLog(SQLModel, table=True):
    """I9: sim_label is stored here only, for tools/eval_*.py. Rules/engine code must never
    read this table."""

    __tablename__ = "sim_label_log"
    id: int | None = Field(default=None, primary_key=True)
    machine_id: str
    ts: str
    sim_label: str


# Bump when a table changes shape: create_all never alters an existing table, so an older
# DB must be deleted and re-seeded (session.create_all refuses to run on one).
# v2 (Phase 4): operator.role.
# v3 (Phase 5): shift.handover_note, readiness_check.reasons.
SCHEMA_VERSION = 3


class SchemaVersion(SQLModel, table=True):
    __tablename__ = "schema_version"
    version: int = Field(primary_key=True)
    applied_at: str
