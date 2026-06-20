import pytest

from app.core.display_transform import display_transform


@pytest.mark.parametrize(
    "text,gate_name,expected",
    [
        # Approved + time
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅\n⌿ Time: 32.95s",
            "amz",
            "◈ Aprobada ✅ — 377481016137504|05|2033|3845 · 32.95s\n▸ TARJETA VINCULADA LIVE 🌟",
        ),
        # Declined + time
        (
            "☇ CC: 377481016138023|05|2033|7050\n⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente.\n⌿ Time: 28.14s",
            "amz",
            "◈ Rechazada ❌ — 377481016138023|05|2033|7050 · 28.14s\n▸ TARJETA INVALIDA DEAD ➕",
        ),
        # Approved no time
        (
            "☇ CC: 377481016137504|05|2033|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. | Removed: ✅",
            "amz",
            "◈ Aprobada ✅ — 377481016137504|05|2033|3845\n▸ TARJETA VINCULADA LIVE 🌟",
        ),
        # cookie_dead
        (
            "⌿ Status: ❌ Cookies Inválidas",
            "amz",
            "◈ No procesada ❌\n▸ COOKIE MUERTA ❌",
        ),
        # Non-AMZ gate
        (
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
            "zephyr",
            "☇ CC: 123|01|2030|456\n⌿ Status: Approved ✅",
        ),
        # gate_name None
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
        # No parseable structure
        (
            "☇ some random text without structure",
            "amz",
            "some random text without structure",
        ),
    ],
)
def test_display_transform(text, gate_name, expected):
    assert display_transform(text, gate_name) == expected
