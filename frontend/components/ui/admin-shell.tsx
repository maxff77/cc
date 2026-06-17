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
import { Mark } from "@/components/ui/logo";
import { Btn } from "@/components/ui/btn";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { RxBackdrop } from "@/components/ui/rx-backdrop";

// AdminShell only ever renders for admin/owner (middleware gates /admin/*),
// so Envío + Historial are always shown — staff's path BACK to the sender
// (owner/admins send too: 3-tier priority owner > admin > client).
const ITEMS = [
  { href: "/", label: "Envío", ownerOnly: false },
  { href: "/sessions", label: "Historial", ownerOnly: false },
  { href: "/admin/users", label: "Usuarios", ownerOnly: false },
  { href: "/admin/plans", label: "Planes", ownerOnly: true },
  { href: "/admin/gates", label: "Gates", ownerOnly: true },
  { href: "/admin/destinos", label: "Destinos", ownerOnly: true },
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

  return (
    <div className="relative flex min-h-screen flex-col">
      <RxBackdrop />
      <header className="sticky top-0 z-30 flex items-center justify-between gap-4 border-b border-border bg-[color-mix(in_oklch,var(--background)_82%,transparent)] px-4 py-3 backdrop-blur-md lg:px-6">
        <div className="flex min-w-0 items-center gap-6">
          <Link
            className="rx-focus flex shrink-0 items-center gap-2.5"
            href="/admin/users"
          >
            <Mark size={28} />
            <span className="gradient-text font-display text-xl font-extrabold italic leading-none tracking-[0.01em]">
              RANGER-X
            </span>
          </Link>
          <nav className="flex items-center gap-1">
            {ITEMS.filter((item) => !item.ownerOnly || gatesVisible).map(
              (item) => {
                const active =
                  pathname === item.href ||
                  pathname.startsWith(item.href + "/");

                return (
                  <Link
                    key={item.href}
                    className={clsx(
                      "rx-focus relative rounded-[var(--radius-sm)] px-3 py-2 font-display text-sm font-semibold tracking-[0.01em] transition-colors",
                      active
                        ? "bg-surface-tertiary text-foreground"
                        : "text-muted hover:text-foreground",
                    )}
                    href={item.href}
                  >
                    {item.label}
                    {active && (
                      <span
                        aria-hidden
                        className="brand-fill absolute inset-x-3 -bottom-[13px] h-0.5 rounded"
                      />
                    )}
                  </Link>
                );
              },
            )}
          </nav>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <ThemeToggle />
          <Btn size="sm" variant="secondary" onClick={logout}>
            Cerrar sesión
          </Btn>
        </div>
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
