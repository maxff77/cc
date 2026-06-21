"""``core.redact`` — the operator "Checked By" line must never survive, the
``Credits:`` segment is scrubbed everywhere, and special-mode strips the
Approveds!/Deads! stats."""

from app.core.redact import (
    parse_approveds,
    redact_reply_text,
    strip_special_stats,
)

_NAME_LINE = "⌿ Checked By : Richard [User]"

# The canonical special-mode stats line (the user's false-positive example).
_STATS = "↳ Approveds! ✅: 0 ヾ⌿ Deads! ❌: 1 ヾ⌿ Credits: 999996044 ヾ⌿ Time: 32.95s"


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


# --- Credits scrub (GLOBAL — every category) --------------------------------


def test_credits_segment_removed_globally():
    # Non-special path: redact removes Credits but KEEPS Approveds!/Deads!.
    out = redact_reply_text(_STATS)
    assert "Credits" not in out
    assert "999996044" not in out
    assert "Approveds! ✅: 0" in out
    assert "Deads! ❌: 1" in out
    assert "Time: 32.95s" in out
    # The separator a removed middle segment leaves is collapsed, not doubled.
    assert "ヾ⌿ ヾ⌿" not in out


def test_credits_scrub_on_a_plain_line():
    assert "Credits" not in redact_reply_text("Credits: 42")


def test_credits_scrub_is_idempotent():
    once = redact_reply_text(_STATS)
    assert redact_reply_text(once) == once


# --- Special-mode stripping + validity --------------------------------------


def test_parse_approveds_reads_the_count():
    assert parse_approveds(_STATS) == 0
    assert parse_approveds("↳ Approveds! ✅: 3 ヾ⌿ Deads! ❌: 0") == 3


def test_parse_approveds_none_when_absent():
    # No Approveds line yet ⇒ the legacy ⏳ intermediate state.
    assert parse_approveds("⏳ procesando…") is None


def test_special_strip_drops_stats_keeps_time():
    # Capture applies redact (Credits gone) THEN strip_special_stats.
    out = strip_special_stats(redact_reply_text(_STATS))
    assert out == "↳ Time: 32.95s"
    for token in ("Approveds", "Deads", "Credits", "999996044", "✅", "❌"):
        assert token not in out


def test_special_strip_is_idempotent():
    out = strip_special_stats(redact_reply_text(_STATS))
    assert strip_special_stats(out) == out


def test_special_strip_leaves_non_stats_text_alone():
    text = "✅ Approved\nCC: 4111111111111111|12|2026|123"
    assert strip_special_stats(text) == text


# --- Dot-divider scrub (GLOBAL — every gateway) -----------------------------

# A typical gateway reply: dot dividers wrap the CC block (Checked By redacted).
_REPLY = (
    "Amazon MX\n"
    "· · · · · · · · · · · · · · ·\n"
    "\n"
    "☇ CC: 377481016137504|05|2033|3845\n"
    "⌿ Status: Approved ✅\n"
    "⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido\n"
    "· · · · · · · · · · · · · · ·\n"
    "⌿ Gate: Amazon MX\n"
    "⌿ Total Time: 8's\n"
    f"{_NAME_LINE}"
)


def test_dot_dividers_removed_data_lines_kept():
    out = redact_reply_text(_REPLY)
    assert "·" not in out  # every decorative dot is gone
    # The real fields survive verbatim.
    for line in (
        "☇ CC: 377481016137504|05|2033|3845",
        "⌿ Status: Approved ✅",
        "⌿ Gate: Amazon MX",
        "⌿ Total Time: 8's",
    ):
        assert line in out
    # Removing a divider leaves a single blank gap, never a triple newline.
    assert "\n\n\n" not in out


def test_dot_divider_needs_two_dots():
    # A lone middle dot inside real content is NOT a divider — left untouched.
    assert redact_reply_text("a · b") == "a · b"


def test_dot_divider_scrub_is_idempotent():
    once = redact_reply_text(_REPLY)
    assert redact_reply_text(once) == once
