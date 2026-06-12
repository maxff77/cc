"use client";

// Dual Completa/Filtrada views (Story 3.2) — AC 1 "same components,
// recomposed": ONE panel (header/badge + scrollable list + empty state)
// instantiated per view; on mobile the views are segmented HeroUI Tabs (the
// tab strip carries the labels + badges), on desktop two side-by-side panels
// with label-caps headers. Props-driven on purpose (rows + counts in, no
// store reads inside): Story 3.3's Historial detail reuses these panels
// verbatim. Export (Story 3.5): each panel takes an optional `exportPath` —
// when present, a `↓ .txt` footer link downloads the view via fetch+blob; no
// prop, no footer (zero dead buttons). On mobile the link lives in the
// panel footer INSIDE each Tabs.Panel, not in the tab strip (recorded
// deviation from DESIGN: the strip would force controlled Tabs just to know
// which view to export; the spine — one per view, both sections — is met).
import type { CcRow, ResponseRow } from "@/lib/ws";

import { useLayoutEffect, useRef, useState } from "react";
import { Card, Tabs } from "@heroui/react";
import clsx from "clsx";

import { ApiError, downloadFile } from "@/lib/api";
import { DataRow, type DataRowProps } from "@/components/sessions/response-row";

// Empty states — copy VERBATIM (EXPERIENCE.md): no fake rows, badges at 0.
const EMPTY_COMPLETA = "Aún no hay respuestas.";
const EMPTY_FILTRADA_CON = "Aún no hay respuestas con ✅.";
const EMPTY_FILTRADA = "Aún no hay datos CC: capturados.";

// A pane counts as "at the bottom" within this many px — generous enough to
// absorb sub-row scroll jitter without unpinning.
const BOTTOM_THRESHOLD_PX = 24;

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
  // slot is the 001-style insertion index (parity with filtrada.txt).
  // Offset by the authoritative total: the lists ship only the LAST rows
  // (snapshot capped server-side), so the array position misnumbers rows
  // whenever the cap kicked in. Identity when nothing was trimmed.
  return cc.map((row, index) => ({
    key: row.key,
    left: String(total - cc.length + index + 1).padStart(3, "0"),
    text: row.text,
    nueva: row.nueva,
  }));
}

// Live mono count badge — Filtrada's travels in success green
// (DESIGN.md `dual-view-tabs.count-badge-filtrada`). Visible at 0 too (AC 5).
function CountBadge({ value, tone }: { value: number; tone?: "success" }) {
  return (
    <span
      className={clsx(
        "rounded-md bg-surface-secondary px-1.5 font-mono text-[11px] leading-5 tabular-nums",
        tone === "success" && "text-success",
      )}
    >
      {value}
    </span>
  );
}

// Scrollable row list with auto-scroll pinning (AC 4): follow new rows ONLY
// when the pane was already at the bottom; scrolled away, the view stays
// pinned where the operator left it (legacy rule, literal).
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
      className={clsx("min-h-0 overflow-y-auto", className)}
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

// Footer export link (Story 3.5) — plain button on purpose (consola
// density; the DESIGN calls it a "footer export link" and a plain <button>
// skips verifying HeroUI variant typings — 3.3 lesson). Pending and error
// are local: a failed download never breaks the panel.
function ExportLink({ path }: { path: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <>
      <button
        className="font-mono text-[11px] text-accent disabled:opacity-50"
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
        {pending ? "Descargando…" : "↓ .txt"}
      </button>
      {error && <span className="text-[11px] text-danger">{error}</span>}
    </>
  );
}

