"""MachineEngine: one machine's full detect pipeline, synchronous and clock-injected
(plan.md Phase 3 items 1–8).

    raw -> validate (drop+count) -> strip sim_label (I9) -> normalise -> debounce + switch
    events -> stale -> context -> idle -> evaluate
    evaluate = predicates -> exit checks -> ExitTracker -> rules -> AlertManager -> class

Evaluation runs on every raw frame, proximity message, env change, DTC change and 1 s
tick (item 8). Zone changes, hazard-list updates and shift/readiness changes (Phase 5) call
`evaluate` the same way. Every entry point
returns an `Out` with what to persist and publish; `supervisor.py` does the I/O, so this
class stays deterministic and unit-testable with a fake clock.
"""

import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from common.db.models import MachineEvent
from common.ids import uuid7
from common.levels import RANK
from common.log import jlog
from common.timeutil import to_site_iso
from edge.engine import predicates, snapshot
from edge.engine.alerts import AlertManager
from edge.engine.classify import Classifier, raw_class
from edge.engine.context import MachineContext
from edge.engine.exits import ExitTracker
from edge.engine.registry import Inputs, compute_inputs, evaluate_rule
from edge.engine.rules import RuleSet
from edge.geo import geofence
from edge.ingest.dtc import DtcTracker, lookup_action_class
from edge.ingest.env import CurrentEnv, build_environment_obs
from edge.ingest.events import Seq, event_type_for_switch, make_event
from edge.ingest.idle import IdleTracker
from edge.ingest.normalise import normalise, validate_raw
from edge.ingest.stale import StaleTracker
from edge.ingest.switches import SWITCH_NAMES, Debouncer
from edge.ingest.telemetry import to_telemetry_v1
from edge.nudge.generator import make_nudge

log = logging.getLogger("edge.engine")


@dataclass
class Out:
    telemetry: dict | None = None  # normalised row (telemetry_sample)
    telemetry_v1: dict | None = None  # wire shape for MQTT/WS
    events: list[MachineEvent] = field(default_factory=list)
    alerts: list[tuple[str, dict]] = field(default_factory=list)
    exit_rows: list[dict] = field(default_factory=list)
    idle_episodes: list[dict] = field(default_factory=list)
    dtc_occurrences: list[dict] = field(default_factory=list)
    env_obs: list[dict] = field(default_factory=list)
    sim_labels: list[dict] = field(default_factory=list)
    nudges: list[dict] = field(default_factory=list)  # nudge.v1
    state: dict | None = None  # state.v1, only when it changed

    def changed(self) -> bool:
        return bool(
            self.events
            or self.alerts
            or self.exit_rows
            or self.idle_episodes
            or self.dtc_occurrences
            or self.nudges
            or self.state
        )


