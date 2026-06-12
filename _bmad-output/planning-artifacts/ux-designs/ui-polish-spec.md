# UI Polish Spec — "Cabina de datos" (rack instrumental)

**Fecha:** 2026-06-12 · **Estado:** listo para implementar (excepto §4, DIFERIDO)
**Base de diseño:** `_bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/DESIGN.md` (paleta oklch hue-243 fija en `frontend/styles/globals.css`, Public Sans, dark por defecto, radio 0.25rem, escalera de superficies, sin sombras).
**Stack:** HeroUI `@heroui/react 3.1.0` (API compuesta) + Tailwind v4. Todas las rutas son relativas a `frontend/` salvo indicación.

**Intención en una frase:** un instrumento montado en rack — leyendas grabadas sobre las placas, un solo anillo, filas mono tipo cinta — no un dashboard de plantilla.

---

## 0. Resumen de cambios por archivo

| Archivo | Acción |
|---|---|
| `app/layout.tsx` | cablear `fontMono.variable` + `themeColor` real |
| `config/fonts.ts` | renombrar variable CSS de la mono a `--font-fira-code` |
| `styles/globals.css` | `--font-mono: var(--font-fira-code), monospace` en `@theme` |
| `components/ui/*` (NUEVOS) | 9 primitivas (§2) |
| `app/error.tsx` | reescritura completa (§3.1) |
| `app/login/page.tsx`, `app/change-password/page.tsx`, `app/expired/page.tsx`, `components/contact-panel.tsx` | shell de auth (§3.2) |
| `components/client-nav.tsx` | hover/focus, brand-link, safe-area, usar `StatePill` compartido (§3.3) |
| `app/admin/users/page.tsx`, `app/admin/gates/page.tsx` | shell admin dos zonas + modales (§3.4–3.6) |
| `app/(client)/sessions/page.tsx` | Historial (§3.7) |
| `app/(client)/sessions/[id]/page.tsx` | Detalle (§3.8) |
| `components/sessions/response-views.tsx`, `response-row.tsx` | paneles de datos → patrón SectionCard (§3.9) |
| `app/(client)/page.tsx`, `components/batch/*` | **NO TOCAR** — §4 DIFERIDO |
| `components/primitives.ts`, `components/theme-switch.tsx`, `components/icons.tsx` | código muerto: borrar (verificado por grep: cero imports) |

---

## 1. Tokens y reglas globales

### 1.1 PRIMERO: cablear Fira Code (hoy `font-mono` cae en monospace del sistema)

`config/fonts.ts` carga Fira Code pero `app/layout.tsx` nunca aplica `fontMono.variable` al `<body>`, y además el `@theme` de `globals.css` tiene una autorreferencia (`--font-mono: var(--font-mono)`). Arreglo en tres puntos (espejo exacto de cómo ya funciona `--font-sans`/`--font-public-sans`):

1. `config/fonts.ts`: `variable: "--font-mono"` → `variable: "--font-fira-code"`.
2. `styles/globals.css` línea 101: `--font-mono: var(--font-mono), monospace;` → `--font-mono: var(--font-fira-code), monospace;`.
3. `app/layout.tsx`: importar `fontMono` y añadir `fontMono.variable` al `clsx(...)` del `<body>`.

Las cifras tabulares y el `0/O` distinguible de Fira Code SON la identidad de datos del producto. Este es el cambio de mayor retorno de toda la spec.

### 1.2 `themeColor` del viewport

`app/layout.tsx:21-26` apunta a `white`/`black`. Apuntar a aproximaciones hex de los tokens reales de `--background`:

```ts
themeColor: [
  { media: "(prefers-color-scheme: light)", color: "#f5f6f8" }, // ≈ oklch(97.02% 0.0026 243)
  { media: "(prefers-color-scheme: dark)",  color: "#15181b" }, // ≈ oklch(12% 0.0026 243)
],
```

### 1.3 Escala de espaciado (base 4px, solo tres huecos con nombre)

| Nombre | Valor | Uso EXCLUSIVO |
|---|---|---|
| `row` | `py-1` (4px) | filas de datos mono |
| `gutter` | `p-3` (12px) | padding interno de toda tarjeta |
| `block` | `gap-5` (20px) | entre tarjetas dentro de una columna |
| `section` | `gap-6` (24px) | entre columnas de grid / secciones de página |

Nada más. Eliminar los `mb-8`/`mb-6`/`mt-8` mezclados de admin y los `gap-4` ad-hoc: cada página es un stack `flex flex-col gap-6` (o grid `gap-6`) y dentro de cada zona `gap-5`.

### 1.4 Rampa tipográfica (Public Sans = voz, mono = datos; nunca mezclados)

| Rol | Clases exactas |
|---|---|
| Título de página | `text-lg font-bold tracking-[-0.01em]` (18px/700) — vive solo en `PageHeader` |
| Leyenda de sección | `text-[10px] font-bold uppercase tracking-[0.1em] text-muted` — vive solo en `LabelCaps` |
| Cuerpo y controles | `text-sm` (14px/400/1.5) |
| Label de campo | `text-xs font-medium` (12px/500) |
| Fila de datos mono | `font-mono text-[11px] leading-[1.4] tabular-nums` |
| Métrica | `font-mono text-lg font-extrabold tabular-nums` (ya correcto en `Metric`) |
| Centro del anillo | `font-mono text-[26px] font-extrabold tracking-[-0.03em] tabular-nums` |

**Un solo tracking para caps: `tracking-[0.1em]`.** Hoy conviven `0.08em`/`0.12em` — todos migran a `LabelCaps` (§2.3). La etiqueta `nueva` de `response-row.tsx:27` también pasa a `tracking-[0.1em]`.

