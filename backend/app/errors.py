"""Domain error type + the project-wide error contract.

Every handled error surfaces as HTTP ``status`` + body ``{"code", "message"}``
where ``code`` is machine-readable (snake_case) and ``message`` is user-facing
Spanish. The handler that renders this lives in ``app.main`` and every later
story reuses the same shape.
"""


class AppError(Exception):
    """An error carrying the HTTP status + ``{code, message}`` contract."""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# --- Codes this story (1.2) defines --------------------------------------


def invalid_credentials() -> AppError:
    return AppError(
        status_code=401,
        code="invalid_credentials",
        message="Correo o contraseña incorrectos.",
    )


def account_blocked() -> AppError:
    return AppError(
        status_code=403,
        code="account_blocked",
        message=(
            "Tu cuenta está bloqueada. Escríbenos por WhatsApp o Telegram para "
            "reactivarla."
        ),
    )


def too_many_attempts() -> AppError:
    return AppError(
        status_code=429,
        code="too_many_attempts",
        message="Demasiados intentos. Espera unos minutos.",
    )


def not_authenticated() -> AppError:
    return AppError(
        status_code=401,
        code="not_authenticated",
        message="No has iniciado sesión.",
    )


def forbidden() -> AppError:
    return AppError(
        status_code=403,
        code="forbidden",
        message="No tienes permiso para acceder a esto.",
    )


# --- Codes this story (1.3) defines --------------------------------------


def email_taken() -> AppError:
    return AppError(
        status_code=409,
        code="email_taken",
        message="Ya existe un cliente con ese email.",
    )


def invalid_plan_days() -> AppError:
    return AppError(
        status_code=400,
        code="invalid_plan_days",
        message="Indica los días del plan.",
    )


def user_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="user_not_found",
        message="Usuario no encontrado.",
    )


def invalid_contact() -> AppError:
    return AppError(
        status_code=400,
        code="invalid_contact",
        message="Usuario de Telegram inválido (5–32 caracteres: letras, números o _).",
    )


# --- Codes this story (1.4) defines --------------------------------------


def plan_expired() -> AppError:
    return AppError(
        status_code=403,
        code="plan_expired",
        message="Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos.",
    )


# --- Codes this story (1.5) defines --------------------------------------


def invalid_renewal() -> AppError:
    return AppError(
        status_code=400,
        code="invalid_renewal",
        message="Indica los días del plan o una fecha de vencimiento futura.",
    )


def renewal_would_shorten() -> AppError:
    return AppError(
        status_code=400,
        code="renewal_would_shorten",
        message=(
            "La fecha indicada es anterior al vencimiento actual. Para cortar "
            "el acceso usa Bloquear."
        ),
    )


# --- Codes this story (1.6) defines --------------------------------------


def password_change_required() -> AppError:
    # Raised by get_current_user for every gated route/API while the
    # must_change_password flag is set; middleware and lib/api.ts route on it.
    return AppError(
        status_code=403,
        code="password_change_required",
        message="Elige una contraseña nueva para continuar.",
    )


def password_reuse() -> AppError:
    # The new password must not equal the current (temp) one, or the
    # "one-time" property of the temp password dies.
    return AppError(
        status_code=400,
        code="password_reuse",
        message="Elige una contraseña distinta a la temporal.",
    )


# --- Codes this story (2.1) defines --------------------------------------


def gate_exists() -> AppError:
    return AppError(
        status_code=409,
        code="gate_exists",
        message="Ya existe ese gateway en el catálogo.",
    )


def gate_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="gate_not_found",
        message="Ese gateway no existe.",
    )


# --- Codes this story (2.2) defines --------------------------------------


def invalid_gate(message: str | None = None) -> AppError:
    # A gate field failed validation (credits feature: ``credit_cost`` negative
    # or over the column ceiling). The caller may pass field-specific Spanish
    # copy; the default covers the generic case.
    return AppError(
        status_code=400,
        code="invalid_gate",
        message=message or "Datos del gateway inválidos.",
    )


def category_exists() -> AppError:
    return AppError(
        status_code=409,
        code="category_exists",
        message="Ya existe esa categoría.",
    )


def category_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="category_not_found",
        message="Esa categoría no existe.",
    )


def category_in_use() -> AppError:
    return AppError(
        status_code=409,
        code="category_in_use",
        message="No puedes eliminar una categoría con gateways. Reasigna sus gateways primero.",
    )


def empty_batch() -> AppError:
    return AppError(
        status_code=400,
        code="empty_batch",
        message="No hay líneas para enviar.",
    )


