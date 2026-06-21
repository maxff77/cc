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
import { useEffect, useState } from "react";
import clsx from "clsx";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { siteConfig, telegramHref } from "@/config/site";
import { useLiveBatch, type BatchSurfaceState } from "@/lib/ws";
import { Mark, Wordmark } from "@/components/ui/logo";
import { Btn } from "@/components/ui/btn";
import { Icon } from "@/components/ui/icon";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { StatePill, type PillTone } from "@/components/ui/state-pill";
import { PlanBadge } from "@/components/ui/plan-badge";
import { KeyModal } from "@/components/keys/key-modal";

interface Me {
  role: string;
  expires_at: string | null;
}

type NavLink = { href: string; label: string };

const ITEMS: readonly NavLink[] = [
  { href: "/app", label: "Envío" },
  { href: "/app/historial", label: "Historial" },
];

// Cross-links to admin, shown ONLY to staff. Gates/Destinos are owner-only.
const ADMIN_ITEMS: readonly NavLink[] = [
  { href: "/admin/users", label: "Usuarios" },
];
const OWNER_ITEMS: readonly NavLink[] = [
  { href: "/admin/gates", label: "Gateways" },
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
        "tap-44 rx-focus relative flex items-center justify-center rounded-[var(--radius-sm)] px-3 py-2 font-display text-sm font-semibold tracking-[0.01em] transition-colors",
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
  // Cliente Redesign: "Canjear key" lives in a modal opened from the nav, and
  // Soporte + Cerrar sesión collapse into the ⋯ overflow menu.
  const [keyOpen, setKeyOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  // Escape closes the overflow menu (click-outside is the backdrop button).
  useEffect(() => {
    if (!menuOpen) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("keydown", onKey);

    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const role = me.data?.role;
  const navItems: readonly NavLink[] =
    role === "owner"
      ? [...ITEMS, ...ADMIN_ITEMS, ...OWNER_ITEMS]
      : role === "admin"
        ? [...ITEMS, ...ADMIN_ITEMS]
        : ITEMS;
  // The support contact is for clients to reach the seller/support; staff ARE
  // the seller, so the link is hidden for them (also keeps the mobile bottom
  // nav at 3 items for clients — staff's 5 cross-links + Soporte would wrap).
  const isStaff = role === "owner" || role === "admin";

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
          (item.href !== "/app" && pathname.startsWith(item.href + "/"))
        }
        className={itemClassName}
        dot={item.href === "/app" ? dot : null}
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
            href="/app"
          >
            <Mark size={28} />
            <Wordmark height={22} />
          </Link>
          {/* Desktop: inline nav tabs. */}
          <nav className="hidden items-center gap-1 lg:flex">{items()}</nav>
          {/* On phones the cockpit ring + bottom-nav live dot already carry
              state; the header pill returns at sm+ where there's room. */}
          {live.state !== "idle" && (
            <span className="hidden sm:inline-flex">
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
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          {/* Always-visible plan status (clients only): days left + tone.
              Shown on every client screen, desktop and mobile. */}
          {!isStaff && (
            <PlanBadge expiresAt={me.data?.expires_at ?? null} />
          )}
          {/* Canjear key → modal (Cliente Redesign). Icon-only on phones (the
              label returns at sm+) so the header clears the plan badge + theme
              toggle + ⋯ without overflow on a ~360px screen. */}
          <button
            className="tap-44 rx-focus inline-flex h-[34px] items-center gap-2 rounded-[var(--radius-field)] border border-[color-mix(in_oklch,var(--accent)_45%,transparent)] bg-[var(--accent-soft)] px-3 font-display text-[13px] font-semibold text-foreground transition-[transform] duration-150 hover:-translate-y-px"
            type="button"
            onClick={() => setKeyOpen(true)}
          >
            <Icon className="text-accent" name="key" size={15} />
            <span className="hidden sm:inline">Canjear key</span>
          </button>
          <ThemeToggle />
          {/* Overflow ⋯ — Soporte (clients only) + Cerrar sesión. Consolidates
              what used to be two standalone header buttons. */}
          <div className="relative">
            <Btn
              aria-label="Más"
              size="sm"
              variant="secondary"
              onClick={() => setMenuOpen((o) => !o)}
            >
              <Icon name="dots" size={18} />
            </Btn>
            {menuOpen && (
              <>
                <button
                  aria-label="Cerrar menú"
                  className="fixed inset-0 z-40 cursor-default"
                  tabIndex={-1}
                  type="button"
                  onClick={() => setMenuOpen(false)}
                />
                <div className="rx-enter glow-soft absolute right-0 top-[calc(100%+8px)] z-50 w-56 rounded-[var(--radius)] border border-[var(--border-strong)] bg-surface-secondary p-1.5">
                  {!isStaff && siteConfig.contacts[0] && (
                    <button
                      className="tap-44 rx-focus flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-3 py-2.5 text-left text-[13.5px] text-foreground transition-colors hover:bg-surface-tertiary"
                      type="button"
                      onClick={() => {
                        setMenuOpen(false);
                        window.open(
                          telegramHref(siteConfig.contacts[0].handle),
                          "_blank",
                          "noopener,noreferrer",
                        );
                      }}
                    >
                      <Icon className="text-accent" name="phone" size={17} />
                      Soporte · @{siteConfig.contacts[0].handle}
                    </button>
                  )}
                  {!isStaff && (
                    <div className="mx-2 my-1 h-px bg-[var(--separator)]" />
                  )}
                  <button
                    className="tap-44 rx-focus flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-3 py-2.5 text-left text-[13.5px] text-danger transition-colors hover:bg-surface-tertiary"
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      logout();
                    }}
                  >
                    <Icon name="logout" size={17} />
                    Cerrar sesión
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Mobile: fixed bottom nav (the cockpit never scrolls away). Clients get a
          Key entry that opens the claim modal (Soporte now lives in the header ⋯
          menu); staff keep their cross-links and no Key. */}
      <nav className="fixed inset-x-0 bottom-0 z-10 flex items-center justify-around border-t border-border bg-background pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 lg:hidden">
        {items("flex-1 text-center")}
        {!isStaff && (
          <button
            className="tap-44 rx-focus relative flex flex-1 items-center justify-center rounded-[var(--radius-sm)] px-3 py-2 text-center font-display text-sm font-semibold tracking-[0.01em] text-muted transition-colors hover:text-foreground"
            type="button"
            onClick={() => setKeyOpen(true)}
          >
            Key
          </button>
        )}
      </nav>

      <KeyModal open={keyOpen} onClose={() => setKeyOpen(false)} />
    </>
  );
}