### 1.5 Color (un significado por color — HeroUI semantic tokens)

- `accent` (azul) = el envío vivo: anillo enviando, Enviar, nav activa, focus. Nunca decorativo.
- `warning` (ámbar) = pausado/esperando, nunca roto: anillo en pausa, Pausar, FloodWait.
- `success` (verde) = valor capturado: CC nuevas, badge Filtrada, highlight `nueva`, Reanudar (el ÚNICO fill sólido), dot vivo.
- `danger` (rojo) = destructivo/fallido: Detener, Eliminar, ❌, errores reales. Nunca FloodWait.
- Profundidad = escalera `background → surface → surface-secondary → surface-tertiary` + bordes 1px. Sin sombras, sin gradientes (el cónico del anillo es la única excepción).

**Tintes alfa canónicos** (siempre alfa sobre token semántico, nunca valores dark hardcodeados):

| Uso | Clases |
|---|---|
| Pill Enviando / En curso | `bg-accent/22 text-accent` |
| Pill En pausa | `bg-warning/18 text-warning` |
| Pill Deteniendo | `bg-danger/18 text-danger` |
| Pill Cerrada / inactivo | `bg-surface-tertiary text-muted` |
| Highlight fila `nueva` | `bg-success/12 text-success` (tag: `bg-success/20`) |
| Strip FloodWait | `border-warning/50 bg-warning/12` |
| Strip líneas fallidas / ContactPanel | `border-danger/50 bg-danger/10` |

### 1.6 Tokens de borde y radio (matar la deriva)

- **Borde de tarjeta:** `border-border` SIEMPRE. Reemplazar todo `border-default/30` (`admin/users:259`, `admin/gates:305,606`).
- **Divisor de filas:** `divide-separator` / `border-separator` SIEMPRE. Reemplazar `divide-default/20` (`admin/gates:358`).
- **Texto atenuado:** `text-muted` SIEMPRE. Reemplazar todo `text-default-500` (`admin/users:93,161,689`; `admin/gates:135,192,356`).
- **Radio:** `rounded` (0.25rem, el token `--radius`) en toda superficie/badge/strip. Reemplazar `rounded-md` y `rounded-lg` en tarjetas, paneles, strips y badges. `rounded-full` queda reservado para: StatePill, dots (`size-1.5`/`size-6`) y nada más.

### 1.7 Grid de página y anchos máximos

- **Shell cliente** (`app/(client)/layout.tsx`): queda `max-w-6xl`. Envío conserva `lg:grid-cols-[300px_1fr_1fr] gap-6` con cockpit `sticky top-6` (no se toca, §4). Historial: contenido en `mx-auto w-full max-w-3xl`. Detalle: `max-w-6xl` (el del layout).
- **Admin:** mismo sistema de chrome, `max-w-5xl`, dos zonas `lg:grid-cols-[320px_1fr] gap-6` — formularios (columna 320px, `lg:sticky lg:top-6 lg:self-start`) + tabla.
- **Auth** (`login`, `change-password`, `expired`, `error.tsx`): centrado `max-w-sm`, centrado vertical (`min-h-screen items-center justify-center`), sin nav.
- **Mobile:** `px-4`, una columna, bottom nav fija, `pb-24` (ya correcto en el layout cliente).

### 1.8 Idioma de errores (tres casos, tres componentes — nunca `<span>` suelto)

1. **Error de campo** → HeroUI `FieldError` dentro del `TextField`/`Select` con `isInvalid` (idioma ya existente en login).
2. **Error de operación / banner** → HeroUI `Alert` compuesto: `Alert` con `status="danger"`; cuando hay qué-hacer, `Alert.Title` (qué falló) + `Alert.Description` (qué hacer).
3. **Espera impuesta** → strip ámbar (FloodWait), jamás rojo.

Eliminar TODOS los `<span className="text-sm text-danger">` sueltos (`admin/users:376,515,582,601,692,719`; `admin/gates:537,661,790,863`; `sessions/page.tsx:405,409,431`; `sessions/[id]/page.tsx:273`; los de `batch/*` quedan para §4).

### 1.9 Vacío / cargando

- **Vacío** = invitación dentro del panel al que pertenece: `EmptyState` (§2.7) — eyebrow en label-caps + una frase + acción opcional. Nunca icon-dump, nunca botón muerto.
- **Cargando** = `PanelSkeleton` (§2.8) con forma fiel (barras de altura de fila para paneles de datos y tablas). `Spinner color="accent"` queda SOLO para estados pending sub-segundo de botones. Eliminar todos los `<Spinner />` centrados flotantes (`admin/users:122-126`, `admin/gates:158-162,345-349`, `sessions/page.tsx:139-144`, `sessions/[id]:185-190`; el de `(client)/page.tsx` queda para §4).

### 1.10 Elemento firma: leyendas grabadas en la placa

Todo encabezado de sección se convierte en una leyenda caps trackeada que SE MONTA sobre el borde superior de la tarjeta, como grabado en equipo de rack:

```html
<section class="relative rounded border border-border bg-surface p-3">
  <span class="absolute -top-2 left-3 bg-background px-1.5
               text-[10px] font-bold uppercase tracking-[0.1em] text-muted">
    LEYENDA
  </span>
  …
</section>
```

Tailwind puro, cero dependencias. Implementado una sola vez en `SectionCard` (§2.1). Nunca anidar dos bordes: el énfasis interno dentro de una tarjeta es un bloque `bg-surface-secondary` sin borde.

---

## 2. Primitivas compartidas — `components/ui/` (archivos NUEVOS)

