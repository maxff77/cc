"""Idempotent owner bootstrap (AC1).

There is no product UI to create the first owner, so this run-once script seeds
it on deploy (Story 1.7 runs it on the VPS). NOT an API route — never expose an
owner-creation path over HTTP.

Owner credentials are read from ``os.environ`` directly (with argv overrides for
local use) so they NEVER live in ``app.config.Settings`` / the app process.

Usage (from backend/, venv active):
    OWNER_EMAIL=owner@cc.local OWNER_PASSWORD=secret python -m scripts.bootstrap_owner
    python -m scripts.bootstrap_owner EMAIL PASSWORD            # argv overrides env

Re-running with an existing email updates that user's password and ensures
``role="owner"`` — it never errors or duplicates.
"""

import asyncio
import os
import sys

from app.db.base import async_session_factory
from app.db.models import Tenant, User
from app.db.repos import users as users_repo
from app.services.auth import hash_password


async def bootstrap(email: str, password: str) -> None:
    email = email.lower()  # canonical storage — login looks up case-insensitively
    async with async_session_factory() as session:
        user = await users_repo.get_by_email(session, email)
        if user is None:
            # Fresh owner: a dedicated tenant (one tenant per user) + the row.
            tenant = Tenant(name=email)
            session.add(tenant)
            await session.flush()
            user = User(
                tenant_id=tenant.id,
                email=email,
                password_hash=hash_password(password),
                role="owner",
                expires_at=None,  # owner carries no plan
            )
            session.add(user)
            action = "created"
        else:
            # Idempotent re-run: refresh password, ensure the owner role, and
            # clear any plan expiry (an owner carries no plan — without this a
            # promoted client keeps expires_at and gets locked out in 1.4).
            user.password_hash = hash_password(password)
            user.role = "owner"
            user.expires_at = None
            action = "updated"

        await session.commit()
        print(
            f"{action} owner id={user.id} email={email} "
            f"role={user.role} tenant={user.tenant_id}"
        )


def main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OWNER_EMAIL")
    password = (
        sys.argv[2] if len(sys.argv) > 2 else os.environ.get("OWNER_PASSWORD")
    )
    if not email or not password:
        sys.exit(
            "OWNER_EMAIL and OWNER_PASSWORD are required "
            "(env vars or argv[1]/argv[2])."
        )
    asyncio.run(bootstrap(email, password))


if __name__ == "__main__":
    main()
