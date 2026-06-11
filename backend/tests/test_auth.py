"""Unit tests for the auth service (hashing + throttle).

Pure / no-DB by design — the session-validity and login flows are exercised
end-to-end by the manual verification gate (seed → login → me → logout). These
cover the logic that is cheap to assert in isolation.
"""

from app.services.auth import (
    DUMMY_HASH,
    LoginThrottle,
    hash_password,
    verify_password,
)


def test_hash_verify_round_trip() -> None:
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"  # not stored in clear
    assert verify_password(h, "correct horse battery staple") is True


def test_verify_wrong_password_is_false() -> None:
    h = hash_password("s3cret")
    assert verify_password(h, "wrong") is False


def test_verify_malformed_hash_is_false_not_raised() -> None:
    assert verify_password("not-a-real-argon2-hash", "whatever") is False


def test_dummy_hash_is_argon2id_and_never_matches() -> None:
    assert DUMMY_HASH.startswith("$argon2id$")
    assert verify_password(DUMMY_HASH, "anything") is False


def test_throttle_blocks_after_threshold() -> None:
    t = LoginThrottle(max_attempts=3, window_seconds=900)
    assert t.is_blocked("a@b.com", "1.2.3.4", now=0.0) is False
    for i in range(3):
        t.register_failure("a@b.com", "1.2.3.4", now=float(i))
    assert t.is_blocked("a@b.com", "1.2.3.4", now=3.0) is True


def test_throttle_window_expiry_unblocks() -> None:
    t = LoginThrottle(max_attempts=2, window_seconds=900)
    t.register_failure("a@b.com", "1.2.3.4", now=0.0)
    t.register_failure("a@b.com", "1.2.3.4", now=1.0)
    assert t.is_blocked("a@b.com", "1.2.3.4", now=10.0) is True
    # After the window elapses the counter no longer blocks.
    assert t.is_blocked("a@b.com", "1.2.3.4", now=901.0) is False


def test_throttle_reset_on_success() -> None:
    t = LoginThrottle(max_attempts=2, window_seconds=900)
    t.register_failure("a@b.com", "1.2.3.4", now=0.0)
    t.register_failure("a@b.com", "1.2.3.4", now=1.0)
    assert t.is_blocked("a@b.com", "1.2.3.4", now=2.0) is True
    t.reset("a@b.com", "1.2.3.4")
    assert t.is_blocked("a@b.com", "1.2.3.4", now=2.0) is False


def test_throttle_prunes_elapsed_buckets() -> None:
    t = LoginThrottle(max_attempts=5, window_seconds=900)
    t.register_failure("a@b.com", "1.2.3.4", now=0.0)
    assert len(t._buckets) == 1
    # A later failure (window elapsed) for a different key prunes the stale one.
    t.register_failure("c@d.com", "9.9.9.9", now=1000.0)
    assert len(t._buckets) == 1
    assert t._buckets.get(("a@b.com", "1.2.3.4")) is None


def test_throttle_is_keyed_by_email_and_ip() -> None:
    t = LoginThrottle(max_attempts=1, window_seconds=900)
    t.register_failure("a@b.com", "1.2.3.4", now=0.0)
    assert t.is_blocked("a@b.com", "1.2.3.4", now=0.0) is True
    # Different IP, same email → independent bucket.
    assert t.is_blocked("a@b.com", "9.9.9.9", now=0.0) is False
    # Different email, same IP → independent bucket.
    assert t.is_blocked("c@d.com", "1.2.3.4", now=0.0) is False
