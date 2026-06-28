"""Environment-based application configuration (pydantic-settings)."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, resolved from this file so the CWD the server is launched from
# doesn't matter (config.py lives at backend/app/config.py).
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Settings loaded from ``backend/.env`` (and process environment)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # asyncpg URL, e.g. postgresql+asyncpg://user:pass@host:5432/db
    # Required — no default, so a missing DATABASE_URL fails at boot instead of
    # silently connecting to the wrong database.
    database_url: str

    # --- Auth / session cookie (Story 1.2) -------------------------------
    # Name of the opaque session cookie set on login.
    session_cookie_name: str = "cc_session"
    # MUST be True in production (HTTPS via Caddy, Story 1.7) and False in local
    # dev (plain http) — otherwise the browser silently drops the cookie and
    # login "succeeds" but no session sticks. Override to True in prod .env.
    cookie_secure: bool = False
    # Server-side session lifetime; also the cookie max-age.
    session_ttl_days: int = 14
    # Trust the leftmost X-Forwarded-For entry for the client IP. MUST stay
    # False unless a trusted proxy (Caddy, Story 1.7) sets the header — a
    # client-spoofable XFF would otherwise defeat the per-(email, IP) throttle.
    trust_forwarded_for: bool = False
    # --- Login throttle (per process; resets on restart) -----------------
    # Reject further attempts past this many failures within the window.
    throttle_max_attempts: int = 5
    throttle_window_seconds: int = 900  # 15 minutes

    # --- Telegram (Story 2.2) ---------------------------------------------
    # Defaults are deliberately PERMISSIVE (unlike database_url): a machine
    # without Telegram keys must still import the app and run the full test
    # suite. The gateway treats missing/zero credentials as "not authorized"
    # and sending stays down (POST /api/batches → 503) — nothing crashes.
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    # Outside the repo and the web root on the VPS (Story 1.7 convention).
    telegram_session_path: str = "/var/lib/cc/anon.session"
    # SEED destination (multi-target sending): on a fresh DB the boot seeds the
    # ``send_targets`` list with this one chat so the legacy single-target
    # deployment keeps sending. After that the owner-managed DB list is
    # authoritative (the gateway round-robins over it). Leading ``@`` optional.
    telegram_target: str = ""
    # The CONSTANT send interval, in seconds (owner decision 2026-06-13): the
    # scheduler paces every send at exactly G_min regardless of how many
    # clients are active (round-robin spreads the slot, so each client's turn
    # comes every G×n). The FloodWait governor still self-tunes G_min UPWARD
    # from this floor — it is the real ban protection. Server config ONLY —
    # never accepted from any request (FR12). This IS the effective interval
    # now (no adaptive band). 4.0s is the owner's hard floor. A residual
    # SEND_INTERVAL_SECONDS in a VPS .env is harmless (extra="ignore").
    scheduler_g_min_seconds: float = 4.0

    # Out-of-box per-tenant antispam cooldown for CLIENTS (antispam-per-user
    # feature), used only until the owner sets one via /api/admin/antispam
    # (system_settings key ``default_antispam_seconds``). Sits well above
    # ``scheduler_g_min_seconds`` ON PURPOSE: a client who buys a lower override
    # needs headroom below the default to actually send faster. Distinct from the
    # g_min FLOOR — this is the per-tenant cooldown, not the account-wide pace.
    scheduler_default_antispam_seconds: float = 15.0

    @field_validator("scheduler_default_antispam_seconds")
    @classmethod
    def _clamp_default_antispam(cls, v: float) -> float:
        # Keep the env-loaded default cooldown in the [1, 30] band the admin API
        # (services.antispam.ANTISPAM_MIN/MAX) and scheduler enforce — 30 == the
        # governor ceiling / ``_prune_cooldowns`` cutoff. A typo'd env override is
        # clamped, not silently pushed past where the prune can still gate a
        # tenant. (Tests neutralize the cooldown by assigning this field directly,
        # which bypasses validation — validate_assignment is off — on purpose.)
        return min(max(v, 1.0), 30.0)

    # --- Credential vault API key ----------------------------------------
    # Single shared key for the /api/credentials endpoints (header X-Api-Key).
    # When unset (default) those endpoints answer 503 api_key_not_configured —
    # the vault is closed until an operator puts a strong key in the prod .env
    # (CREDENTIALS_API_KEY=...) and restarts cc-core. Never committed.
    credentials_api_key: str | None = None

    @property
    def session_ttl_seconds(self) -> int:
        """Session TTL expressed in seconds (cookie max-age)."""
        return self.session_ttl_days * 24 * 60 * 60


def get_settings() -> Settings:
    """Return the application settings."""
    return Settings()


settings = get_settings()
