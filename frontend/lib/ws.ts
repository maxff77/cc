// Singleton auto-reconnecting WebSocket client + live-batch store (Story 2.2;
// pause/resume/stop + FloodWait state since Story 2.3; failed lines since
// 2.5; capture-session rows for the Completa/Filtrada views since 3.2).
//
// WS event payloads are NOT in the generated OpenAPI types — this is the one
// legitimate hand-typed contract, kept next to the reducer. Envelope:
// {"event": "<name>", "data": {...}}; server→client only.
//
// Reconnect contract (UX-DR13 / AC 11): capped exponential backoff (1s → 10s,
// forever), and every fresh `snapshot` REPLACES the whole store — that's the
// silent reconciliation. NO offline banners, NO queued actions.

import { useSyncExternalStore } from "react";

// Surface states (architecture state machine): DB terminals travel as "idle".
export type BatchSurfaceState = "idle" | "sending" | "paused" | "stopping";

export interface FailedLine {
  position: number;
  text: string;
  code: string;
}

// One Completa row (Story 3.2): a captured 'full' revision. `nueva` is true
// ONLY for rows that landed live via `response.captured` — snapshot rows are
// reconciliation, not novelty.
export interface ResponseRow {
  key: string;
  messageId: number;
  status: "ok" | "rejected";
  text: string;
  capturedAt: string;
  nueva: boolean;
}

// One Filtrada row: a session-new deduped CC value (no timestamp — parity
// with legacy filtrada.txt: one value per line).
export interface CcRow {
  key: string;
  text: string;
  nueva: boolean;
}

export interface LiveBatchState {
  state: BatchSurfaceState;
  batchId: number | null;
  gateName: string | null;
  gateValue: string | null;
  sent: number;
  queued: number;
  // Lines the retry cap gave up on (Story 2.5). They live with the LIVE
  // batch only (recorded decision): a reconnect rebuilds them from the
  // snapshot; post-batch persistence is Epic 3's history, not invented here.
  failed: number;
  failedLines: FailedLine[];
  total: number;
  etaSeconds: number;
  ccNew: number;
  // Capture-session fields (Story 3.2). They belong to the SESSION, not the
  // batch: they survive the idle reset and seedFromBatch — only a
  // snapshot/batch.state carrying ANOTHER session_id clears them.
  sessionId: number | null;
  responses: ResponseRow[];
  cc: CcRow[];
  // Real total of 'full' revisions — honest even when the snapshot list is
  // capped server-side (the Completa badge).
  responsesTotal: number;
  // Epoch ms when the FloodWait window ends; null = no active notice. Set by
  // `flood.wait`, cleared by any signal that sending flows again (AC 6).
  floodUntil: number | null;
}

// --- Hand-typed WS payload shapes (mirror backend services/batches.py) ------

interface SnapshotResponseRow {
  id: number;
  message_id: number;
  status: "ok" | "rejected";
  text: string;
  created_at: string;
}

interface SnapshotCcRow {
  id: number;
  text: string;
}

interface SnapshotData {
  state: BatchSurfaceState;
  batch_id: number | null;
  gate_name: string | null;
  gate_value: string | null;
  sent: number;
  queued: number;
  failed: number;
  failed_lines: FailedLine[];
  total: number;
  eta_seconds: number;
  cc_new: number;
  // Story 3.2: the active capture session's slice — rows capped server-side,
  // totals real.
  session_id: number | null;
  responses: SnapshotResponseRow[];
  cc: SnapshotCcRow[];
  responses_total: number;
}

interface ProgressData {
  batch_id: number;
  sent: number;
  queued: number;
  failed: number;
  total: number;
  eta_seconds: number;
}

// Tenant-scoped `batch.line_failed` (Story 2.5, AC 4): a line the retry cap
// gave up on — `code` maps to Spanish copy in components/batch/failed-lines.
interface LineFailedData {
  batch_id: number;
  position: number;
  text: string;
  code: string;
}

// Full-context batch.state payload (Story 2.3, Task 5 — fixes the 2.2 finding
// where a second tab never learned the gate of a batch started elsewhere).
// `session_id` since 3.2: the capture-session binding travels with EVERY
// batch.state so the reducer can tell a session change apart.
interface BatchStateData {
  state: BatchSurfaceState;
  batch_id: number | null;
  gate_name: string | null;
  gate_value: string | null;
  session_id: number | null;
}

