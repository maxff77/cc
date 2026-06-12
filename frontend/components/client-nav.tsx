"use client";

// Client navigation (UX-DR10): EXACTLY Envío | Historial. Bottom nav on
// mobile (< lg) with a 6px live dot on Envío (success green while sending,
// warning amber while paused/stopping — Story 2.3); inline header strip on
// desktop. The header also hosts the state pill (DESIGN.md: brand, nav,
// state pill) — the ONLY full-round piece of the system, mirroring
// `batch.state` verbatim and hidden at idle (AC 2).
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { Button, Chip } from "@heroui/react";

import { api } from "@/lib/api";
import { useLiveBatch, type BatchSurfaceState } from "@/lib/ws";

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

// Tints per DESIGN.md state-pill tokens: accent .22 / warning .18; 'stopping'
// has no token — recorded decision: danger tint at the same ~18% (Detener
// wears danger; the state lasts sub-seconds in practice). 'waiting' wears
// warning like paused: same "vivo pero no enviando" family (Story 4.2).
const PILL_CLASS: Record<Exclude<BatchSurfaceState, "idle">, string> = {
  sending: "bg-accent/22 text-accent",
  // text-warning (not -foreground): the app is fixed dark-mode and
  // --warning-foreground is the near-black contrast color for SOLID warning
  // fills — on a tint it was unreadable (deferred 2-3 #2, absorbed here).
  paused: "bg-warning/18 text-warning",
  stopping: "bg-danger/18 text-danger",
  waiting: "bg-warning/18 text-warning",
};

function StatePill({ state }: { state: BatchSurfaceState }) {
  if (state === "idle") return null; // hidden at idle — never renders

  return (
    <Chip
      className={clsx(
        "rounded-full text-[10px] font-medium uppercase tracking-[0.12em]",
        PILL_CLASS[state],
      )}
    >
      {PILL_COPY[state]}
    </Chip>
  );
}

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
        "rounded-md px-3 py-2 text-sm font-medium",
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

  const items = ITEMS.map((item) => (
    <NavItem
      key={item.href}
      // Prefix match keeps Historial lit on /sessions/[id] (Story 3.3); the
      // "/" item stays exact-only so it never lights up everywhere.
      active={
        pathname === item.href ||
        (item.href !== "/" && pathname.startsWith(item.href + "/"))
      }
      dot={item.href === "/" ? dot : null}
      href={item.href}
      label={item.label}
    />
  ));

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-4 py-3 lg:px-6">
        <div className="flex items-center gap-6">
          <span className="text-lg font-semibold">CC</span>
          {/* Desktop: the two items inline in the header strip. */}
          <nav className="hidden items-center gap-1 lg:flex">{items}</nav>
          <StatePill state={live.state} />
        </div>
        <Button size="sm" variant="secondary" onPress={logout}>
          Cerrar sesión
        </Button>
      </header>

      {/* Mobile: fixed bottom nav (the cockpit never scrolls away). */}
      <nav className="fixed inset-x-0 bottom-0 z-10 flex items-center justify-around border-t border-border bg-background py-2 lg:hidden">
        {items}
      </nav>
    </>
  );
}