Crear `frontend/components/ui/` con estos 9 módulos. Todos client-safe (sin hooks de servidor), sin estado propio salvo donde se indica, exportes con nombre.

### 2.1 `components/ui/section-card.tsx`

```ts
export function SectionCard(props: {
  legend?: string;                       // texto de la leyenda grabada (se renderiza en caps)
  legendRight?: React.ReactNode;         // slot derecho sobre el borde (p.ej. CountBadge), absolute -top-2 right-3 bg-background px-1.5
  rail?: "accent" | "warning" | "none";  // riel izquierdo 2px de estado vivo; default "none"
  padding?: "gutter" | "none";           // default "gutter" (p-3); "none" para paneles de datos con scroll interno
  className?: string;
  children: React.ReactNode;
}): JSX.Element;
```

- Markup base: `<section className={clsx("relative rounded border border-border bg-surface", padding === "gutter" && "p-3", rail === "accent" && "border-l-2 border-l-accent", rail === "warning" && "border-l-2 border-l-warning", className)}>`.
- `legend` → el span de §1.10 (usa `LabelCaps` por dentro). `legendRight` → mismo patrón en `right-3`.
- Div hand-rolled a propósito (NO HeroUI `Card`): necesitamos la leyenda superpuesta y elevación cero.

### 2.2 `components/ui/page-header.tsx`

```ts
export function PageHeader(props: {
  title: string;
  mono?: string;                 // sub-línea mono opcional (font-mono text-[11px] text-muted truncate)
  back?: { href: string; label: string }; // link de vuelta estilo leyenda: "← LABEL"
  actions?: React.ReactNode;     // slot derecho (botones, pills)
  className?: string;
}): JSX.Element;
```

- `back` → `<Link href>` con clases de `LabelCaps` + `hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent`, contenido `← {label.toUpperCase()}`, renderizado encima del título con `gap-1`.
- Título: `text-lg font-bold tracking-[-0.01em] truncate`. Layout: `flex items-center justify-between gap-3`, título+mono en `min-w-0`.

### 2.3 `components/ui/label-caps.tsx`

```ts
export function LabelCaps(props: {
  children: React.ReactNode;
  className?: string;            // permite text-success etc. encima del default text-muted
  as?: "span" | "h2" | "label";  // default "span"
}): JSX.Element;
// clases: "text-[10px] font-bold uppercase tracking-[0.1em] text-muted"
```

Mata las 6+ copias divergentes (`metric.tsx:16`, `send-form.tsx:164` [§4], `sessions/page.tsx:91,173`, `sessions/[id]:84`, `response-views.tsx:203`).

### 2.4 `components/ui/state-pill.tsx`

```ts
export type PillTone = "accent" | "warning" | "danger" | "muted";
export function StatePill(props: {
  tone: PillTone;
  dot?: "pulse" | "static";      // dot 6px (size-1.5) a la izquierda; "pulse" = animate-pulse (respeta prefers-reduced-motion: motion-safe:animate-pulse)
  children: React.ReactNode;
  className?: string;
}): JSX.Element;
```

- HeroUI `Chip` con `rounded-full` (la ÚNICA forma full-round del sistema) + `text-[10px] font-bold uppercase tracking-[0.1em]`.
- Tintes por tono según tabla §1.5 (`accent` → `bg-accent/22 text-accent`, `warning` → `bg-warning/18 text-warning`, `danger` → `bg-danger/18 text-danger`, `muted` → `bg-surface-tertiary text-muted`).
- Consumidores: `client-nav.tsx` (Enviando=accent+pulse / En pausa=warning+static / Deteniendo=danger), Historial y Detalle ("En curso"=accent sin dot / "Cerrada"=muted) — reemplaza los dos `SessionBadge` duplicados a mano.

### 2.5 `components/ui/count-badge.tsx`

Extraer verbatim de `response-views.tsx:70-81`, cambiando `rounded-md` → `rounded`:

```ts
export function CountBadge(props: { value: number; tone?: "success" }): JSX.Element;
// "rounded bg-surface-secondary px-1.5 font-mono text-[11px] leading-5 tabular-nums" + tone success → text-success
```

### 2.6 `components/ui/mono-chip.tsx`

```ts
export function MonoChip(props: { children: React.ReactNode; className?: string }): JSX.Element;
// <span className="rounded border border-border bg-surface-secondary px-1.5 py-0.5 font-mono text-[11px] tabular-nums">
```

Para el valor de gate/prefijo (sub-líneas de Historial/Detalle, chip "Gate activo" en §4).

### 2.7 `components/ui/empty-state.tsx`

```ts
export function EmptyState(props: {
  eyebrow?: string;              // LabelCaps encima de la frase
  message: string;               // una frase plana, text-sm text-muted
  action?: React.ReactNode;      // opcional: Button o Link real, nunca disabled
  className?: string;
}): JSX.Element;
// layout: flex flex-col items-center gap-2 px-3 py-10 text-center
```

(HeroUI 3.1 trae `EmptyState`, pero esta versión hand-rolled de 10 líneas evita verificar su API compuesta; permitido migrar a la de HeroUI si se verifican los typings.)

### 2.8 `components/ui/panel-skeleton.tsx`

```ts
export function PanelSkeleton(props: { rows?: number; className?: string }): JSX.Element;
// default rows=5; por fila: <Skeleton className="h-4 rounded" /> de HeroUI dentro de
// un contenedor "flex flex-col gap-2 p-3"; verificar el typing de Skeleton en
// node_modules/@heroui/react antes de usar (lección 3.3) — fallback: div bg-surface-secondary animate-pulse.
```

