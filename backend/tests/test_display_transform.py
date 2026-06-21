import pytest

from app.core.display_transform import display_transform


@pytest.mark.parametrize(
    "text,cookie_mode,expected",
    [
        # Approved — drop ONLY the Response line, keep CC + Status
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido",
            True,
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅",
        ),
        # Full production shape — keep Gate / Total Time / dividers / blank line,
        # drop only the Response line
        (
            "· · · · · · · · · · · · · · ·\n\n☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido\n· · · · · · · · · · · · · · ·\n⌿ Gate: Amazon MX\n⌿ Total Time: 8's",
            True,
            "· · · · · · · · · · · · · · ·\n\n☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n· · · · · · · · · · · · · · ·\n⌿ Gate: Amazon MX\n⌿ Total Time: 8's",
        ),
        # Approved + Time — Response dropped, Time KEPT
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅\n⌿ Time: 32.95s",
            True,
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Time: 32.95s",
        ),
        # Declined WITH a CC line → drop Response, keep CC + Status + Time
        (
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente.\n⌿ Time: 28.14s",
            True,
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌\n⌿ Time: 28.14s",
        ),
        # Declined with the Response as the trailing line → just what's left
        (
            "⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente / datos inválidos. | Removed: ✅ Removido",
            True,
            "⌿ Status: Declined ❌",
        ),
        # Inline ☇/⌿ separators (single line) → drop the inline Response segment,
        # keep the rest inline
        (
            "☇ CC: 123|01|2030|456 ⌿ Status: Approved ✅ ⌿ Response: ok",
            True,
            "☇ CC: 123|01|2030|456 ⌿ Status: Approved ✅",
        ),
        # Approved with no Response field → untouched
        (
            "⌿ Status: Approved ✅ trailing junk here",
            True,
            "⌿ Status: Approved ✅ trailing junk here",
        ),
        # Near-miss token (NOT exact "approved") → engine treats as dead cookie,
        # so display must NOT touch it; pass through raw.
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
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅\n⌿ Response: ok",
            False,
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅\n⌿ Response: ok",
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