// VERBATIM mirror of the `response.captured` emit (backend core/capture.py).
interface ResponseCapturedData {
  session_id: number;
  batch_id: number | null;
  message_id: number;
  status: "ok" | "rejected";
  previous_status: "ok" | "rejected" | null;
  edited: boolean;
  text: string;
  new_cc: string[];
  cc_total: number;
  captured_at: string;
}

interface FloodWaitData {
  seconds: number;
}

const IDLE: LiveBatchState = {
  state: "idle",
  batchId: null,
  gateName: null,
  gateValue: null,
  sent: 0,
  queued: 0,
  failed: 0,
  failedLines: [],
  total: 0,
  etaSeconds: 0,
  ccNew: 0,
  sessionId: null,
  responses: [],
  cc: [],
  responsesTotal: 0,
  floodUntil: null,
};

let store: LiveBatchState = IDLE;
const listeners = new Set<() => void>();

// Monotonic key source for rows that land live — `response.captured` carries
// no DB row id, so `l-${n}` keeps React keys stable; snapshot rows use the
// DB id (`s-${id}`).
let liveRowSeq = 0;

// Cap for the live-append lists (responses/cc). The server caps snapshots at
// `_SNAPSHOT_ROWS = 200` so reconnects don't weigh megas; this is the same
// rationale applied to the live path, where a long-lived tab otherwise
// accumulates rows forever ("counters never reset" — sessions do not either).
const _LIVE_ROWS = 500;

let socket: WebSocket | null = null;
let started = false;
let backoffMs = 1000;

function setStore(next: LiveBatchState) {
  store = next;
  listeners.forEach((listener) => listener());
}