def telegram_unauthorized() -> AppError:
    return AppError(
        status_code=503,
        code="telegram_unauthorized",
        message="Telegram no está autorizado todavía. Contacta al administrador.",
    )


# --- Send-target management (multi-target sending) -----------------------


def telegram_target_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="telegram_target_not_found",
        message="Ese destino no existe.",
    )


def telegram_target_exists() -> AppError:
    return AppError(
        status_code=409,
        code="telegram_target_exists",
        message="Ese destino ya está en la lista.",
    )


def telegram_target_unresolvable() -> AppError:
    return AppError(
        status_code=422,
        code="telegram_target_unresolvable",
        message="No pudimos resolver ese destino. ¿La cuenta sigue en ese chat?",
    )


# --- Codes this story (2.3) defines --------------------------------------


def batch_not_found() -> AppError:
    # Unknown id, another tenant's id, id > int32 — existence is never leaked.
    return AppError(
        status_code=404,
        code="batch_not_found",
        message="Ese lote no existe.",
    )


def batch_not_live() -> AppError:
    # Control action on a terminal batch ('completed' / 'stopped').
    return AppError(
        status_code=409,
        code="batch_not_live",
        message="Ese lote ya terminó.",
    )


def batch_stopping() -> AppError:
    # pause/resume (or append) while the worker finishes a stop in flight.
    return AppError(
        status_code=409,
        code="batch_stopping",
        message="El lote se está deteniendo. Espera un momento.",
    )


# --- Codes this story (3.3) defines --------------------------------------


def session_not_found() -> AppError:
    # Unknown id, another tenant's id, id > int32 — existence is never leaked
    # (idiom batch_not_found).
    return AppError(
        status_code=404,
        code="session_not_found",
        message="Esa sesión no existe.",
    )


def session_in_use() -> AppError:
    # DELETE on the session bound to a LIVE batch (AC 6) — the message IS the
    # AC copy verbatim: the UI renders it as-is ({code, message} contract).
    return AppError(
        status_code=409,
        code="session_in_use",
        message="Detén el lote antes de eliminar esta sesión.",
    )


# --- Codes this story (3.4) defines --------------------------------------


def batch_live() -> AppError:
    # Continue while ANY of the tenant's batches is live or paused (AC 3) —
    # legacy `_lote_vivo` parity. The message IS the AC copy verbatim: the UI
    # renders it as-is ({code, message} contract, same treatment as
    # session_in_use gave 3.3's AC 6).
    return AppError(
        status_code=409,
        code="batch_live",
        message="Termina o detén el lote actual antes de continuar otra sesión.",
    )


def session_conflict() -> AppError:
    # Two continues (or a continue crossed with a batch start) raced into
    # uq_capture_sessions_one_active_per_tenant at commit — mapped so the
    # {code, message} contract never degrades to a raw 500.
    return AppError(
        status_code=409,
        code="session_conflict",
        message="No pudimos continuar la sesión. Intenta de nuevo.",
    )


# --- Codes this story (3.6) defines --------------------------------------


def tenant_not_found() -> AppError:
    # Unknown tenant id, a tenant whose user is NOT a client (owner/admin),
    # and id > int32 all answer IDENTICAL — existence is never leaked to
    # whoever probes ids (idiom session_not_found).
    return AppError(
        status_code=404,
        code="tenant_not_found",
        message="Ese cliente no existe.",
    )


# --- Codes this story (4.1) defines --------------------------------------


def sending_paused() -> AppError:
    # POST /api/batches (create AND append) while the watchdog's global pause
    # is latched — queuing lines that will not send invites confusion; the WS
    # banner explains the state and only the owner can resume.
    return AppError(
        status_code=503,
        code="sending_paused",
        message=(
            "Los envíos están pausados por protección de la cuenta. "
            "Intenta más tarde."
        ),
    )


# --- Codes this story (4.2) defines --------------------------------------


def batch_waiting() -> AppError:
    # pause/resume on a batch still queued for admission — batch_not_live
    # ("Ese lote ya terminó.") would lie; there is nothing to pause yet.
    return AppError(
        status_code=409,
        code="batch_waiting",
        message="El lote está en cola de espera. Puedes detenerlo si no quieres esperar.",
    )


def invalid_admission_cap() -> AppError:
    return AppError(
        status_code=400,
        code="invalid_admission_cap",
        message="Indica un límite entre 0 y 1000 (0 desactiva el control de admisión).",
    )


def invalid_send_interval() -> AppError:
    return AppError(
        status_code=400,
        code="invalid_send_interval",
        message="Indica un intervalo entre 1 y 30 segundos.",
    )


