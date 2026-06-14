"""Owner-only management of the Telegram send-target list (multi-target sending).

Mirrors the gate CRUD (Story 2.1): a GLOBAL catalog whose authorization is the
``require_owner`` dependency, never the request body. Every mutation reloads the
gateway LIVE so the change takes effect without a process restart. Discovery
(`/targets/discover`) lists the account's chats so the owner can pick private
groups that have no @username.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.telegram import gateway
from app.db.base import get_session
from app.db.models import SendTarget, User
from app.db.repos import targets as targets_repo
from app.errors import (
    telegram_target_exists,
    telegram_target_not_found,
    telegram_target_unresolvable,
    telegram_unauthorized,
)
from app.services import targets as targets_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])
require_owner = require_role("owner")

_PG_INT_MAX = 2**31 - 1  # send_targets.id is int4
_PG_BIGINT_MAX = 2**63 - 1  # chat_id is int8


# --- Schemas -----------------------------------------------------------------


class TargetOut(BaseModel):
    id: int
    chat_id: int
    label: str
    enabled: bool
    resolved: bool  # live: does the gateway currently have this chat resolved?
    created_at: datetime


class TargetListResponse(BaseModel):
    items: list[TargetOut]
    total: int


class DiscoveredChat(BaseModel):
    chat_id: int
    title: str


class CreateTargetRequest(BaseModel):
    chat_id: int
    label: str

    @field_validator("label")
    @classmethod
    def _valid_label(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 80:
            raise ValueError("label debe tener entre 1 y 80 caracteres")
        return v

    @field_validator("chat_id")
    @classmethod
    def _valid_chat_id(cls, v: int) -> int:
        if not -_PG_BIGINT_MAX <= v <= _PG_BIGINT_MAX:
            raise ValueError("chat_id fuera de rango")
        return v


class UpdateTargetRequest(BaseModel):
    enabled: bool


def _to_out(target: SendTarget, *, resolved: bool) -> TargetOut:
    return TargetOut(
        id=target.id,
        chat_id=target.chat_id,
        label=target.label,
        enabled=target.enabled,
        resolved=resolved,
        created_at=target.created_at,
    )


async def _require_target(session: AsyncSession, target_id: int) -> SendTarget:
    """Resolve a target row or raise 404 (out-of-int4 ids can't exist).

    ``FOR UPDATE`` mirrors the gate edit path: toggle/delete are
    read-modify-write, so concurrent mutations serialize.
    """
    if not 0 < target_id <= _PG_INT_MAX:
        raise telegram_target_not_found()
    target = await targets_repo.get_by_id(session, target_id, for_update=True)
    if target is None:
        raise telegram_target_not_found()
    return target


# --- Routes ------------------------------------------------------------------


@router.get("/targets", response_model=TargetListResponse)
async def list_targets(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> TargetListResponse:
    """List every send target with its live resolution status (owner view)."""
    rows = await targets_service.list_with_status(session)
    return TargetListResponse(
        items=[_to_out(t, resolved=r) for t, r in rows], total=len(rows)
    )


@router.get("/targets/discover", response_model=list[DiscoveredChat])
async def discover_targets(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> list[DiscoveredChat]:
    """List the account's chats so the owner can pick a destination."""
    try:
        chats = await targets_service.discover()
    except RuntimeError as exc:  # gateway not authorized
        raise telegram_unauthorized() from exc
    return [DiscoveredChat(chat_id=c, title=t) for c, t in chats]


@router.post("/targets", response_model=TargetOut, status_code=201)
async def create_target(
    body: CreateTargetRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> TargetOut:
    """Add a destination; duplicate chat → 409, unresolvable → 422."""
    if not gateway.authorized:
        # Session down → 503 (retry later), NOT 422 — the chat may be perfectly
        # valid; only resolution is unavailable right now.
        raise telegram_unauthorized()
    if await targets_repo.get_by_chat_id(session, body.chat_id) is not None:
        raise telegram_target_exists()
    # Validate the account can actually reach it before persisting.
    if await gateway.resolve_one(body.chat_id) is None:
        raise telegram_target_unresolvable()
    try:
        target = await targets_repo.create(
            session, chat_id=body.chat_id, label=body.label
        )
        await session.commit()
    except IntegrityError as exc:  # racy duplicate trips uq_send_targets_chat_id
        raise telegram_target_exists() from exc
    await targets_service.reload_gateway(session)
    return _to_out(target, resolved=body.chat_id in gateway.resolved_ids())


@router.patch("/targets/{target_id}", response_model=TargetOut)
async def update_target(
    target_id: int,
    body: UpdateTargetRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> TargetOut:
    """Enable/disable a destination; reloads the gateway live."""
    target = await _require_target(session, target_id)
    target.enabled = body.enabled
    await session.commit()
    await targets_service.reload_gateway(session)
    return _to_out(target, resolved=target.chat_id in gateway.resolved_ids())


@router.delete("/targets/{target_id}", status_code=204)
async def delete_target(
    target_id: int,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a destination; reloads the gateway live."""
    target = await _require_target(session, target_id)
    await targets_repo.delete(session, target)
    await session.commit()
    await targets_service.reload_gateway(session)
