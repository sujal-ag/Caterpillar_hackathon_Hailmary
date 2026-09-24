"""Load `contracts/rules.yaml` and the per-model thresholds it points at (plan.md Phase 3
item 3). Thresholds are never hard-coded in the engine: they come from here."""

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from common.config import REPO_ROOT, Settings

_SETTINGS = Settings()  # CONTRACTS_DIR / DATA_DIR env vars (infra/.env.example)
ROOT = REPO_ROOT
RULES_PATH = _SETTINGS.contracts_dir / "rules.yaml"
SEED_DIR = _SETTINGS.data_dir / "seed"
CATALOGUE_PATH = _SETTINGS.data_dir / "catalogue" / "diagnostic_codes.yaml"


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
    audio_priority: int = 0  # higher wins the audio budget when raised on the same tick


_RULE_FIELDS = {f.name for f in fields(Rule)}


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[Rule, ...]
    predicates: dict
    exit_checks: dict
    policy: dict
    version: int = 0
    readiness: dict = field(default_factory=dict)  # HLD §7.4 score table
    hazards: dict = field(default_factory=dict)  # HLD §4.12 pin limits + expiry
    rag: dict = field(default_factory=dict)  # HLD §7.5 retrieval gate (D13)

    def by_id(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)


def load_rules(path: Path = RULES_PATH) -> RuleSet:
    doc = yaml.safe_load(path.read_text())
    rules = tuple(Rule(**{k: v for k, v in r.items() if k in _RULE_FIELDS}) for r in doc["rules"])
    return RuleSet(
        rules,
        doc["predicates"],
        doc["exit_checks"],
        doc["policy"],
        doc.get("version", 0),
        doc.get("readiness", {}),
        doc.get("hazards", {}),
        doc.get("rag", {}),
    )


def load_machine_models(path: Path = SEED_DIR / "machine_models.yaml") -> dict[str, dict]:
    return {m["model_id"]: m for m in yaml.safe_load(path.read_text())}


def load_machines(path: Path = SEED_DIR / "machines.yaml") -> dict[str, dict]:
    return {m["machine_id"]: m for m in yaml.safe_load(path.read_text())}


def load_catalogue(path: Path = CATALOGUE_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text())
