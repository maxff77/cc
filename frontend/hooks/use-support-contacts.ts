"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { siteConfig } from "@/config/site";

export interface SupportContact {
  handle: string;
}

// Owner-managed Telegram support handles (editable-support-contacts). Fetched
// once from the public endpoint and cached for the SPA session, so the many
// surfaces that show Soporte (login, /expired, client-nav) share a single
// request. Falls back to the static siteConfig defaults while loading or if the
// call fails — the support channel must never render empty.
let cache: SupportContact[] | null = null;
let inflight: Promise<SupportContact[]> | null = null;

function load(): Promise<SupportContact[]> {
  if (cache) return Promise.resolve(cache);
  if (!inflight) {
    inflight = api
      .get<{ contacts: SupportContact[] }>("/api/public/support-contacts")
      .then((res) => {
        cache =
          res.contacts.length > 0 ? res.contacts : [...siteConfig.contacts];

        return cache;
      })
      .catch(() => [...siteConfig.contacts]) // keep static fallback; retry next mount
      .finally(() => {
        inflight = null;
      });
  }

  return inflight;
}

export function useSupportContacts(): SupportContact[] {
  const [contacts, setContacts] = useState<SupportContact[]>(
    cache ?? [...siteConfig.contacts],
  );

  useEffect(() => {
    let cancelled = false;

    void load().then((resolved) => {
      if (!cancelled) setContacts(resolved);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return contacts;
}
