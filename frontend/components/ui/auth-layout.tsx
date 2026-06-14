// Centered auth scaffold (Ranger-X handoff screens3 `AuthLayout`): ambient
// backdrop + Logo + a brand-gradient-soft card + footer Mark/©, shared by
// change-password / expired / error. Login renders its own richer card but
// reuses CornerTicks from here. Pure presentation — no client hooks, so it can
// wrap a server-rendered page or the error boundary alike.
import Link from "next/link";

import { Logo, Mark } from "@/components/ui/logo";
import { RxBackdrop } from "@/components/ui/rx-backdrop";

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
        <Logo height={44} />
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
          <span className="font-mono text-[11px] tracking-[0.1em] text-[var(--faint)]">
            RANGER-X CHECK © 2026
          </span>
        </div>
      </div>
    </main>
  );
}

// Neon L-corners (handoff screens.jsx `CornerTicks`) — accent/magenta/cyan
// brackets mounted on the login card's four corners.
export function CornerTicks() {
  const corner = "pointer-events-none absolute size-[22px]";

  return (
    <>
      <span className={`${corner} -left-px -top-px`}>
        <span className="absolute left-0 top-0 h-0.5 w-[22px] bg-accent/50" />
        <span className="absolute left-0 top-0 h-[22px] w-0.5 bg-accent/50" />
      </span>
      <span className={`${corner} -right-px -top-px`}>
        <span className="absolute right-0 top-0 h-0.5 w-[22px] bg-[var(--magenta)] opacity-50" />
        <span className="absolute right-0 top-0 h-[22px] w-0.5 bg-[var(--magenta)] opacity-50" />
      </span>
      <span className={`${corner} -bottom-px -left-px`}>
        <span className="absolute bottom-0 left-0 h-0.5 w-[22px] bg-[var(--cyan)] opacity-50" />
        <span className="absolute bottom-0 left-0 h-[22px] w-0.5 bg-[var(--cyan)] opacity-50" />
      </span>
      <span className={`${corner} -bottom-px -right-px`}>
        <span className="absolute bottom-0 right-0 h-0.5 w-[22px] bg-[var(--magenta)] opacity-50" />
        <span className="absolute bottom-0 right-0 h-[22px] w-0.5 bg-[var(--magenta)] opacity-50" />
      </span>
    </>
  );
}
