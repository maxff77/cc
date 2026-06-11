# Extracto UX — architecture.md

## Stack frontend
- Next.js 16.2.x LTS (app router) + **HeroUI v3** + Tailwind CSS v4, TypeScript estricto
- TanStack Query v5 (estado REST) + WebSocket nativo auto-reconectante (eventos live)
- Base: template oficial HeroUI `next-app-template` (`npx heroui-cli@latest init frontend -t app`)
- Theming integrado de HeroUI

## Rutas (Next.js app router)
```
/login — entrada de autenticación
/(client)/ — workspace del cliente (rol: client)
  /page.tsx — envío: pegar lote, selector de prefijo, cola en vivo
  /sessions — historial de sesiones (lista, renombrar, continuar, eliminar)
  /sessions/[id] — vistas Completa/Filtrada, live follow, export
/admin/ — panel admin/owner
  /users — crear clientes, renovar planes, bloquear, resetear password
  /prefixes — gestión de catálogo global (solo owner)
  /tenants/[id] — vista de soporte a sesiones de clientes (cross-tenant, FR20)
/expired — mensaje de plan expirado con canal de contacto
```

## Eventos WebSocket (endpoint único `/ws`, tenant-scoped por cookie)
Envelope: `{"event": "<name>", "data": {...}}`
- `batch.progress` — progreso de envío
- `batch.line_sent` — confirmación de línea enviada
- `batch.state` — ciclo de vida del lote: `idle | sending | paused | stopping`
- `response.captured` — respuesta del bot recibida
- `flood.wait` — rate limit (explica stalls de ETA)
- `session.active` — sesión de captura abierta
- `auth.state` — cambios de estado de auth
- `error` — errores generales
- Conexión nueva siempre recibe snapshot completo primero

## Patrones de estado y error
- Loading: convenciones TanStack Query `isPending`/`isError`
- Máquina de estados del lote: `idle | sending | paused | stopping` (fuente única: `batch.state`)
- Contrato de error: HTTP status + JSON `{"code": "snake_case", "message": "texto en español user-facing"}`
- Errores se muestran por `code`, fallback a `message`

## Auth
- Cookie httpOnly+Secure+SameSite (FastAPI); chequeo en `/api/me` al cargar
- Middleware Next.js: redirect a login si no autenticado, protección por rol, cambio de password forzado bloquea todo excepto `/api/auth/change-password`
- Tres roles: owner / admin / client (gatea cada página y conexión WS)
- Expiración de plan: chequeada en auth; sesión expirada → página `/expired`

## Superficies clave
### Client Send Workspace (`/(client)/page.tsx`)
- Input de lote pegado, selector de prefijo (catálogo global por API)
- Panel de cola en vivo con pausa/reanudar/detener
- Progreso/ETA con matemática adaptativa honesta: `G×n` por cliente (degrada transparente a escala)
- FloodWait como mensajes explicativos, no errores

### Client Session History (`/(client)/sessions/`)
- Lista con renombrar, continuar, eliminar
- Por sesión: vistas "Completa" / "Filtrada" (CC filtrado, dedupeado)
- Live follow (eventos `response.captured`)
- Export `.txt` (backend genera; frontend dispara descarga)

### Admin/Owner (`/admin/`)
- Gestión de usuarios: crear, renovar, bloquear, resetear password
- Catálogo de prefijos: crear/editar (owner)
- Vista de soporte cross-tenant (FR20)

## Convenciones API consumidas por frontend
- REST: sustantivos plurales — `/api/batches`, `/api/sessions/{id}`, `/api/admin/users`
- Acciones: sufijo verbo POST — `/api/batches/{id}/pause|resume|stop`
- Query params snake_case (`?type=filtered`)
- Auth: `/api/auth/login|logout|me|change-password`

## Idioma
- Identificadores de código: inglés. Texto UI: español (mensajes user-facing vienen del campo `message` de la API)

## Constraints UX clave
1. Multi-tenancy: todo evento WS y llamada API es tenant-scoped; UI nunca cruza tenants salvo rutas admin explícitas
2. Degradación de capacidad: con n>6 concurrentes, intervalos por cliente degradan linealmente; UI debe mostrar ETA honesto
3. Ciclo de sesión: cliente puede "continuar" sesión pasada (ver respuestas viejas, mantener dedup para nuevos envíos)
4. Cambio de password forzado: bloquea acceso hasta cambiar
5. Expiración de plan: lockout duro → página de contacto

## Componentes
- Organización: `components/batch/`, `components/sessions/`, `components/admin/`
- WS: WebSocket auto-reconectante → un handler estilo reducer por evento
- Archivos clave: `middleware.ts`, `lib/api.ts`, `lib/ws.ts`, `types/api.ts` (generado de OpenAPI)
