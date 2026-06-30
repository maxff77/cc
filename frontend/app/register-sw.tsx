"use client";

import { useEffect } from "react";

// Registers the minimal PWA service worker (public/sw.js). Production only: a
// service worker under `next dev` fights HMR and serves stale chunks. Failure
// is non-fatal — install is a progressive enhancement, the app works without it.
export function RegisterSW() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;

    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }, []);

  return null;
}
