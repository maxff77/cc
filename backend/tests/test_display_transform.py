import pytest

from app.core.display_transform import display_transform


@pytest.mark.parametrize(
    "text,gate_name,expected",
    [
        # Approved (full reply) → canonical bare status line
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅\n⌿ Time: 32.95s",
            "amz",
            "⌿ Status: Approved ✅",
        ),
        # Declined (full reply) → canonical bare status line
        (
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente.\n⌿ Time: 28.14s",
            "amz",
            "⌿ Status: Declined ❌",
        ),
        # Approved, no time
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅",
            "amz",
            "⌿ Status: Approved ✅",
        ),
        # Inline ☇/⌿ separators (single line) still classify correctly
        (
            "☇ CC: 123|01|2030|456 ⌿ Status: Approved ✅ ⌿ Response: ok",
            "amz",
            "⌿ Status: Approved ✅",
        ),
        # Trailing content after the glyph is dropped — output stays bare
        (
            "⌿ Status: Approved ✅ trailing junk here",
            "amz",
            "⌿ Status: Approved ✅",
        ),
        # Near-miss token (NOT exact "approved") → engine treats as dead cookie,
        # so display must NOT collapse it to Approved; pass through raw.
        (
            "⌿ Status: Approvedance ✅\n⌿ Time: 1s",
            "amz",
            "⌿ Status: Approvedance ✅\n⌿ Time: 1s",
        ),
        (
            "⌿ Status: Declinedxyz ❌",
            "amz",
            "⌿ Status: Declinedxyz ❌",
        ),
        # cookie error / anything else → pass through raw
        (
            "⌿ Status: ❌ Cookies Inválidas",
            "amz",
            "⌿ Status: ❌ Cookies Inválidas",
        ),
        # Non-AMZ gate → raw
        (
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
            "zephyr",
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
        ),
        # gate_name None → raw
        (
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
            None,
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
        ),
        # Empty text
        (
            "",
            "amz",
            "",
        ),
        # No status field → pass through raw (untouched)
        (
            "☇ some random text without structure",
            "amz",
            "☇ some random text without structure",
        ),
    ],
)
def test_display_transform(text, gate_name, expected):
    assert display_transform(text, gate_name) == expected
