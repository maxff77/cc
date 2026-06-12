"use client";

// Client navigation (UX-DR10): EXACTLY Envío | Historial. Bottom nav on
// mobile (< lg) with a 6px live dot on Envío (success green while sending —
// warning-while-paused arrives with 2.3); inline header strip on desktop.
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { Button } from "@heroui/react";

import { api } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";

const ITEMS = [
  { href: "/", label: "Envío" },
  { href: "/sessions", label: "Historial" },
] as const;

function NavItem({
  href,
  label,
  active,
  showDot,
  className,
}: {
  href: string;
  label: string;
  active: boolean;
  showDot: boolean;
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
        {showDot && (
          <span
            aria-hidden
            className="absolute -right-2.5 top-0 size-1.5 rounded-full bg-success"
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

  const items = ITEMS.map((item) => (
    <NavItem
      key={item.href}
      active={pathname === item.href}
      href={item.href}
      label={item.label}
      showDot={item.href === "/" && live.state === "sending"}
    />
  ));

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-4 py-3 lg:px-6">
        <div className="flex items-center gap-6">
          <span className="text-lg font-semibold">CC</span>
          {/* Desktop: the two items inline in the header strip. */}
          <nav className="hidden items-center gap-1 lg:flex">{items}</nav>
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