### 2.9 `components/ui/admin-shell.tsx`

```ts
export function AdminShell(props: {
  title: string;                 // va al PageHeader
  gatesVisible?: boolean;        // owner ve el item Gates
  actions?: React.ReactNode;     // slot extra junto a Cerrar sesión
  children: React.ReactNode;
}): JSX.Element;
```

- Header strip idéntico en estructura al de `ClientNav`: `border-b border-border px-4 py-3 lg:px-6`, brand `CC` (link a `/admin/users`, `font-mono text-lg font-bold`), nav inline `Usuarios | Gates` (mismos estilos de item que §3.3, activo por `usePathname()` prefix-match), `Button size="sm" variant="secondary"` Cerrar sesión con el handler de logout actual (un solo sitio — mata las 3 copias).
- Debajo: `<main className="mx-auto w-full max-w-5xl px-4 py-6 lg:px-6"><div className="flex flex-col gap-6"><PageHeader title={title} actions={actions} />{children}</div></main>`.
- `"use client"` (usa pathname y el POST de logout).

**Nota `Metric`:** `components/batch/metric.tsx` queda como está funcionalmente (ya cumple la rampa); su label interno migra a `LabelCaps` en el pase §4, no antes (archivo batch = diferido).

---

## 3. Blueprint por superficie

### 3.1 `app/error.tsx` — reescritura completa

**Problemas:** bloque crudo sin estilo, inglés, `<h2>`/`<button>` pelados, sin centrado. La peor superficie de la app.

**Target exacto:**

```tsx
"use client";
import { useEffect } from "react";
import { Alert, Button } from "@heroui/react";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => { console.error(error); }, [error]);  // conservar
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="flex w-full max-w-sm flex-col gap-5">
        <Alert status="danger">
          <Alert.Title>Algo salió mal.</Alert.Title>
          <Alert.Description>Recarga la página o intenta de nuevo.</Alert.Description>
        </Alert>
        <Button variant="primary" onPress={() => reset()}>Reintentar</Button>
      </div>
    </main>
  );
}
```

(Verificar `Alert.Title`/`Alert.Description` en los typings; si la versión instalada no los expone, `Alert` con children planos.)

### 3.2 Auth: `login`, `change-password`, `expired` + `contact-panel.tsx`

**Problemas:** formulario flotando sin superficie ni marca; dos lenguajes de aviso danger en una pantalla (Alert vs ContactPanel hand-rolled); h1-frase en change-password; regla de 8 caracteres revelada solo tras fallar; `rounded-lg` fuera de token.

**Shell auth común (los tres + error.tsx):** `main` centrado vertical, `div` `flex w-full max-w-sm flex-col gap-5`:

1. **Marca:** `<span className="self-center font-mono text-2xl font-extrabold tracking-[-0.03em]">CC</span>` (mismo glifo que la nav; aquí no es link — no hay home sin sesión).
2. **PageHeader-lite:** título centrado `text-lg font-bold tracking-[-0.01em] text-center`.
3. **Avisos** (Alert / ContactPanel) en su orden actual.
4. **Formulario dentro de `SectionCard`** con leyenda.

**Login (`app/login/page.tsx`):**
- Título: `Iniciar sesión` (queda). Form en `<SectionCard legend="ACCESO">`; dentro, el `Form` actual sin cambios de campos/handlers/copys.
- El banner `too_many_attempts`/genérico sigue en `Alert status="danger"`; el caso blocked sigue en `ContactPanel` (restilizado abajo) — un solo lenguaje visual al unificar ContactPanel sobre Alert.
- Opcional (sin cambio de comportamiento — son links externos ya existentes en `siteConfig.contact`): línea muted bajo la tarjeta: `¿Problemas para entrar? Escríbenos por WhatsApp o Telegram.` con los dos links.

**Change-password (`app/change-password/page.tsx`):**
- `h1` deja de ser frase: título `Contraseña nueva`; la frase actual baja a subtexto `<p className="text-center text-sm text-muted">Elige una contraseña nueva para continuar.</p>` (copy intacta).
- Bajo el campo "Contraseña nueva", helper SIEMPRE visible: HeroUI `Description` dentro del TextField con `Mínimo 8 caracteres.` (el componente `description` existe en 3.1.0; verificar typing — fallback `<span className="text-xs text-muted">`). La validación y su mensaje de error no cambian.
- Form dentro de `<SectionCard legend="CONTRASEÑA">`.

**Expired (`app/expired/page.tsx`):** marca CC + título `Tu plan venció` + ContactPanel. Sin SectionCard extra (el ContactPanel ya es la superficie). Lógica del probe `/me` intacta.

**ContactPanel (`components/contact-panel.tsx`)** — mismo archivo, restilizado sobre Alert:

```tsx
<Alert className={className} status="danger">
  <Alert.Description>{message}</Alert.Description>
  <div className="mt-3 flex gap-2">
    {CHANNELS.map((c) => (
      <Button key={c.label} size="sm" variant="secondary" onPress={...}>{c.label}</Button>
    ))}
  </div>
</Alert>
```

(Si los slots compuestos no admiten children extra, conservar el div actual cambiando `rounded-lg` → `rounded` y manteniendo `border-danger/50 bg-danger/10` — prioridad: un solo radio y tintes canónicos.)

### 3.3 Nav shell cliente (`components/client-nav.tsx`)

**Problemas:** links sin hover/focus; brand `<span>` muerto; bottom nav sin safe-area; StatePill local en vez de compartido.

