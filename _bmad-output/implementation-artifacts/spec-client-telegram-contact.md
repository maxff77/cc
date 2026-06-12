---
title: 'Contacto de Telegram por cliente en admin/usuarios'
type: 'feature'
created: '2026-06-12'
status: 'done'
baseline_commit: '9d2114c7adabae6ef3d73a8f827220fe2c71322e'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Cuando un cliente vence, el operador puede pulsar "Renovar" pero no tiene forma de saber a quién ni cómo contactar para cobrarle/renovar. No hay dato de contacto guardado en ninguna parte.

**Approach:** Agregar un campo opcional de contacto de Telegram (`@usuario`) por usuario: editable al crear y editable inline en la fila del cliente. Mostrarlo en una columna nueva como link clickable a `https://t.me/<usuario>` para escribirle directo.

## Boundaries & Constraints

**Always:**
- `tenant_id` SIEMPRE viene de la sesión (no del body/path); esta feature no toca esa regla. El target se resuelve por `user_id` con `require_admin_or_owner`, igual que renew/block.
- Migración Alembic ANTES del restart (`alembic upgrade head` corre en cada deploy). Columna nullable, sin default que rompa filas existentes.
- Almacenar el handle canónico SIN `@` (un solo formato en DB). El front antepone `@` para mostrar y `https://t.me/` para el link.
- Copy en español; mensajes de error vía contrato `{code, message}` (message en español).
- Repos hacen flush-not-commit; el router commitea (patrón existente).

**Ask First:**
- Si se pide hacer el contacto obligatorio o aplicarlo a algo distinto de Telegram (tel/email/texto libre) — el alcance acordado es Telegram opcional.

**Never:**
- NO tocar legacy (`app.py`/`core.py`/`static/`).
- NO agregar validación de existencia real del usuario de Telegram (no se consulta a Telegram).
- NO hacer el campo requerido. Clientes existentes quedan sin contacto (NULL) hasta que se llene.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Crear con contacto | `contact: "@yesterWhite"` | Se guarda `yesterWhite` (sin `@`); fila muestra link a `t.me/yesterWhite` | N/A |
| Crear sin contacto | `contact` ausente/`""` | Se guarda `NULL`; columna muestra "—" | N/A |
| Editar contacto inline | cliente existente, nuevo `"@nuevo"` | Se persiste `nuevo`; fila refresca link | N/A |
| Borrar contacto | enviar `""`/vacío en editar | Se persiste `NULL`; columna muestra "—" | N/A |
| Pegar link/`@`/espacios | `" https://t.me/foo "`, `"@foo"` | Se normaliza a `foo` antes de guardar | N/A |
| Handle inválido | `"a b!"`, `">32 chars"` | Rechazo `invalid_contact` (400) con mensaje español | banner/FieldError en el form |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- `User`: agregar columna `contact: Mapped[str | None]` (String(32), nullable).
- `backend/migrations/versions/` -- nueva revisión, `down_revision = 'a1b2c3d4e5f6'` (head actual); `add_column('users', contact)` / `drop_column`.
- `backend/app/api/admin.py` -- `CreateUserRequest` + nuevo campo y validador `_normalize_contact`; `UserOut` + `_to_out` exponen `contact`; nuevo endpoint `POST /users/{user_id}/contact`.
- `backend/app/services/users.py` -- `create_account(..., contact)` pasa el valor al repo; nuevo `set_contact(session, target, contact)`.
- `backend/app/db/repos/users.py` -- `create_user(..., contact)`; (set_contact muta el ORM en el service, no requiere repo nuevo).
- `backend/app/errors.py` -- nuevo `invalid_contact()` (AppError, 400, message español).
- `frontend/app/admin/users/page.tsx` -- `UserOut.contact`; columna "Contacto" con link `t.me`; input opcional en `CreateUserForm`; nueva acción `EditContactAction` en `ClientLifecycleActions`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- agregar `contact: Mapped[str | None] = mapped_column(String(32), nullable=True)` a `User` -- almacén canónico del handle.
- [x] `backend/migrations/versions/c4e1a2b3d5f6_user_contact_telegram.py` -- nueva migración add/drop column `users.contact` con `down_revision='a1b2c3d4e5f6'` -- esquema antes del restart.
- [x] `backend/app/errors.py` -- `invalid_contact()` 400, code `invalid_contact` -- contrato de error.
- [x] `backend/app/api/admin.py` -- `contact` opcional en `CreateUserRequest`; helper `_normalize_contact` (trim, quita `@`/`https://t.me/`/`t.me/`, valida `^[A-Za-z0-9_]{5,32}$`, vacío→None) llamado en la ruta para preservar el 400; `contact` en `UserOut`/`_to_out`; `SetContactRequest`; endpoint `POST /users/{user_id}/contact` (reusa `_require_client_target`, `set_contact`, commit, `_to_out`) -- API.
- [x] `backend/app/services/users.py` -- `create_account` acepta y persiste `contact`; `set_contact(session, target, contact)` (flush) -- orquestación.
- [x] `backend/app/db/repos/users.py` -- `create_user` acepta `contact` y lo setea en la fila -- inserción.
- [x] `frontend/app/admin/users/page.tsx` -- `contact` en interface `UserOut`; columna "Contacto" (`ContactLink`: link `https://t.me/{contact}` texto `@{contact}`, o "—"); input opcional "Telegram" en `CreateUserForm`; `EditContactAction` (AlertDialog, `POST /api/admin/users/{id}/contact`, invalida `USERS_KEY`) en `ClientLifecycleActions` -- UI.
- [x] `backend/tests/test_admin_users.py` -- tests del normalizador (parametrizados) + endpoint create/set/clear/invalid (casos de la matriz I/O).

