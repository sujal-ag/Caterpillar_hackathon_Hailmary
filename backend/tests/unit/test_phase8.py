"""Phase 8 gate: alarm explainer + manual Q&A (FTS5 retrieval, catalogue action_class, guard,
template fallback with no LLM). The LLM is mocked or pointed at a dead port; the one live
Ollama test skips itself when Ollama isn't reachable."""

import socket

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from common.config import REPO_ROOT, Settings
from edge import rag
from edge.bus import MemoryBus
from edge.main import create_app
from tests.engine_support import load
from tests.unit.test_api import Clock, auth, feed, login, settings

CODES = {c["code_id"]: c for c in yaml.safe_load(
    (REPO_ROOT / "data/catalogue/diagnostic_codes.yaml").read_text())}  # fmt: skip
DEAD = "http://127.0.0.1:9"


@pytest.fixture
def index(tmp_path):
    db = str(tmp_path / "rag.db")
    rag.build_index(db, list(CODES.values()), REPO_ROOT / "data/manuals")
    return db


def test_retrieval_exact_fts_and_off_corpus(index):
    assert rag.exact_code("what is SPN 1638 FMI 16?", None, CODES)["code_id"] == "J1939-1638-16"
    assert rag.exact_code("E361 on the screen", None, CODES)["code_id"] == "CAT-E361"
    assert rag.exact_code("anything", "J1939-110-0", CODES)["action_class"] == "STOP"
    top3 = [c["section"] for c in rag.search(index, "three points of contact climbing down")[:3]]
    assert "Mounting and dismounting" in top3
    assert rag.search(index, "what's the cricket score") == []


def test_guard_blocks_continue_phrasing_only_for_stop():
    assert not rag.guard_ok("You can continue operating.", "STOP")
    assert not rag.guard_ok("It is safe to operate for now.", "STOP")
    assert rag.guard_ok("Stop the machine and shut down.", "STOP")
    assert rag.guard_ok("You can continue operating.", "MONITOR")


@pytest.fixture
def env(tmp_path):
    lines = load("dtc_1638_16")
    bus, clock = MemoryBus(), Clock(lines[0]["payload"]["ts"])
    app = create_app(settings(tmp_path, ollama_url=DEAD), bus=bus, clock=clock)
    with TestClient(app) as client:
        yield client, bus, clock, lines, login(client, "EMP1001", "1234", "EXC001")


def ask(client, token, **body):
    r = client.post("/assistant/ask", json=body, headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_diagnostics_card_and_template_answer_without_llm(env):
    client, bus, clock, lines, op = env
    feed(client, bus, clock, lines)
    r = client.get("/diagnostics/active?machine=EXC001", headers=auth(op)).json()
    (card,) = r["diagnostics"]
    assert card["code_id"] == "J1939-1638-16" and card["action_class"] == "MONITOR"
    assert card["what_happened"] and card["what_to_do"] and r["as_of"]

    # Ollama unreachable -> catalogue template, never a 5xx; context = the active DTC
    out = ask(client, op, question="Can I keep working?", machine="EXC001")
    assert out["model"] == "template" and out["action_class"] == "MONITOR"
    assert out["answer"].startswith(CODES["J1939-1638-16"]["what_happened"])
    assert out["citations"][0]["code_id"] == "J1939-1638-16"
    assert client.get("/system/status", headers=auth(op)).json()["models"]["llm"] == "UNAVAILABLE"


def test_off_corpus_refusal_and_stop_guard(env, monkeypatch):
    client, _, _, _, op = env
    out = ask(client, op, question="what's the cricket score")
    assert (out["answer"], out["action_class"], out["citations"]) == (
        rag.REFUSAL,
        "UNDOCUMENTED",
        [],
    )

    async def reckless(*_a, **_k):
        return "You can continue operating normally."

    monkeypatch.setattr(rag, "chat", reckless)
    out = ask(client, op, question="Can I keep working?", context_code="J1939-110-0")
    assert out["model"] == "template" and out["action_class"] == "STOP"
    assert "continue operating" not in out["answer"]

    async def fine(*_a, **_k):
        return "Reduce load and check the cooler [1]."

    monkeypatch.setattr(rag, "chat", fine)
    out = ask(client, op, question="hydraulic oil hot", context_code="J1939-1638-16")
    assert out["model"] == "local" and out["answer"] == "Reduce load and check the cooler [1]."
    assert client.post("/assistant/ask", json={"question": ""}, headers=auth(op)).status_code == 422


def _ollama_up(url: str) -> bool:
    host, port = url.split("//")[1].split(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
        return httpx.get(f"{url}/api/tags", timeout=2).status_code == 200
    except OSError:
        return False


@pytest.mark.skipif(not _ollama_up(Settings().ollama_url), reason="Ollama not reachable")
def test_live_ollama_answers_with_citation(tmp_path):
    s = settings(tmp_path, rag_llm_timeout_s=60)  # a cold model load can take > 8 s
    app = create_app(s, bus=MemoryBus(), clock=Clock("2026-10-14T07:00:00+05:30"))
    with TestClient(app) as client:
        op = login(client, "EMP1001", "1234", "EXC001")
        out = ask(client, op, question="Can I keep working?", context_code="J1939-1638-16")
        assert out["model"] == "local" and out["action_class"] == "MONITOR", out
        assert out["citations"] and out["answer"]


def test_demo_e001_machine_specific_fault(tmp_path):
    lines = load("demo_e001")
    bus, clock = MemoryBus(), Clock(lines[0]["payload"]["ts"])
    app = create_app(settings(tmp_path, ollama_url=DEAD), bus=bus, clock=clock)
    with TestClient(app) as client:
        op = login(client, "EMP1001", "1234", "EXC001")
        feed(client, bus, clock, lines)
        (card,) = client.get("/diagnostics/active?machine=EXC001", headers=auth(op)).json()[
            "diagnostics"
        ]
        assert (card["code_id"], card["action_class"]) == ("DEMO-E001", "STOP")
        state = client.get("/state/current?machine=EXC001", headers=auth(op)).json()
        assert any(a["rule_id"] == "R16" for a in state["alerts"])
        out = ask(client, op, question="What does error E001 mean?")
        assert out["action_class"] == "STOP" and out["citations"][0]["code_id"] == "DEMO-E001"