**Target:**
- `StatePill` local (líneas 23-54) se elimina; importar de `components/ui/state-pill.tsx` y mapear: `sending → tone="accent" dot="pulse"`, `paused → tone="warning" dot="static"`, `stopping → tone="danger"`. Copys `PILL_COPY` intactas.
- `NavItem` (línea 70-92): añadir a las clases del Link: `transition-colors hover:bg-surface-secondary hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent`, y `rounded-md` → `rounded`. Activo queda `bg-surface-tertiary text-foreground`.
- Brand (línea 135): `<Link href="/" className="font-mono text-lg font-bold tracking-[-0.03em]">CC</Link>`.
- Bottom nav (línea 146): añadir `pb-[max(0.5rem,env(safe-area-inset-bottom))]` (sustituye `py-2` por `pt-2` + ese pb); items con `flex-1 text-center` para que ocupen la barra (sigue `justify-around`-equivalente sin barra vacía).
- Dots del NavItem: sin cambios.

### 3.4 Shell admin (ambas páginas)

**Problemas:** sin shell real (cross-link como texto subrayado junto al h1); logout duplicado; ritmo `mb-8/mb-6/mt-8`; `max-w-4xl` y formularios apilados que entierran la tabla.

**Target:** ambas páginas se envuelven en `AdminShell` (§2.9) y montan el grid dos zonas:

```tsx
<AdminShell title="Usuarios" gatesVisible={isOwner}>
  <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
    <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">{/* formularios */}</div>
    <div>{/* tabla */}</div>
  </div>
</AdminShell>
```

Desaparecen: el `<header>` propio de cada página (users:88-103, gates:130-143), el link subrayado y la función `logout` local.

### 3.5 Admin Usuarios (`app/admin/users/page.tsx`)

**Problemas:** celdas Acciones con botones apilados + editores inline que inflan filas 3-6x; editor Renovar con dos TextFields embutidos en celda; dos tarjetas de creación consumiendo media pantalla; empty-state string pelado; `sm:mb-1` hack; spinner flotante.

**Target:**

*Zona izquierda (320px):*
- `<SectionCard legend="CREAR CLIENTE">` y (owner) `<SectionCard legend="CREAR ADMIN">`, `gap-5` entre ellas.
- `CreateUserForm`: campos **apilados en vertical** (`flex flex-col gap-3`, todos `w-full` — eliminar `sm:flex-row sm:items-end`, `sm:w-32` y el hack `sm:mb-1`), botón al final `w-full`. El `<h2>` interno desaparece (lo sustituye la leyenda). Handlers, validación y copys intactos.

*Zona derecha:*
- `<SectionCard legend="USUARIOS" padding="none">` envolviendo la `Table` actual.
- Loading: `PanelSkeleton rows={5}` en lugar del Spinner. Error: Alert (queda).
- `renderEmptyState`: `<EmptyState eyebrow="Usuarios" message="Todavía no hay clientes." />` (la acción de crear está visible al lado — no necesita CTA).
- Estado: `Bloqueado` queda `font-medium text-danger`; `Activo` pasa a `text-muted`.
- **Celda Acciones — fila de botones horizontales, sin expansión inline JAMÁS:** `<div className="flex gap-2">` con `Renovar`/`Bloquear|Desbloquear`/`Resetear` (cliente) o `Eliminar` (admin, owner). Todo lo que hoy se expande dentro de la celda migra a `AlertDialog` (componente ya probado en `sessions/page.tsx:414-458` — patrón calcado):
  - **Renovar** → AlertDialog con heading `Renovar plan`, body con los dos TextFields actuales (Días `w-24` + Hasta date, lado a lado), error como `Alert status="danger"` dentro del body, footer Cancelar/`Renovar` (primary). Mutación y validación `hasDays === hasDate` intactas.
  - **Bloquear** (confirm) → AlertDialog: heading = copy actual `¿Bloquear a {email}? Su sesión se cerrará al instante.`, footer Cancelar / `Sí, bloquear` (danger). **Desbloquear** sigue siendo single-press sin confirm (no destructivo) — su error pasa a Alert bajo… no: al no haber ya layout inline, el error de desbloquear se muestra en un `Alert status="danger"` renderizado por la página encima de la tabla? No — mantenerlo simple: el error de Desbloquear se muestra en un AlertDialog NO; conservar el render condicional actual pero como `<Alert status="danger" className="mt-1">` compacto bajo el botón (excepción única documentada: error de acción single-press).
  - **Resetear** → AlertDialog para el confirm (copy actual) Y para la contraseña temporal: al éxito el mismo diálogo muta a heading `Contraseña temporal`, body con `<span className="font-mono text-sm">{tempPassword}</span>` + `Cópiala ahora: no volverá a mostrarse.` (text-sm text-muted) + error de copy como Alert, footer `Copiar` (secondary) / `Listo` (primary). **Cerrar el diálogo por CUALQUIER vía ejecuta `dismiss()`** (semántica exactly-once intacta: `onOpenChange(false)` → dismiss).
  - **Eliminar admin** → AlertDialog confirm (copy actual `¿Eliminar este admin? ({email})`), footer Cancelar / `Sí, eliminar` (danger).
- Con esto las filas tienen altura constante y la columna Acciones ancho estable; no hace falta control de anchos de columna.

### 3.6 Admin Gates (`app/admin/gates/page.tsx`)

**Problemas:** editor Editar (3 campos) dentro de celda — el peor ballooning; error de CategorySelect como span fuera del campo sin `isInvalid`; tres tarjetas apiladas antes de la tabla; lista de categorías con sistema visual distinto al de la tabla hermana; select vacío sin pista; spinner flotante.

**Target:**

