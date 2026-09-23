"""`SIM_MODE=replay` (contracts/sim_control.md): play `SIM_SCENARIOS_DIR/{name}.jsonl` onto
the bus, re-timed to now, so the frames take the real ingest path. The scenario list is the
directory listing. With `SIM_REPLAY_HOLD` the last raw frame of each machine keeps being
re-sent at the fixture's own raw frame spacing after the scenario ends, so the machine sits
in the scenario's steady state instead of going stale (the real simulator never stops).
Only one scenario runs at a time; starting another cancels it.
"""

import asyncio
import json
from pathlib import Path

from common.config import Settings
from common.replay import retimed, schedule
from common.timeutil import now_utc, parse_iso, to_site_iso
from edge.bus import Bus


class UnknownScenario(LookupError):
    pass


class Replayer:
    def __init__(self, bus: Bus, site_id: str, settings: Settings):
        self.bus, self.site_id, self.settings = bus, site_id, settings
        self.task: asyncio.Task | None = None
        self.current: str | None = None

    @property
    def dir(self) -> Path:
        return Path(self.settings.sim_scenarios_dir)

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.jsonl"))

    async def start(self, name: str, machine_id: str | None, speed: float = 1.0) -> dict:
        if name not in self.names():
            raise UnknownScenario(name)
        lines = [
            self._rewrite(json.loads(s), machine_id)
            for s in (self.dir / f"{name}.jsonl").read_text().splitlines()
            if s.strip()
        ]
        await self.stop()
        started_at = now_utc()
        self.current = name
        self.task = asyncio.create_task(self._run(lines, speed, started_at), name=f"replay-{name}")
        return {"ok": True, "name": name, "started_at": to_site_iso(started_at)}

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task, self.current = None, None

    def _rewrite(self, line: dict, machine_id: str | None) -> dict:
        """Point a fixture at this edge's site (and optionally another machine)."""
        parts = line["topic"].split("/")
        parts[1] = self.site_id
        if machine_id and len(parts) >= 4:
            parts[2] = machine_id
        payload = dict(line["payload"])
        if "site_id" in payload:
            payload["site_id"] = self.site_id
        if machine_id and "machine_id" in payload:
            payload["machine_id"] = machine_id
        return {**line, "topic": "/".join(parts), "payload": payload}

    async def _run(self, lines: list[dict], speed: float, start) -> None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for offset, line in schedule(lines, speed):
            delay = t0 + offset - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            await self.bus.publish(line["topic"], retimed(line["payload"], offset, start))
        if self.settings.sim_replay_hold:
            await self._hold(lines)

    async def _hold(self, lines: list[dict]) -> None:
        """Steady state runs at the fixture's real frame rate, whatever the playback speed."""
        raw: dict[str, list[dict]] = {}
        for line in lines:
            if line["topic"].endswith("/raw"):
                raw.setdefault(line["topic"], []).append(line["payload"])
        if not raw:
            return
        spacing = min(self._spacing(frames) for frames in raw.values())
        while True:
            await asyncio.sleep(spacing)
            for topic, frames in raw.items():
                await self.bus.publish(topic, {**frames[-1], "ts": to_site_iso(now_utc())})

    @staticmethod
    def _spacing(frames: list[dict]) -> float:
        """The fixture's own raw frame period (last two frames), 1 s if it has only one."""
        if len(frames) < 2:
            return 1.0
        gap = (parse_iso(frames[-1]["ts"]) - parse_iso(frames[-2]["ts"])).total_seconds()
        return gap if gap > 0 else 1.0
