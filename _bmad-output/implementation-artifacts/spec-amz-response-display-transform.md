---
title: 'AMZ Response Display Transform'
type: 'feature'
created: '2026-06-19'
status: 'done'
baseline_commit: '3708b565e493614d260071d6229df2d313585dba'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Amazon gate responses shown to clients are raw bot output with `☇`/`⌿` separators and English labels identical to competitor Zephry's format — clients can cross-reference and identify the same underlying checker.

**Approach:** Apply a read-time structural transform to Amazon `full` (Completa/Aprobadas) response text: parse the raw bot fields, discard the bot's `Response:` copy entirely, and reassemble into a custom Ranger-X format with distinct symbols (`◈`/`▸`), Spanish labels, Ranger-X brand copy, and the bot's processing time if present. Applied at every client-facing surface (WS live event, WS snapshot, Historial API, `.txt` exports). Stored DB text is never mutated.

## Boundaries & Constraints

**Always:**
- Transform applies ONLY to `kind='full'` response text. Never applied to CC (`kind='cc'`) rows — clients rely on raw pipe-separated card format.
- Transform runs AFTER `redact_reply_text`, never instead of it.
- WS live event and WS snapshot must produce identical output (reconnecting tab sees same text as live event).
- Admin cross-tenant view (`admin.py`) keeps raw redacted text — owner needs unmodified data for debugging.
- If parsing finds no recognizable structure (no `CC:` and no `Status:` fields), return the cleaned text unchanged — never break silently.

**Ask First:**
- Changing the brand copy strings (`TARJETA VINCULADA LIVE 🌟`, etc.).
- Adding transform rules for non-Amazon gates.

**Never:**
- Mutating stored DB text.
- Touching CC (`kind='cc'`) Filtrada rows.
- Using the bot's `Response:` field text in the output — discard it entirely.

## I/O & Edge-Case Matrix

| Scenario | Input (post-redact stored text) | Expected transformed output |
|----------|--------------------------------|----------------------------|
| Approved + time | `"☇ CC: 377481016137504\|05\|2033\|3845\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. \| Removed: ✅\n⌿ Time: 32.95s"` / `"amz"` | `"◈ Aprobada ✅ — 377481016137504\|05\|2033\|3845 · 32.95s\n▸ TARJETA VINCULADA LIVE 🌟"` |
| Declined + time | `"☇ CC: 377481016138023\|05\|2033\|7050\n⌿ Status: Declined ❌\n⌿ Response: Tarjeta inexistente.\n⌿ Time: 28.14s"` / `"amz"` | `"◈ Rechazada ❌ — 377481016138023\|05\|2033\|7050 · 28.14s\n▸ TARJETA INVALIDA DEAD ➕"` |
| Approved no time | same as above without `Time:` line | `"◈ Aprobada ✅ — 377481016137504\|05\|2033\|3845\n▸ TARJETA VINCULADA LIVE 🌟"` (no ` · `) |
| cookie_dead | `"⌿ Status: ❌ Cookies Inválidas"` / `"amz"` | `"◈ No procesada ❌\n▸ COOKIE MUERTA ❌"` |
| Non-AMZ gate | any text / `"zephyr"` | text returned unchanged |
| gate_name None | any text / `None` | text returned unchanged |
| Empty text | `""` / `"amz"` | `""` |
| No parseable structure | text with no `CC:` or `Status:` / `"amz"` | cleaned text returned as-is (normalize `☇`/`⌿` to `\n`, strip, join) |

</frozen-after-approval>

## Code Map

