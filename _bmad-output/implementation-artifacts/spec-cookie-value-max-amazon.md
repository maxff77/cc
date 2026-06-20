---
title: 'Raise cookie-vault value cap to fit real Amazon cookies'
type: 'bugfix'
created: '2026-06-20'
status: 'done'
route: 'one-shot'
---

# Raise cookie-vault value cap to fit real Amazon cookies

## Intent

**Problem:** Saving a legitimate logged-in Amazon cookie failed with `invalid_cookie` ("La cookie no es válida (vacía, demasiado larga o con caracteres no permitidos)."). Root cause: the in-handler guard `_VALUE_MAX = 2600` in `backend/app/api/cookies.py`, but a real Amazon cookie runs ~2600-3500 chars (`ak_bmsc` / `at-*` / `session-token` alone are each hundreds of chars), so the cap rejected valid credentials. The value was single-line and fully printable — neither the empty nor the `isprintable()` branch fired; only the length branch.

**Approach:** Raise `_VALUE_MAX` to `4000`. The cap is not a storage constraint (dedup is `sha256(value)` over an unbounded `Text` column) — its real ceiling is the Telegram single-message limit (4096): Phase 2 sends the cookie as one `.cookie <value>` message (8-char prefix), so the value must fit in one message. `4000` admits real Amazon cookies and keeps margin under 4096. An added `isascii()` guard makes that ceiling exact (Telegram counts UTF-16 units; ASCII keeps `len()` == units == bytes) and also rejects paste corruption (smart quotes, NBSP, emoji). The empty/`isprintable()` guards are unchanged.

## Suggested Review Order

- Cap raised 2600→4000 with the rationale rewritten to the Telegram-message ceiling.
  [`cookies.py:74`](../../backend/app/api/cookies.py#L74)

- The length branch that was firing on valid cookies — now bounded by the new cap, with `isascii()` added so the cap == UTF-16 budget and a non-ASCII value can't overflow/livelock the `.cookie` send. Unprintable/empty still rejected.
  [`cookies.py:204`](../../backend/app/api/cookies.py#L204)

- Regression test: a ~3000-char single-line printable cookie (between old and new caps) now stores 201; the unprintable parametrize gained non-ASCII + astral-emoji cases.
  [`test_cookies.py:199`](../../backend/tests/test_cookies.py#L199)

## Spec Change Log

- **Adversarial review (one-shot):** patched two findings. (F1, minor) `isprintable()` admitted astral-plane/non-ASCII chars whose UTF-16 length exceeds `len()`, so a value passing `_VALUE_MAX` could overflow the 4096-unit `.cookie` send and livelock the line — added `isascii()` to the length guard (real cookie-octets are ASCII per RFC 6265). (F7, major) the new regression test hardcoded a constant value while asserting 201, breaking the suite's salt-the-value convention (flaky on a re-run against the persistent dev DB after a skipped teardown) — salted one segment with a uuid. Nits rejected: round-number cap (4000 is a deliberate margin under 4088) and shared send/validation constants (would couple modules for no gain).