// One reducer-style handler per event name (architecture state pattern).
// Unknown events are ignored without crashing (2.4+ adds more events).
function reduce(event: string, data: unknown) {
  switch (event) {
    case "snapshot": {
      const d = data as SnapshotData;

      setStore({
        state: d.state,
        batchId: d.batch_id,
        gateName: d.gate_name,
        gateValue: d.gate_value,
        sent: d.sent,
        queued: d.queued,
        // Snapshot REPLACES everything — a tab reconnecting mid-batch
        // rebuilds the failed panel from here (snapshot-first).
        failed: d.failed,
        failedLines: d.failed_lines,
        total: d.total,
        etaSeconds: d.eta_seconds,
        ccNew: d.cc_new,
        // Session slice (3.2): the panels rebuild from the snapshot alone.
        // Rows arrive with `nueva: false` — the highlight marks only what
        // lands live; a snapshot is reconciliation, not novelty.
        sessionId: d.session_id,
        responses: d.responses.map((row) => ({
          key: `s-${row.id}`,
          messageId: row.message_id,
          status: row.status,
          text: row.text,
          capturedAt: row.created_at,
          nueva: false,
        })),
        cc: d.cc.map((row) => ({
          key: `s-${row.id}`,
          text: row.text,
          nueva: false,
        })),
        responsesTotal: d.responses_total,
        // The snapshot carries no flood info → drop any notice. Honest: after
        // a reconnect the countdown is no longer verifiable.
        floodUntil: null,
      });
      break;
    }
    case "batch.progress": {
      const d = data as ProgressData;

      // NEVER touches `state` (AC 1): an append during 'paused' emits
      // progress and the UI must not invent "sending". Progress flowing
      // also means the send resumed → the FloodWait notice self-dismisses.
      setStore({
        ...store,
        batchId: d.batch_id,
        sent: d.sent,
        queued: d.queued,
        failed: d.failed,
        total: d.total,
        etaSeconds: d.eta_seconds,
        floodUntil: null,
      });
      break;
    }
    case "batch.line_failed": {
      const d = data as LineFailedData;

      // Scope to the live batch (deferred 2-5): a stale frame crossing a
      // seedFromBatch of another lote must not attach a foreign row — and its
      // position-collision dedup would then mask a legitimate failure.
      if (store.batchId !== null && store.batchId !== d.batch_id) break;
      // Append with dedup by position: a released/re-claimed line that fails
      // again must not duplicate its panel entry.
      if (store.failedLines.some((line) => line.position === d.position)) break;
      setStore({
        ...store,
        failedLines: [
          ...store.failedLines,
          { position: d.position, text: d.text, code: d.code },
        ],
      });
      break;
    }
    case "batch.state": {
      const d = data as BatchStateData;

      if (d.state === "idle") {
        // Batch drained or stopped → back to the idle surface — but keep the
        // failed-lines info (AC 4): when the failing line is the one that
        // drains the batch, the backend emits line_failed → progress → this
        // idle in one burst, and a full IDLE reset would wipe the red panel
        // milliseconds after it appeared. It clears on the next snapshot or
        // on seedFromBatch for a different batch (both reset it already).
        // The session fields (incl. ccNew) survive too (3.2): capture stays
        // armed between batches — the lote ends and the data stays on screen
        // (legacy "counters never reset").
        setStore({
          ...IDLE,
          failed: store.failed,
          failedLines: store.failedLines,
          sessionId: store.sessionId,
          responses: store.responses,
          cc: store.cc,
          ccNew: store.ccNew,
          responsesTotal: store.responsesTotal,
        });
      } else {
        // A live batch bound to ANOTHER session ⇒ gate change replaced the
        // session — the panels belong to the old one: start clean (3.2).
        const sessionChanged =
          d.session_id !== null &&
          store.sessionId !== null &&
          d.session_id !== store.sessionId;

        setStore({
          ...store,
          state: d.state,
          batchId: d.batch_id,
          gateName: d.gate_name,
          gateValue: d.gate_value,
          sessionId: d.session_id ?? store.sessionId,
          responses: sessionChanged ? [] : store.responses,
          cc: sessionChanged ? [] : store.cc,
          ccNew: sessionChanged ? 0 : store.ccNew,
          responsesTotal: sessionChanged ? 0 : store.responsesTotal,
          // Resumed sending ⇒ the FloodWait notice self-dismisses (AC 6).
          floodUntil: d.state === "sending" ? null : store.floodUntil,
        });
      }
      break;
    }
    case "response.captured": {
      const d = data as ResponseCapturedData;

      // Session guard (3.2): a late reply of an OLD session persists in the
      // DB (Historial 3.3 shows it) but the Envío panels represent the
      // ACTIVE session only. A fresh tab with no session adopts the first
      // event's session. Never touches `state` (same contract as
      // batch.progress): capture stays armed between batches and late
      // replies keep landing with the surface idle.
      if (store.sessionId !== null && d.session_id !== store.sessionId) break;

      // Snapshot-race dedup (3.2 review fix): the backend commits BEFORE it
      // emits (core/capture.py), and ws.py registers a connecting socket
      // BEFORE building its snapshot — so a tab connecting inside that gap
      // gets a snapshot that already contains this row, and this frame
      // arrives AFTER it. The (messageId, status, text) triple can only
      // repeat consecutively via that race: process_incoming already no-ops
      // identical-text editions against the last revision.
      const lastRow = store.responses[store.responses.length - 1];
      const isDupRow =
        lastRow !== undefined &&
        lastRow.messageId === d.message_id &&
        lastRow.status === d.status &&
        lastRow.text === d.text;
      // CC values are session-unique (uq_responses_session_cc), so a plain
      // text match is an exact dedup against snapshot rows.
      const knownCc = new Set(store.cc.map((row) => row.text));
      const freshCc = d.new_cc.filter((value) => !knownCc.has(value));

      setStore({
        ...store,
        sessionId: store.sessionId ?? d.session_id,
        // Live lists are capped (mirror of `_SNAPSHOT_ROWS`): the session
        // never resets while the gate is reused and capture stays armed
        // between batches, so without a cap the longest-lived tab grows —
        // and re-renders — without bound. The badges stay honest (they come
        // from the authoritative totals, not the list lengths), and any
        // reconnect already truncates the lists to the snapshot's 200.
        responses: isDupRow
          ? store.responses
          : [
              ...store.responses,
              {
                key: `l-${++liveRowSeq}`,
                messageId: d.message_id,
                status: d.status,
                text: d.text,
                capturedAt: d.captured_at,
                nueva: true,
              },
            ].slice(-_LIVE_ROWS),
        responsesTotal: isDupRow
          ? store.responsesTotal
          : store.responsesTotal + 1,
        // `new_cc` may be [] (e.g. an edit with nothing session-new).
        cc: [
          ...store.cc,
          ...freshCc.map((value) => ({
            key: `l-${++liveRowSeq}`,
            text: value,
            nueva: true,
          })),
        ].slice(-_LIVE_ROWS),
        // Authoritative server total (the ring's "CC nuevas" metric) — never
        // client-side sums, which drift on lost frames; assigning reconciles
        // for free. Same number as the snapshot's cc_new.
        ccNew: d.cc_total,
      });
      break;
    }
    case "flood.wait": {
      const d = data as FloodWaitData;

      setStore({ ...store, floodUntil: Date.now() + d.seconds * 1000 });
      break;
    }
    case "batch.line_sent":
      // A line went out ⇒ sending flows again — clear any FloodWait notice.
      if (store.floodUntil !== null) {
        setStore({ ...store, floodUntil: null });
      }
      break;
    default:
      break; // forward-compat: ignore anything unknown
  }
}

