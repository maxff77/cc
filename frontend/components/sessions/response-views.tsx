"use client";

// Dual/triple Completa·Filtrada views (Story 3.2) — "same components,
// recomposed": ONE native panel (header/badge + scrollable list + empty state)
// instantiated per view. Desktop: three side-by-side panels (Ranger-X handoff
// ResultPanel grid). Mobile: native segmented tabs carrying the labels + badges.
// Props-driven (rows + counts in, no store reads inside) so Story 3.3's
// Historial detail reuses these panels verbatim. Export (Story 3.5): each panel
// takes an optional `exportPath` — present ⇒ a `↓ .txt` footer downloads the
// view via fetch+blob; absent ⇒ no footer (zero dead buttons).
import type { CcRow, ResponseRow } from "@/lib/ws";

import { useLayoutEffect, useRef, useState } from "react";
import clsx from "clsx";

import { ApiError, downloadFile } from "@/lib/api";
import { CountBadge } from "@/components/ui/count-badge";
import { LabelCaps } from "@/components/ui/label-caps";
import { Icon } from "@/components/ui/icon";
import { DataRow, type DataRowProps } from "@/components/sessions/response-row";

// Empty states — copy VERBATIM (EXPERIENCE.md): no fake rows, badges at 0.
const EMPTY_COMPLETA = "Aún no hay respuestas.";
const EMPTY_FILTRADA_CON = "Aún no hay respuestas con ✅.";
const EMPTY_FILTRADA = "Aún no hay datos CC: capturados.";

const BOTTOM_THRESHOLD_PX = 24;
// Tall internal scroll on desktop columns; capped on the stacked mobile tabs.
const COLUMN_LIST = "max-h-[calc(100vh-220px)]";
const TAB_LIST = "max-h-72";

interface RowData extends DataRowProps {
  key: string;
}