**Acceptance Criteria:**
- Given un admin en /admin/usuarios, when crea un cliente con "@yesterWhite", then la fila muestra "@yesterWhite" como link a https://t.me/yesterWhite.
- Given un cliente sin contacto, when el admin pulsa "Editar contacto" y guarda "@foo", then la columna Contacto refresca al nuevo link sin recargar.
- Given un handle inválido (espacios/símbolos/>32), when se envía, then el backend responde `invalid_contact` (400) y el form muestra el mensaje, sin guardar.
- Given un cliente con contacto, when el admin vacía el campo y guarda, then se persiste NULL y la columna muestra "—".
- Given el deploy, when corre `alembic upgrade head`, then `users.contact` existe y las filas previas quedan en NULL sin error.

## Design Notes

Normalización compartida (backend, una sola fuente de verdad — el front NO valida formato, solo muestra). Pseudocódigo:

```python
def _normalize_contact(v: str | None) -> str | None:
    if v is None: return None
    v = v.strip().removeprefix("https://").removeprefix("t.me/").lstrip("@").strip("/")
    if v == "": return None
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", v):
        raise invalid_contact()
    return v
```

`EditContactAction` sigue el patrón de `RenewAction`: botón `size="sm" variant="secondary"` → AlertDialog con un `TextField` precargado con el contacto actual, botón Guardar que muta y cierra. El endpoint es verb-suffix POST como renew/block (convención del router).

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: aplica la revisión sin error; `\d users` muestra `contact`.
- `cd backend && .venv/bin/pytest` -- expected: tests del normalizador y endpoint en verde.
- `cd frontend && npm run lint` -- expected: sin errores nuevos.

**Manual checks:**
- En /admin/usuarios: crear cliente con `@x`, ver link `t.me/x`; editar a vacío → "—"; editar inválido → mensaje de error sin persistir.

## Suggested Review Order

**Normalización + contrato (el corazón de la feature)**

- Entry point: una sola fuente de verdad del formato del handle (pega-y-limpia + valida o 400).
  [`admin.py:94`](../../backend/app/api/admin.py#L94)

- Gating client-only en alta: admins no guardan contacto (no tienen forma de editarlo).
  [`admin.py:235`](../../backend/app/api/admin.py#L235)

- Endpoint de edición/borrado, verb-suffix como renew/block; reusa `_require_client_target`.
  [`admin.py:365`](../../backend/app/api/admin.py#L365)

- Código de error del contrato `{code, message}`.
  [`errors.py`](../../backend/app/errors.py)

**Esquema + persistencia**

- Columna nueva `users.contact` (String(32), nullable).
  [`models.py:75`](../../backend/app/db/models.py#L75)

- Migración add/drop column; `down_revision` = head actual.
  [`c4e1a2b3d5f6`](../../backend/migrations/versions/c4e1a2b3d5f6_user_contact_telegram.py)

- `set_contact` muta el ORM + flush; el router commitea.
  [`users.py:73`](../../backend/app/services/users.py#L73)

**UI**

- Link clickable a `t.me/<handle>` (o "—") en la columna nueva.
  [`page.tsx:70`](../../frontend/app/admin/users/page.tsx#L70)

- Acción inline "Contacto" (AlertDialog estilo Renovar; vacío = quitar).
  [`page.tsx:617`](../../frontend/app/admin/users/page.tsx#L617)

- Input opcional client-only en el form de alta.
  [`page.tsx:348`](../../frontend/app/admin/users/page.tsx#L348)

**Tests**

- Normalizador parametrizado + endpoint create/set/clear/inválido.
  [`test_admin_users.py`](../../backend/tests/test_admin_users.py)