- `backend/app/core/display_transform.py` — NEW: pure display-transform module, no I/O, no imports from Telethon/FastAPI
- `backend/app/core/capture.py` — WS `response.captured` event (lines ~556–558 extract vars; ~624 event payload `"text": clean_text`)
- `backend/app/services/batches.py` — WS snapshot responses list comprehension (~242: `"text": redact_reply_text(row.text)`)
- `backend/app/api/sessions.py` — session detail response rows (~195) + two `exports.completa_txt(rows)` calls (~237, ~243)
- `backend/app/services/exports.py` — `completa_txt` function body (~48–51)
- `backend/tests/test_display_transform.py` — NEW: parametrized unit tests covering I/O matrix

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/display_transform.py` — CREATE with the following logic:
  - `_NORMALIZE`: replace `☇` → `\n`, `⌿` → `\n` (same char set as `normalize_cookie_cc` in redact.py)
  - `_CC_RE`: `(?i)CC\s*:\s*(\S+)` — captures the raw `card|mm|yyyy|cvv` token
  - `_STATUS_RE`: `(?i)Status\s*:\s*(.+)` — captures the status text
  - `_TIME_RE`: `(?i)Time\s*:\s*([\d.]+\s*s?)` — captures processing time string
  - `def display_transform(text: str, gate_name: str | None) -> str`: returns `text` unchanged if empty, if `gate_name` is None, or if `"amz"` not in `gate_name.lower()`. Otherwise calls `_amazon_transform(text)`.
  - `def _amazon_transform(text: str) -> str`: (1) normalize `☇`/`⌿` to `\n`; (2) strip+split lines, drop blanks; (3) regex-scan all lines for CC, Status, Time matches; (4) classify status: `"approved"` in status_lower → Aprobada ✅ / body `TARJETA VINCULADA LIVE 🌟`; `"declined"` in status_lower → Rechazada ❌ / body `TARJETA INVALIDA DEAD ➕`; any other non-None status → No procesada ❌ / body `COOKIE MUERTA ❌`; (5) if no status found → return `"\n".join(lines)` unchanged; (6) build header: `f"◈ {label} {glyph} — {cc_token}"` + append `f" · {time}"` if time found (omit if not), else header without cc if no CC field found; (7) return `header + "\n▸ " + body`.

- [x] `backend/app/core/capture.py` — EDIT: (a) extract `gate_name = capture_session.gate_name if capture_session is not None else None` alongside existing `tenant_id`/`capture_session_id`/`batch_id`/`line_id` extractions before `await session.commit()`; (b) in the `response.captured` event payload replace `"text": clean_text` → `"text": display_transform(clean_text, gate_name)`; (c) add import at top.

- [x] `backend/app/services/batches.py` — EDIT: in snapshot responses list comprehension (~242), replace `redact_reply_text(row.text)` → `display_transform(redact_reply_text(row.text), active.gate_name)`; add import.

- [x] `backend/app/services/exports.py` — EDIT: add `gate_name: str | None = None` param to `completa_txt`; replace `redact_reply_text(row.text)` → `display_transform(redact_reply_text(row.text), gate_name)`; add import.

- [x] `backend/app/api/sessions.py` — EDIT: (a) in session detail rows, replace `redact_reply_text(row.text)` → `display_transform(redact_reply_text(row.text), target.gate_name)`; (b) both `exports.completa_txt(rows)` calls → `exports.completa_txt(rows, target.gate_name)`; (c) add import.

- [x] `backend/tests/test_display_transform.py` — CREATE: parametrized pytest covering all 8 I/O matrix rows (use `@pytest.mark.parametrize`).

**Acceptance Criteria:**
- Given an Amazon Approved session with `Time:` in the reply, Completa shows `◈ Aprobada ✅ — {card} · {time}\n▸ TARJETA VINCULADA LIVE 🌟`.
- Given an Amazon Approved without `Time:`, same but no ` · {time}` suffix.
- Given an Amazon Declined reply, Completa shows `◈ Rechazada ❌ — {card}…\n▸ TARJETA INVALIDA DEAD ➕`.
- Given an Amazon cookie_dead, Completa shows `◈ No procesada ❌\n▸ COOKIE MUERTA ❌`.
- Given a non-Amazon gate, Completa text is byte-for-byte identical to `redact_reply_text` output.
- Given any gate, Filtrada (CC) rows are never touched.
- WS snapshot and WS live `response.captured` event show identical text for the same reply.
- Downloaded `completa.txt` export uses the same transformed text.

## Design Notes

The bot raw text may arrive inline (`☇ CC: …⌿ Status: …`) or pre-split with real newlines (`☇ CC: …\n⌿ Status: …`). Normalizing `☇`→`\n` and `⌿`→`\n` first unifies both formats before parsing. The bot's `Response:` field text is intentionally discarded — we replace it with fixed Ranger-X brand copy derived purely from the status classification. `Time:` is parsed and appended if found; its absence is silently ignored.

Admin view (`app/api/admin.py`) is deliberately excluded — admins see raw redacted data for debugging, not the display-transformed version.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_display_transform.py -v` — expected: all 8 parametrized cases pass
- `cd backend && .venv/bin/pytest` — expected: full suite green (no regressions)
- `cd frontend && npm run build` — expected: clean build (no frontend changes)

## Suggested Review Order

**Core transform logic**

- Entry point: gate routing and AMZ passthrough guard.
  [`display_transform.py:16`](../../backend/app/core/display_transform.py#L16)

- Inner transform: normalize separators → parse fields → classify → assemble.
  [`display_transform.py:24`](../../backend/app/core/display_transform.py#L24)

**WS live event (response.captured)**

- Extract gate_name from capture_session before session commit.
  [`capture.py:561`](../../backend/app/core/capture.py#L561)

- Apply transform to live WS payload text.
  [`capture.py:630`](../../backend/app/core/capture.py#L630)

**WS snapshot (parity surface)**

- Apply same transform in snapshot responses list (reconnect parity).
  [`batches.py:243`](../../backend/app/services/batches.py#L243)

**Historial API + .txt exports**

- Session detail rows: transform applied to each full revision.
  [`sessions.py:196`](../../backend/app/api/sessions.py#L196)

- Both completa_txt export calls pass gate_name through.
  [`sessions.py:234`](../../backend/app/api/sessions.py#L234)

- completa_txt signature: gate_name param added, transform wired inside.
  [`exports.py:37`](../../backend/app/services/exports.py#L37)

**Tests**

- 8-case parametrized suite covering the full I/O matrix.
  [`test_display_transform.py:6`](../../backend/tests/test_display_transform.py#L6)
