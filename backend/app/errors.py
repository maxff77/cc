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
        message="Ya existe ese gate en el catálogo.",
    )


def gate_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="gate_not_found",
        message="Ese gate no existe.",
    )


# --- Codes this story (2.2) defines --------------------------------------


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
        message="No puedes eliminar una categoría con gates. Reasigna sus gates primero.",
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