class MachineEngine:
    def __init__(
        self,
        machine_id: str,
        site_id: str,
        model: dict,
        ruleset: RuleSet,
        catalogue: dict,
        *,
        has_rear_camera: bool = False,
    ):
        self.ruleset, self.catalogue = ruleset, catalogue
        self.P, self.policy = ruleset.predicates, ruleset.policy
        self.rules_by_id = {r.id: r for r in ruleset.rules}
        self.ctx = MachineContext(machine_id, site_id, model, has_rear_camera=has_rear_camera)
        self.debouncer = Debouncer(self.policy["switch_flap_suppress_ms"])
        self.seq = Seq()
        self.stale = StaleTracker(self.policy["data_stale_s"])
        self.idle = IdleTracker(model["low_idle_rpm"])
        self.dtc = DtcTracker()
        self.current_env = CurrentEnv()
        self.exits = ExitTracker()
        self.alerts = AlertManager(self.policy["audio_budget_s"])
        self.classifier = Classifier(self.policy["class_hysteresis_s"])
        self.disabled_rules: dict[str, str] = {}  # rule_id -> error (visible in /system/status)
        self.dropped_frames = 0
        self.state: dict | None = None
        self._state_key = None

    @property
    def machine_id(self) -> str:
        return self.ctx.machine_id

    # --- inputs -------------------------------------------------------------------------

    def on_raw(self, frame: dict, now: datetime) -> Out:
        out = Out()
        errors = validate_raw(frame)
        if errors:
            self.dropped_frames += 1
            jlog(log, logging.WARNING, "raw_dropped", machine_id=self.machine_id, errors=errors)
            return out
        frame = dict(frame)
        label = frame.pop("sim_label", None)  # I9: never reaches normalise/rules
        if label is not None:
            out.sim_labels.append(
                {"machine_id": self.machine_id, "ts": frame["ts"], "sim_label": label}
            )

        ctx = self.ctx
        t = normalise(frame)
        t["operator_id"], t["shift_id"] = ctx.operator_id, ctx.shift_id
        for name in SWITCH_NAMES:
            if t.get(name) is None:
                continue  # UNKNOWN is not a switch position; never debounce it into one
            t[name], changed = self.debouncer.apply(name, t[name], now)
            etype = event_type_for_switch(name, t[name]) if changed else None
            if etype:
                self._event(out, etype, "ingest", frame["ts"], {name: t[name]})

        if self.stale.mark_received(self.machine_id, now):
            self._event(out, "DATA_RESTORED", "ingest", frame["ts"], {})
        ctx.data_stale = False

        frame_active = {}

        def derive(c: MachineContext):
            act = predicates.active(c, now, self.P)
            frame_active["v"] = act
            press = predicates.k_and(
                predicates.grounded(c, self.P),
                predicates.gt(
                    c.sig("hyd_pump_press_kpa"), self.P["active"]["hyd_pump_press_kpa_gt"]
                ),
            )
            return act, press

        r22 = self.rules_by_id["R22"].params
        ctx.apply_telemetry(t, now, derive, r22["ground_press_hold_s"])
        self._geofence(now, out)

        etype, episode = self.idle.update(
            running=t["engine_state"] == "RUNNING",
            active=frame_active["v"],
            seatbelt=t["seatbelt"],
            engine_rpm=t["engine_rpm"],
            fuel_used_total_l=t["fuel_used_total_l"],
            ts=frame["ts"],
        )
        if etype:
            self._event(out, etype, "ingest", frame["ts"], {})
        if episode:
            out.idle_episodes.append(
                {**episode, "machine_id": self.machine_id, "operator_id": ctx.operator_id}
            )
        out.telemetry = t
        self.evaluate(now, out)
        fresh = self.policy["proximity_fresh_s"]
        out.telemetry_v1 = to_telemetry_v1(
            t, (ctx.proximity_now(now, fresh)[0], ctx.fresh_proximity_msg(now, fresh))
        )
        return out

    def on_dtc(self, frame: dict, now: datetime) -> Out:
        out = Out()
        occ, ev = self.dtc.process(frame, self.catalogue, dict(self.ctx.signals) or None, seq=0)
        if occ is not None:
            row = self.catalogue.get((occ["spn"], occ["fmi"])) or (
                frame.get("cat_code") and self.catalogue.get(frame["cat_code"])
            )
            occ["code_id"] = row["code_id"] if row else None
            out.dtc_occurrences.append(occ)
            key = occ["occurrence_id"]
            if occ["active"]:
                self.ctx.active_dtcs[key] = {
                    "spn": occ["spn"],
                    "fmi": occ["fmi"],
                    "code_id": occ["code_id"],
                    "action_class": lookup_action_class(
                        self.catalogue, occ["spn"], occ["fmi"], frame.get("cat_code")
                    ),
                }
            else:
                self.ctx.active_dtcs.pop(key, None)
        if ev is not None:
            self._event(out, ev["type"], "ingest", ev["ts"], ev["payload"], ev["severity"])
        self.evaluate(now, out)
        return out

    def on_env(self, frame: dict, now: datetime) -> Out:
        out = Out()
        obs = build_environment_obs(frame)
        self.current_env.update(frame["site_id"], obs)
        self.ctx.env = self.current_env.get(frame["site_id"])
        out.env_obs.append(obs)
        self.evaluate(now, out)
        return out

    def on_proximity(self, msg: dict, now: datetime) -> Out:
        out = Out()
        self.ctx.proximity[msg["source"]] = {"msg": msg, "at": now}
        self.evaluate(now, out)
        return out

    def tick(self, now: datetime) -> Out:
        out = Out()
        if self.stale.check(self.machine_id, now):
            self.ctx.data_stale = True
            self._event(out, "DATA_STALE", "ingest", to_site_iso(now), {})
        self.evaluate(now, out)
        return out

    # --- operator services (Phase 5; REST calls these through EngineRunner.call) -----------

    def set_shift(
        self, operator_id: str | None, shift_id: str | None, readiness: str | None, now: datetime
    ) -> Out:
        """Shift start/end: tags every later telemetry row/event/alert; R20 sees the rating."""
        ctx = self.ctx
        ctx.operator_id, ctx.shift_id, ctx.readiness = operator_id, shift_id, readiness
        out = Out()
        self.evaluate(now, out)
        return out

    def set_readiness(self, readiness: str | None, now: datetime) -> Out:
        self.ctx.readiness = readiness
        out = Out()
        self.evaluate(now, out)
        return out

    def set_hazards(self, pins: list[dict], now: datetime) -> Out:
        """New retained hazard list. A zone whose pin is gone (resolved/expired/deleted) is
        left at once; a pin dropped on top of the machine is entered at once."""
        ctx, out = self.ctx, Out()
        ctx.hazard_pins = {
            p["pin_id"]: p for p in pins if p["status"] == "ACTIVE" and not p["deleted"]
        }
        for pin_id in [z for z in ctx.zones_inside if z not in ctx.hazard_pins]:
            zone = ctx.zones_inside.pop(pin_id)
            self._event(
                out,
                "GEOFENCE_EXIT",
                "risk-engine",
                to_site_iso(now),
                {"pin_id": pin_id, "type": zone["type"], "reason": "PIN_REMOVED"},
            )
        self._geofence(now, out)
        self.evaluate(now, out)
        return out

    def _geofence(self, now: datetime, out: Out) -> None:
        """Zone entry/exit (HLD §4.12). Paused while position is not OK: zones hold, R14
        evaluates UNKNOWN and `sensor_health.position` shows it (never silent)."""
        ctx = self.ctx
        if not ctx.hazard_pins and not ctx.zones_inside:
            return
        if ctx.sensor_health(now, self.policy)["position"] != "OK":
            return
        x, y = ctx.sig("x_m"), ctx.sig("y_m")
        hyst = self.rules_by_id["R14"].params["exit_hysteresis_m"]
        entered, exited = geofence.update(ctx.zones_inside, ctx.hazard_pins, x, y, hyst)
        ts = to_site_iso(now)
        for pin_id in exited:
            zone = ctx.zones_inside.pop(pin_id)
            payload = {"pin_id": pin_id, "type": zone["type"], "x_m": x, "y_m": y}
            self._event(out, "GEOFENCE_EXIT", "risk-engine", ts, payload)
        for pin_id in entered:
            pin = ctx.hazard_pins[pin_id]
            ctx.zones_inside[pin_id] = {
                "type": pin["type"],
                "line_clearance_m": pin.get("line_clearance_m"),
                "entry_id": uuid7(),
            }
            payload = {"pin_id": pin_id, "type": pin["type"], "x_m": x, "y_m": y}
            self._event(out, "GEOFENCE_ENTER", "risk-engine", ts, payload)

    # --- evaluation -----------------------------------------------------------------------

    def evaluate(self, now: datetime, out: Out) -> None:
        ctx = self.ctx
        inp = compute_inputs(ctx, now, self.ruleset, self.exits.open)
        events, row = self.exits.update(ctx, now, inp.p, inp.checks, inp.exit_state)
        inp.exit_open = self.exits.open
        for type_, payload in events:
            self._event(out, type_, "risk-engine", to_site_iso(now), payload)
        if row:
            out.exit_rows.append(row)

        results = []
        for rule in self.ruleset.rules:
            if not rule.enabled or rule.id in self.disabled_rules:
                continue
            try:
                results.append((rule, evaluate_rule(rule, inp)))
            except Exception as exc:  # noqa: BLE001 - HLD §4.4: a failing rule is disabled, others run
                self.disabled_rules[rule.id] = repr(exc)
                jlog(
                    log,
                    logging.ERROR,
                    "rule_disabled",
                    rule_id=rule.id,
                    machine_id=self.machine_id,
                    error=repr(exc),
                    traceback=traceback.format_exc(),
                )
        out.alerts.extend(self.alerts.process(results, ctx, now))
        for id_ in self.alerts.nudge_due:
            entry = self.alerts.active[id_]
            out.nudges.append(
                make_nudge(
                    entry["alert"],
                    entry,
                    self.rules_by_id[entry["rule_id"]],
                    self.ruleset,
                    inp.checks,
                    now,
                )
            )
        self._update_state(inp, now, out)

    def ack(self, alert_id: str, by: str | None, now: datetime) -> Out | None:
        """Operator ack (REST). None if the alert isn't active on this machine."""
        action = self.alerts.ack(alert_id, by, now)
        return None if action is None else Out(alerts=[action])

    def _update_state(self, inp: Inputs, now: datetime, out: Out) -> None:
        ctx = self.ctx
        active = self.alerts.active_records()
        effects = {self.rules_by_id[a["rule_id"]].state_effect for a in active}
        health = ctx.sensor_health(now, self.policy)
        cls = self.classifier.update(raw_class(effects, health, inp.p, ctx.sig("hyd_lockout")), now)
        levels: dict[str, str] = {}
        for a in active:
            prev = levels.get(a["rule_id"])
            if prev is None or RANK[a["level"]] > RANK[prev]:
                levels[a["rule_id"]] = a["level"]
        exit_open = self.exits.open is not None
        state = {
            "schema": "state.v1",
            "ts": to_site_iso(now),
            "machine_id": ctx.machine_id,
            "operator_id": ctx.operator_id,
            "shift_id": ctx.shift_id,
            "class": cls,
            "active_rules": [{"rule_id": r, "level": lv} for r, lv in sorted(levels.items())],
            "exit_state": inp.exit_state if exit_open else "NONE",
            "exit_checks": dict(inp.checks) if exit_open else {},
            "readiness": ctx.readiness,
            "sensor_health": health,
            "data_stale": ctx.data_stale,
        }
        key = {k: v for k, v in state.items() if k != "ts"}
        self.state = state
        if key != self._state_key:
            self._state_key = key
            out.state = state

    # --- helpers --------------------------------------------------------------------------

    def _event(self, out, type_, source, ts, payload, severity=None) -> None:
        out.events.append(
            make_event(
                machine_id=self.machine_id,
                ts=ts,
                type_=type_,
                seq=self.seq.next(self.machine_id),
                source=source,
                severity=severity,
                operator_id=self.ctx.operator_id,
                shift_id=self.ctx.shift_id,
                payload=payload,
            )
        )

    def snapshot(self) -> dict:
        return snapshot.dump(self)

    def restore(self, payload: dict) -> None:
        snapshot.load(self, payload)

    def metrics(self) -> dict:
        return {
            "alerts_per_operating_hour": self.alerts.alerts_per_operating_hour(self.ctx.running_s),
            "disabled_rules": dict(self.disabled_rules),
            "dropped_frames": self.dropped_frames,
        }
