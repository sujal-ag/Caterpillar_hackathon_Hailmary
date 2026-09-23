"""Alert -> `nudge.v1` (plan.md Phase 4 item 1; HLD §4.10).

Everything comes from config: display and voice clip from the rule (rules.yaml, or the
alert's per-raise override such as R05's degraded-seat BANNER), tone from
`policy.tone_patterns[level]`, checklist order from `exit_checks.checklist_order`. The
channels are the alert's channels *after* the audio budget: when AUDIO was dropped, there
is no voice clip and no tone either (visual/vibration only). The LLM is never involved (I3).
"""

from datetime import datetime

from common.ids import uuid7
from common.timeutil import to_site_iso
from edge.engine.rules import Rule, RuleSet


def make_nudge(
    alert: dict, entry: dict, rule: Rule, ruleset: RuleSet, checks: dict, now: datetime
) -> dict:
    audible = "AUDIO" in alert["channels"]
    nudge = {
        "schema": "nudge.v1",
        "ts": to_site_iso(now),
        "nudge_id": uuid7(),
        "alert_id": alert["alert_id"],
        "machine_id": alert["machine_id"],
        "rule_id": alert["rule_id"],
        "level": alert["level"],
        "display": entry["display"],
        "message_key": alert["message_key"],
        "slots": alert["slots"],
        "audio_clip": entry["audio_clip"] if audible else None,
        "tone_pattern": ruleset.policy["tone_patterns"][alert["level"]] if audible else None,
        "channels": list(alert["channels"]),
    }
    if rule.params.get("checklist"):
        nudge["checklist"] = [c for c in ruleset.exit_checks["checklist_order"] if c in checks]
    return nudge
