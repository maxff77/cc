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
