"use client";

// Client navigation (UX-DR10 / Ranger-X handoff `Chrome`): sticky blurred
// header with the shield Mark + gradient RANGER-X wordmark, nav tabs carrying a
// brand-gradient underline when active, the live StatePill, a light/dark toggle
// and Cerrar sesión. Staff (owner/admin) also send, so for them the nav
// cross-links to admin (Usuarios, + Gates/Destinos for owner) — clients never
// see admin links. Bottom nav on mobile (< lg) keeps the cockpit reachable; a
// 6px live dot rides Envío there (success while sending, warning while
// paused/stopping). The header StatePill mirrors `batch.state`, hidden at idle.
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useLiveBatch, type BatchSurfaceState } from "@/lib/ws";
import { Mark } from "@/components/ui/logo";
import { Btn } from "@/components/ui/btn";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { StatePill, type PillTone } from "@/components/ui/state-pill";

interface Me {
  role: string;
}

type NavLink = { href: string; label: string };

const ITEMS: readonly NavLink[] = [
  { href: "/", label: "Envío" },
  { href: "/sessions", label: "Historial" },
];

// Cross-links to admin, shown ONLY to staff. Gates/Destinos are owner-only.
const ADMIN_ITEMS: readonly NavLink[] = [
  { href: "/admin/users", label: "Usuarios" },
];
const OWNER_ITEMS: readonly NavLink[] = [
  { href: "/admin/gates", label: "Gates" },
  { href: "/admin/destinos", label: "Destinos" },
];

// Verbatim copy per state (EXPERIENCE.md microcopy — tuteo, exact).
const PILL_COPY: Record<Exclude<BatchSurfaceState, "idle">, string> = {
  sending: "Enviando",
  paused: "En pausa",
  stopping: "Deteniendo",
  waiting: "En espera",
};

// Tone + dot per state (ui-polish-spec §3.3): sending = accent + pulse,
// paused/waiting = warning + static, stopping = danger (no dot — sub-second).
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
        "rx-focus relative rounded-[var(--radius-sm)] px-3 py-2 font-display text-sm font-semibold tracking-[0.01em] transition-colors",
        active
          ? "bg-surface-tertiary text-foreground"
          : "text-muted hover:text-foreground",
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
      {/* Brand-gradient underline marks the active tab (handoff Chrome). */}
      {active && (
        <span
          aria-hidden
          className="brand-fill absolute inset-x-3 -bottom-[13px] h-0.5 rounded"
        />
      )}
    </Link>
  );
}

export function ClientNav() {
  const pathname = usePathname();
  const live = useLiveBatch();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/api/auth/me"),
  });
  const role = me.data?.role;
  const navItems: readonly NavLink[] =
    role === "owner"
      ? [...ITEMS, ...ADMIN_ITEMS, ...OWNER_ITEMS]
      : role === "admin"
        ? [...ITEMS, ...ADMIN_ITEMS]
        : ITEMS;

  async function logout() {
    try {
      await api.post("/api/auth/logout");
    } finally {
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
    navItems.map((item) => (
      <NavItem
        key={item.href}
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
      <header className="sticky top-0 z-30 flex items-center justify-between gap-4 border-b border-border bg-[color-mix(in_oklch,var(--background)_82%,transparent)] px-4 py-3 backdrop-blur-md lg:px-6">
        <div className="flex min-w-0 items-center gap-6">
          <Link
            className="rx-focus flex shrink-0 items-center gap-2.5"
            href="/"
          >
            <Mark size={28} />
            <span className="gradient-text font-display text-xl font-extrabold italic leading-none tracking-[0.01em]">
              RANGER-X
            </span>
          </Link>
          {/* Desktop: inline nav tabs. */}
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
        <div className="flex shrink-0 items-center gap-2.5">
          <ThemeToggle />
          <Btn size="sm" variant="secondary" onClick={logout}>
            Cerrar sesión
          </Btn>
        </div>
      </header>

      {/* Mobile: fixed bottom nav (the cockpit never scrolls away). */}
      <nav className="fixed inset-x-0 bottom-0 z-10 flex items-center justify-around border-t border-border bg-background pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 lg:hidden">
        {items("flex-1 text-center")}
      </nav>
    </>
  );
}
