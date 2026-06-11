"""Auth router: login, logout, current-user.

Implements the error contract from ``app.errors`` and sets the opaque
server-side session cookie (see Dev Notes / architecture for exact flags).
"""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import settings
from app.db.base import get_session
from app.db.models import User
from app.errors import account_blocked, invalid_credentials, too_many_attempts
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


# --- Schemas (snake_case, pydantic v2) -----------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class MeResponse(BaseModel):
    id: int
    email: str
    role: str
    tenant_id: int


class LoginResponse(MeResponse):
    # Where the client should redirect after a successful login.
    home_path: str


def _home_path_for(role: str) -> str:
    """Role → landing surface (AC1)."""
    return "/" if role == "client" else "/admin/users"


def _client_ip(request: Request) -> str:
    """Best-effort client IP for throttling.

    ``X-Forwarded-For`` is client-spoofable, so it is only honored when
    ``trust_forwarded_for`` is set (i.e. a trusted proxy like Caddy populates
    it). Otherwise — and in local dev — use the socket peer so the per-(email,
    IP) throttle can't be bypassed with a forged header.
    """
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_seconds,
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """Verify credentials, open a server-side session, set the cookie."""
    ip = _client_ip(request)
    if auth_service.login_throttle.is_blocked(body.email, ip):
        raise too_many_attempts()

    user = await auth_service.users_repo.get_by_email(session, body.email)

    if user is None:
        # Equalize timing with the real verify path (no user enumeration).
        auth_service.verify_password(auth_service.DUMMY_HASH, body.password)
        auth_service.login_throttle.register_failure(body.email, ip)
        raise invalid_credentials()

    if not auth_service.verify_password(user.password_hash, body.password):
        auth_service.login_throttle.register_failure(body.email, ip)
        raise invalid_credentials()

    # Reveal the blocked state only AFTER the password checks out, so the notice
    # (AC4) reaches the real owner — not anyone enumerating emails.
    if user.is_blocked:
        raise account_blocked()

    auth_service.login_throttle.reset(body.email, ip)
    auth_session = await auth_service.create_session(session, user)
    await session.commit()
    _set_session_cookie(response, auth_session.token)

    return LoginResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        home_path=_home_path_for(user.role),
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke the current session and clear the cookie."""
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await auth_service.revoke_session(session, token)
        await session.commit()
    response = Response(status_code=204)
    response.delete_cookie(
        key=settings.session_cookie_name, path="/", samesite="lax"
    )
    return response


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)) -> MeResponse:
    """Return the authenticated user; 401 when unauthenticated."""
    return MeResponse(
        id=user.id, email=user.email, role=user.role, tenant_id=user.tenant_id
    )