function formatTime(iso: string): string {
  const date = new Date(iso);

  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function completaRows(responses: ResponseRow[]): RowData[] {
  return responses.map((row) => ({
    key: row.key,
    left: formatTime(row.capturedAt),
    text: row.text,
    status: row.status,
    nueva: row.nueva,
  }));
}

function filtradaRows(cc: CcRow[], total: number): RowData[] {
  // No glyph and no timestamp — Filtrada rows are data, not states; the left
  // slot is the 001-style insertion index (parity with filtrada.txt). Offset by
  // the authoritative total: the lists ship only the LAST rows (snapshot
  // capped), so the array position misnumbers rows whenever the cap kicked in.
  return cc.map((row, index) => ({
    key: row.key,
    left: String(total - cc.length + index + 1).padStart(3, "0"),
    text: row.text,
    nueva: row.nueva,
  }));
}

// Scrollable row list with auto-scroll pinning (AC 4): follow new rows ONLY
// when the pane was already at the bottom; scrolled away, the view stays pinned
// where the operator left it (legacy rule, literal).
function PanelList({
  rows,
  emptyText,
  className,
}: {
  rows: RowData[];
  emptyText: string;
  className?: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  useLayoutEffect(() => {
    const el = listRef.current;

    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [rows.length]);

  return (
    <div
      ref={listRef}
      className={clsx("rx-scroll min-h-0 flex-1 overflow-y-auto", className)}
      onScroll={() => {
        const el = listRef.current;

        if (el) {
          atBottom.current =
            el.scrollHeight - el.scrollTop - el.clientHeight <
            BOTTOM_THRESHOLD_PX;
        }
      }}
    >
      {rows.length === 0 ? (
        <p className="px-3 py-4 text-sm text-muted">{emptyText}</p>
      ) : (
        rows.map((row) => (
          <DataRow
            key={row.key}
            left={row.left}
            nueva={row.nueva}
            status={row.status}
            text={row.text}
          />
        ))
      )}
    </div>
  );
}

// Footer export link (Story 3.5) — plain button (console density). Pending and
// error are local: a failed download never breaks the panel.
function ExportLink({ path }: { path: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <>
      <button
        className="rx-focus inline-flex items-center gap-1.5 font-mono text-[11.5px] text-accent disabled:opacity-50"
        disabled={pending}
        type="button"
        onClick={async () => {
          setPending(true);
          setError(null);
          try {
            await downloadFile(path);
          } catch (err) {
            setError(
              err instanceof ApiError
                ? err.message
                : "No pudimos conectar. Intenta de nuevo.",
            );
          } finally {
            setPending(false);
          }
        }}
      >
        <Icon name="download" size={13} />
        {pending ? "Descargando…" : ".txt"}
      </button>
      {error && <span className="text-[11px] text-danger">{error}</span>}
    </>
  );
}

// One-line set-relationship legend (kept for Historial detail callers).
export function ResponseViewsLegend({ className }: { className?: string }) {
  return (
    <p className={clsx("text-xs text-muted", className)}>
      <span className="text-foreground">Todas las respuestas</span> incluye ✅ y
      ❌; <span className="text-foreground">Aprobadas</span> son solo las ✅; y{" "}
      <span className="text-foreground">Datos CC</span> son los datos extraídos
      de esas respuestas.
    </p>
  );
}

// THE panel — native rack plate (Flat-Plate doctrine: bg-surface + 1px border,
// zero elevation in BOTH themes), optional LabelCaps header (mobile tabs carry
// the label/badge instead) and optional `↓ .txt` export footer.
function ResponsePanel({
  header,
  count,
  countTone,
  emptyText,
  rows,
  exportPath,
  listClassName,
  className,
}: {
  header?: string;
  count: number;
  countTone?: "success";
  emptyText: string;
  rows: RowData[];
  exportPath?: string;
  listClassName?: string;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "flex min-w-0 flex-col overflow-hidden rounded-[var(--radius)] border border-border bg-surface",
        className,
      )}
    >
      {header && (
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
          <LabelCaps className="tracking-[0.12em]">{header}</LabelCaps>
          <CountBadge tone={countTone} value={count} />
        </div>
      )}
      <PanelList className={listClassName} emptyText={emptyText} rows={rows} />
      {exportPath && (
        <div className="border-t border-border px-3 py-2">
          <ExportLink path={exportPath} />
        </div>
      )}
    </div>
  );
}

export function CompletaPanel({
  responses,
  total,
  header = true,
  exportPath,
  listClassName,
  className,
}: {
  responses: ResponseRow[];
  total: number;
  header?: boolean;
  exportPath?: string;
  listClassName?: string;
  className?: string;
}) {
  return (
    <ResponsePanel
      className={className}
      count={total}
      emptyText={EMPTY_COMPLETA}
      exportPath={exportPath}
      header={header ? "Completa" : undefined}
      listClassName={listClassName}
      rows={completaRows(responses)}
    />
  );
}

// "Filtrada con respuesta" (full text of only the ✅ revisions). SAME row shape
// as Completa — full text + the ✅ glyph — just the status-filtered subset.
export function FiltradaConResponsePanel({
  responses,
  total,
  header = true,
  exportPath,
  listClassName,
  className,
}: {
  responses: ResponseRow[];
  total: number;
  header?: boolean;
  exportPath?: string;
  listClassName?: string;
  className?: string;
}) {
  return (
    <ResponsePanel
      className={className}
      count={total}
      countTone="success"
      emptyText={EMPTY_FILTRADA_CON}
      exportPath={exportPath}
      header={header ? "Filtrada con respuesta" : undefined}
      listClassName={listClassName}
      rows={completaRows(responses.filter((row) => row.status === "ok"))}
    />
  );
}

export function FiltradaPanel({
  cc,
  total,
  header = true,
  exportPath,
  listClassName,
  className,
}: {
  cc: CcRow[];
  total: number;
  header?: boolean;
  exportPath?: string;
  listClassName?: string;
  className?: string;
}) {
  return (
    <ResponsePanel
      className={className}
      count={total}
      countTone="success"
      emptyText={EMPTY_FILTRADA}
      exportPath={exportPath}
      header={header ? "Filtrada sin respuesta" : undefined}
      listClassName={listClassName}
      rows={filtradaRows(cc, total)}
    />
  );
}

interface ResponseViewsProps {
  responses: ResponseRow[];
  cc: CcRow[];
  responsesTotal: number;
  responsesOkTotal: number;
  ccTotal: number;
  exportPathCompleta?: string;
  exportPathFiltradaCompleta?: string;
  exportPathFiltrada?: string;
  className?: string;
}

// Desktop recomposition: three side-by-side panels (handoff ResultPanel grid),
// each with its header + live count badge and tall internal scroll.
export function ResponseColumns({
  responses,
  cc,
  responsesTotal,
  responsesOkTotal,
  ccTotal,
  exportPathCompleta,
  exportPathFiltradaCompleta,
  exportPathFiltrada,
  className,
}: ResponseViewsProps) {
  return (
    <div className={clsx("grid grid-cols-3 gap-5", className)}>
      <CompletaPanel
        exportPath={exportPathCompleta}
        listClassName={COLUMN_LIST}
        responses={responses}
        total={responsesTotal}
      />
      <FiltradaConResponsePanel
        exportPath={exportPathFiltradaCompleta}
        listClassName={COLUMN_LIST}
        responses={responses}
        total={responsesOkTotal}
      />
      <FiltradaPanel
        cc={cc}
        exportPath={exportPathFiltrada}
        listClassName={COLUMN_LIST}
        total={ccTotal}
      />
    </div>
  );
}

// Mobile recomposition: native segmented Completa | Aprobadas | Datos CC tabs,
// each with its live count badge; the lists keep a capped height with internal
// scroll so the cockpit form stays reachable.
type TabId = "completa" | "con-response" | "sin-response";

export function ResponseTabs({
  responses,
  cc,
  responsesTotal,
  responsesOkTotal,
  ccTotal,
  exportPathCompleta,
  exportPathFiltradaCompleta,
  exportPathFiltrada,
  className,
}: ResponseViewsProps) {
  const [tab, setTab] = useState<TabId>("completa");

  const TABS: { id: TabId; label: string; count: number; tone?: "success" }[] =
    [
      { id: "completa", label: "Completa", count: responsesTotal },
      {
        id: "con-response",
        label: "Con respuesta",
        count: responsesOkTotal,
        tone: "success",
      },
      {
        id: "sin-response",
        label: "Sin respuesta",
        count: ccTotal,
        tone: "success",
      },
    ];

  return (
    <div className={className}>
      <div className="flex gap-1 rounded-[var(--radius-field)] border border-border bg-surface-secondary p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            aria-selected={tab === t.id}
            className={clsx(
              "rx-focus flex flex-1 items-center justify-center gap-1.5 rounded-[var(--radius-sm)] px-2 py-2 font-display text-[13px] font-semibold transition-colors",
              tab === t.id
                ? "bg-surface-tertiary text-foreground"
                : "text-muted hover:text-foreground",
            )}
            role="tab"
            type="button"
            onClick={() => setTab(t.id)}
          >
            {t.label} <CountBadge tone={t.tone} value={t.count} />
          </button>
        ))}
      </div>
      <div className="mt-3">
        {tab === "completa" && (
          <CompletaPanel
            exportPath={exportPathCompleta}
            header={false}
            listClassName={TAB_LIST}
            responses={responses}
            total={responsesTotal}
          />
        )}
        {tab === "con-response" && (
          <FiltradaConResponsePanel
            exportPath={exportPathFiltradaCompleta}
            header={false}
            listClassName={TAB_LIST}
            responses={responses}
            total={responsesOkTotal}
          />
        )}
        {tab === "sin-response" && (
          <FiltradaPanel
            cc={cc}
            exportPath={exportPathFiltrada}
            header={false}
            listClassName={TAB_LIST}
            total={ccTotal}
          />
        )}
      </div>
    </div>
  );
}
