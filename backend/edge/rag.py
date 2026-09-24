"""Alarm explainer + manual Q&A (HLD §4.9, §7.5; plan.md Phase 8).

Retrieval: exact code (SQL catalogue) first, else SQLite FTS5 BM25 over the catalogue and
`data/manuals/*.md` (the vector leg is cut, 24 h plan). The LLM (local Ollama) only
rephrases retrieved text. `action_class` always comes from the catalogue (I3); a STOP code
whose answer sounds like "carry on" is replaced by the catalogue template. Ollama down or
slow → template. Ported from the team's `rag_module` (chunking/prompt/citation pattern)."""

import re
import sqlite3
from pathlib import Path

import httpx

REFUSAL = "Not in the documentation I have — contact your supervisor."
SYSTEM_PROMPT = (
    "You help a heavy-equipment operator. Answer only from the numbered context. "
    "Use at most 3 short sentences in plain, simple words. Cite sources as [n]. "
    "Never say whether it is safe to keep working; that decision is shown separately. "
    "If the context does not answer the question, say you don't know."
)
_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunk USING fts5("
    "kind UNINDEXED, code_id UNINDEXED, doc UNINDEXED, section UNINDEXED, text, "
    "tokenize='porter')"
)
_CODE_RES = (
    re.compile(r"\bJ1939-(\d+)-(\d+)\b", re.I),
    re.compile(r"\bSPN\s*(\d+)\D{0,5}FMI\s*(\d+)", re.I),
)
_CAT_RE = re.compile(r"\b(E\d{3})\b", re.I)
_STOP = set(
    "a an and are am be can could do does for how i if in is it me my of on or should "
    "the this to what when where which why will with you your".split()
)
_CONTINUE_RE = re.compile(
    r"\b(continue|carry on|keep (working|operating|running|going|digging)"
    r"|safe to (operate|continue|work|use)|no need to stop|fine to (operate|work))\b",
    re.I,
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def code_text(row: dict) -> str:
    parts = [row["code_id"], row.get("cat_code"), row.get("component"), row.get("description")]
    if row.get("spn") is not None:
        parts.append(f"SPN {row['spn']} FMI {row['fmi']}")
    parts += [row.get("what_happened"), row.get("why_it_matters"), row.get("what_to_do")]
    return ". ".join(p for p in parts if p)


def manual_sections(manuals_dir: Path) -> list[tuple[str, str, str]]:
    """(doc, section, text) per `## ` header of every `*.md` file."""
    out = []
    for path in sorted(manuals_dir.glob("*.md")):
        for block in path.read_text(encoding="utf-8").split("\n## ")[1:]:
            section, _, body = block.partition("\n")
            if body.strip():
                out.append((path.stem, section.strip(), body.strip()))
    return out


def build_index(db_path: str, codes: list[dict], manuals_dir: Path) -> int:
    """Rebuild the FTS5 index (idempotent, one transaction). Not a sync entity."""
    rows = [("CODE", c["code_id"], "diagnostic_codes", c["code_id"], code_text(c)) for c in codes]
    rows += [
        ("MANUAL", None, doc, sec, f"{sec}. {text}")
        for doc, sec, text in manual_sections(manuals_dir)
    ]
    with _connect(db_path) as con:
        con.execute(_DDL)
        con.execute("DELETE FROM rag_chunk")
        con.executemany("INSERT INTO rag_chunk VALUES (?, ?, ?, ?, ?)", rows)
    return len(rows)


def exact_code(question: str, context_code: str | None, codes: dict[str, dict]) -> dict | None:
    """Catalogue row named by `context_code` or a code written in the question."""
    if context_code and context_code in codes:
        return codes[context_code]
    for rx in _CODE_RES:
        if m := rx.search(question):
            return codes.get(f"J1939-{m[1]}-{m[2]}")
    if m := _CAT_RE.search(question):
        return next((c for c in codes.values() if c.get("cat_code") == m[1].upper()), None)
    return None


def search(db_path: str, question: str, k: int = 4) -> list[dict]:
    """BM25 top-k; `relevance` = −bm25 (higher is better)."""
    terms = [t for t in re.findall(r"[a-z0-9]+", question.lower()) if t not in _STOP]
    if not terms:
        return []
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT kind, code_id, doc, section, text, -bm25(rag_chunk) AS relevance "
            "FROM rag_chunk WHERE rag_chunk MATCH ? ORDER BY bm25(rag_chunk) LIMIT ?",
            (" OR ".join(f'"{t}"' for t in terms), k),
        ).fetchall()
    return [dict(r) for r in rows]


def retrieve(
    db_path: str, question: str, context_code: str | None, codes: dict[str, dict], cfg: dict
) -> tuple[dict | None, list[dict]]:
    """(exact catalogue row that sets action_class, or None; context chunks).
    No chunks = refuse (D13 gate: exact code OR top BM25 relevance >= `min_relevance`)."""
    k, min_rel = cfg["top_k"], cfg["min_relevance"]
    row = exact_code(question, context_code, codes)
    # A known code widens the query with its own names, so its manual sections come along.
    extra_terms = f" {row['component'] or ''} {row.get('cat_code') or ''}" if row else ""
    chunks = search(db_path, question + extra_terms, k)
    if row:
        own = {"kind": "CODE", "code_id": row["code_id"], "doc": "diagnostic_codes",
               "section": row["code_id"], "text": code_text(row)}  # fmt: skip
        extra = [c for c in chunks if c["kind"] == "MANUAL" and c["relevance"] >= min_rel]
        return row, [own] + extra[: k - 1]  # other codes only confuse the answer
    if chunks and chunks[0]["relevance"] >= min_rel:
        return None, chunks  # no exact code: never guess an action_class from a BM25 hit (I3)
    return None, []


def guard_ok(answer: str, action_class: str) -> bool:
    return not (action_class == "STOP" and _CONTINUE_RE.search(answer))


def template(row: dict | None, chunks: list[dict]) -> str:
    if row:
        return " ".join(
            p
            for p in (row.get("what_happened"), row.get("why_it_matters"), row.get("what_to_do"))
            if p
        )
    return chunks[0]["text"] if chunks else REFUSAL


async def chat(url: str, model: str, timeout_s: float, question: str, chunks: list[dict]) -> str:
    context = "\n\n".join(
        f"[{i}] ({c['doc']} — {c['section']})\n{c['text']}" for i, c in enumerate(chunks, 1)
    )
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 160, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(f"{url.rstrip('/')}/api/chat", json=body)
        r.raise_for_status()
        text = _THINK_RE.sub("", r.json()["message"]["content"]).strip()
    if not text:
        raise ValueError("empty LLM answer")
    return text
