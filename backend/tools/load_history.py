"""Import P1's historical export (contracts/history.md) into the edge DB.

Stub for Phase 1: no-op with a clear message if the files aren't there yet, so this can
be wired into a phase-end checklist now and filled in once P1 delivers (Phase 2-3 use the
anchor traces; this loader itself is finished later, whenever that data exists).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY_DIR = ROOT / "data" / "history"
EXPECTED = ["windows.parquet", "tasks.parquet", "operators.parquet"]


def missing_files() -> list[str]:
    return [name for name in EXPECTED if not (HISTORY_DIR / name).exists()]


def main() -> None:
    missing = missing_files()
    if missing:
        print(
            f"data/history/ not found or incomplete (missing: {', '.join(missing)}). "
            "Nothing to load yet — see contracts/history.md for the format P1 will deliver.",
            file=sys.stderr,
        )
        return
    raise NotImplementedError("data/history/ is present — implement the real import (Phase 2/3)")


if __name__ == "__main__":
    main()
