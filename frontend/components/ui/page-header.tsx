// Page header (ui-polish-spec §2.2): the ONLY home of the page-title type
// step, with an optional legend-style back link ("← LABEL"), an optional mono
// sub-line and a right actions slot.
import Link from "next/link";
import clsx from "clsx";

export function PageHeader({
  title,
  mono,
  back,
  actions,
  className,
}: {
  title: string;
  mono?: string;
  back?: { href: string; label: string };
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <header
      className={clsx("flex items-center justify-between gap-3", className)}
    >
      <div className="flex min-w-0 flex-col gap-1">
        {back && (
          <Link
            className="self-start text-[10px] font-bold uppercase tracking-[0.1em] text-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent"
            href={back.href}
          >
            {/* The caps are visual only (CSS `uppercase` above) — an
                all-caps string in the accessibility tree gets spelled
                letter-by-letter by some screen readers. */}
            ← {back.label}
          </Link>
        )}
        <h1 className="truncate text-lg font-bold tracking-[-0.01em]">
          {title}
        </h1>
        {mono && (
          <span className="truncate font-mono text-[11px] text-muted">
            {mono}
          </span>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 items-center gap-3">{actions}</div>
      )}
    </header>
  );
}