def invalid_live_channel() -> AppError:
    return AppError(
        status_code=400,
        code="invalid_live_channel",
        message="No pudimos resolver ese canal. Revisa el id/@usuario y el acceso de la cuenta.",
    )


# --- Codes the plan-catalog feature defines ------------------------------


def invalid_plan(message: str | None = None) -> AppError:
    # A plan field failed validation (antispam/duration/max-lines < 1, or a
    # negative price) OR a client was assigned an unknown/inactive plan. The
    # caller may pass a field-specific Spanish message; the default covers the
    # generic "no such usable plan" case.
    return AppError(
        status_code=400,
        code="invalid_plan",
        message=message or "Plan inválido o no disponible.",
    )


def invalid_credits() -> AppError:
    # Owner recharge with a negative / out-of-range credit balance (credits
    # feature). 400 — the request is malformed.
    return AppError(
        status_code=400,
        code="invalid_credits",
        message="Indica una cantidad de créditos válida (0 o más).",
    )


def plan_name_taken() -> AppError:
    return AppError(
        status_code=409,
        code="plan_name_taken",
        message="Ya existe un plan con ese nombre.",
    )


def plan_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="plan_not_found",
        message="Ese plan no existe.",
    )


def plan_in_use() -> AppError:
    return AppError(
        status_code=409,
        code="plan_in_use",
        message=(
            "No puedes eliminar un plan en uso (asignado a clientes o con keys "
            "generadas). Desactívalo en su lugar."
        ),
    )


def insufficient_credits(*, gate_name: str) -> AppError:
    # The client tried to start or append a batch on a costed gate
    # (``credit_cost > 0``, credits feature) with a credit balance of 0.
    # Free gates (cost 0) are never blocked; the day-plan is untouched. 403 (an
    # authorization-shaped block on THIS gate), not 400 — the request is
    # well-formed, the account just lacks credits for this gate.
    return AppError(
        status_code=403,
        code="insufficient_credits",
        message=(
            f"No tienes créditos para usar el gateway «{gate_name}». "
            "Recarga créditos para continuar."
        ),
    )


def batch_line_limit(*, cap: int, attempted: int) -> AppError:
    # The client's plan caps lines per batch (plan-catalog feature). Raised on
    # create AND append when the resulting line count would exceed the cap;
    # nothing is queued. The message states the cap and the attempted count so
    # the client knows exactly how many to trim.
    return AppError(
        status_code=400,
        code="batch_line_limit",
        message=(
            f"Tu plan permite máximo {cap} líneas por lote; intentaste enviar "
            f"{attempted}. Reduce la cantidad."
        ),
    )


# --- Codes the gift-keys feature defines ---------------------------------


def invalid_key_days() -> AppError:
    # Days now allow 0 (a credits-only key); the bound is 0..KEY_DAYS_MAX
    # (gift-key-credits feature).
    return AppError(
        status_code=400,
        code="invalid_key_days",
        message="Indica los días de la key (0 o más).",
    )


def empty_gift_key() -> AppError:
    # A key must grant SOMETHING (gift-key-credits feature): days==0 AND
    # credits==0 is rejected — otherwise the mint produces a no-op key.
    return AppError(
        status_code=400,
        code="empty_gift_key",
        message="La key debe otorgar días o créditos (al menos uno).",
    )


def no_default_plan() -> AppError:
    return AppError(
        status_code=409,
        code="no_default_plan",
        message=(
            "Configura un plan predeterminado antes de generar keys "
            "(márcalo en /admin/plans)."
        ),
    )


def key_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="key_not_found",
        message="Esa key no existe.",
    )


def credential_not_found() -> AppError:
    # id desconocido / de otro tenant / oversized → 404 idéntico (value-free,
    # no filtra existencia ni el id).
    return AppError(
        status_code=404,
        code="credential_not_found",
        message="Credencial no encontrada.",
    )


def key_already_claimed() -> AppError:
    return AppError(
        status_code=409,
        code="key_already_claimed",
        message="Esa key ya fue canjeada.",
    )


def key_revoked() -> AppError:
    return AppError(
        status_code=409,
        code="key_revoked",
        message="Esa key fue revocada.",
    )


# --- Codes the client history feature defines (PR-2) ---------------------


def history_response_not_found() -> AppError:
    # Unknown id, another tenant's id, id > int4 — existence is never leaked
    # (idiom session_not_found); the id is never logged.
    return AppError(
        status_code=404,
        code="history_response_not_found",
        message="Esa respuesta no existe.",
    )


# --- Codes the cookie-vault feature defines (Phase 1) --------------------


