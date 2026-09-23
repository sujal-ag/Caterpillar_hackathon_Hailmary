"""Alert manager: lifecycle + alert-fatigue controls (plan.md Phase 3 item 5; HLD §4.4).

Per (rule_id, key):
- RAISED when the rule turns True, its cooldown has elapsed and its once-per key is unused.
- UPDATED on `repeat_while_true_s` (R01 every 20 s), on escalation (R02 INFO -> CAUTION
  after 300 s) and when suppression changes.
- CLEARED only when the rule is definitively False (or its key is gone). UNKNOWN (None)
  holds the current state: an active alert never clears because a sensor dropped out (I1).

Fatigue controls: a higher-level active alert on the same subject marks lower ones
`suppressed=true, HIGHER_ACTIVE` (stored, not nudged); at most one non-CRITICAL audio per
`audio_budget_s` per machine, over budget -> AUDIO dropped, VISUAL/VIBRATION kept.
CRITICAL is exempt from cooldown, suppression and the audio budget (I4) — a CRITICAL rule
re-raises every time its condition becomes true again. Raises on the same tick are budgeted
in `audio_priority` order (rules.yaml; ties keep rule order).

`nudge_due` (reset every `process`) lists the alerts whose change the operator must be told
about: a raise, a repeat, an escalation or an un-suppression — never a clear, an ack or a
suppressed alert (Phase 4 nudge generator reads it).
"""

import logging
from datetime import datetime

from common.ids import uuid7
from common.levels import RANK
from common.log import jlog
from common.timeutil import to_site_iso
from edge.engine.context import MachineContext
from edge.engine.registry import Eval
from edge.engine.rules import Rule

log = logging.getLogger("edge.engine.alerts")

_FIRED_KEYS_MAX = 64  # per rule; once-per keys only need to outlive their exit/episode/...


def _slot_format(template: str | None, slots: dict) -> str | None:
    if template is None or "{" not in template:
        return template
    return template.format(**slots)