function connect() {
  if (typeof window === "undefined") return;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";

  socket = new WebSocket(`${proto}://${window.location.host}/ws`);

  socket.onopen = () => {
    backoffMs = 1000; // healthy again — reset the backoff
  };

  socket.onmessage = (e: MessageEvent<string>) => {
    let parsed: { event?: string; data?: unknown };

    try {
      parsed = JSON.parse(e.data) as { event?: string; data?: unknown };
    } catch {
      return; // garbage frame — ignore
    }
    if (typeof parsed.event === "string") reduce(parsed.event, parsed.data);
  };

  socket.onclose = () => {
    // Silent auto-reconnect, forever (AC 11 — no offline UX). The snapshot
    // that follows the next successful handshake reconciles everything.
    socket = null;
    window.setTimeout(connect, backoffMs);
    backoffMs = Math.min(backoffMs * 2, 10000);
  };

  socket.onerror = () => {
    socket?.close();
  };
}

function ensureStarted() {
  if (started || typeof window === "undefined") return;
  started = true;
  connect();
}

export function subscribeLiveBatch(listener: () => void): () => void {
  ensureStarted();
  listeners.add(listener);

  return () => listeners.delete(listener);
}

export function getLiveBatch(): LiveBatchState {
  return store;
}

export function getServerLiveBatch(): LiveBatchState {
  return IDLE; // SSR snapshot — the client reconciles after hydration
}

// Single WS store, one hook — every consumer (Envío surface, nav live dot)
// reads the same reducer output.
export function useLiveBatch(): LiveBatchState {
  return useSyncExternalStore(
    subscribeLiveBatch,
    getLiveBatch,
    getServerLiveBatch,
  );
}

// Local clear confirmed by REST (Story 3.3): after DELETE /api/sessions/{id}
// succeeds, the deleting tab resets ONLY the session fields — otherwise the
// operator deletes the session in Historial, returns to Envío and the panels
// keep showing rows that no longer exist server-side until the next
// reconnection. Same pattern as `seedFromBatch` (REST-confirmed local seed;
// the WS snapshot stays the source of truth afterwards). Recorded decision:
// other open tabs reconcile on their next snapshot (stale visual accepted at
// MVP scale). NO new WS event — `session.active` belongs to Story 3.4.
export function clearSession(sessionId: number) {
  if (store.sessionId !== sessionId) return;
  setStore({
    ...store,
    sessionId: null,
    responses: [],
    cc: [],
    ccNew: 0,
    responsesTotal: 0,
  });
}

// Seed the store from a successful POST /api/batches response so the ring
// appears without waiting for the next WS event. `snapshot`/`batch.state`
// remain the source of truth thereafter (UX-DR12 — no optimistic jumps
// beyond this server-confirmed shape).
export function seedFromBatch(batch: {
  id: number;
  gate_name: string;
  gate_value: string;
  sent: number;
  queued: number;
  failed: number;
  total: number;
}) {
  // Same batch already in the store ⇒ WS got there first (the backend emits
  // progress — even line 1 — before the POST returns): seeding would REGRESS
  // fresher state, rolling the ring back up to a full interval (deferred 2-2).
  if (store.batchId === batch.id) return;
  setStore({
    state: "sending",
    batchId: batch.id,
    gateName: batch.gate_name,
    gateValue: batch.gate_value,
    sent: batch.sent,
    queued: batch.queued,
    failed: batch.failed,
    // The POST response carries no line detail — start the panel clean for
    // the new batch (the snapshot remains the source of truth).
    failedLines: [],
    total: batch.total,
    etaSeconds: 0,
    // The session fields belong to the SESSION, not the batch — never seeded
    // away (3.2). If this batch activated ANOTHER session, the batch.state
    // the POST emits right after carries the new session_id and the reducer
    // clears — the server decides, the seed never guesses.
    ccNew: store.ccNew,
    sessionId: store.sessionId,
    responses: store.responses,
    cc: store.cc,
    responsesTotal: store.responsesTotal,
    // A global FloodWait window doesn't end because a batch was posted.
    floodUntil: store.floodUntil,
  });
}
