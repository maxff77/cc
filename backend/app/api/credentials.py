"""Credentials router (personal credential vault): store, list, pick-random and
delete email+password entries.

Auth is a SINGLE shared API key (owner choice): every endpoint requires the
header ``X-Api-Key`` matching ``settings.credentials_api_key``. There is no
session/login here. All rows live under one dedicated "vault" tenant resolved by
``_vault_tenant_id`` — this vault is single-user by design.

🔒 Security contract:
- The key is compared in constant time; a missing/wrong key is a generic 401
  ``invalid_api_key``. With no key configured the vault is closed (503).
- ``password`` is stored PLAINTEXT (CC / gate_cookies precedent) and, by owner
  request, IS echoed back (POST + GET) so the holder can read their saved
  passwords. Every read carries ``Cache-Control: no-store``.
- Value validation is raised INSIDE the handler (never a pydantic validator on
  the password) so the secret can't surface in a default 422 body or access log.

The router owns the transaction; queries are inline (single small table).
"""

import re
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import async_session_factory, get_session
from app.db.models import Credential, Tenant
from app.errors import (
    api_key_not_configured,
    credential_not_found,
    invalid_api_key,
    invalid_credential,
)

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

_EMAIL_MAX = 320
_PASSWORD_MAX = 1024
_LIST_LIMIT = 200
_PG_INT_MAX = 2**31 - 1  # ids son int4; binds mayores desbordan asyncpg
# Validación pragmática (no RFC): un @, sin espacios, dominio con punto. Se
# valida in-handler como el resto, para no filtrar el valor en un 422.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# El vault es de una sola "cuenta" lógica (key global) — todas las filas cuelgan
# de este tenant dedicado, creado on-demand la primera vez.
_VAULT_TENANT_NAME = "api-key-vault"
_vault_tenant_id_cache: int | None = None


async def _vault_tenant_id() -> int:
    """Resuelve (get-or-create) el tenant del vault y cachea su id.

    ponytail: get-or-create simple en su propia transacción; si dos requests
    cruzan la primera creación podrían nacer dos tenants "api-key-vault" — vault
    de un solo usuario, riesgo nulo en la práctica. Añadir UNIQUE(name) si algún
    día importa.
    """
    global _vault_tenant_id_cache
    if _vault_tenant_id_cache is not None:
        return _vault_tenant_id_cache
    async with async_session_factory() as db:
        tid = (
            await db.execute(
                select(Tenant.id).where(Tenant.name == _VAULT_TENANT_NAME)
            )
        ).scalar_one_or_none()
        if tid is None:
            tenant = Tenant(name=_VAULT_TENANT_NAME)
            db.add(tenant)
            await db.flush()
            tid = tenant.id
            await db.commit()
    _vault_tenant_id_cache = tid
    return tid


async def require_api_key(request: Request) -> int:
    """Dependency: validate ``X-Api-Key`` and return the vault tenant id.

    No key configured → 503; missing/wrong key → 401 (constant-time compare).
    """
    expected = settings.credentials_api_key
    if not expected:
        raise api_key_not_configured()
    provided = request.headers.get("X-Api-Key")
    if not provided or not secrets.compare_digest(provided, expected):
        raise invalid_api_key()
    return await _vault_tenant_id()


class CreateCredentialRequest(BaseModel):
    # password is validated in the HANDLER, never here — a pydantic validator
    # would leak the rejected secret into a default 422 body.
    email: str
    password: str


class CredentialOut(BaseModel):
    """Visible entry — includes ``password`` (plaintext, by owner request)."""

    id: int
    email: str
    password: str
    used: bool
    created_at: datetime


def _to_out(c: Credential) -> CredentialOut:
    return CredentialOut(
        id=c.id, email=c.email, password=c.password, used=c.used, created_at=c.created_at
    )


@router.post("", response_model=CredentialOut, status_code=201)
async def store_credential(
    body: CreateCredentialRequest,
    tenant_id: int = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> CredentialOut:
    """Store one email+password entry."""
    email = body.email.strip()
    password = body.password.strip()
    if (
        not email
        or len(email) > _EMAIL_MAX
        or not _EMAIL_RE.match(email)
        or not password
        or len(password) > _PASSWORD_MAX
    ):
        raise invalid_credential()
    cred = Credential(tenant_id=tenant_id, email=email, password=password)
    session.add(cred)
    await session.flush()
    await session.commit()
    return _to_out(cred)


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    response: Response,
    tenant_id: int = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> list[CredentialOut]:
    """List every entry, OLDEST first (includes passwords; no-store)."""
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            select(Credential)
            .where(Credential.tenant_id == tenant_id)
            .order_by(Credential.id.asc())
            .limit(_LIST_LIMIT)
        )
    ).scalars().all()
    return [_to_out(c) for c in rows]


@router.get("/oldest", response_model=CredentialOut)
async def oldest_credential(
    response: Response,
    tenant_id: int = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> CredentialOut:
    """Return the OLDEST entry (id, email, password) — FIFO. Empty vault → 404.

    ``id`` is the monotonic serial PK, so ``id ASC`` is creation order (the same
    tie-immune ordering the Limpiar cutoff relies on). The holder can delete it
    by its id afterward and the next call returns the following one.
    """
    response.headers["Cache-Control"] = "no-store"
    cred = (
        await session.execute(
            select(Credential)
            .where(Credential.tenant_id == tenant_id)
            .order_by(Credential.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cred is None:
        raise credential_not_found()
    return _to_out(cred)


@router.delete("/by-email", status_code=204)
async def delete_by_email(
    email: str,
    tenant_id: int = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete every entry whose email matches ``email`` (query param).

    No match (incl. empty/garbage email) → 404 ``credential_not_found``.
    """
    result = await session.execute(
        delete(Credential).where(
            Credential.tenant_id == tenant_id,
            Credential.email == email.strip(),
        )
    )
    if (getattr(result, "rowcount", 0) or 0) == 0:
        raise credential_not_found()
    await session.commit()


@router.delete("/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: int,
    tenant_id: int = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete one entry by id. Unknown / oversized id → 404 (no existence leak)."""
    if not 0 < credential_id <= _PG_INT_MAX:
        raise credential_not_found()
    result = await session.execute(
        delete(Credential).where(
            Credential.tenant_id == tenant_id,
            Credential.id == credential_id,
        )
    )
    if (getattr(result, "rowcount", 0) or 0) == 0:
        raise credential_not_found()
    await session.commit()