class AlertManager:
    PERSIST = ("active", "last_raised", "fired_keys", "last_audio_at", "noncritical_raised")

    def __init__(self, audio_budget_s: float):
        self.audio_budget_s = audio_budget_s
        # "rule_id|key" -> {"alert": record, "rule_id", "key", "raised_at", "last_repeat_at",
        #                   "escalated_level": bool}
        self.active: dict[str, dict] = {}
        self.last_raised: dict[str, datetime] = {}
        self.fired_keys: dict[str, list[str]] = {}
        self.last_audio_at: datetime | None = None
        self.noncritical_raised = 0
        self.nudge_due: list[str] = []

    # --- public -----------------------------------------------------------------------

    def process(
        self, results: list[tuple[Rule, list[Eval]]], ctx: MachineContext, now: datetime
    ) -> list[tuple[str, dict]]:
        """`results` holds every enabled, non-disabled rule's evals for this tick.
        Returns [(action, safety_alert record)] in order."""
        out: list[tuple[str, dict]] = []
        due: list[str] = []
        candidates: list[tuple[Rule, Eval]] = []
        for rule, evals in results:
            seen = {e.key: e for e in evals}
            for id_, entry in list(self.active.items()):
                if entry["rule_id"] != rule.id:
                    continue
                ev = seen.get(entry["key"])
                if ev is None or ev.value is False:
                    out.append(self._clear(id_, now, ev))
                elif ev.value is True:
                    updates = self._maybe_repeat_or_escalate(rule, entry, now)
                    out.extend(updates)
                    due += [id_] * bool(updates)
            candidates += [
                (rule, ev)
                for ev in evals
                if ev.value is True and f"{rule.id}|{ev.key}" not in self.active
            ]
        # sorted() is stable: equal priorities keep rule order.
        for rule, ev in sorted(candidates, key=lambda c: -c[0].audio_priority):
            raised = self._maybe_raise(rule, ev, ctx, now)
            if raised:
                out.append(raised)
                due.append(f"{rule.id}|{ev.key}")
        suppression = self._apply_suppression(now)
        out.extend(suppression)
        due += [self._id_of(a) for _, a in suppression if not a["suppressed"]]
        self.nudge_due = [
            i
            for i in dict.fromkeys(due)
            if i in self.active and not self.active[i]["alert"]["suppressed"]
        ]
        return out

    def ack(self, alert_id: str, by: str | None, now: datetime) -> tuple[str, dict] | None:
        """Acknowledge an active alert. Ack is not clear: the alert stays active (and a
        FULLSCREEN CRITICAL stays on screen) until its condition is definitively false."""
        for entry in self.active.values():
            alert = entry["alert"]
            if alert["alert_id"] == alert_id:
                alert["acknowledged_at"], alert["ack_by"] = to_site_iso(now), by
                jlog(
                    log,
                    logging.INFO,
                    "alert_acked",
                    rule_id=alert["rule_id"],
                    alert_id=alert_id,
                    ack_by=by,
                    machine_id=alert["machine_id"],
                )
                return "ACKED", alert
        return None

    def _id_of(self, alert: dict) -> str:
        return next(i for i, e in self.active.items() if e["alert"] is alert)

    def active_records(self) -> list[dict]:
        return [e["alert"] for e in self.active.values()]

    def alerts_per_operating_hour(self, running_s: float) -> float | None:
        """Non-critical alerts raised per engine-running hour (HLD §4.4 KPI, target < 4)."""
        return None if running_s <= 0 else self.noncritical_raised / (running_s / 3600)

    # --- lifecycle --------------------------------------------------------------------

    def _maybe_raise(self, rule: Rule, ev: Eval, ctx: MachineContext, now: datetime):
        critical = rule.level == "CRITICAL"
        cooldown_id = f"{rule.id}|{ev.key}"
        last = self.last_raised.get(cooldown_id)
        if not critical and last is not None and (now - last).total_seconds() < rule.cooldown_s:
            return None
        if rule.once_per and ev.key in self.fired_keys.get(rule.id, []):
            return None

        suppressed = any(
            e["alert"]["subject"] == rule.subject and RANK[e["alert"]["level"]] > RANK[rule.level]
            for e in self.active.values()
        )
        # A suppressed alert is stored but never nudged, so it must not spend the audio budget.
        channels = (
            list(rule.channels)
            if suppressed
            else self._budget(rule.level, list(rule.channels), now)
        )
        alert = {
            "alert_id": uuid7(),
            "ts": to_site_iso(now),
            "machine_id": ctx.machine_id,
            "operator_id": ctx.operator_id,
            "shift_id": ctx.shift_id,
            "rule_id": rule.id,
            "level": rule.level,
            "subject": rule.subject,
            "state_snapshot": {"inputs": ev.inputs, "thresholds": ev.thresholds},
            "message_key": _slot_format(rule.message_key, ev.slots),
            "slots": ev.slots,
            "channels": channels,
            "acknowledged_at": None,
            "ack_by": None,
            "suppressed": suppressed,
            "suppress_reason": "HIGHER_ACTIVE" if suppressed else None,
            "escalated": bool(rule.notify_supervisor),
            "active": True,
            "cleared_at": None,
        }
        self.active[cooldown_id] = {
            "alert": alert,
            "rule_id": rule.id,
            "key": ev.key,
            "raised_at": now,
            "last_repeat_at": now,
            "escalated_level": False,
            # nudge-side presentation (Phase 4): rule display unless the eval overrides it
            "display": ev.display or rule.display,
            "audio_clip": _slot_format(rule.audio_clip, ev.slots),
        }
        self.last_raised[cooldown_id] = now
        if rule.once_per:
            keys = self.fired_keys.setdefault(rule.id, [])
            keys.append(ev.key)
            del keys[:-_FIRED_KEYS_MAX]
        if not critical:
            self.noncritical_raised += 1
        jlog(
            log,
            logging.INFO,
            "alert_raised",
            rule_id=rule.id,
            level=rule.level,
            machine_id=ctx.machine_id,
            key=ev.key,
            inputs=ev.inputs,
            thresholds=ev.thresholds,
            channels=channels,
        )
        return "RAISED", alert

    def _maybe_repeat_or_escalate(self, rule: Rule, entry: dict, now: datetime):
        alert = entry["alert"]
        out = []
        if (
            rule.escalate_after_s is not None
            and rule.escalate_to
            and not entry["escalated_level"]
            and (now - entry["raised_at"]).total_seconds() >= rule.escalate_after_s
        ):
            esc = rule.escalate_to
            entry["escalated_level"] = True
            alert["level"] = esc.get("level", alert["level"])
            if esc.get("audio_clip"):
                entry["audio_clip"] = esc["audio_clip"]
            alert["channels"] = self._budget(alert["level"], list(esc.get("channels", [])), now)
            entry["last_repeat_at"] = now
            jlog(
                log,
                logging.INFO,
                "alert_escalated",
                rule_id=rule.id,
                level=alert["level"],
                machine_id=alert["machine_id"],
            )
            out.append(("UPDATED", alert))
        elif (
            rule.repeat_while_true_s
            and (now - entry["last_repeat_at"]).total_seconds() >= rule.repeat_while_true_s
        ):
            entry["last_repeat_at"] = now
            channels = rule.escalate_to["channels"] if entry["escalated_level"] else rule.channels
            alert["channels"] = self._budget(alert["level"], list(channels), now)
            out.append(("UPDATED", alert))
        return out

    def _clear(self, id_: str, now: datetime, ev: Eval | None):
        entry = self.active.pop(id_)
        alert = entry["alert"]
        alert["active"] = False
        alert["cleared_at"] = to_site_iso(now)
        jlog(
            log,
            logging.INFO,
            "alert_cleared",
            rule_id=entry["rule_id"],
            key=entry["key"],
            machine_id=alert["machine_id"],
            inputs=ev.inputs if ev else {"key_gone": entry["key"]},
        )
        return "CLEARED", alert

    # --- fatigue controls ---------------------------------------------------------------

    def _budget(self, level: str, channels: list, now: datetime) -> list:
        if "AUDIO" not in channels or level == "CRITICAL":
            return channels  # I4: CRITICAL audio is never rate-limited
        last = self.last_audio_at
        if last is not None and (now - last).total_seconds() < self.audio_budget_s:
            return [c for c in channels if c != "AUDIO"]
        self.last_audio_at = now
        return channels

    def _apply_suppression(self, now: datetime) -> list[tuple[str, dict]]:
        top: dict[str, int] = {}
        for e in self.active.values():
            a = e["alert"]
            top[a["subject"]] = max(top.get(a["subject"], -1), RANK[a["level"]])
        out = []
        for e in self.active.values():
            a = e["alert"]
            should = RANK[a["level"]] < top[a["subject"]]
            if should != a["suppressed"]:
                a["suppressed"] = should
                a["suppress_reason"] = "HIGHER_ACTIVE" if should else None
                out.append(("UPDATED", a))
        return out
