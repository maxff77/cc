// Singleton auto-reconnecting WebSocket client + live-batch store (Story 2.2;
// pause/resume/stop + FloodWait state since Story 2.3).
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

export interface LiveBatchState {
  state: BatchSurfaceState;
  batchId: number | null;
  gateName: string | null;
  gateValue: string | null;
  sent: number;
  queued: number;
  total: number;
  etaSeconds: number;
  ccNew: number;
  // Epoch ms when the FloodWait window ends; null = no active notice. Set by
  // `flood.wait`, cleared by any signal that sending flows again (AC 6).
  floodUntil: number | null;
}

// --- Hand-typed WS payload shapes (mirror backend services/batches.py) ------

interface SnapshotData {
  state: BatchSurfaceState;
  batch_id: number | null;
  gate_name: string | null;
  gate_value: string | null;
  sent: number;
  queued: number;
  total: number;
  eta_seconds: number;
  cc_new: number;
}

interface ProgressData {
  batch_id: number;
  sent: number;
  queued: number;
  total: number;
  eta_seconds: number;
}

// Full-context batch.state payload (Story 2.3, Task 5 — fixes the 2.2 finding
// where a second tab never learned the gate of a batch started elsewhere).
interface BatchStateData {
  state: BatchSurfaceState;
  batch_id: number | null;
  gate_name: string | null;
  gate_value: string | null;
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
  total: 0,
  etaSeconds: 0,
  ccNew: 0,
  floodUntil: null,
};

let store: LiveBatchState = IDLE;
const listeners = new Set<() => void>();

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
        total: d.total,
        etaSeconds: d.eta_seconds,
        ccNew: d.cc_new,
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
        total: d.total,
        etaSeconds: d.eta_seconds,
        floodUntil: null,
      });
      break;
    }
    case "batch.state": {
      const d = data as BatchStateData;

      if (d.state === "idle") {
        // Batch drained or stopped → back to the idle surface.
        setStore(IDLE);
      } else {
        setStore({
          ...store,
          state: d.state,
          batchId: d.batch_id,
          gateName: d.gate_name,
          gateValue: d.gate_value,
          // Resumed sending ⇒ the FloodWait notice self-dismisses (AC 6).
          floodUntil: d.state === "sending" ? null : store.floodUntil,
        });
      }
      break;
    }
    case "flood.wait": {
      const d = data as FloodWaitData;

      setStore({ ...store, floodUntil: Date.now() + d.seconds * 1000 });
      break;
    }
    case "batch.line_sent":
      // A line went out ⇒ sending flows again — clear any FloodWait notice.
      // (Other consumers arrive in Stories 2.5/3.2.)
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
  total: number;
}) {
  setStore({
    state: "sending",
    batchId: batch.id,
    gateName: batch.gate_name,
    gateValue: batch.gate_value,
    sent: batch.sent,
    queued: batch.queued,
    total: batch.total,
    etaSeconds: store.batchId === batch.id ? store.etaSeconds : 0,
    ccNew: store.batchId === batch.id ? store.ccNew : 0,
    // A global FloodWait window doesn't end because a batch was posted.
    floodUntil: store.floodUntil,
  });
}
