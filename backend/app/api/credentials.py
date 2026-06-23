"""Credentials router (personal credential vault): a tenant stores and lists
its own email+password entries.

🔒 Security contract (mirrors the cookie-vault precedent):
- ``tenant_id`` comes ONLY from the session — never from body/path.
- ``password`` is stored PLAINTEXT (CC / gate_cookies precedent) but NEVER
  echoed to a client (``CredentialOut`` has no ``password`` field) and never
  logged.
- Validation is raised as ``invalid_credential`` (400) INSIDE the handler, not
  via a pydantic validator on the password, so the secret can't surface in a
  default 422 body or an access log.

The router owns the transaction; queries are inline (single small table).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.base import get_session
from app.db.models import Credential, User
from app.errors import invalid_credential

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

_EMAIL_MAX = 320
_PASSWORD_MAX = 1024
_LIST_LIMIT = 200


class CreateCredentialRequest(BaseModel):
    # password is validated in the HANDLER, never here — a pydantic validator
    # would leak the rejected secret into a default 422 body.
    email: str
    password: str


class CredentialOut(BaseModel):
    """Client-visible entry — deliberately WITHOUT ``password``."""

    id: int
    email: str
    used: bool
    created_at: datetime


def _to_out(c: Credential) -> CredentialOut:
    return CredentialOut(id=c.id, email=c.email, used=c.used, created_at=c.created_at)


@router.post("", response_model=CredentialOut, status_code=201)
async def store_credential(
    body: CreateCredentialRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CredentialOut:
    """Store one email+password entry for the tenant."""
    email = body.email.strip()
    password = body.password.strip()
    if not email or len(email) > _EMAIL_MAX or not password or len(password) > _PASSWORD_MAX:
        raise invalid_credential()
    cred = Credential(tenant_id=user.tenant_id, email=email, password=password)
    session.add(cred)
    await session.flush()
    await session.commit()
    return _to_out(cred)


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CredentialOut]:
    """List the tenant's own entries, newest first (passwords omitted)."""
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            select(Credential)
            .where(Credential.tenant_id == user.tenant_id)
            .order_by(Credential.id.desc())
            .limit(_LIST_LIMIT)
        )
    ).scalars().all()
    return [_to_out(c) for c in rows]
