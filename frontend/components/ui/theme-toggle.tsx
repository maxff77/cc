"use client";

// Light/dark toggle (Ranger-X handoff Chrome control). Drives the existing
// next-themes provider (attribute:"class", defaultTheme:"dark") — flips the
// .light/.dark class the token layer keys off. Mounted-guard renders a stable
// placeholder until hydration so the icon never mismatches the SSR markup.
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";

import { Icon } from "@/components/ui/icon";

export function ThemeToggle({ className }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const isDark = resolvedTheme !== "light";
  const cls =
    "rx-focus flex size-[38px] items-center justify-center rounded-[var(--radius-field)] border border-border bg-surface-secondary text-foreground " +
    (className ?? "");

  if (!mounted) {
    // Placeholder keeps layout stable; aria-hidden until interactive.
    return <span aria-hidden className={cls} />;
  }

  return (
    <button
      aria-label="Cambiar tema"
      className={cls}
      title="Cambiar tema"
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      <Icon name={isDark ? "sun" : "moon"} size={18} />
    </button>
  );
}
