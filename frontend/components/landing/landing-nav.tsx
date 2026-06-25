import { LinkBtn } from "./link-btn";

import { Mark } from "@/components/ui/logo";
import { VersionPill } from "@/components/ui/version-badge";

// Public top bar. Minimal, non-sticky: brand mark + wordmark text on the left,
// the two conversion CTAs on the right. The wordmark is set in Saira (the
// display face) rather than the raster lockup so the bar stays compact.
export function LandingNav() {
  return (
    <header className="relative z-10 mx-auto flex w-full max-w-[1180px] items-center justify-between px-5 py-5 sm:px-8">
      <div className="flex items-center gap-2.5">
        <Mark size={30} />
        <span className="font-display text-[15px] font-extrabold uppercase tracking-[0.18em] text-foreground">
          Ranger-X<span className="text-muted"> Check</span>
        </span>
        <VersionPill />
      </div>
      <nav className="flex items-center gap-2.5">
        <LinkBtn
          className="hidden sm:inline-flex"
          href="/login"
          variant="ghost"
        >
          Iniciar sesión
        </LinkBtn>
        <LinkBtn href="/register" iconRight="arrow" variant="primary">
          Crear cuenta
        </LinkBtn>
      </nav>
    </header>
  );
}
