"""Phase 8 eval (HLD §7.5): retrieval hit@3 and action-class accuracy on
`tests/rag_golden.yaml`, plus live LLM latency when Ollama is up → `docs/eval_rag.md`.

    .venv/bin/python tools/eval_rag.py
"""

import asyncio
import statistics
import tempfile
import time
from pathlib import Path

import yaml

from common.config import REPO_ROOT, Settings
from edge import rag
from edge.engine.rules import load_rules

GOLDEN = REPO_ROOT / "backend/tests/rag_golden.yaml"


def main() -> None:
    s = Settings()
    cfg = load_rules(s.contracts_dir / "rules.yaml").rag
    codes = {c["code_id"]: c for c in yaml.safe_load(
        (s.data_dir / "catalogue/diagnostic_codes.yaml").read_text())}  # fmt: skip
    db = str(Path(tempfile.mkdtemp()) / "rag.db")
    rag.build_index(db, list(codes.values()), s.data_dir / "manuals")

    lines, hits, classes, latencies = [], 0, 0, []
    golden = yaml.safe_load(GOLDEN.read_text())
    for g in golden:
        row, chunks = rag.retrieve(db, g["q"], g.get("context_code"), codes, cfg)
        top3 = [c["code_id"] or c["section"] for c in chunks[:3]]
        hit = (g["expect"] in top3) if g["expect"] else not chunks
        action = row["action_class"] if row else "UNDOCUMENTED"
        hits, classes = hits + hit, classes + (action == g["action"])
        took = None
        if chunks:
            t0 = time.monotonic()
            try:
                asyncio.run(rag.chat(s.ollama_url, s.ollama_model, 60, g["q"], chunks))
                took = time.monotonic() - t0
                latencies.append(took)
            except Exception:  # noqa: BLE001 - no LLM: retrieval numbers still count
                pass
        lines.append(
            f"| {g['q']} | {g['expect']} | {'✅' if hit else '❌'} {top3} | {action} "
            f"| {'' if took is None else f'{took:.2f}'} |"
        )

    n = len(golden)
    p50 = f"{statistics.median(latencies):.2f} s" if latencies else "n/a (Ollama down)"
    p95 = f"{sorted(latencies)[int(0.95 * (len(latencies) - 1))]:.2f} s" if latencies else "n/a"
    report = "\n".join(
        [
            "# RAG eval (Phase 8)",
            "",
            f"Model `{s.ollama_model}` · retrieval FTS5 BM25 (min_relevance "
            f"{cfg['min_relevance']}, top_k {cfg['top_k']})",
            "",
            f"- **hit@3: {hits}/{n} = {hits / n:.0%}** (target ≥ 90 %)",
            f"- **action-class accuracy: {classes}/{n} = {classes / n:.0%}** (target 100 %)",
            f"- LLM latency p50 {p50} · p95 {p95} (target p50 < 5 s)",
            "",
            "| question | expected | top-3 | action_class | LLM s |",
            "|---|---|---|---|---|",
            *lines,
            "",
        ]
    )
    (REPO_ROOT / "docs/eval_rag.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
