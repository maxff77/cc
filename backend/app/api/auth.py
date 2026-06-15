"""Auth router: login, logout, current-user.

Implements the error contract from ``app.errors`` and sets the opaque
server-side session cookie (see Dev Notes / architecture for exact flags).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_allow_pending_password
from app.config import settings
from app.db.base import get_session
from app.db.models import User
from app.errors import (
    account_blocked,
    forbidden,
    invalid_credentials,
    password_reuse,
    plan_expired,
    too_many_attempts,
)
from app.services import auth as auth_service
from app.services import plans as plans_service

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
    # Plan deadline for the client header badge. Null for owner/admin (they
    # carry no plan); serialized to ISO 8601 for the frontend.
    expires_at: datetime | None = None


class LoginResponse(MeResponse):
    # Where the client should redirect after a successful login.
    home_path: str


class ChangePasswordRequest(BaseModel):
    # Proof of the temp password (1.6 review): a session that survived the
    # reset's bulk revoke must not be able to set the new password.
    current_password: str
    new_password: str

    @field_validator("current_password", "new_password")
    @classmethod
    def _password_max_length(cls, v: str) -> str:
        # Bound the argon2 input — this endpoint has no login-style throttle,
        # so an unbounded body would be a cheap CPU-exhaustion vector.
        if len(v) > 128:
            raise ValueError("contraseña demasiado larga")
        return v

    @field_validator("new_password")
    @classmethod
    def _password_length(cls, v: str) -> str:
        # Same boundary contract as creation (admin.py _PASSWORD_MIN = 8):
        # a short password is a 422.
        if len(v) < 8:
            raise ValueError("contraseña demasiado corta")
        return v


class ChangePasswordResponse(BaseModel):
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

    # The password checked out, so clear the failure counter BEFORE the expiry
    # check — otherwise earlier typos linger and a throttled expired client
    # gets 429 too_many_attempts instead of learning the real plan_expired
    # state (and one later typo would re-trip the 429).
    auth_service.login_throttle.reset(body.email, ip)

    # Reveal expiry only AFTER the password checks out (same reasoning as the
    # blocked check above). No session row is created for an expired client.
    if plans_service.is_plan_expired(user):
        raise plan_expired()

    auth_session = await auth_service.create_session(session, user)
    await session.commit()
    _set_session_cookie(response, auth_session.token)

    # The must_change_password flag never blocks login — it steers it (1.6):
    # a flagged user gets a normal session whose every subsequent request is
    # gated server-side by deps; home_path routes them to the forced screen.
    return LoginResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        expires_at=user.expires_at,
        home_path=(
            "/change-password"
            if user.must_change_password
            else _home_path_for(user.role)
        ),
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
        key=settings.session_cookie_name,
        path="/",
        samesite="lax",
        httponly=True,
        secure=settings.cookie_secure,
    )
    return response


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)) -> MeResponse:
    """Return the authenticated user; 401 when unauthenticated."""
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        expires_at=user.expires_at,
    )


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user_allow_pending_password),
    session: AsyncSession = Depends(get_session),
) -> ChangePasswordResponse:
    """Complete the forced password change (Story 1.6, AC3).

    Serves ONLY the forced flow: a voluntary change is out of MVP scope, hence
    403 when the flag is not set. Hardening from the 1.6 review:

    - ``current_password`` (the temp password) must be proven, so a session
      that survived the reset's bulk revoke (a login racing the reset with the
      OLD credentials) cannot set the new password.
    - The row is re-read ``FOR UPDATE`` so a reset committing concurrently is
      not silently overwritten (lost update), and the flag is re-checked on
      the locked row.
    - Every OTHER session is revoked on success: a second device that logged
      in with the leaked temp password dies the moment the change completes.
      The CURRENT session stays alive (the user continues straight to their
      home surface — no re-login).
    """
    locked = await auth_service.users_repo.get_user_by_id(
        session, user.id, for_update=True
    )
    if locked is None or not locked.must_change_password:
        raise forbidden()
    if not auth_service.verify_password(
        locked.password_hash, body.current_password
    ):
        raise invalid_credentials()
    if auth_service.verify_password(locked.password_hash, body.new_password):
        raise password_reuse()
    locked.password_hash = auth_service.hash_password(body.new_password)
    locked.must_change_password = False
    await auth_service.users_repo.revoke_all_sessions_for_user(
        session,
        locked.id,
        except_token=request.cookies.get(settings.session_cookie_name),
    )
    await session.commit()
    return ChangePasswordResponse(home_path=_home_path_for(locked.role))
