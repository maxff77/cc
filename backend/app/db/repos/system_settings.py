"""Data access for owner-tunable runtime settings (Story 4.2).

GLOBAL — this IS one of the documented cross-tenant exceptions (like gates):
system knobs have no tenant. Values are short strings; parsing/validation
belongs to the owning service (``services/admission`` for the cap).

Pure ORM-ish, flush not commit — callers own the transaction. The upsert uses
the PostgreSQL ``INSERT … ON CONFLICT`` construct on purpose: a plain
read-then-write would race two concurrent PUTs into a PK violation (the 2.1
TOCTOU lesson, solved here in one statement instead of a catch-and-retry).
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemSetting


async def get_value(session: AsyncSession, key: str) -> str | None:
    """Read one setting's raw value (``None`` when the row doesn't exist)."""
    stmt = select(SystemSetting.value).where(SystemSetting.key == key)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_value_for_update(session: AsyncSession, key: str) -> str | None:
    """Read one setting locking its row until commit (FOR UPDATE).

    The admission paths (POST /api/batches and the worker's promotion sweep)
    both lock the cap row before counting admitted senders — that lock is the
    serializer that prevents two concurrent decisions from overshooting the
    cap. A missing row takes no lock — and means "disabled", where there is
    no decision to protect.
    """
    stmt = (
        select(SystemSetting.value)
        .where(SystemSetting.key == key)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    """Upsert one setting (insert-or-update in a single race-free statement)."""
    stmt = (
        insert(SystemSetting)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=[SystemSetting.key],
            # Core statement — the ORM-level ``onupdate`` doesn't fire here.
            set_={"value": value, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
