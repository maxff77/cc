import pytest

from app.core.display_transform import display_transform


@pytest.mark.parametrize(
    "text,cookie_mode,expected",
    [
        # Approved — exact production shape: keep CC, drop Response/Removed
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido",
            True,
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅",
        ),
        # Approved + Time — Time is dropped too (only CC + Status kept)
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅\n⌿ Time: 32.95s",
            True,
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅",
        ),
        # Declined WITH a CC line → keep CC + status
        (
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente.\n⌿ Time: 28.14s",
            True,
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌",
        ),
        # Declined with NO CC line → just the status line
        (
            "⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente / datos inválidos. | Removed: ✅ Removido",
            True,
            "⌿ Status: Declined ❌",
        ),
        # Inline ☇/⌿ separators (single line) → CC terminates before Status
        (
            "☇ CC: 123|01|2030|456 ⌿ Status: Approved ✅ ⌿ Response: ok",
            True,
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
        ),
        # Approved with no CC line at all → just the status line
        (
            "⌿ Status: Approved ✅ trailing junk here",
            True,
            "⌿ Status: Approved ✅",
        ),
        # Near-miss token (NOT exact "approved") → engine treats as dead cookie,
        # so display must NOT collapse it; pass through raw.
        (
            "☇ CC: 1|01|2030|2\n⌿ Status: Approvedance ✅\n⌿ Time: 1s",
            True,
            "☇ CC: 1|01|2030|2\n⌿ Status: Approvedance ✅\n⌿ Time: 1s",
        ),
        # cookie error / anything else → pass through raw
        (
            "⌿ Status: ❌ Cookies Inválidas",
            True,
            "⌿ Status: ❌ Cookies Inválidas",
        ),
        # Non-cookie-mode session → raw (keys off cookie_mode, not gate name)
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
