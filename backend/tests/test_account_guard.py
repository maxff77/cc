"""Boot-time Telegram account-identity guard (services/account_guard).

The decision matrix is the security-critical part — a wrong branch means a
cross-tenant leak (LOCKED missed) or a falsely-latched account (OK missed). It
is a pure function, so the matrix test needs no DB/Telethon; two IO tests cover
the wrapper's unauthorized skip and its read→classify wiring.
"""

import pytest
from app.db.base import async_session_factory
from app.db.repos import system_settings as system_settings_repo
from app.services import account_guard


def test_classify_matrix() -> None:
    c = account_guard._classify
    # First boot: no baseline yet → adopt this account as the baseline.
    assert c(None, "111", has_data=False) == account_guard.FIRST_BOOT
    assert c(None, "111", has_data=True) == account_guard.FIRST_BOOT
    # Same account (a plain restart) → proceed, regardless of data.
    assert c("111", "111", has_data=True) == account_guard.OK
    assert c("111", "111", has_data=False) == account_guard.OK
    # Account CHANGED with historical data → fail-closed latch.
    assert c("111", "222", has_data=True) == account_guard.LOCKED
    # Account changed but nothing to mis-attribute → safe to adopt.
    assert c("111", "222", has_data=False) == account_guard.ADOPTED


@pytest.mark.asyncio(loop_scope="session")
async def test_unauthorized_is_skipped() -> None:
    """No id to compare → SKIPPED, no latch, never records a baseline."""
    async with async_session_factory() as session:
        assert (
            await account_guard.decide_account_identity(session, None)
            == account_guard.SKIPPED
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_same_recorded_account_reads_ok() -> None:
    """Wrapper wiring: with the fingerprint already recorded, the SAME account
    reads OK (a plain restart) — deterministic regardless of table data. Stashes
    and restores the real key so the shared test DB's prod baseline is untouched."""
    async with async_session_factory() as session:
        prior = await system_settings_repo.get_value(
            session, account_guard.ACCOUNT_ID_KEY
        )
        try:
            await system_settings_repo.set_value(
                session, account_guard.ACCOUNT_ID_KEY, "424242"
            )
            assert (
                await account_guard.decide_account_identity(session, 424242)
                == account_guard.OK
            )
        finally:
            if prior is not None:
                await system_settings_repo.set_value(
                    session, account_guard.ACCOUNT_ID_KEY, prior
                )
            await session.commit()
