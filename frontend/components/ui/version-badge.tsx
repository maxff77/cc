// Discreet, non-interactive version stamp pinned to the bottom-right corner.
// Reads the build-time version injected from package.json (next.config.mjs).
// Single source of truth: bump package.json `version` to change what shows.
export function VersionBadge() {
  const version = process.env.NEXT_PUBLIC_APP_VERSION;
  if (!version) return null;

  // z-30 stays below z-50 modal overlays; bottom-16 clears the lg:hidden mobile
  // cockpit nav, lg:bottom-2 hugs the corner on desktop where there is none.
  return (
    <span
      aria-hidden
      className="pointer-events-none fixed bottom-16 right-2 z-30 select-none font-mono text-[10px] leading-none text-foreground/25 lg:bottom-2"
    >
      v{version}
    </span>
  );
}
