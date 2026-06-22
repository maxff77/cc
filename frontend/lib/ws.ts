// Singleton auto-reconnecting WebSocket client + live-batch store (Story 2.2;
// pause/resume/stop + FloodWait state since Story 2.3; failed lines since
// 2.5; capture-session rows for the Completa/Filtrada views since 3.2;
// `session.active` rebinding on Continuar since 3.4).
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
// "waiting" (Story 4.2): queued for admission — live but not yet sending.
export type BatchSurfaceState =
  | "idle"
  | "sending"
  | "paused"
  | "stopping"
  | "waiting";

export interface FailedLine {
  position: number;
  text: string;
  code: string;
}

// One still-queued line (Pendientes panel): keyed by `position`, unique within
// the single live batch. It drains as the backend emits batch.line_sent /
// batch.line_failed for that position; the snapshot rebuilds it on reconnect.
export interface PendingLine {
  position: number;
  text: string;
}

// One Completa row (Story 3.2): a message's LATEST captured 'full' revision —
// ONE row per message, not per edit (cockpit-completa-one-row-per-message). A
// later revision updates this row in place. `nueva` is true ONLY for rows that
// landed/updated live via `response.captured` — snapshot rows are
// reconciliation, not novelty.
export interface ResponseRow {
  key: string;
  // `messageId` is the row IDENTITY (the live upsert key, parity with the
  // server's latest-per-(chat,message) collapse). `responseId` is the persisted
  // Response.id of the revision now shown — used only to drop an EXACT
  // re-delivery (same id) carried by a snapshot/session.active re-seed race.
  responseId: number;
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

// Watchdog global-pause latch (Story 4.1): SYSTEM state, not batch state —
// it survives the idle reset and seedFromBatch, and only the snapshot or the
// watchdog.* events touch it. `reason` is the backend's machine string
// ('reply_rate_collapse' | 'session_lost'); copy lives in watchdog-notice.
export interface WatchdogInfo {
  paused: boolean;
  reason: string | null;
  detail: string | null;
  pausedAt: string | null;
}

export interface LiveBatchState {
  state: BatchSurfaceState;
  batchId: number | null;
  gateName: string | null;
  gateDisplayValue: string | null;
  sent: number;
  queued: number;
  // Lines the retry cap gave up on (Story 2.5). They live with the LIVE
  // batch only (recorded decision): a reconnect rebuilds them from the
  // snapshot; post-batch persistence is Epic 3's history, not invented here.
  failed: number;
  failedLines: FailedLine[];
  // Still-queued lines (Pendientes): BATCH-scoped, not session-scoped — they
  // clear when the batch drains or another batch starts. The count badge uses
  // `queued` (authoritative); this list may be capped.
  pending: PendingLine[];
  total: number;
  etaSeconds: number;
  ccNew: number;
  // Capture-session fields (Story 3.2). They belong to the SESSION, not the
  // batch: they survive the idle reset and seedFromBatch — only a
  // snapshot/batch.state carrying ANOTHER session_id clears them.
  sessionId: number | null;
  // Active-session identity for the cockpit strip (Nueva/Renombrar/Mostrar).
  // `sessionName` is null for an unnamed session — the strip shows a generic
  // label + the gate (the cockpit doesn't carry created_at). Like `sessionId`
  // these survive the idle reset: capture stays armed between batches.
  sessionName: string | null;
  sessionGateName: string | null;
  sessionDisplayValue: string | null;
  responses: ResponseRow[];
  cc: CcRow[];
  // Real total of 'full' revisions — honest even when the snapshot list is
  // capped server-side (the Completa badge).
  responsesTotal: number;
  // Real total of ✅ 'full' revisions — the "Filtrada con response" badge
  // (the rows the panel filters out of `responses` by status === "ok").
  responsesOkTotal: number;
  // Delivered lines still without a ✅/❌ reply ("esperando respuesta") —
  // session-scoped like the totals above: it survives the idle reset and
  // seedFromBatch, resets only on a session change/clear. Authoritative from
  // the backend (snapshot/session.active rebuild it, batch.progress climbs it
  // on each send, response.captured drops it) — ASSIGNED, never delta-summed.
  awaitingReply: number;
  // Epoch ms when the FloodWait window ends; null = no active notice. Set by
  // `flood.wait`, cleared by any signal that sending flows again (AC 6).
  floodUntil: number | null;
  // Watchdog global pause (Story 4.1) — system-wide, role-agnostic state;
  // the banner renders for everyone, the resume button only for the owner.
  watchdog: WatchdogInfo;
  // FIFO admission position (Story 4.2) — non-null only while `state` is
  // "waiting"; the server assigns it (snapshot + every batch.state).
  queuePosition: number | null;
  // Why a batch is paused (amazon-gate-send-rotation Phase 2): a machine code
  // carried on `batch.state` when `state === "paused"`. `'cookies_exhausted'`
  // ⇒ the cockpit renders the add-cookies prompt; null = an ordinary pause
  // (operator Pausar) with no special reason. Cleared on any non-paused state
  // and on a session change — never lingers across a resume.
  pauseReason: string | null;
  // The tenant's credit balance (credits feature). TENANT-scoped (like the
  // session fields): it survives the idle reset and seedFromBatch — only the
  // snapshot or a `credits.updated` event moves it. The cockpit shows it and
  // blocks costed gates client-side when it's 0.
  creditBalance: number;
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
  gate_display_value: string | null;
  sent: number;
  queued: number;
  failed: number;
  failed_lines: FailedLine[];
  // Still-queued line texts (Pendientes) — capped server-side like the other
  // snapshot lists; rebuilds the panel on reconnect.
  pending_lines: PendingLine[];
  total: number;
  eta_seconds: number;
  // Story 4.2: admission position — null unless state === "waiting".
  queue_position: number | null;
  // Phase 2 (amazon-gate-send-rotation): the pause reason on a reconnect —
  // present only when state === "paused"; null/absent otherwise. Snapshot-first:
  // a tab connecting into a `cookies_exhausted` pause rebuilds the prompt here.
  pause_reason?: string | null;
  cc_new: number;
  // Story 3.2: the active capture session's slice — rows capped server-side,
  // totals real.
  session_id: number | null;
  // Active-session identity (cockpit strip) — distinct from the live batch's
  // top-level gate_name/value so the spread never collides server-side.
  session_name: string | null;
  session_gate_name: string | null;
  session_gate_display_value: string | null;
  responses: SnapshotResponseRow[];
  cc: SnapshotCcRow[];
  responses_total: number;
  // "Filtrada con response": total of ✅ 'full' revisions.
  responses_ok_total: number;
  // "Esperando respuesta": delivered lines without a ✅/❌ yet (session-scoped).
  awaiting_reply: number;
  // Story 4.1: the watchdog latch — a reconnected tab rebuilds the
  // global-pause banner from the snapshot alone (snapshot-first).
  watchdog: {
    paused: boolean;
    reason: string | null;
    detail: string | null;
    paused_at: string | null;
  };
  // Credits feature: the tenant's balance, carried in every snapshot.
  credit_balance: number;
}

// `credits.updated` (credits feature): the tenant's balance changed — a capture
// charge debited it, or an owner recharge set it. The reducer ASSIGNS it.
interface CreditsUpdatedData {
  balance: number;
}

interface ProgressData {
  batch_id: number;
  sent: number;
  queued: number;
  failed: number;
  total: number;
  eta_seconds: number;
  // Fires after every send/fail → carries the live "esperando respuesta".
  awaiting_reply: number;
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
  gate_display_value: string | null;
  session_id: number | null;
  // Story 4.2: travels in EVERY batch.state — null outside "waiting".
  queue_position: number | null;
  // Phase 2 (amazon-gate-send-rotation): the pause reason, present only when
  // `state === "paused"` (e.g. "cookies_exhausted" / "verdict_timeout"); null
  // otherwise. The backend's `state_data(... pause_reason=...)` builds it.
  pause_reason?: string | null;
}

// VERBATIM mirror of the `response.captured` emit (backend core/capture.py).
interface ResponseCapturedData {
  // The persisted Response.id (same value as the snapshot row's `id`) — the
  // dedup key: a re-seed-race re-delivery repeats it, a new revision is fresh.
  id: number;
  session_id: number;
  batch_id: number | null;
  message_id: number;
  status: "ok" | "rejected";
  previous_status: "ok" | "rejected" | null;
  edited: boolean;
  text: string;
  new_cc: string[];
  cc_total: number;
  // Authoritative ✅-message total (Aprobadas badge) — assigned, not summed, so
  // a lost frame / cap eviction can't drift it (parity with cc_total).
  responses_ok_total: number;
  // Recomputed after this reply persists: a message's first ✅/❌ drops it.
  awaiting_reply: number;
  captured_at: string;
}

// `batch.lines_queued` (Pendientes): the lines just added to the queue — on
// create AND append. Carries `batch_id` so a frame crossing a seedFromBatch of
// another lote is dropped (same guard as batch.line_failed).
interface LinesQueuedData {
  batch_id: number;
  lines: PendingLine[];
}

// `batch.line_sent` (backend send_worker `_record_sent`): one line went out.
// Carries `position` so Pendientes drops exactly that row, and doubles as the
// "sending flows again" signal that dismisses a FloodWait notice.
interface LineSentData {
  batch_id: number;
  position: number;
  text: string;
}

interface FloodWaitData {
  seconds: number;
}

// `watchdog.paused` (Story 4.1, GLOBAL like flood.wait): the watchdog latched
// the system-wide pause — reply-rate collapse or session loss.
interface WatchdogPausedData {
  reason: string;
  detail: string | null;
  paused_at: string;
}

// VERBATIM mirror of `active_session_data` (backend services/batches.py) —
// the `session.active` payload (Story 3.4) IS the snapshot's session slice:
// a tab that misses the event reconciles with its next snapshot without any
// shape difference.
interface SessionActiveData {
  // null when the just-activated session was deleted before the post-commit
  // payload build (active_session_data's "no active session" shape).
  session_id: number | null;
  session_name: string | null;
  session_gate_name: string | null;
  session_gate_display_value: string | null;
  cc_new: number;
  responses_total: number;
  responses_ok_total: number;
  awaiting_reply: number;
  responses: SnapshotResponseRow[];
  cc: SnapshotCcRow[];
}

const IDLE: LiveBatchState = {
  state: "idle",
  batchId: null,
  gateName: null,
  gateDisplayValue: null,
  sent: 0,
  queued: 0,
  failed: 0,
  failedLines: [],
  pending: [],
  total: 0,
  etaSeconds: 0,
  ccNew: 0,
  sessionId: null,
  sessionName: null,
  sessionGateName: null,
  sessionDisplayValue: null,
  responses: [],
  cc: [],
  responsesTotal: 0,
  responsesOkTotal: 0,
  awaitingReply: 0,
  floodUntil: null,
  watchdog: { paused: false, reason: null, detail: null, pausedAt: null },
  queuePosition: null,
  pauseReason: null,
  creditBalance: 0,
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
        gateDisplayValue: d.gate_display_value,
        sent: d.sent,
        queued: d.queued,
        // Snapshot REPLACES everything — a tab reconnecting mid-batch
        // rebuilds the failed panel from here (snapshot-first).
        failed: d.failed,
        failedLines: d.failed_lines,
        // Pendientes rebuilt from the snapshot alone (survives reload).
        pending: d.pending_lines,
        total: d.total,
        etaSeconds: d.eta_seconds,
        ccNew: d.cc_new,
        // Session slice (3.2): the panels rebuild from the snapshot alone.
        // Rows arrive with `nueva: false` — the highlight marks only what
        // lands live; a snapshot is reconciliation, not novelty.
        sessionId: d.session_id,
        sessionName: d.session_name,
        sessionGateName: d.session_gate_name,
        sessionDisplayValue: d.session_gate_display_value,
        responses: d.responses.map((row) => ({
          key: `s-${row.id}`,
          responseId: row.id,
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
        responsesOkTotal: d.responses_ok_total,
        // Authoritative on reconnect — rebuilds the badge from the snapshot
        // alone (snapshot-first), no client-side drift.
        awaitingReply: d.awaiting_reply,
        // The snapshot carries no flood info → drop any notice. Honest: after
        // a reconnect the countdown is no longer verifiable.
        floodUntil: null,
        // Watchdog latch (4.1): the snapshot is authoritative — a reconnect
        // rebuilds (or clears) the banner from here alone.
        watchdog: {
          paused: d.watchdog.paused,
          reason: d.watchdog.reason,
          detail: d.watchdog.detail,
          pausedAt: d.watchdog.paused_at,
        },
        // Admission position (4.2): a tab connecting mid-wait renders its
        // place from the snapshot alone.
        queuePosition: d.queue_position ?? null,
        // Pause reason (Phase 2): only meaningful while paused — a reconnect
        // into a `cookies_exhausted` pause rebuilds the prompt; null otherwise.
        pauseReason: d.state === "paused" ? (d.pause_reason ?? null) : null,
        // Credits balance (credits feature): authoritative on reconnect.
        creditBalance: d.credit_balance,
      });
      break;
    }
    case "credits.updated": {
      const d = data as CreditsUpdatedData;

      // Assigned, never summed — the server is authoritative (a charge or an
      // owner recharge). Never touches batch/session/state.
      setStore({ ...store, creditBalance: d.balance });
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
        // Climbs as lines go out (this event fires per send) — assigned, not
        // summed, so a missed frame self-heals on the next progress/snapshot.
        awaitingReply: d.awaiting_reply,
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
        // A failed line leaves the queue → drop it from Pendientes (it now
        // lives in the red Fallidas strip instead).
        pending: store.pending.filter((line) => line.position !== d.position),
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
          sessionName: store.sessionName,
          sessionGateName: store.sessionGateName,
          sessionDisplayValue: store.sessionDisplayValue,
          responses: store.responses,
          cc: store.cc,
          ccNew: store.ccNew,
          responsesTotal: store.responsesTotal,
          responsesOkTotal: store.responsesOkTotal,
          // Survives the idle reset like the totals: late replies keep landing
          // after the lote drains, so the badge stays honest until answered.
          awaitingReply: store.awaitingReply,
          // System state, not batch state (4.1): a draining batch never
          // clears the global-pause banner.
          watchdog: store.watchdog,
          // Idle is a non-paused state (Phase 2): drop any pause reason — the
          // add-cookies prompt belongs only to a live `cookies_exhausted` pause.
          pauseReason: null,
          // Tenant-scoped (credits feature): a draining batch never resets it.
          creditBalance: store.creditBalance,
        });
      } else {
        // A live batch bound to ANOTHER session ⇒ gate change replaced the
        // session — the panels belong to the old one: start clean (3.2).
        // `adopting` = this batch binds a session DIFFERENT from the store's
        // (incl. the null→id case: fresh load). Its gate
        // IS this batch's gate (the batch is bound to it); its name is unknown
        // here (batch.state carries no session_name) and a freshly-forked
        // session is unnamed anyway — null until the next snapshot/
        // session.active fills it. `sessionChanged` (the STRICTER swap: a known
        // session replaced by another) still gates the rows/counters reset, so
        // adopting from null never wipes data that wasn't there.
        const adopting =
          d.session_id !== null && d.session_id !== store.sessionId;
        const sessionChanged = adopting && store.sessionId !== null;

        setStore({
          ...store,
          state: d.state,
          batchId: d.batch_id,
          gateName: d.gate_name,
          gateDisplayValue: d.gate_display_value,
          // Pendientes is batch-scoped: a different batch id ⇒ start clean
          // (the create's batch.lines_queued — fanned to every tenant tab —
          // refills it). batch.state carries no line texts itself.
          pending:
            d.batch_id !== null && d.batch_id !== store.batchId
              ? []
              : store.pending,
          sessionId: d.session_id ?? store.sessionId,
          sessionName: adopting ? null : store.sessionName,
          sessionGateName: adopting ? d.gate_name : store.sessionGateName,
          sessionDisplayValue: adopting
            ? d.gate_display_value
            : store.sessionDisplayValue,
          responses: sessionChanged ? [] : store.responses,
          cc: sessionChanged ? [] : store.cc,
          ccNew: sessionChanged ? 0 : store.ccNew,
          responsesTotal: sessionChanged ? 0 : store.responsesTotal,
          responsesOkTotal: sessionChanged ? 0 : store.responsesOkTotal,
          // New session ⇒ nothing awaiting yet; otherwise carry it (the next
          // progress/snapshot reconciles the exact number anyway).
          awaitingReply: sessionChanged ? 0 : store.awaitingReply,
          // Resumed sending ⇒ the FloodWait notice self-dismisses (AC 6).
          floodUntil: d.state === "sending" ? null : store.floodUntil,
          // Admission position (4.2): assigned, never guessed — the server
          // sends null on every non-waiting state.
          queuePosition: d.queue_position ?? null,
          // Pause reason (Phase 2): only a paused batch carries one — when it
          // is paused take the frame's reason, on any non-paused state (sending/
          // stopping/waiting) clear it. A session swap also clears it, so a
          // stale `cookies_exhausted` prompt never bleeds into the new session.
          pauseReason:
            sessionChanged || d.state !== "paused"
              ? null
              : (d.pause_reason ?? null),
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

      // One row per message (cockpit-completa-one-row-per-message): the cockpit
      // shows each message's LATEST revision, never every edit. The snapshot
      // collapses server-side (latest-per-(chat_id,message_id), parity with
      // Historial); the live path must match, so a later revision of a message
      // already on screen REPLACES its row in place instead of appending a
      // revision twin (the perpetual session never resets the list, so an append
      // would accumulate ⏳→✅→edit rows that only a refresh used to reveal).
      // Keyed on `messageId` — the durable per-message identity. This also
      // subsumes the old re-seed-race dedup: a snapshot/session.active slice
      // already carries the message as an `s-${id}` row, and an in-flight
      // `response.captured` for it now updates that same row rather than twinning
      // it. An EXACT re-delivery (same persisted `responseId`) is a no-op.
      const existing = store.responses.find(
        (row) => row.messageId === d.message_id,
      );
      // `d.id != null` tolerates the brief deploy rollover where an old backend
      // emits no id; without it a null id would falsely match an existing null.
      const isReDelivery =
        d.id != null && existing != null && existing.responseId === d.id;

      setStore({
        ...store,
        sessionId: store.sessionId ?? d.session_id,
        // Live lists are capped (mirror of `_SNAPSHOT_ROWS`): a long-lived tab
        // otherwise grows — and re-renders — without bound. Only a NEW message
        // grows the list (and trips the cap); a revision updates in place.
        responses: isReDelivery
          ? store.responses
          : existing != null
            ? store.responses.map((row) =>
                row.messageId === d.message_id
                  ? {
                      ...row,
                      responseId: d.id,
                      status: d.status,
                      text: d.text,
                      capturedAt: d.captured_at,
                      nueva: true,
                    }
                  : row,
              )
            : [
                ...store.responses,
                {
                  key: `l-${++liveRowSeq}`,
                  responseId: d.id,
                  messageId: d.message_id,
                  status: d.status,
                  text: d.text,
                  capturedAt: d.captured_at,
                  nueva: true,
                },
              ].slice(-_LIVE_ROWS),
        // New message ⇒ +1; a revision of a message already counted ⇒ unchanged.
        responsesTotal:
          isReDelivery || existing != null
            ? store.responsesTotal
            : store.responsesTotal + 1,
        // Authoritative server total (full_count status=ok, same Limpiar cutoff)
        // — assigned not summed, so it can't drift on a lost frame or a row
        // evicted past the live cap. Reconciles for free, like cc_total/ccNew.
        // `?? store` tolerates the brief deploy rollover where an old backend
        // emits no field (keep last-known, don't blank the badge); the next
        // snapshot reconciles — same defensive intent as the `d.id != null` guard.
        responsesOkTotal: d.responses_ok_total ?? store.responsesOkTotal,
        // `new_cc` is the message's freshly-inserted CC (backend dedups
        // PER-MESSAGE now — uq_responses_session_msg_cc), so append it verbatim:
        // the same value on another approved message is intentionally a new row
        // (Datos CC mirrors Aprobadas). NO client-side text dedup — that hid
        // duplicates the server stores and re-introduced panel/server drift.
        // `isReDelivery` (same persisted id) still guards an exact re-delivery
        // so a snapshot/session.active re-seed race can't double-append.
        // `new_cc` may be [] (e.g. an edit with nothing new for this message).
        cc: isReDelivery
          ? store.cc
          : [
              ...store.cc,
              ...d.new_cc.map((value) => ({
                key: `l-${++liveRowSeq}`,
                text: value,
                nueva: true,
              })),
            ].slice(-_LIVE_ROWS),
        // Authoritative server total (the ring's "CC nuevas" metric) — never
        // client-side sums, which drift on lost frames; assigning reconciles
        // for free. Same number as the snapshot's cc_new.
        ccNew: d.cc_total,
        // Recomputed server-side after this reply persisted — a message's
        // first ✅/❌ drops it, a later revision leaves it unchanged. The
        // session guard above already dropped late replies of an OLD session.
        awaitingReply: d.awaiting_reply,
      });
      break;
    }
    case "session.active": {
      const d = data as SessionActiveData;

      // Continuar (Story 3.4): the server says which session is active NOW —
      // unconditional replacement of the SESSION fields only ("Envío binds to
      // it" in every tab of the tenant, including the one that fired the
      // continue). Rows arrive with `nueva: false` — the event is
      // reconciliation, not novelty; the "nueva" highlight stays reserved to
      // `response.captured` (whose session guard now matches the continued
      // session by construction). NEVER touches `state`/batch/flood — same
      // contract as `response.captured`: the session is the session's, the
      // batch is the batch's.
      setStore({
        ...store,
        sessionId: d.session_id,
        sessionName: d.session_name,
        sessionGateName: d.session_gate_name,
        sessionDisplayValue: d.session_gate_display_value,
        responses: d.responses.map((row) => ({
          key: `s-${row.id}`,
          responseId: row.id,
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
        ccNew: d.cc_new,
        responsesTotal: d.responses_total,
        responsesOkTotal: d.responses_ok_total,
        // Continuar rebinds to the continued session's authoritative count.
        awaitingReply: d.awaiting_reply,
      });
      break;
    }
    case "flood.wait": {
      const d = data as FloodWaitData;

      setStore({ ...store, floodUntil: Date.now() + d.seconds * 1000 });
      break;
    }
    case "watchdog.paused": {
      const d = data as WatchdogPausedData;

      // Global latch (4.1): never touches `state` — batches keep their DB
      // state and resume where they were once the owner explicitly resumes.
      setStore({
        ...store,
        watchdog: {
          paused: true,
          reason: d.reason,
          detail: d.detail,
          pausedAt: d.paused_at,
        },
      });
      break;
    }
    case "watchdog.resumed":
      setStore({
        ...store,
        watchdog: { paused: false, reason: null, detail: null, pausedAt: null },
      });
      break;
    case "batch.line_sent": {
      const d = data as LineSentData;

      // Scope to the live batch (same guard as line_failed): a stale frame
      // crossing a seedFromBatch of another lote must not drain a foreign row.
      if (store.batchId !== null && store.batchId !== d.batch_id) break;
      // A line went out ⇒ drop it from Pendientes AND clear any FloodWait
      // notice (sending flows again). Skip the write only when neither changes.
      const remaining = store.pending.filter(
        (line) => line.position !== d.position,
      );
      const drained = remaining.length !== store.pending.length;

      if (drained || store.floodUntil !== null) {
        setStore({ ...store, pending: remaining, floodUntil: null });
      }
      break;
    }
    case "batch.lines_queued": {
      const d = data as LinesQueuedData;

      // Guard + dedup by position (an at-least-once frame must not double a
      // row); cap mirrors the response/cc lists so a long-lived tab is bounded.
      if (store.batchId !== null && store.batchId !== d.batch_id) break;
      const known = new Set(store.pending.map((line) => line.position));
      const fresh = d.lines.filter((line) => !known.has(line.position));

      if (fresh.length === 0) break;
      setStore({
        ...store,
        // Keep the LOWEST positions (next-to-send): pending is ascending by
        // construction (snapshot ordered, line_sent drains the front), so
        // slice(0, cap) preserves the "top row = next to send" contract — a
        // tail slice would drop the very rows about to go out. The badge stays
        // honest via `queued` when the window is capped.
        pending: [...store.pending, ...fresh].slice(0, _LIVE_ROWS),
      });
      break;
    }
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

// Local clear confirmed by REST (PR-1 "Limpiar literal"): after POST
// /api/sessions/clear succeeds, the acting tab empties all three live panels
// immediately — the server stamps a per-session `cleared_response_id` view
// cutoff and re-emits `session.active` carrying the (now-empty) post-cutoff
// slice, so other tabs reconcile on that event/their next snapshot. This local
// reset is the same REST-confirmed seed pattern as `seedFromBatch`; the WS
// snapshot stays the source of truth afterwards.
//
// KEEPS `sessionId` (the perpetual session persists — Limpiar never rotates it),
// `awaitingReply` (server-side cutoff-agnostic: zeroing it here would flicker
// the "esperando respuesta" badge 0→N on the next frame), and the batch/
// watchdog/credits state untouched. Only the three panels and their totals
// reset.
export function clearCockpit() {
  setStore({
    ...store,
    responses: [],
    cc: [],
    ccNew: 0,
    responsesTotal: 0,
    responsesOkTotal: 0,
  });
}

// Seed the store from a successful POST /api/batches response so the ring
// appears without waiting for the next WS event. `snapshot`/`batch.state`
// remain the source of truth thereafter (UX-DR12 — no optimistic jumps
// beyond this server-confirmed shape). Since Story 4.2 the POST may answer
// `state: "waiting"` (admission queue) — the seed mirrors the REAL state
// instead of hardcoding "sending".
export function seedFromBatch(batch: {
  id: number;
  gate_name: string;
  gate_display_value: string;
  state: string;
  sent: number;
  queued: number;
  failed: number;
  total: number;
  queue_position?: number | null;
}) {
  // Same batch already in the store ⇒ WS got there first (the backend emits
  // progress — even line 1 — before the POST returns): seeding would REGRESS
  // fresher state, rolling the ring back up to a full interval (deferred 2-2).
  if (store.batchId === batch.id) return;
  const waiting = batch.state === "waiting";

  setStore({
    state: waiting ? "waiting" : "sending",
    batchId: batch.id,
    gateName: batch.gate_name,
    gateDisplayValue: batch.gate_display_value,
    sent: batch.sent,
    queued: batch.queued,
    failed: batch.failed,
    // The POST response carries no line detail — start the panels clean for
    // the new batch. Pendientes refills from the batch.lines_queued the POST
    // triggers right after (the snapshot remains the source of truth).
    failedLines: [],
    pending: [],
    total: batch.total,
    etaSeconds: 0,
    queuePosition: waiting ? (batch.queue_position ?? null) : null,
    // The seed flips into "sending"/"waiting" — never a paused state, so there
    // is no pause reason (Phase 2). batch.state remains the source of truth.
    pauseReason: null,
    // The session fields belong to the SESSION, not the batch — never seeded
    // away (3.2). If this batch activated ANOTHER session, the batch.state
    // the POST emits right after carries the new session_id and the reducer
    // clears — the server decides, the seed never guesses.
    ccNew: store.ccNew,
    sessionId: store.sessionId,
    sessionName: store.sessionName,
    sessionGateName: store.sessionGateName,
    sessionDisplayValue: store.sessionDisplayValue,
    responses: store.responses,
    cc: store.cc,
    responsesTotal: store.responsesTotal,
    responsesOkTotal: store.responsesOkTotal,
    // Session-scoped: never seeded away (the POST's batch.state/progress
    // reconciles it). Preserved like the other session totals above.
    awaitingReply: store.awaitingReply,
    // A global FloodWait window doesn't end because a batch was posted.
    floodUntil: store.floodUntil,
    // System state (4.1) — a new batch never clears the global-pause banner
    // (and the backend rejects the POST while latched anyway).
    watchdog: store.watchdog,
    // Tenant-scoped (credits feature) — never seeded away.
    creditBalance: store.creditBalance,
  });
}
