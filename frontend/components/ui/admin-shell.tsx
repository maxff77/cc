"use client";

// Admin chrome (ui-polish-spec §2.9 / Ranger-X handoff `Chrome`): header strip
// structurally identical to ClientNav's (shield Mark + gradient wordmark, nav
// tabs with brand-gradient underline, theme toggle, logout — the ONE home of
// the admin logout handler) over a max-w-6xl main with PageHeader. Both admin
// pages mount their two-zone grid inside.
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/page-header";
import { Mark, Wordmark } from "@/components/ui/logo";
import { Btn } from "@/components/ui/btn";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { RxBackdrop } from "@/components/ui/rx-backdrop";

// AdminShell only ever renders for admin/owner (middleware gates /admin/*),
// so Envío is always shown — staff's path BACK to the sender (owner/admins
// send too: 3-tier priority owner > admin > client).
const ITEMS = [
  { href: "/app", label: "Envío", ownerOnly: false },
  { href: "/admin/users", label: "Usuarios", ownerOnly: false },
  // Gift keys: admins + owner mint here (the tier is owner-fixed in Planes).
  { href: "/admin/keys", label: "Keys", ownerOnly: false },
  { href: "/admin/plans", label: "Planes", ownerOnly: true },
  { href: "/admin/gates", label: "Gateways", ownerOnly: true },
  { href: "/admin/destinos", label: "Destinos", ownerOnly: true },
  { href: "/admin/monitor", label: "Monitoreo", ownerOnly: true },
] as const;

export function AdminShell({
  title,
  gatesVisible = false,
  actions,
  children,
}: {
  title: string;
  gatesVisible?: boolean;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  async function logout() {
    try {
      await api.post("/api/auth/logout");
    } finally {
      // Full navigation so middleware re-reads the cleared cookie.
      window.location.assign("/login");
    }
  }

  const navItems = ITEMS.filter((item) => !item.ownerOnly || gatesVisible);

  // One renderer for both nav strips. The gradient underline is desktop-only:
  // on the mobile scroll strip overflow-x clips anything below the item, so
  // active state there leans on the surface-tertiary fill instead.
  const renderNavItem = (
    item: (typeof ITEMS)[number],
    { underline, className }: { underline: boolean; className?: string },
  ) => {
    // The cockpit root (/app) matches exactly only — a prefix match would light
    // up Envío for any nested /app/* route (the old "/" href had this property
    // for free).
    const active =
      pathname === item.href ||
      (item.href !== "/app" && pathname.startsWith(item.href + "/"));

    return (
      <Link
        key={item.href}
        className={clsx(
          "tap-44 rx-focus relative flex items-center rounded-[var(--radius-sm)] px-3 py-2 font-display text-sm font-semibold tracking-[0.01em] transition-colors",
          active
            ? "bg-surface-tertiary text-foreground"
            : "text-muted hover:text-foreground",
          className,
        )}
        href={item.href}
      >
        {item.label}
        {underline && active && (
          <span
            aria-hidden
            className="brand-fill absolute inset-x-3 -bottom-[13px] h-0.5 rounded"
          />
        )}
      </Link>
    );
  };

  return (
    <div className="relative flex min-h-screen flex-col">
      <RxBackdrop />
      <header className="sticky top-0 z-30 border-b border-border bg-[color-mix(in_oklch,var(--background)_82%,transparent)] backdrop-blur-md">
        <div className="flex items-center justify-between gap-4 px-4 py-3 lg:px-6">
          <div className="flex min-w-0 items-center gap-6">
            <Link
              className="rx-focus flex shrink-0 items-center gap-2.5"
              href="/admin/users"
            >
              <Mark size={28} />
              <Wordmark height={22} />
            </Link>
            {/* Desktop: inline tabs with the gradient active-underline. */}
            <nav className="hidden items-center gap-1 lg:flex">
              {navItems.map((item) => renderNavItem(item, { underline: true }))}
            </nav>
          </div>
          <div className="flex shrink-0 items-center gap-2.5">
            <ThemeToggle />
            <Btn size="sm" variant="secondary" onClick={logout}>
              Cerrar sesión
            </Btn>
          </div>
        </div>
        {/* Mobile: a horizontally-scrollable strip so all six owner tabs stay
            reachable without crushing the bar (no bottom nav on admin). */}
        <nav className="flex items-center gap-1 overflow-x-auto rx-scroll border-t border-border px-4 pb-2 pt-1.5 lg:hidden">
          {navItems.map((item) =>
            renderNavItem(item, {
              underline: false,
              className: "shrink-0 whitespace-nowrap",
            }),
          )}
        </nav>
      </header>

      <main className="relative z-[1] mx-auto w-full max-w-6xl px-4 py-6 sm:px-5 lg:px-8">
        <div className="flex flex-col gap-6">
          <PageHeader actions={actions} title={title} />
          {children}
        </div>
      </main>
    </div>
  );
}
