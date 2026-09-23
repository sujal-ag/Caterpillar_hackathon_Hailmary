"""In-process fan-out of engine output to WebSocket clients and MQTT (plan.md §2 data flow,
Phase 4 item 2–3).

`publish(machine_id, type, data)` wraps `data` in the `ws_envelope.v1` envelope for every
matching WS client and publishes the bare payload on `cat/{site}/{machine}/{topic}` per
contracts/topics.md. A client whose queue overflows gets one OVERFLOW marker and is closed by
its socket handler: it reconnects and receives a fresh snapshot, rather than silently
missing messages or seeing stale ones (I6).
"""

import asyncio
from dataclasses import dataclass, field

from common.timeutil import now_utc, to_site_iso
from edge.bus import Bus

# WS message type -> MQTT topic suffix (contracts/topics.md). Types not listed are WS-only.
MQTT_TOPICS = {
    "state": "state",
    "alert": "alert",
    "nudge": "nudge",
    "event": "event",
    "telemetry": "telemetry",
    "sync": "sync/status",
}
OVERFLOW = {"type": "__overflow__"}


def envelope(type_: str, data: dict, ts: str | None = None) -> dict:
    return {"type": type_, "ts": ts or data.get("ts") or to_site_iso(now_utc()), "data": data}


@dataclass(eq=False)
class Client:
    machine_ids: set[str] | None  # None = every machine
    types: set[str] | None  # None = every type
    queue: asyncio.Queue
    overflowed: bool = False
    meta: dict = field(default_factory=dict)

    def wants(self, machine_id: str, type_: str) -> bool:
        return (self.machine_ids is None or machine_id in self.machine_ids) and (
            self.types is None or type_ in self.types
        )


class Broadcaster:
    def __init__(self, bus: Bus, site_id: str, queue_max: int = 256):
        self.bus, self.site_id, self.queue_max = bus, site_id, queue_max
        self.clients: set[Client] = set()

    def subscribe(self, machine_ids: set[str] | None, types: set[str] | None) -> Client:
        client = Client(machine_ids, types, asyncio.Queue(self.queue_max))
        self.clients.add(client)
        return client

    def unsubscribe(self, client: Client) -> None:
        self.clients.discard(client)

    async def publish(self, machine_id: str, type_: str, data: dict) -> None:
        env = envelope(type_, data)
        for client in list(self.clients):
            if client.overflowed or not client.wants(machine_id, type_):
                continue
            try:
                client.queue.put_nowait(env)
            except asyncio.QueueFull:
                client.overflowed = True
                while not client.queue.empty():
                    client.queue.get_nowait()
                client.queue.put_nowait(OVERFLOW)
        suffix = MQTT_TOPICS.get(type_)
        if suffix:
            await self.bus.publish(f"cat/{self.site_id}/{machine_id}/{suffix}", data)
