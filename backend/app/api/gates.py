"""Gates router: read-only catalog for authenticated users (Story 2.1).

`/api/gates` feeds the gate selector (Story 2.2's HeroUI Select — UX-DR9:
never free text). Any authenticated, non-expired, non-blocked role may read;
curation (CRUD) is owner-only and lives in the admin router.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import GateListResponse, _gate_to_out
from app.api.deps import get_current_user
from app.db.base import get_session
from app.db.models import User
from app.db.repos import gates as gates_repo

router = APIRouter(prefix="/api/gates", tags=["gates"])


@router.get("", response_model=GateListResponse)
async def list_gates(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GateListResponse:
    """List active catalog entries (read-only — clients pick, never type)."""
    gates = await gates_repo.list_active(session)
    return GateListResponse(items=[_gate_to_out(g) for g in gates], total=len(gates))
