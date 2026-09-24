"""Phase 0 gate: contracts are self-consistent (plan.md Phase 0 verification)."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
SCHEMAS = {
    p.name.removesuffix(".json"): json.loads(p.read_text())
    for p in (CONTRACTS / "schemas").glob("*.json")
}
EXAMPLES = sorted((CONTRACTS / "examples").glob("*.json"))
RULES = yaml.safe_load((CONTRACTS / "rules.yaml").read_text())
EN = json.loads((CONTRACTS / "i18n" / "en.json").read_text())
CLIPS = yaml.safe_load((CONTRACTS / "audio_clips.yaml").read_text())

ALERT = SCHEMAS["alert.v1"]["properties"]["alert"]["properties"]
LEVELS = ALERT["level"]["enum"]
SUBJECTS = ALERT["subject"]["enum"]
CHANNELS = ALERT["channels"]["items"]["enum"]
DISPLAYS = SCHEMAS["nudge.v1"]["properties"]["display"]["enum"]
TONES = [t for t in SCHEMAS["nudge.v1"]["properties"]["tone_pattern"]["enum"] if t]
HAZARD_TYPES = SCHEMAS["hazards.v1"]["properties"]["pins"]["items"]["properties"]["type"]["enum"]

RULE_KEYS = {
    "id",
    "name",
    "subject",
    "level",
    "state_effect",
    "predicate",
    "params",
    "display",
    "channels",
    "message_key",
    "audio_clip",
    "cooldown_s",
    "repeat_while_true_s",
    "escalate_after_s",
    "escalate_to",
    "once_per",
    "notify_supervisor",
    "lesson_trigger",
    "enabled",
    "hld_ref",
}


def schema_for(example: Path) -> str:
    # raw.v1.json and raw.v1.seat_sensor_missing.json both validate against raw.v1
    return ".".join(example.name.split(".")[:2])


def expand(template: str | None) -> list[str]:
    if template is None:
        return []
    if "{hazard_type}" in template:
        return [template.replace("{hazard_type}", t) for t in HAZARD_TYPES]
    return [template]


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_and_has_example(name):
    Draft202012Validator.check_schema(SCHEMAS[name])
    assert SCHEMAS[name]["$id"] == name
    assert any(schema_for(e) == name for e in EXAMPLES), f"no example for {name}"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_example_validates(example):
    schema = SCHEMAS[schema_for(example)]
    errors = list(Draft202012Validator(schema).iter_errors(json.loads(example.read_text())))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


def test_ts_pattern_rejects_naive_timestamp():
    v = Draft202012Validator(SCHEMAS["proximity.v1"])
    ok = {
        "schema": "proximity.v1",
        "machine_id": "EXC001",
        "min_dist_m": None,
        "frame_ok": True,
        "source": "sim",
    }
    assert v.is_valid({**ok, "ts": "2026-10-14T10:00:00+05:30"})
    assert not v.is_valid({**ok, "ts": "2026-10-14T10:00:00"})


def test_rules_complete_and_well_formed():
    rules = RULES["rules"]
    assert [r["id"] for r in rules] == [f"R{i:02d}" for i in range(1, 25)]
    for r in rules:
        missing = RULE_KEYS - r.keys()
        assert not missing, f"{r['id']} missing {missing}"
        assert r["subject"] in SUBJECTS, r["id"]
        assert r["level"] in LEVELS, r["id"]
        assert r["state_effect"] in ("UNSAFE", "ATTENTION", "NONE"), r["id"]
        assert r["display"] in DISPLAYS, r["id"]
        assert set(r["channels"]) <= set(CHANNELS), r["id"]
        assert r["once_per"] in (None, "EXIT", "EVENT", "EPISODE", "CODE", "ZONE_ENTRY", "SHIFT")
        assert isinstance(r["params"], dict) and r["hld_ref"]
        if r["escalate_after_s"] is not None:
            assert r["escalate_to"]["level"] in LEVELS, r["id"]


def test_critical_rules_always_audible():
    # I4: CRITICAL audio is never downgraded, so every CRITICAL rule must carry AUDIO.
    for r in RULES["rules"]:
        if r["level"] == "CRITICAL":
            assert "AUDIO" in r["channels"], r["id"]
            assert r["state_effect"] == "UNSAFE", r["id"]


def test_stretch_and_cut_rules_disabled():
    # D21 stretch (R19, R22) + the 24 h scope cut (PROGRESS.md Phase 5).
    disabled = {"R19", "R22"} | {"R08", "R09", "R15", "R23", "R24"}
    enabled = {r["id"]: r["enabled"] for r in RULES["rules"]}
    assert {k for k, v in enabled.items() if not v} == disabled


def test_message_keys_and_audio_clips_exist():
    voice, tones = CLIPS["voice"], CLIPS["tones"]
    for r in RULES["rules"]:
        for key in expand(r["message_key"]):
            assert key in EN, f"{r['id']}: {key} not in en.json"
        for clip in expand(r["audio_clip"]):
            assert clip in voice, f"{r['id']}: {clip} not in audio_clips.yaml"
        if r["escalate_to"] and r["escalate_to"].get("audio_clip"):
            assert r["escalate_to"]["audio_clip"] in voice.keys() | tones.keys(), r["id"]
    for clip, spec in voice.items():
        assert spec["message_key"] in EN, clip
        assert spec["text"] == EN[spec["message_key"]], clip
    assert set(RULES["policy"]["tone_patterns"].values()) == set(TONES) <= tones.keys()
