"""Gates router: read-only catalog for authenticated users (Story 2.1).

`/api/gates` feeds the gate selector (Story 2.2's HeroUI Select — UX-DR9:
never free text). Any authenticated, non-expired, non-blocked role may read;
curation (CRUD) is owner-only and lives in the admin router.

CLIENT-SAFE SHAPE: this public endpoint exposes ``display_value`` (the
owner-authored "Comando visible") and NEVER the real ``value`` (the command the
engine sends). Clients pick a gate by ``id``; the server resolves the real
``value`` from the catalog at send time (api/batches.py). The owner-only
``GateOut`` (with ``value``) lives in the admin router.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.base import get_session
from app.db.models import Gate, User
from app.db.repos import gates as gates_repo

router = APIRouter(prefix="/api/gates", tags=["gates"])


class PublicGateOut(BaseModel):
    """Client-visible gate — deliberately WITHOUT the real ``value``."""

    id: int
    name: str
    display_value: str
    # Credits charged per captured ✅ (credits feature). Client-safe to show:
    # the cockpit displays the cost and blocks a costed gate at balance 0. 0 ⇒
    # free gate.
    credit_cost: int
    category_id: int
    category_name: str
    created_at: datetime


class PublicGateListResponse(BaseModel):
    items: list[PublicGateOut]
    total: int


def gate_to_public_out(gate: Gate) -> PublicGateOut:
    """Gate → PublicGateOut (no ``value``). Requires ``gate.category`` eagerly
    loaded (``selectinload`` / ``refresh``) — an async lazy-load would raise."""
    return PublicGateOut(
        id=gate.id,
        name=gate.name,
        display_value=gate.display_value,
        credit_cost=gate.credit_cost,
        category_id=gate.category_id,
        category_name=gate.category.name,
        created_at=gate.created_at,
    )


@router.get("", response_model=PublicGateListResponse)
async def list_gates(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PublicGateListResponse:
    """List active catalog entries (read-only — clients pick, never type).

    The real command (``value``) is intentionally omitted from this response.
    """
    gates = await gates_repo.list_active(session)
    return PublicGateListResponse(
        items=[gate_to_public_out(g) for g in gates], total=len(gates)
    )