*Zona izquierda (320px):*
- `<SectionCard legend="CATEGORÍAS">`: form de crear (campo + botón apilados en vertical, sin `sm:w-64` ni `sm:mb-1`) y debajo la lista. Lista: `divide-default/20` → `divide-separator`; filas `py-2`; nombre `text-sm`. Rename inline se CONSERVA (un solo campo, cabe en 320px) pero su error pasa a `FieldError` dentro del TextField con `isInvalid`. El confirm de eliminar categoría migra a `AlertDialog` (heading = copy actual `¿Eliminar la categoría "{name}"?`; el error `category_in_use` se muestra dentro del diálogo como Alert sin cerrarlo). Loading de la lista: `PanelSkeleton rows={3}`. Vacío: `<EmptyState message="Todavía no hay categorías." />`.
- `<SectionCard legend="CREAR GATE">`: Nombre, Gate, Categoría **apilados en vertical, todos `w-full`** (eliminar `sm:w-56`/`sm:w-48` y `sm:mb-1`), botón al final.

*`CategorySelect` (líneas 219-253) — firma ampliada:*

```ts
function CategorySelect(props: {
  categories: CategoryOut[];
  value: number | null;
  onChange: (id: number | null) => void;
  label?: string;
  isInvalid?: boolean;
  errorMessage?: string | null;   // se renderiza como <FieldError> dentro del Select
  className?: string;             // default w-full
}): JSX.Element;
```

- Pasar `isInvalid` al `Select` y renderizar `<FieldError>{errorMessage}</FieldError>` dentro (verificar en typings que `Select` acepta `isInvalid` y el slot FieldError — lección 3.3; fallback: `<span className="text-xs text-danger">` JUNTO al label, dentro del mismo bloque del campo, nunca suelto fuera). Los dos call-sites (`CreateGateForm`, `EditGateAction`) pasan su `categoryError`/`error` por aquí y eliminan los spans sueltos (líneas 660-663, 790).
- **Catálogo de categorías vacío:** si `categories.length === 0`, el ListBox renderiza un único item deshabilitado: `<ListBox.Item id="__none" isDisabled textValue="Sin categorías">Primero crea una categoría.</ListBox.Item>` — no seleccionable, cero cambio de comportamiento.

*Zona derecha:*
- `<SectionCard legend="CATÁLOGO" padding="none">` con la Table. Loading: `PanelSkeleton rows={5}`. Vacío: `<EmptyState message="El catálogo está vacío." />`.
- Celda Gate: queda `font-mono text-sm` → pasar a `MonoChip` (`<MonoChip>{g.value}</MonoChip>`). Celda Creado: `font-mono text-[11px] text-muted tabular-nums`.
- **Celda Acciones:** `<div className="flex gap-2">` Editar / Eliminar (horizontales).
  - **Editar** → AlertDialog: heading `Editar gate`, body con los tres campos actuales apilados `w-full` (Nombre, Gate mono, `CategorySelect`), error de validación/operación como Alert dentro del body, footer Cancelar / Guardar (primary). Mutación, validaciones y manejo de `gate_not_found` intactos.
  - **Eliminar** → AlertDialog confirm (copy actual `¿Eliminar este gate? ({value} en mono)`), error dentro del diálogo como Alert, footer Cancelar / Eliminar (danger).

### 3.7 Historial (`app/(client)/sessions/page.tsx`)

**Problemas:** tres botones sm en CADA fila aplastando el título en mobile; header de grupo más débil que sus filas (jerarquía invertida); id interno crudo (`{gate_value} · {session.id}`) expuesto al cliente; SessionBadge duplicado a mano; spinner flotante; errores como spans.

**Target:**
- Envolver todo en `<div className="mx-auto w-full max-w-3xl flex flex-col gap-6">` con `<PageHeader title="Historial" />` arriba.
- Loading: `PanelSkeleton rows={6}`. Error: Alert (queda). Vacío: `<EmptyState eyebrow="Historial" message="Todavía no tienes sesiones. Tu primer lote crea una." action={<Button variant="primary" onPress={→ router.push("/")}>Ir a Envío</Button>} />` — copy del mensaje VERBATIM como hoy (AC 7); el link actual pasa a Button-link (HeroUI `Link` o Button con navegación — comportamiento idéntico: navegar a `/`).
- **Grupo por gate → SectionCard:** `<SectionCard legend={group.gateName} legendRight={<MonoChip>{group.gateValue}</MonoChip>} padding="none">` conteniendo el `<ul className="flex flex-col divide-y divide-separator">`. El header suelto actual (líneas 171-179) desaparece — la leyenda grabada ES el header y queda jerárquicamente encima de las filas.
- **Fila** (`SessionRow`):
  - Layout: `flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2` — en <sm los botones envuelven a su propia línea en vez de aplastar el título (`min-w-0 flex-1` del Link queda).
  - Sub-línea: **eliminar `{session.gate_value} · {session.id}`** (el gate ya es la leyenda del grupo; el id es dato de debug). Nueva sub-línea: `fallbackName(session.created_at)` en `font-mono text-[11px] text-muted` — y SOLO cuando `session.name !== null` (si no, el título ya es esa fecha; sin sub-línea).
  - Badge: `SessionBadge` local (líneas 87-100) se elimina → `<StatePill tone={isActive ? "accent" : "muted"}>{isActive ? "En curso" : "Cerrada"}</StatePill>`.
  - Acciones: quedan los tres botones (Continuar solo en cerradas — regla intacta), `size="sm"`; Eliminar mantiene `variant="danger"`.
  - Rename inline: el TextField gana `isInvalid={renameError !== null}` y `<FieldError>{renameError}</FieldError>` dentro; el span suelto (línea 404-406) desaparece.
  - `continueError` (líneas 408-410) → `<Alert status="danger" className="w-full">` (basis completa dentro del wrap).
  - AlertDialog de borrado: queda; `deleteError` dentro del body pasa de span a `<Alert status="danger">`. Copys intactas.