// THE panel — HeroUI Card (elevated surface: bg-surface + shadow), optional
// label-caps header (the mobile tabs carry the label/badge instead) and
// optional `↓ .txt` export footer (Story 3.5; no path ⇒ no footer).
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
  // HeroUI Card (compound) instead of the hand-rolled <section>: variant
  // "default" gives the bg-surface body. We neutralize the card's own
  // p-4/gap-3/overflow-visible/32px-radius to console density (utilities
  // layer wins over the .card component layer) and re-add the header/footer
  // separators per slot. The outer `border border-border` is explicit on
  // purpose: .card relies on shadow-surface for its edge, but the dark theme
  // (app default) nulls --surface-shadow to transparent, so without the
  // border the panel would lose all containment against bg-background.
  // `min-w-0` on the root is the actual fix for the grid overflow — without
  // it the long mono rows blow the 1fr track wide and `truncate` never bites.
  return (
    <Card
      className={clsx(
        "flex min-w-0 flex-col gap-0 overflow-hidden rounded-lg border border-border p-0",
        className,
      )}
      variant="default"
    >
      {header && (
        <Card.Header className="flex-row items-center justify-between gap-2 border-b border-border px-3 py-2">
          <Card.Title className="text-[10px] font-medium uppercase leading-4 tracking-[0.12em] text-muted">
            {header}
          </Card.Title>
          <CountBadge tone={countTone} value={count} />
        </Card.Header>
      )}
      <Card.Content className="min-h-0 p-0">
        <PanelList
          className={listClassName}
          emptyText={emptyText}
          rows={rows}
        />
      </Card.Content>
      {exportPath && (
        <Card.Footer className="justify-between border-t border-border px-3 py-2">
          <ExportLink path={exportPath} />
        </Card.Footer>
      )}
    </Card>
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
      header={header ? "COMPLETA" : undefined}
      listClassName={listClassName}
      rows={completaRows(responses)}
    />
  );
}

// "Filtrada con response" (full text of only the ✅ revisions). SAME row
// shape as Completa — full text + the ✅ glyph — just the status-filtered
// subset. Props-driven like its siblings: callers pass the full `responses`
// list and the authoritative ok total; the panel filters to status === "ok".
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
      header={header ? "FILTRADA CON RESPONSE" : undefined}
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
      header={header ? "FILTRADA SIN RESPONSE" : undefined}
      listClassName={listClassName}
      rows={filtradaRows(cc, total)}
    />
  );
}

// Mobile recomposition: segmented Completa | Filtrada tabs (DESIGN token
// `dual-view-tabs`), each with its live count badge; the lists keep a capped
// height with internal scroll so the cockpit form stays reachable.
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
}: {
  responses: ResponseRow[];
  cc: CcRow[];
  responsesTotal: number;
  responsesOkTotal: number;
  ccTotal: number;
  exportPathCompleta?: string;
  exportPathFiltradaCompleta?: string;
  exportPathFiltrada?: string;
  className?: string;
}) {
  return (
    <Tabs className={className}>
      <Tabs.ListContainer>
        <Tabs.List aria-label="Respuestas capturadas">
          <Tabs.Tab id="completa">
            <span className="flex items-center gap-2">
              Completa <CountBadge value={responsesTotal} />
            </span>
            <Tabs.Indicator />
          </Tabs.Tab>
          <Tabs.Tab id="con-response">
            <span className="flex items-center gap-2">
              Con response{" "}
              <CountBadge tone="success" value={responsesOkTotal} />
            </span>
            <Tabs.Indicator />
          </Tabs.Tab>
          <Tabs.Tab id="sin-response">
            <span className="flex items-center gap-2">
              Sin response <CountBadge tone="success" value={ccTotal} />
            </span>
            <Tabs.Indicator />
          </Tabs.Tab>
        </Tabs.List>
      </Tabs.ListContainer>
      <Tabs.Panel id="completa">
        <CompletaPanel
          exportPath={exportPathCompleta}
          header={false}
          listClassName="max-h-72"
          responses={responses}
          total={responsesTotal}
        />
      </Tabs.Panel>
      <Tabs.Panel id="con-response">
        <FiltradaConResponsePanel
          exportPath={exportPathFiltradaCompleta}
          header={false}
          listClassName="max-h-72"
          responses={responses}
          total={responsesOkTotal}
        />
      </Tabs.Panel>
      <Tabs.Panel id="sin-response">
        <FiltradaPanel
          cc={cc}
          exportPath={exportPathFiltrada}
          header={false}
          listClassName="max-h-72"
          total={ccTotal}
        />
      </Tabs.Panel>
    </Tabs>
  );
}
