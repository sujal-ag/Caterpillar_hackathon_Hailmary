"""Load `contracts/rules.yaml` and the per-model thresholds it points at (plan.md Phase 3
item 3). Thresholds are never hard-coded in the engine: they come from here."""

from dataclasses import dataclass, fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = ROOT / "contracts" / "rules.yaml"
SEED_DIR = ROOT / "data" / "seed"
CATALOGUE_PATH = ROOT / "data" / "catalogue" / "diagnostic_codes.yaml"


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    subject: str
    level: str
    state_effect: str
    predicate: str
    params: dict
    display: str
    channels: list
    message_key: str
    audio_clip: str | None
    cooldown_s: float
    repeat_while_true_s: float | None
    escalate_after_s: float | None
    escalate_to: dict | None
    once_per: str | None
    notify_supervisor: bool
    lesson_trigger: bool
    enabled: bool
    hld_ref: str


_RULE_FIELDS = {f.name for f in fields(Rule)}


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[Rule, ...]
    predicates: dict
    exit_checks: dict
    policy: dict

    def by_id(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)


def load_rules(path: Path = RULES_PATH) -> RuleSet:
    doc = yaml.safe_load(path.read_text())
    rules = tuple(Rule(**{k: v for k, v in r.items() if k in _RULE_FIELDS}) for r in doc["rules"])
    return RuleSet(rules, doc["predicates"], doc["exit_checks"], doc["policy"])


def load_machine_models(path: Path = SEED_DIR / "machine_models.yaml") -> dict[str, dict]:
    return {m["model_id"]: m for m in yaml.safe_load(path.read_text())}


def load_machines(path: Path = SEED_DIR / "machines.yaml") -> dict[str, dict]:
    return {m["machine_id"]: m for m in yaml.safe_load(path.read_text())}


def load_catalogue(path: Path = CATALOGUE_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text())
