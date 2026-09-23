"""Offline auth (HLD §4.14, D6; plan.md Phase 4 minimal — Phase 5 adds guests and shift-bound
expiry). Badge QR = `employee_code`; PIN checked with bcrypt against the cached roster; an
HS256 JWT signed with `EDGE_JWT_SECRET`. Roles come from `operator.role` in the DB.

`AUTH_DISABLED=true` (P3 development only) makes every request an admin; the runtime logs
it at WARNING on start and `/system/status` reports it.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlmodel import Session, select

from common.db.models import Operator
from common.timeutil import now_utc, to_site_iso

# admin ⊇ supervisor ⊇ operator (HLD §4.14 roles)
ROLE_RANK = {"operator": 0, "supervisor": 1, "admin": 2}
ALGORITHM = "HS256"

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str
    machine_id: str | None = None

    def at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[role]

    def may_see(self, machine_id: str) -> bool:
        """An operator token bound to a machine only sees that machine."""
        return self.at_least("supervisor") or self.machine_id in (None, machine_id)


def principal_from_token(settings, token: str | None) -> Principal:
    if settings.auth_disabled:
        return Principal("auth-disabled", "admin")
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(token, settings.edge_jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc
    return Principal(claims["sub"], claims["role"], claims.get("machine_id"))


def require(min_role: str):
    def dep(
        request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
    ) -> Principal:
        p = principal_from_token(request.app.state.rt.settings, creds and creds.credentials)
        if not p.at_least(min_role):
            raise HTTPException(403, f"requires role {min_role}")
        return p

    return dep


class LoginRequest(BaseModel):
    badge_id: str
    pin: str
    machine_id: str | None = None


class OperatorOut(BaseModel):
    operator_id: str
    name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    expires_at: str
    operator: OperatorOut
    shift: dict | None = None  # Phase 5


def _lookup(rt, badge_id: str) -> Operator | None:
    with Session(rt.db) as session:
        return session.exec(select(Operator).where(Operator.employee_code == badge_id)).first()


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    rt = request.app.state.rt
    if body.machine_id is not None and body.machine_id not in rt.runners:
        raise HTTPException(404, f"unknown machine {body.machine_id}")
    op = await asyncio.to_thread(_lookup, rt, body.badge_id)
    ok = op is not None and await asyncio.to_thread(
        bcrypt.checkpw, body.pin.encode(), op.pin_hash.encode()
    )
    if not ok:
        raise HTTPException(401, "unknown badge or wrong PIN")
    exp = now_utc() + timedelta(seconds=rt.settings.jwt_ttl_s)
    claims = {"sub": op.operator_id, "role": op.role, "machine_id": body.machine_id, "exp": exp}
    token = jwt.encode(claims, rt.settings.edge_jwt_secret or "auth-disabled", ALGORITHM)
    return LoginResponse(
        token=token,
        expires_at=to_site_iso(exp),
        operator=OperatorOut(operator_id=op.operator_id, name=op.name, role=op.role),
    )