def invalid_cookie() -> AppError:
    # The posted cookie value failed validation (empty/whitespace-only,
    # oversized, or contains unprintable characters). Raised INSIDE the router
    # body — never via a pydantic field validator — so the rejected value can
    # never surface in a default 422 body or an access log. The message is
    # deliberately value-free.
    return AppError(
        status_code=400,
        code="invalid_cookie",
        message="La cookie no es válida (vacía, demasiado larga o con caracteres no permitidos).",
    )


def gate_not_cookie_mode() -> AppError:
    # The gate is visible to the tenant but its category is NOT in cookie mode,
    # so it has no vault. Evaluated only AFTER the gate is confirmed visible
    # (resolve/authorize first → identical 404 for unknown/foreign/retired).
    return AppError(
        status_code=409,
        code="gate_not_cookie_mode",
        message="Ese gateway no admite cookies.",
    )


def cookie_not_found() -> AppError:
    # Unknown id, another tenant's id, id > int4 — existence is never leaked
    # (idiom session_not_found); the id is never logged.
    return AppError(
        status_code=404,
        code="cookie_not_found",
        message="Esa cookie no existe.",
    )


def cookie_limit_reached() -> AppError:
    # The tenant already holds the per-(tenant, gate) cookie cap; storing
    # another distinct value is rejected.
    return AppError(
        status_code=409,
        code="cookie_limit_reached",
        message="Alcanzaste el máximo de cookies para este gateway. Elimina alguna para agregar otra.",
    )


def cookie_conflict_retry() -> AppError:
    # Raced unique violation: the conflicting cookie was deleted between the
    # IntegrityError and the re-fetch, so there is no row to dedup to. Surface a
    # mapped, retryable conflict (keeps the {code,message} contract) instead of
    # letting the bare IntegrityError become an unmapped 500. Value-free.
    return AppError(
        status_code=409,
        code="cookie_conflict_retry",
        message="No pudimos guardar la cookie por una operación simultánea. Vuelve a intentar.",
    )


def cookie_delete_failed() -> AppError:
    # Defense-in-depth for DELETE: the FK ``failed_cookie_id`` is
    # ``ON DELETE SET NULL``, so a referenced cookie deletes cleanly — but should
    # any future/edge IntegrityError fire, map it to a retryable conflict instead
    # of letting the bare exception become an unmapped 500 (the "error inesperado"
    # this feature exists to kill). Value-free.
    return AppError(
        status_code=409,
        code="cookie_delete_failed",
        message="No pudimos eliminar la cookie por una operación simultánea. Vuelve a intentar.",
    )


# --- Codes the credential vault defines ----------------------------------


def invalid_credential() -> AppError:
    # Empty/whitespace-only email or password, or oversized email. Raised INSIDE
    # the router (never a pydantic validator on the password) so the secret can
    # never surface in a default 422 body or an access log. Value-free.
    return AppError(
        status_code=400,
        code="invalid_credential",
        message="Correo o contraseña inválidos.",
    )


# --- Cookie-mode pause reasons (cookie rotation feature, Phase 2) ---------
#
# NOT ``AppError`` factories: a ``cookies_exhausted`` / ``verdict_timeout``
# pause is an ORDINARY ``STATE_PAUSED`` batch discriminated by
# ``Batch.pause_reason`` (the same string codes live as the canonical constants
# ``PAUSE_COOKIES_EXHAUSTED`` / ``PAUSE_VERDICT_TIMEOUT`` in
# ``repos.batches``). The reason rides the ``batch.state`` WS frame's
# ``pause_reason``; the cockpit renders the prompt off the CODE (the
# add-cookies notice has its own inlined copy). This mapping keeps the
# machine-code → Spanish-copy contract of this module in ONE place so any
# server-side surface (logs, a future REST read, an alert) renders the same
# user-facing sentence the {code, message} contract guarantees everywhere else.

PAUSE_REASON_MESSAGES: dict[str, str] = {
    "cookies_exhausted": (
        "Se agotaron las cookies de este gateway. Agrega más cookies y reanuda "
        "para continuar desde la línea pendiente."
    ),
    "verdict_timeout": (
        "El checker dejó de responder. Reanuda para reintentar la línea "
        "pendiente; si persiste, vuelve a intentarlo más tarde."
    ),
}


def pause_reason_message(reason: str | None) -> str | None:
    """Spanish cockpit copy for a cookie-mode ``pause_reason`` code, or ``None``.

    ``None`` for a plain client pause (no reason) and for an unknown code — the
    caller falls back to a generic "pausado" copy, never leaking a raw code.
    """
    if reason is None:
        return None
    return PAUSE_REASON_MESSAGES.get(reason)
