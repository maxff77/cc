"use client";

// Client navigation (UX-DR10): EXACTLY Envío | Historial. Bottom nav on
// mobile (< lg) with a 6px live dot on Envío (success green while sending,
// warning amber while paused/stopping — Story 2.3); inline header strip on
// desktop. The header also hosts the state pill (DESIGN.md: brand, nav,
// state pill) — the ONLY full-round piece of the system (now the shared
// StatePill primitive), mirroring `batch.state` verbatim and hidden at idle
// (AC 2).
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { Button } from "@heroui/react";

import { api } from "@/lib/api";
import { useLiveBatch, type BatchSurfaceState } from "@/lib/ws";
import { StatePill, type PillTone } from "@/components/ui/state-pill";

const ITEMS = [
  { href: "/", label: "Envío" },
  { href: "/sessions", label: "Historial" },
] as const;

// Verbatim copy per state (EXPERIENCE.md microcopy — tuteo, exact).
const PILL_COPY: Record<Exclude<BatchSurfaceState, "idle">, string> = {
  sending: "Enviando",
  paused: "En pausa",
  stopping: "Deteniendo",
  // Story 4.2: queued for admission — live but not sending yet.
  waiting: "En espera",
};

// Tone + dot per state (ui-polish-spec §3.3): sending = accent + pulse,
// paused = warning + static, stopping = danger (no dot — sub-second state).
// 'waiting' wears warning like paused: same "vivo pero no enviando" family
// (Story 4.2).
const PILL_TONE: Record<Exclude<BatchSurfaceState, "idle">, PillTone> = {
  sending: "accent",
  paused: "warning",
  stopping: "danger",
  waiting: "warning",
};

function NavItem({
  href,
  label,
  active,
  dot,
  className,
}: {
  href: string;
  label: string;
  active: boolean;
  dot: "success" | "warning" | null;
  className?: string;
}) {
  return (
    <Link
      className={clsx(
        "rounded px-3 py-2 text-sm font-medium transition-colors hover:bg-surface-secondary hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        active ? "bg-surface-tertiary text-foreground" : "text-muted",
        className,
      )}
      href={href}
    >
      <span className="relative">
        {label}
        {dot && (
          <span
            aria-hidden
            className={clsx(
              "absolute -right-2.5 top-0 size-1.5 rounded-full",
              dot === "success" ? "bg-success" : "bg-warning",
            )}
          />
        )}
      </span>
    </Link>
  );
}

export function ClientNav() {
  const pathname = usePathname();
  const live = useLiveBatch();

  async function logout() {
    try {
      await api.post("/api/auth/logout");
    } finally {
      // Full navigation so middleware re-reads the cleared cookie.
      window.location.assign("/login");
    }
  }

  // Live dot (UX-DR10 / AC 6): success while sending, warning while paused,
  // stopping or waiting ("vivo pero no enviando"), none at idle.
  const dot: "success" | "warning" | null =
    live.state === "sending"
      ? "success"
      : live.state === "paused" ||
          live.state === "stopping" ||
          live.state === "waiting"
        ? "warning"
        : null;

  const items = (itemClassName?: string) =>
    ITEMS.map((item) => (
      <NavItem
        key={item.href}
        // Prefix match keeps Historial lit on /sessions/[id] (Story 3.3); the
        // "/" item stays exact-only so it never lights up everywhere.
        active={
          pathname === item.href ||
          (item.href !== "/" && pathname.startsWith(item.href + "/"))
        }
        className={itemClassName}
        dot={item.href === "/" ? dot : null}
        href={item.href}
        label={item.label}
      />
    ));

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-4 py-3 lg:px-6">
        <div className="flex items-center gap-6">
          <Link
            className="font-mono text-lg font-bold tracking-[-0.03em] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            href="/"
          >
            CC
          </Link>
          {/* Desktop: the two items inline in the header strip. */}
          <nav className="hidden items-center gap-1 lg:flex">{items()}</nav>
          {live.state !== "idle" && (
            <StatePill
              dot={
                live.state === "sending"
                  ? "pulse"
                  : live.state === "paused" || live.state === "waiting"
                    ? "static"
                    : undefined
              }
              tone={PILL_TONE[live.state]}
            >
              {PILL_COPY[live.state]}
            </StatePill>
          )}
        </div>
        <Button size="sm" variant="secondary" onPress={logout}>
          Cerrar sesión
        </Button>
      </header>

      {/* Mobile: fixed bottom nav (the cockpit never scrolls away);
          safe-area padding for home-indicator devices. */}
      <nav className="fixed inset-x-0 bottom-0 z-10 flex items-center justify-around border-t border-border bg-background pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 lg:hidden">
        {items("flex-1 text-center")}
      </nav>
    </>
  );
}
