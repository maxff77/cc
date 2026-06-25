// Centered auth scaffold (Ranger-X handoff screens3 `AuthLayout`): ambient
// backdrop + Logo + a brand-gradient-soft card + footer Mark/©, shared by
// change-password / expired / error. Login renders its own card. Pure
// presentation — no client hooks, so it can wrap a server-rendered page or the
// error boundary alike.
import Link from "next/link";

import { Logo, Mark } from "@/components/ui/logo";
import { RxBackdrop } from "@/components/ui/rx-backdrop";
import { VersionPill } from "@/components/ui/version-badge";

export function AuthLayout({
  title,
  subtitle,
  children,
  back,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  back?: { href: string; label: string };
}) {
  return (
    <main className="relative flex min-h-screen items-center justify-center px-5 py-10">
      <RxBackdrop />
      <div className="rx-enter relative z-[1] flex w-full max-w-[420px] flex-col items-center gap-6">
        <Logo priority maxWidth={300} />
        <div
          className="w-full rounded-[18px] border border-border bg-surface p-7"
          style={{ backgroundImage: "var(--brand-gradient-soft)" }}
        >
          <div className="mb-5 text-center">
            <h1 className="font-display text-xl font-extrabold tracking-[0.01em] text-foreground">
              {title}
            </h1>
            {subtitle && (
              <p className="mt-2 text-sm leading-relaxed text-muted">
                {subtitle}
              </p>
            )}
          </div>
          {children}
        </div>
        {back && (
          <Link
            className="rx-focus flex items-center gap-1.5 text-sm text-muted hover:text-foreground"
            href={back.href}
          >
            ← {back.label}
          </Link>
        )}
        <div className="flex flex-col items-center gap-2">
          <Mark size={26} />
          <span className="font-mono text-[11px] tracking-[0.1em] text-muted">
            RANGER-X CHECK © 2026
          </span>
          <VersionPill />
        </div>
      </div>
    </main>
  );
}
