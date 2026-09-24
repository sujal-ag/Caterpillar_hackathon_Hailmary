"""Builds the RAG search index from data/catalogue/diagnostic_codes.yaml — the
retrieval side of the Alarm Explainer (HLD §4.9/§7.5; docs/plan.md Phase 8).

IMPORTANT — this is NOT a training step. Qwen itself is a pretrained model and
is never fine-tuned on this project's data (see README's "About Qwen" section).
This script builds the searchable INDEX that Qwen retrieves from at answer time
— the RAG equivalent of what train_models.py does for the other two models, but
there's no gradient descent happening here, just chunking + embedding + storing.

Output: rag_index.db (SQLite) with two tables:
  - chunks_fts: FTS5 (BM25 keyword search) over each code's text
  - chunks_vec: sqlite-vec vec0 table (384-dim) for semantic search
Both keyed by the same chunk_id, so retrieval does BM25 + cosine and merges
results (HLD §7.5: hybrid retrieval).

Run this once per catalogue update, on any machine with internet access to
download the embedding model on first run (~130MB, cached after that).
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import sqlite_vec
import yaml


def load_catalogue(path: Path) -> list[dict]:
    codes = yaml.safe_load(path.read_text())
    if len(codes) < 30:
        print(f"WARNING: catalogue has only {len(codes)} entries — HLD/plan.md target "
              f"30-50. Fine for a pipeline test, but expand this before the real demo.")
    return codes


def chunk_text(code: dict) -> str:
    """One chunk per code (HLD §7.5): concatenate the three explanation fields
    so a single embedding captures the whole answer, not just a fragment."""
    return (
        f"{code['component']} (SPN {code['spn']} FMI {code['fmi']}): "
        f"{code['what_happened']} {code['why_it_matters']} {code['what_to_do']}"
    )


def build_index(catalogue_path: Path, out_path: Path, embedder="fastembed") -> None:
    codes = load_catalogue(catalogue_path)
    chunks = [{"chunk_id": c["code_id"], "text": chunk_text(c), "code": c} for c in codes]

    if embedder == "fastembed":
        from fastembed import TextEmbedding
        model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")  # 384-dim, HLD §5/§7.5
        vectors = list(model.embed([c["text"] for c in chunks]))
    elif embedder == "mock":
        # Deterministic fake embedding for testing the SQL/schema mechanics without
        # network access to download the real model. NEVER use this for the real
        # index — retrieval quality depends entirely on real embeddings.
        import hashlib
        import numpy as np
        vectors = []
        for c in chunks:
            h = hashlib.sha256(c["text"].encode()).digest()
            vectors.append(np.frombuffer((h * 12)[:384 * 4], dtype=np.float32))
    else:
        raise ValueError(f"unknown embedder: {embedder}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(out_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute(
        "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, text, code_id UNINDEXED)"
    )
    conn.execute(f"CREATE VIRTUAL TABLE chunks_vec USING vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[{len(vectors[0])}])")
    conn.execute(
        "CREATE TABLE chunks_meta (chunk_id TEXT PRIMARY KEY, spn INT, fmi INT, "
        "action_class TEXT, source_doc TEXT, component TEXT)"
    )

    for chunk, vec in zip(chunks, vectors):
        c = chunk["code"]
        conn.execute(
            "INSERT INTO chunks_fts (chunk_id, text, code_id) VALUES (?, ?, ?)",
            (chunk["chunk_id"], chunk["text"], c["code_id"]),
        )
        conn.execute(
            "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
            (chunk["chunk_id"], sqlite_vec.serialize_float32(list(vec))),
        )
        conn.execute(
            "INSERT INTO chunks_meta (chunk_id, spn, fmi, action_class, source_doc, component) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chunk["chunk_id"], c["spn"], c["fmi"], c["action_class"], c["source_doc"], c["component"]),
        )
    conn.commit()

    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    print(f"built {out_path}: {n_chunks} chunks, {len(vectors[0])}-dim embeddings, "
          f"{out_path.stat().st_size / 1024:.1f} KB")
    conn.close()


def query_test(out_path: Path, question: str) -> None:
    """Quick smoke test: exact-code BM25 lookup, no embedding needed at query time
    for this simple check (semantic query needs the same embedder as build time)."""
    conn = sqlite3.connect(out_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    rows = conn.execute(
        "SELECT chunk_id, text FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 3",
        (question,),
    ).fetchall()
    print(f"\nBM25 test query {question!r}:")
    for chunk_id, text in rows:
        print(f"  [{chunk_id}] {text[:100]}...")
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogue", default="data/catalogue/diagnostic_codes.yaml")
    ap.add_argument("--out", default="./data/catalogue/rag_index.db",
                     help="NOTE: this project's .gitignore excludes *.db and all of "
                          "data/history/ — this default deliberately avoids both so the "
                          "built index is actually committable. If you change this, add "
                          "a .gitignore exception (e.g. `!data/catalogue/rag_index.db`) "
                          "or it will silently never get committed.")
    ap.add_argument("--embedder", choices=["fastembed", "mock"], default="fastembed",
                     help="Use 'mock' only to test the SQL/schema mechanics without "
                          "network access — never for the real index.")
    ap.add_argument("--test-query", default=None, help="Run a BM25 smoke test after building")
    args = ap.parse_args()

    build_index(Path(args.catalogue), Path(args.out), embedder=args.embedder)
    if args.test_query:
        query_test(Path(args.out), args.test_query)


if __name__ == "__main__":
    main()
