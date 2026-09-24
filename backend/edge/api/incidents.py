"""`POST /incidents` (HLD §6.11, §9.1 Report; plan.md Phase 5 item 5). Multipart:

- `incident`: JSON string — `type`, `category`, `severity_self`, optional `transcript`,
  `machine_id` (default: the token's machine) and `create_hazard {type, radius_m?}`
- `voice`: 0–1 audio file; `photos`: 0–3 images

Files are a trust boundary: content-type whitelist, `MEDIA_MAX_BYTES` per file, names made
by the server under `MEDIA_DIR/{incident_id}/` (paths stored relative to MEDIA_DIR). The
server fills ts, position (None when stale, never a stale fix), a state snapshot (engine
state + last 60 s telemetry summary) and the machine's alerts from the last 5 min. The
incident (and its pin, for "Report → Worker zone") is one unit of work with P0 outbox rows.
A report is never lost to a hazard problem: with no position, the incident is saved without
a pin and the response says why.
"""

import asyncio
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

from common.db import repo
from common.db.models import Incident, SafetyAlert, TelemetrySample
from common.ids import uuid7
from common.timeutil import to_site_iso
from edge import hazards
from edge.api.auth import Principal, require
from edge.api.hazards import HazardType, validate_pin
from edge.api.shift import machine_for

router = APIRouter(tags=["incidents"])

VOICE_TYPES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
}
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTOS = 3  # HLD §6.11 photo_paths 0–3
LINK_ALERTS_S = 300  # plan.md Phase 5 item 5: alerts in the last 5 min
SNAPSHOT_S = 60  # HLD §6.11: last 60 s telemetry summary


class CreateHazard(BaseModel):
    type: HazardType
    radius_m: float | None = None


class IncidentIn(BaseModel):
    type: Literal["NEAR_MISS", "INCIDENT", "INJURY", "EQUIPMENT_DAMAGE", "HAZARD_OBSERVATION"]
    category: Literal[
        "PERSON_PROXIMITY", "SLIP_FALL", "ROLLOVER_RISK", "POWER_LINE", "UTILITY_STRIKE", "OTHER"
    ]
    severity_self: int = Field(ge=1, le=3)
    transcript: str | None = Field(default=None, max_length=5000)
    machine_id: str | None = None
    create_hazard: CreateHazard | None = None


def _ext(upload: UploadFile, allowed: dict[str, str]) -> str:
    ctype = (upload.content_type or "").split(";")[0].strip().lower()
    if ctype not in allowed:
        raise HTTPException(415, f"{upload.filename!r}: unsupported type {ctype or 'none'}")
    return allowed[ctype]


async def _save(upload: UploadFile, dest: Path, max_bytes: int) -> None:
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"{upload.filename!r} is over {max_bytes} bytes")
    await asyncio.to_thread(dest.write_bytes, data)


def _summary(session: Session, machine_id: str, now: datetime) -> dict:
    since = to_site_iso(now - timedelta(seconds=SNAPSHOT_S))
    rows = session.exec(
        select(TelemetrySample)
        .where(TelemetrySample.machine_id == machine_id, TelemetrySample.ts >= since)
        .order_by(TelemetrySample.ts)
    ).all()

    def vals(name):
        return [getattr(r, name) for r in rows if getattr(r, name) is not None]

    swing, speed, prox = (
        vals("swing_rate_deg_s"),
        vals("travel_speed_kmh"),
        vals("proximity_min_dist_m"),
    )
    nearest = min(
        (r for r in rows if r.proximity_min_dist_m is not None),
        key=lambda r: r.proximity_min_dist_m,
        default=None,
    )
    last = rows[-1] if rows else None
    return {
        "samples": len(rows),
        "from": rows[0].ts if rows else None,
        "to": last.ts if last else None,
        "activity": last.machine_activity if last else None,
        "max_abs_swing_deg_s": max((abs(v) for v in swing), default=None),
        "max_travel_kmh": max(speed, default=None),
        "proximity_min_dist_m": min(prox, default=None),
        "sector": nearest.proximity_sector if nearest else None,
        "x_m": last.x_m if last else None,
        "y_m": last.y_m if last else None,
    }


