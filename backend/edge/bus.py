"""Message bus interface (plan.md §6 `bus.py`). `MemoryBus` is the in-process
implementation unit tests use instead of MQTT (plan.md §4 "Tests"); `MqttBus` (aiomqtt)
arrives with the Phase 4 lifespan wiring, behind the same two methods."""

import asyncio
from typing import Protocol


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT wildcard match: `+` = one level, `#` = this level and everything below."""
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class Bus(Protocol):
    async def publish(self, topic: str, payload: dict) -> None: ...

    def subscribe(self, pattern: str, queue: asyncio.Queue) -> None: ...


class MemoryBus:
    def __init__(self):
        self._subs: list[tuple[str, asyncio.Queue]] = []

    async def publish(self, topic: str, payload: dict) -> None:
        for pattern, queue in self._subs:
            if topic_matches(pattern, topic):
                queue.put_nowait((topic, payload))

    def subscribe(self, pattern: str, queue: asyncio.Queue) -> None:
        self._subs.append((pattern, queue))