### 3.8 Detalle de sesión (`app/(client)/sessions/[id]/page.tsx`)

**Problemas:** "← Historial" como link crudo flotante; continueError span sin anclar; sin fecha de creación en el header; SessionBadge duplicado; spinner flotante.

**Target:**
- Header completo via `PageHeader`:

```tsx
<PageHeader
  back={{ href: "/sessions", label: "Historial" }}
  title={data.name ?? fallbackName(data.created_at)}
  actions={<>
    {!data.is_active && <Button …>Continuar</Button>}   {/* botón actual intacto */}
    <StatePill tone={data.is_active ? "accent" : "muted"}>{data.is_active ? "En curso" : "Cerrada"}</StatePill>
  </>}
/>
```

- Sub-línea del header (slot `mono` de PageHeader o línea propia bajo el título): `<MonoChip>{data.gate_value}</MonoChip>` + `<span className="font-mono text-[11px] text-muted">{fallbackName(data.created_at)}</span>` — **se elimina el `· {data.id}`** y se AÑADE la fecha de creación (hoy ausente).
- `continueError` → `<Alert status="danger">` inmediatamente bajo el header (anclado al stack `gap-5`).
- `SessionBadge` local (líneas 80-93) se elimina → `StatePill`.
- Loading: `<div className="grid gap-6 lg:grid-cols-2"><PanelSkeleton rows={8} /><PanelSkeleton rows={8} className="hidden lg:flex" /></div>`.
- `NotFound`: pasar a `<EmptyState eyebrow="Historial" message="Esa sesión no existe." action={<Link …>Volver a Historial</Link>} />` centrado en `py-24` (copys intactas).
- Paneles y tabs: sin cambios aquí — heredan §3.9. Stack de página: `flex flex-col gap-5`.
- **Fuera de alcance** (cambiaría comportamiento): añadir Renombrar en el detalle. Anotar como mejora futura, no implementar.

### 3.9 Paneles de datos (`components/sessions/response-views.tsx`, `response-row.tsx`)

(Compartidos por Envío y Detalle; NO son archivos batch — se tocan ahora. El efecto visual aparecerá también en Envío sin editar archivos diferidos.)

**`ResponsePanel` (líneas 175-217) → patrón SectionCard tier-2 (panel de datos):**
- El `<section className="flex flex-col rounded-md border border-border bg-surface">` pasa a usar `SectionCard padding="none"` con `className="flex flex-col"`; cuando `header` está presente: `legend={header}` (leyenda grabada COMPLETA/FILTRADA sobre el borde) y `legendRight={<CountBadge tone={countTone} value={count} />}` (el contador como LED en la placa). El header strip interno (líneas 201-208) **desaparece** — la lista empieza directamente; darle `pt-2` al PanelList para despejar la leyenda.
- Cuando `header` es undefined (tabs mobile: el strip de tabs lleva label+badge): SectionCard sin leyenda, igual que hoy.
- Footer export: queda (`border-t border-border px-3 py-2`); `ExportLink` sigue siendo `<button>` plano (decisión deliberada registrada) pero gana `hover:underline focus-visible:outline-2 focus-visible:outline-accent`; su error pasa a `text-[11px] text-danger` (queda — es micro-feedback de footer, excepción documentada).
- `CountBadge` local (líneas 70-81) se elimina → importar de `components/ui/count-badge.tsx`. Empty texts (`EMPTY_COMPLETA`/`EMPTY_FILTRADA`) VERBATIM; el `<p>` vacío de PanelList puede quedar como está (es el tier "vacío dentro de panel" más simple) o pasar a `EmptyState` sin eyebrow — preferencia: `EmptyState`.

**`DataRow` (`response-row.tsx`):** única densidad consola del sistema, apretada:
- Gutter izquierdo a ancho fijo: `<span className="w-14 shrink-0 text-right text-muted tabular-nums">` (timestamps e índices alinean como cinta verdadera; hoy `shrink-0` sin ancho).
- Tag `nueva`: `tracking-[0.08em]` → `tracking-[0.1em]`, `rounded-md` → `rounded`.
- El resto (py-1 px-3, border-separator, truncate, glifo derecha, `bg-success/12 text-success`) ya cumple — no tocar.
- Mejora opcional (CSS puro, respeta `prefers-reduced-motion`): `transition-colors duration-700 motion-reduce:transition-none` en el contenedor para que el highlight `nueva` se desvanezca suavemente cuando el store le quita el flag.

---

## 4. Envío — **DIFERIDO — NO IMPLEMENTAR EN ESTE PASE**

> `app/(client)/page.tsx` y `components/batch/*` están bajo desarrollo activo (story 2-2 en curso). Este pase NO los toca. Lo siguiente es el target para que un pase posterior lo aplique sin re-derivar decisiones. Los paneles Completa/Filtrada del Envío SÍ se actualizan vía §3.9 (archivos compartidos, permitidos).

**Problemas registrados:** columna 300px vs selects de 224/256px que envuelven feo entre sm y lg; estado idle = un `<p>` donde vive el anillo de 128px (salto de layout al arrancar); `gap-8` del ring desborda la columna; botones de control con overrides bg/text crudos peleando con las variantes HeroUI; selectError desanclado de ambos selects; lista de líneas fallidas sin tope de altura; radios `rounded-md` en strips.

**Target (pase futuro):**

