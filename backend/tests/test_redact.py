"""``core.redact`` — the operator "Checked By" line must never survive."""

from app.core.redact import redact_reply_text

_NAME_LINE = "⌿ Checked By : Richard [User]"


def test_drops_the_checked_by_line_keeping_the_rest():
    text = f"✅ Approved\nCC: 4111111111111111|12|2026|123\n{_NAME_LINE}\nStatus: live"
    out = redact_reply_text(text)
    assert "Richard" not in out
    assert "Checked By" not in out
    assert "✅ Approved" in out
    assert "CC: 4111111111111111|12|2026|123" in out
    assert "Status: live" in out


def test_case_and_spacing_insensitive():
    for line in ("⌿ CHECKED BY : Ana", "checked  by: x", "» Checked  By  :  Z [User]"):
        assert redact_reply_text(f"a\n{line}\nb") == "a\nb"


def test_no_match_is_unchanged():
    text = "✅ Approved\nCC: 12345\nStatus: live"
    assert redact_reply_text(text) == text


def test_idempotent():
    text = f"✅\n{_NAME_LINE}\nStatus"
    once = redact_reply_text(text)
    assert redact_reply_text(once) == once


def test_empty_is_safe():
    assert redact_reply_text("") == ""
