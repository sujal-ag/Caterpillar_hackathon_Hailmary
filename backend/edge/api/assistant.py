"""`/diagnostics/active` (catalogue cards, no LLM) and `/assistant/ask` (plan.md Phase 8,
contracts/rest.md). The LLM is never on the safety path: `action_class` is copied from the
catalogue (I3) and any LLM failure or guard trip returns the catalogue template."""

import asyncio
import hashlib
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from common.log import jlog
from common.timeutil import to_site_iso
from edge import rag
from edge.api.auth import Principal, require
from edge.api.state import _runner

router = APIRouter(tags=["assistant"])
log = logging.getLogger("edge.assistant")


class DiagnosticCard(BaseModel):
    code_id: str | None
    spn: int | None
    fmi: int | None
    component: str | None
    what_happened: str | None
    why_it_matters: str | None
    what_to_do: str | None
    action_class: str
    source_doc: str | None
    source_section: str | None


class Diagnostics(BaseModel):
    as_of: str
    machine_id: str
    diagnostics: list[DiagnosticCard]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    context_code: str | None = None  # catalogue code_id, e.g. "J1939-1638-16"
    machine: str | None = None  # default context = this machine's most severe active DTC


class Citation(BaseModel):
    n: int
    doc: str
    section: str
    code_id: str | None


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    action_class: str
    model: str  # local | template
    latency_ms: int
    as_of: str


def _active(runner) -> list[dict]:
    return list(runner.engine.ctx.active_dtcs.values())


@router.get("/diagnostics/active", response_model=Diagnostics)
async def diagnostics_active(
    request: Request,
    machine: str = Query(..., description="machine_id, e.g. EXC001"),
    who: Principal = Depends(require("operator")),
) -> Diagnostics:
    rt = request.app.state.rt
    cards = []
    for d in _active(_runner(rt, machine, who)):
        row = rt.codes.get(d["code_id"]) or {}
        cards.append(
            DiagnosticCard(
                code_id=d["code_id"],
                spn=d["spn"],
                fmi=d["fmi"],
                component=row.get("component"),
                what_happened=row.get("what_happened"),
                why_it_matters=row.get("why_it_matters"),
                what_to_do=row.get("what_to_do"),
                action_class=d["action_class"],
                source_doc=row.get("source_doc"),
                source_section=row.get("source_section"),
            )
        )
    return Diagnostics(as_of=to_site_iso(rt.clock()), machine_id=machine, diagnostics=cards)


@router.post("/assistant/ask", response_model=AskResponse)
async def ask(
    body: AskRequest, request: Request, who: Principal = Depends(require("operator"))
) -> AskResponse:
    rt, s = request.app.state.rt, request.app.state.rt.settings
    t0 = time.monotonic()
    context_code = body.context_code
    if context_code is None and body.machine:
        active = [d for d in _active(_runner(rt, body.machine, who)) if d["code_id"]]
        active.sort(key=lambda d: rt.codes[d["code_id"]].get("severity") or 9)
        context_code = active[0]["code_id"] if active else None

    row, chunks = await asyncio.to_thread(
        rag.retrieve, s.edge_db_path, body.question, context_code, rt.codes, rt.ruleset.rag
    )
    action_class = row["action_class"] if row else "UNDOCUMENTED"

    guard = "refused"
    if not chunks:
        answer, model = rag.REFUSAL, "template"
    else:
        try:
            answer = await rag.chat(
                s.ollama_url, s.ollama_model, s.rag_llm_timeout_s, body.question, chunks
            )
            rt.llm_status, model = "LOCAL", "local"
            guard = "ok" if rag.guard_ok(answer, action_class) else "tripped"
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            jlog(log, logging.WARNING, "llm_failed", error=repr(exc))
            rt.llm_status, guard = "UNAVAILABLE", "llm_down"
        if guard != "ok":
            answer, model = rag.template(row, chunks), "template"

    latency_ms = int((time.monotonic() - t0) * 1000)
    jlog(
        log,
        logging.INFO,
        "assistant_ask",
        question_sha=hashlib.sha256(body.question.encode()).hexdigest()[:12],
        code_id=row and row["code_id"],
        action_class=action_class,
        model=model,
        guard=guard,
        latency_ms=latency_ms,
    )
    return AskResponse(
        answer=answer,
        citations=[
            Citation(n=i, doc=c["doc"], section=c["section"], code_id=c["code_id"])
            for i, c in enumerate(chunks, 1)
        ],
        action_class=action_class,
        model=model,
        latency_ms=latency_ms,
        as_of=to_site_iso(rt.clock()),
    )
