"""Write edge-api's OpenAPI spec to contracts/openapi.json (run at every phase end)."""

import json
from pathlib import Path

from edge.main import app

OUT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT}")
