// Singleton auto-reconnecting WebSocket client + live-batch store (Story 2.2).
//
// WS event payloads are NOT in the generated OpenAPI types — this is the one
// legitimate hand-typed contract, kept next to the reducer. Envelope:
// {"event": "<name>", "data": {...}}; server→client only.
//
// Reconnect contract (UX-DR13 / AC 11): capped exponential backoff (1s → 10s,
// forever), and every fresh `snapshot` REPLACES the whole store — that's the
// silent reconciliation. NO offline banners, NO queued actions.

import { useSyncExternalStore } from "react";

export interface LiveBatchState {
  state: "idle" | "sending";
  batchId: number | null;
  gateName: string | null;
  gateValue: string | null;
  sent: number;
  queued: number;
  total: number;
  etaSeconds: number;
  ccNew: number;
}

// --- Hand-typed WS payload shapes (mirror backend services/batches.py) ------

interface SnapshotData {
  state: "idle" | "sending";
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

interface BatchStateData {
  state: "idle" | "sending";
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
// Unknown events are ignored without crashing (2.3+ adds more events).
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
      });
      break;
    }
    case "batch.progress": {
      const d = data as ProgressData;

      setStore({
        ...store,
        state: "sending",
        batchId: d.batch_id,
        sent: d.sent,
        queued: d.queued,
        total: d.total,
        etaSeconds: d.eta_seconds,
      });
      break;
    }
    case "batch.state": {
      const d = data as BatchStateData;

      if (d.state === "idle") {
        // Batch drained → back to the idle surface.
        setStore(IDLE);
      } else {
        setStore({ ...store, state: d.state });
      }
      break;
    }
    case "batch.line_sent":
      // Received and tolerated — consumers arrive in Stories 2.5/3.2.
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
  });
}
