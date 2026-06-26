// Brand version stamp. Reads the build-time version injected from package.json
// (next.config.mjs) — single source of truth: bump package.json `version`.
// Rendered inline in the navbar as a brand-gradient pill (`.btn-fill` is the
// AA-safe gradient for white text). Non-interactive, decorative.
export function VersionPill() {
  const version = process.env.NEXT_PUBLIC_APP_VERSION;
  if (!version) return null;

  // Canvas version pill: mono brand-gradient stamp, 22px tall, r7, white text
  // with a soft brand glow. Hidden on mobile (desktop chrome only).
  return (
    <span
      aria-label={`Versión ${version}`}
      className="brand-fill hidden h-[22px] shrink-0 select-none items-center rounded-[7px] px-[9px] font-mono text-[10.5px] font-semibold uppercase leading-none text-white sm:inline-flex"
      style={{
        letterSpacing: ".06em",
        boxShadow: "0 2px 10px oklch(64% 0.21 295 / 0.30)",
      }}
      title="Versión del sistema"
    >
      v{version}
    </span>
  );
}
