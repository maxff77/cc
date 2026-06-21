import pytest

from app.core.display_transform import display_transform

_LIVE = (
    "- Status: LIVE 100% ✅\n"
    "- Details: Tarjeta apta / Card successfully linked\n"
    "- Response: Process completed successfully ✅\n"
    "- System: Ranger Validation Engine"
)
_DEAD = (
    "- Status: DEAD ❌\n"
    "- Details: No apta / Not eligible\n"
    "- Response: Delete complete / Eliminación completada ✅\n"
    "- System: Ranger Validation Engine"
)


@pytest.mark.parametrize(
    "text,cookie_mode,expected",
    [
        # Approved → LIVE template, CC card kept
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido",
            True,
            "CC: 377481016137504|05|2033|3845\n" + _LIVE,
        ),
        # Full production shape — Gate/Total Time/dividers all replaced by the
        # template; only the CC card survives
        (
            "· · · · · · · · · · · · · · ·\n\n☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido\n· · · · · · · · · · · · · · ·\n⌿ Gate: Amazon MX\n⌿ Total Time: 8's",
            True,
            "CC: 377481016137504|05|2033|3845\n" + _LIVE,
        ),
        # Declined WITH a CC line → DEAD template
        (
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente.\n⌿ Time: 28.14s",
            True,
            "CC: 377481016138023|05|2033|7050\n" + _DEAD,
        ),
        # Declined with no CC line → DEAD template, bare "CC:" line
        (
            "⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente / datos inválidos. | Removed: ✅ Removido",
            True,
            "CC:\n" + _DEAD,
        ),
        # Inline ☇/⌿ separators (single line) → card stripped clean of the welded
        # separator, LIVE template
        (
            "☇ CC: 123|01|2030|456 ⌿ Status: Approved ✅ ⌿ Response: ok",
            True,
            "CC: 123|01|2030|456\n" + _LIVE,
        ),
        # Approved with no Response field → still rewritten (verdict is the trigger)
        (
            "☇ CC: 1|01|2030|2\n⌿ Status: Approved ✅ trailing junk here",
            True,
            "CC: 1|01|2030|2\n" + _LIVE,
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
