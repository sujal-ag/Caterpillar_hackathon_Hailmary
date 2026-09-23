"""Replay P1's labelled traces through the rule engine and score it against `sim_label`
(plan.md Phase 3 item 9; HLD §7.3: target 100% recall on UNSAFE_EXIT and BELT_BYPASS).

This is the only engine-side reader of `sim_label` (I9): the engine strips it before
normalising; here it is read straight from the trace lines to score the output.

Scoring is per labelled episode (a contiguous run of one `sim_label`): detected if the
label's rule raised an alert inside the episode (or within GRACE_S after it ends).
A frame-level confusion matrix (sim_label vs the label implied by the active rules on that
frame) is included too. Output: a Markdown report (default `docs/eval_rules.md`).

Traces: `data/history/traces/*.jsonl` in `tools/replay.py` format (contracts/history.md).
"""

import argparse
import json
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

from common.config import Settings
from common.timeutil import parse_iso
from edge.engine.machine import MachineEngine
from edge.engine.rules import ROOT, load_catalogue, load_machine_models, load_machines, load_rules
from edge.ingest.dtc import build_catalogue_index

TRACES_DIR = Settings().data_dir / "history" / "traces"
REPORT = ROOT / "docs" / "eval_rules.md"
LABEL_RULES = {"UNSAFE_EXIT": {"R03"}, "BELT_BYPASS": {"R07"}}  # HLD §7.3 recall targets
GRACE_S = 2


def _predicted(engine: MachineEngine) -> str:
    active = {a["rule_id"] for a in engine.alerts.active_records()}
    return next((label for label, rules in LABEL_RULES.items() if rules & active), "NONE")


def run_trace(path: Path) -> tuple[list[dict], Counter]:
    """Returns (episodes [{label, start, end, detected}], confusion {(actual, predicted): n})."""
    lines = [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
    machines, models = load_machines(), load_machine_models()
    catalogue = build_catalogue_index(load_catalogue())
    ruleset = load_rules()
    engine = None
    raised: list[tuple] = []
    labels: list[tuple] = []  # (ts, label) per raw frame
    confusion: Counter = Counter()
    for line in lines:
        topic, payload = line["topic"], line["payload"]
        now = parse_iso(payload["ts"])
        if engine is None and "machine_id" in payload:
            m = machines[payload["machine_id"]]
            engine = MachineEngine(
                m["machine_id"],
                m["site_id"],
                models[m["model_id"]],
                ruleset,
                catalogue,
                has_rear_camera=m.get("has_rear_camera", False),
            )
        if engine is None:
            continue
        if topic.endswith("/raw"):
            labels.append((now, payload.get("sim_label")))
            out = engine.on_raw(payload, now)
            confusion[(payload.get("sim_label") or "NONE", _predicted(engine))] += 1
        elif topic.endswith("/dtc"):
            out = engine.on_dtc(payload, now)
        elif topic.endswith("/env"):
            out = engine.on_env(payload, now)
        elif topic.endswith("/proximity"):
            out = engine.on_proximity(payload, now)
        else:
            continue
        raised += [(now, a["rule_id"]) for action, a in out.alerts if action == "RAISED"]

    episodes: list[dict] = []
    prev = None
    for ts, label in labels:
        if label is not None and label == prev:
            episodes[-1]["end"] = ts
        elif label is not None:
            episodes.append({"label": label, "start": ts, "end": ts})
        prev = label
    for ep in episodes:
        rules = LABEL_RULES.get(ep["label"], set())
        end = ep["end"] + timedelta(seconds=GRACE_S)
        ep["detected"] = any(ep["start"] <= t <= end and r in rules for t, r in raised)
    return episodes, confusion


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--traces", type=Path, default=TRACES_DIR)
    ap.add_argument("--out", type=Path, default=REPORT)
    args = ap.parse_args()
    files = sorted(args.traces.glob("*.jsonl")) if args.traces.is_dir() else []
    if not files:
        print(
            f"no labelled traces in {args.traces} (P1 export, contracts/history.md) — eval skipped",
            file=sys.stderr,
        )
        return 0

    per_label: Counter = Counter()
    hit: Counter = Counter()
    rows = []
    confusion: Counter = Counter()
    for f in files:
        episodes, conf = run_trace(f)
        confusion += conf
        for ep in episodes:
            per_label[ep["label"]] += 1
            hit[ep["label"]] += ep["detected"]
            rows.append(
                f"| {f.name} | {ep['label']} | {ep['start'].isoformat()} | "
                f"{'yes' if ep['detected'] else '**no**'} |"
            )
    lines = [
        "# Rule evaluation vs sim_label",
        "",
        "| Label | Rules | Episodes | Detected | Recall |",
        "|---|---|---|---|---|",
    ]
    ok = True
    for label, n in sorted(per_label.items()):
        rules = ",".join(sorted(LABEL_RULES.get(label, []))) or "—"
        recall = hit[label] / n
        if label in LABEL_RULES and recall < 1.0:
            ok = False
        lines.append(f"| {label} | {rules} | {n} | {hit[label]} | {recall:.0%} |")
    cols = ["NONE", *LABEL_RULES]
    lines += [
        "",
        "Frames (rows: sim_label, columns: predicted)",
        "",
        "| sim_label | " + " | ".join(cols) + " |",
        "|---" * (len(cols) + 1) + "|",
    ]
    for actual in sorted({a for a, _ in confusion}):
        cells = " | ".join(str(confusion[(actual, c)]) for c in cols)
        lines.append(f"| {actual} | {cells} |")
    lines += ["", "| Trace | Label | Start | Detected |", "|---|---|---|---|", *rows, ""]
    args.out.write_text("\n".join(lines))
    print(f"report -> {args.out}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
