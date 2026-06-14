"use client";

// Admin chrome (ui-polish-spec §2.9): header strip structurally identical to
// ClientNav's (brand, inline nav, logout — the ONE home of the admin logout
// handler) over a max-w-5xl main with PageHeader. Both admin pages mount
// their two-zone grid inside.
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { Button } from "@heroui/react";

import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/page-header";

// AdminShell only ever renders for admin/owner (middleware gates /admin/*),
// so Envío + Historial are always shown — they are staff's path BACK to the
// sender (owner/admins send too: 3-tier priority owner > admin > client).
const ITEMS = [
  { href: "/", label: "Envío", ownerOnly: false },
  { href: "/sessions", label: "Historial", ownerOnly: false },
  { href: "/admin/users", label: "Usuarios", ownerOnly: false },
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
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3 lg:px-6">
        <div className="flex items-center gap-6">
          <Link
            className="font-mono text-lg font-bold tracking-[-0.03em] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            href="/admin/users"
          >
            CC
          </Link>
          <nav className="flex items-center gap-1">
            {ITEMS.filter((item) => !item.ownerOnly || gatesVisible).map(
              (item) => (
                <Link
                  key={item.href}
                  className={clsx(
                    "rounded px-3 py-2 text-sm font-medium transition-colors hover:bg-surface-secondary hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                    pathname === item.href ||
                      pathname.startsWith(item.href + "/")
                      ? "bg-surface-tertiary text-foreground"
                      : "text-muted",
                  )}
                  href={item.href}
                >
                  {item.label}
                </Link>
              ),
            )}
          </nav>
        </div>
        <Button size="sm" variant="secondary" onPress={logout}>
          Cerrar sesión
        </Button>
      </header>

      <main className="mx-auto w-full max-w-5xl px-4 py-6 lg:px-6">
        <div className="flex flex-col gap-6">
          <PageHeader actions={actions} title={title} />
          {children}
        </div>
      </main>
    </div>
  );
}
