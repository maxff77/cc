"""Dev-only seed: insert a tenant + a login-ready user.

There is no creation UI until Story 1.3, so this script gives an end-to-end
testable account. NOT a product route — never ship an admin/creation path here.

Usage (from backend/, venv active):
    python -m scripts.seed_user                       # client@cc.local / changeme123
    python -m scripts.seed_user EMAIL PASSWORD ROLE   # custom

Re-running with an existing email updates that user's password/role (idempotent).
"""

import asyncio
import sys

from app.db.base import async_session_factory
from app.db.models import Tenant, User
from app.services.auth import hash_password
from sqlalchemy import select

DEFAULT_EMAIL = "client@cc.local"
DEFAULT_PASSWORD = "changeme123"  # noqa: S105 — dev seed only, not a secret
DEFAULT_ROLE = "client"


async def seed(email: str, password: str, role: str) -> None:
    email = email.lower()  # canonical storage — login looks up case-insensitively
    async with async_session_factory() as session:
        # Reuse a tenant if one exists, else create the first one.
        tenant = (
            await session.execute(select(Tenant).limit(1))
        ).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="Seed Tenant")
            session.add(tenant)
            await session.flush()

        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                tenant_id=tenant.id,
                email=email,
                password_hash=hash_password(password),
                role=role,
            )
            session.add(user)
            action = "created"
        else:
            user.password_hash = hash_password(password)
            user.role = role
            action = "updated"

        await session.commit()
        print(f"{action} user id={user.id} email={email} role={role} tenant={tenant.id}")


def main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EMAIL
    password = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PASSWORD
    role = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_ROLE
    asyncio.run(seed(email, password, role))


if __name__ == "__main__":
    main()