1. **Cockpit como instrumento único:** los bloques del cockpit pasan a `SectionCard` con riel de estado: `<SectionCard legend="CONTROLES" rail={state === "sending" ? "accent" : state === "paused" ? "warning" : "none"}>` para BatchControls, `<SectionCard legend="NUEVO LOTE">` para SendForm. El anillo va arriba sin tarjeta (es el título de la página — Envío no lleva PageHeader).
2. **Placeholder del anillo en idle (cero salto de layout):** en vez del `<p>` (page.tsx:55-59), renderizar el `ProgressCircle` con `value={0}` y track muted, centro `—` mono, y debajo la frase actual `Pega tus líneas y elige un gate.` en `text-sm text-muted text-center`. Misma altura ocupada idle/vivo.
3. **ProgressRing:** `justify-center gap-8` → `justify-between gap-4` (cabe en 300px); centro a `tracking-[-0.03em]`; labels de `Metric` via `LabelCaps`.
4. **BatchControls:** eliminar overrides `bg-surface-secondary text-warning` etc. Pausar = `variant="secondary"` + `className="text-warning"` (solo color de texto); Detener = `variant="secondary"` + `className="text-danger"`; Reanudar conserva el ÚNICO fill sólido success (`variant="primary"` + `className="bg-success text-success-foreground"` — excepción registrada del DESIGN). Error → Alert.
5. **SendForm:** selects apilados en vertical `w-full` (eliminar `sm:flex-row`, `sm:w-56`, `sm:w-64` — dentro de 300px no hay lado a lado); `selectError` se ancla al select culpable vía `isInvalid` + `FieldError` (categoría sin elegir → bajo Categoría; gate sin elegir → bajo Gate); chip "Gate activo" → `LabelCaps` + `MonoChip`; label del textarea queda, textarea `font-mono` queda.
6. **FloodNotice:** queda strip ámbar custom con countdown mono (NUNCA Alert danger); `rounded-md` → `rounded`.
7. **FailedLines:** `rounded-md` → `rounded`; lista con `max-h-40 overflow-y-auto` (tope: cientos de fallos no estiran el cockpit); filas en `font-mono text-[11px]`.
8. **Gates loading:** Spinner → `PanelSkeleton rows={2}` dentro del SectionCard del form.
9. Grid de página `lg:grid-cols-[300px_1fr_1fr] gap-6` + sticky: sin cambios.

---

## 5. Restricciones duras

1. **Solo HeroUI v3.1.0 + Tailwind v4.** Cero dependencias nuevas, cero CSS custom fuera de utilidades Tailwind (la única excepción ya existente: tokens en `globals.css`).
2. **Cero cambio de comportamiento.** Mismas rutas REST, mismos handlers/mutaciones/invalidaciones de react-query, mismas validaciones cliente, misma máquina de estados WS. Las copys con AC verbatim NO cambian de texto (pueden cambiar de contenedor): empty states de paneles y de Historial, copys de pills, confirmaciones de borrado, mensajes de error mapeados, `Cópiala ahora: no volverá a mostrarse.`, frase de change-password (se mueve a subtexto, no se reescribe).
3. **Sin renombrar ni mover archivos.** Archivos NUEVOS solo bajo `components/ui/`. Borrar está permitido únicamente para el código muerto listado en §0 (verificar con grep que siguen sin imports antes de borrar).
4. **No tocar** `app/(client)/page.tsx` ni `components/batch/*` (§4 diferido). No tocar `lib/*`, `types/*`, `middleware.ts`, backend.
5. **Verificar typings HeroUI antes de usar API compuesta no probada en el repo** (lección 3.3): probados en el código actual — Button, Form, TextField/Input/Label/FieldError, TextArea, Alert, Spinner, Table, Select+ListBox, Tabs, Chip, ProgressCircle, AlertDialog. A verificar en `node_modules/@heroui/react/dist/index.d.ts` antes de usar: `Skeleton`, `Description`, `Alert.Title`/`Alert.Description`, `Select` con `isInvalid`+`FieldError`. Cada uno tiene fallback especificado en su sección. NO introducir `Menu`/`Popover`/`Modal` en este pase (AlertDialog cubre todos los casos y ya está probado).
6. **Accesibilidad mínima:** todo elemento interactivo conserva/gana `focus-visible` (outline accent); animaciones bajo `motion-safe:`/`motion-reduce:`; los AlertDialog conservan heading descriptivo.
7. Reglas del repo: jamás leer `respuestas/`, jamás tocar `.env` ni `anon.session`, jamás `git push`.

## 6. Orden de implementación (payoff visual primero)

1. §1.1–1.2 fuente mono + themeColor (3 archivos, riesgo cero).
2. §2 primitivas `components/ui/` (sin consumidores aún — compilables en aislamiento).
3. Barrido de tokens §1.4–1.6 (tracking/radios/bordes/text-muted) en archivos NO diferidos.
4. §3.1 error.tsx + §3.2 auth (superficies pequeñas, validan las primitivas).
5. §3.3 nav cliente + §3.9 paneles de datos (el look "rack" aparece en Envío y Detalle).
6. §3.7 Historial + §3.8 Detalle.
7. §3.4–3.6 admin (el cambio más grande: shell dos zonas + migración a AlertDialog).
8. Borrado de código muerto. — §4 queda para el pase posterior.

**Criterio de hecho:** `npm run build` limpio; cero `<span className="text-sm text-danger">` sueltos fuera de §4; cero `rounded-lg`/`border-default/30`/`divide-default/20`/`text-default-500` fuera de §4; Fira Code visible en cualquier `font-mono` (verificar en devtools que resuelve a "Fira Code"); ninguna fila de tabla admin cambia de altura al interactuar.
