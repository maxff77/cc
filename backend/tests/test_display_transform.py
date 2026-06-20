import pytest

from app.core.display_transform import display_transform


@pytest.mark.parametrize(
    "text,cookie_mode,expected",
    [
        # Approved (full reply) → canonical bare status line
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅\n⌿ Time: 32.95s",
            True,
            "⌿ Status: Approved ✅",
        ),
        # Declined (full reply) → canonical bare status line
        (
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente.\n⌿ Time: 28.14s",
            True,
            "⌿ Status: Declined ❌",
        ),
        # Approved, no time
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅",
            True,
            "⌿ Status: Approved ✅",
        ),
        # Inline ☇/⌿ separators (single line) still classify correctly
        (
            "☇ CC: 123|01|2030|456 ⌿ Status: Approved ✅ ⌿ Response: ok",
            True,
            "⌿ Status: Approved ✅",
        ),
        # Trailing content after the glyph is dropped — output stays bare
        (
            "⌿ Status: Approved ✅ trailing junk here",
            True,
            "⌿ Status: Approved ✅",
        ),
        # Near-miss token (NOT exact "approved") → engine treats as dead cookie,
        # so display must NOT collapse it to Approved; pass through raw.
        (
            "⌿ Status: Approvedance ✅\n⌿ Time: 1s",
            True,
            "⌿ Status: Approvedance ✅\n⌿ Time: 1s",
        ),
        (
            "⌿ Status: Declinedxyz ❌",
            True,
            "⌿ Status: Declinedxyz ❌",
        ),
        # cookie error / anything else → pass through raw
        (
            "⌿ Status: ❌ Cookies Inválidas",
            True,
            "⌿ Status: ❌ Cookies Inválidas",
        ),
        # Non-cookie-mode session → raw (this is what made the old gate-name
        # guard a no-op: the transform now keys off cookie_mode, not the name).
        (
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
            False,
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
        ),
        # Empty text
        (
            "",
            True,
            "",
        ),
        # No status field → pass through raw (untouched)
        (
            "☇ some random text without structure",
            True,
            "☇ some random text without structure",
        ),
    ],
)
def test_display_transform(text, cookie_mode, expected):
    assert display_transform(text, cookie_mode) == expected