def _store(session: Session, incident: dict, pin: dict | None, machine_id, now) -> dict:
    if machine_id is not None:
        since = to_site_iso(now - timedelta(seconds=LINK_ALERTS_S))
        incident["linked_alert_ids"] = list(
            session.exec(
                select(SafetyAlert.alert_id).where(
                    SafetyAlert.machine_id == machine_id, SafetyAlert.ts >= since
                )
            ).all()
        )
        incident["state_snapshot"]["last_60s"] = _summary(session, machine_id, now)
    if pin is not None:
        hazards.insert(session, pin)
        session.flush()  # the incident's FK points at the pin
        incident["hazard_pin_id"] = pin["pin_id"]
    row = Incident(**incident)
    repo.insert_incident(session, row)
    return row.model_dump()


@router.post("/incidents", status_code=201)
async def create_incident(
    request: Request,
    incident: str = Form(..., description="JSON: type, category, severity_self, …"),
    voice: UploadFile | None = File(None),
    photos: list[UploadFile] = File(default=[]),
    who: Principal = Depends(require("operator")),
) -> dict:
    rt = request.app.state.rt
    try:
        body = IncidentIn.model_validate(json.loads(incident))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, f"incident: {exc}") from exc
    if len(photos) > MAX_PHOTOS:
        raise HTTPException(422, f"at most {MAX_PHOTOS} photos")
    voice_ext = _ext(voice, VOICE_TYPES) if voice is not None else None
    photo_exts = [_ext(p, PHOTO_TYPES) for p in photos]

    mid = machine_for(rt, who, body.machine_id) if (body.machine_id or who.machine_id) else None
    now = rt.clock()
    engine = rt.runners[mid].engine if mid else None
    x = y = None  # auto-filled only from a live position fix (I6)
    if engine is not None and engine.ctx.sensor_health(now, engine.policy)["position"] == "OK":
        x, y = engine.ctx.sig("x_m"), engine.ctx.sig("y_m")

    pin, hazard_error = None, None
    if body.create_hazard is not None:
        if x is None or y is None:
            hazard_error = "position unknown — incident saved without a hazard pin"
        else:
            pin_body = validate_pin(
                {
                    "type": body.create_hazard.type,
                    "geometry": {"type": "Point", "coordinates": [x, y]},
                    "radius_m": body.create_hazard.radius_m,
                },
                rt.ruleset.hazards,
            )
            pin = hazards.new_pin(rt.site_id, pin_body, who.sub, now, rt.ruleset.hazards)

    incident_id = uuid7()
    media_root = Path(rt.settings.media_dir)
    folder = media_root / incident_id
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    voice_path, photo_paths = None, []
    try:
        if voice is not None:
            await _save(voice, folder / f"voice{voice_ext}", rt.settings.media_max_bytes)
            voice_path = f"{incident_id}/voice{voice_ext}"
        for i, (photo, ext) in enumerate(zip(photos, photo_exts, strict=True), start=1):
            await _save(photo, folder / f"photo_{i}{ext}", rt.settings.media_max_bytes)
            photo_paths.append(f"{incident_id}/photo_{i}{ext}")
        record = {
            "incident_id": incident_id,
            "type": body.type,
            "category": body.category,
            "ts": to_site_iso(now),
            "x_m": x,
            "y_m": y,
            "machine_id": mid,
            "operator_id": engine.ctx.operator_id if engine else None,  # on shift at the time
            "reporter_id": who.sub,
            "voice_note_path": voice_path,
            "transcript": body.transcript,
            "photo_paths": photo_paths,
            "severity_self": body.severity_self,
            "state_snapshot": {"state": engine.state if engine else None},
            "linked_alert_ids": [],
            "status": "OPEN",
        }
        stored = await rt.write(lambda s: _store(s, record, pin, mid, now))
    except BaseException:
        await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
        raise
    if pin is not None:
        await hazards.publish(rt)
    return {
        "incident": {"schema": "incident.v1", **stored},
        "hazard": pin,
        "hazard_error": hazard_error,
    }
