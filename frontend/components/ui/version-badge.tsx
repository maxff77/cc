// Brand version stamp. Reads the build-time version injected from package.json
// (next.config.mjs) — single source of truth: bump package.json `version`.
// Rendered inline in the navbar as a brand-gradient pill (`.btn-fill` is the
// AA-safe gradient for white text). Non-interactive, decorative.
export function VersionPill() {
  const version = process.env.NEXT_PUBLIC_APP_VERSION;
  if (!version) return null;

  return (
    <span
      aria-label={`Versión ${version}`}
      className="btn-fill glow-soft inline-flex shrink-0 select-none items-center rounded-full px-2.5 py-1 font-mono text-[11px] font-bold uppercase leading-none tracking-wide text-white shadow-sm ring-1 ring-white/20"
    >
      v{version}
    </span>
  );
}
