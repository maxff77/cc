"use client";

// Dual Completa/Filtrada views (Story 3.2) — AC 1 "same components,
// recomposed": ONE panel (header/badge + scrollable list + empty state)
// instantiated per view; on mobile the views are segmented HeroUI Tabs (the
// tab strip carries the labels + badges), on desktop two side-by-side panels
// with label-caps headers. Props-driven on purpose (rows + counts in, no
// store reads inside): Story 3.3's Historial detail reuses these panels
// verbatim. Export (`↓ .txt`) is Story 3.5 — no dead button here.
import type { CcRow, ResponseRow } from "@/lib/ws";

import { useLayoutEffect, useRef } from "react";
import { Tabs } from "@heroui/react";
import clsx from "clsx";

import { DataRow, type DataRowProps } from "@/components/sessions/response-row";

// Empty states — copy VERBATIM (EXPERIENCE.md): no fake rows, badges at 0.
const EMPTY_COMPLETA = "Aún no hay respuestas.";
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

// THE panel — outlined surface (DESIGN: 1px border, no shadows), optional
// label-caps header (the mobile tabs carry the label/badge instead).
function ResponsePanel({
  header,
  count,
  countTone,
  emptyText,
  rows,
  listClassName,
  className,
}: {
  header?: string;
  count: number;
  countTone?: "success";
  emptyText: string;
  rows: RowData[];
  listClassName?: string;
  className?: string;
}) {
  return (
    <section
      className={clsx(
        "flex flex-col rounded-md border border-border bg-surface",
        className,
      )}
    >
      {header && (
        <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
            {header}
          </span>
          <CountBadge tone={countTone} value={count} />
        </header>
      )}
      <PanelList className={listClassName} emptyText={emptyText} rows={rows} />
    </section>
  );
}

export function CompletaPanel({
  responses,
  total,
  header = true,
  listClassName,
  className,
}: {
  responses: ResponseRow[];
  total: number;
  header?: boolean;
  listClassName?: string;
  className?: string;
}) {
  return (
    <ResponsePanel
      className={className}
      count={total}
      emptyText={EMPTY_COMPLETA}
      header={header ? "COMPLETA" : undefined}
      listClassName={listClassName}
      rows={completaRows(responses)}
    />
  );
}

export function FiltradaPanel({
  cc,
  total,
  header = true,
  listClassName,
  className,
}: {
  cc: CcRow[];
  total: number;
  header?: boolean;
  listClassName?: string;
  className?: string;
}) {
  return (
    <ResponsePanel
      className={className}
      count={total}
      countTone="success"
      emptyText={EMPTY_FILTRADA}
      header={header ? "FILTRADA" : undefined}
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
  ccTotal,
  className,
}: {
  responses: ResponseRow[];
  cc: CcRow[];
  responsesTotal: number;
  ccTotal: number;
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
          <Tabs.Tab id="filtrada">
            <span className="flex items-center gap-2">
              Filtrada <CountBadge tone="success" value={ccTotal} />
            </span>
            <Tabs.Indicator />
          </Tabs.Tab>
        </Tabs.List>
      </Tabs.ListContainer>
      <Tabs.Panel id="completa">
        <CompletaPanel
          header={false}
          listClassName="max-h-72"
          responses={responses}
          total={responsesTotal}
        />
      </Tabs.Panel>
      <Tabs.Panel id="filtrada">
        <FiltradaPanel
          cc={cc}
          header={false}
          listClassName="max-h-72"
          total={ccTotal}
        />
      </Tabs.Panel>
    </Tabs>
  );
}
