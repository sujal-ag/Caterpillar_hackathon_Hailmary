"""WebSocket bridge (contracts/ws.md; plan.md Phase 4 item 2).

`/ws/live?machine=&token=`: one machine, every type. `/ws/site?token=` (supervisor+): all
machines, `state` + `alert` only. On connect the client gets `snapshot` (current truth) —
it subscribes *before* the snapshot is built so nothing emitted in between is lost (a
duplicate is harmless; a gap is not). Telemetry is throttled per client to
`WS_TELEMETRY_MIN_INTERVAL_S`; `ping` every `WS_PING_S`. A client that falls behind is
closed (1013) so it reconnects to a fresh snapshot instead of drifting stale (I6).

Close codes: 4401 bad/missing token, 4403 not allowed, 4404 unknown machine.
"""

import asyncio
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from edge import live
from edge.api.auth import principal_from_token
from edge.broadcast import OVERFLOW, envelope

router = APIRouter()
SITE_TYPES = {"state", "alert"}


async def _authorise(ws: WebSocket, token: str | None):
    try:
        return principal_from_token(ws.app.state.rt, token)
    except HTTPException:
        await ws.close(code=4401)
        return None


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, machine: str, token: str | None = None) -> None:
    rt = ws.app.state.rt
    who = await _authorise(ws, token)
    if who is None:
        return
    if machine not in rt.runners:
        await ws.close(code=4404)
        return
    if not who.may_see(machine):
        await ws.close(code=4403)
        return
    await ws.accept()
    client = rt.sink.subscribe({machine}, None)
    try:
        await ws.send_json(envelope("snapshot", await live.snapshot(rt, machine)))
        await _pump(ws, client, rt)
    finally:
        rt.sink.unsubscribe(client)


@router.websocket("/ws/site")
async def ws_site(ws: WebSocket, token: str | None = None) -> None:
    rt = ws.app.state.rt
    who = await _authorise(ws, token)
    if who is None:
        return
    if not who.at_least("supervisor"):
        await ws.close(code=4403)
        return
    await ws.accept()
    client = rt.sink.subscribe(None, SITE_TYPES)
    try:
        for machine in rt.runners:
            await ws.send_json(envelope("snapshot", await live.snapshot(rt, machine)))
        await _pump(ws, client, rt)
    finally:
        rt.sink.unsubscribe(client)


async def _pump(ws: WebSocket, client, rt) -> None:
    """Send until the socket closes; a reader task notices the disconnect, a pinger task
    queues a `ping` every WS_PING_S. Children are cancelled, never awaited, on the way out:
    awaiting them while this handler is itself being cancelled (client gone, shutdown)
    re-delivers the cancellation and lets it escape the server's cancel scope."""
    tasks = {
        asyncio.create_task(_drain(ws)),
        asyncio.create_task(_send(ws, client, rt.settings)),
        asyncio.create_task(_ping(client, rt.settings)),
    }
    for task in tasks:  # mark every child's outcome retrieved (no "never retrieved" noise)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
    for task in done:
        exc = None if task.cancelled() else task.exception()
        if exc and not isinstance(exc, WebSocketDisconnect):
            raise exc


async def _drain(ws: WebSocket) -> None:
    while True:  # clients don't send anything we act on; this just detects the close
        await ws.receive_text()


async def _ping(client, settings) -> None:
    while True:
        await asyncio.sleep(settings.ws_ping_s)
        try:
            client.queue.put_nowait(envelope("ping", {}))
        except asyncio.QueueFull:
            pass  # a full queue already means an overflow close is coming


async def _send(ws: WebSocket, client, settings) -> None:
    last_telemetry = time.monotonic() - settings.ws_telemetry_min_interval_s
    while True:
        env = await client.queue.get()
        if env is OVERFLOW:
            await ws.close(code=1013)
            return
        if env["type"] == "telemetry":
            if time.monotonic() - last_telemetry < settings.ws_telemetry_min_interval_s:
                continue
            last_telemetry = time.monotonic()
        await ws.send_json(env)
