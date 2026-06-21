import type { Metadata } from "next";

import { RxBackdrop } from "@/components/ui/rx-backdrop";
import { LandingNav } from "@/components/landing/landing-nav";
import { Hero } from "@/components/landing/hero";
import { Features } from "@/components/landing/features";
import { Pricing } from "@/components/landing/pricing";
import { Gates } from "@/components/landing/gates";
import { CtaFooter } from "@/components/landing/cta-footer";

// Public sales landing — the default entry at `/`. Logged-in visitors are
// redirected to `/app` by middleware; this renders for logged-out visitors. The
// pricing + gates sections fetch the public (no-auth) catalog endpoints on the
// client and degrade to a quiet fallback if a fetch fails.
export const metadata: Metadata = {
  title: "Ranger-X Check — Envíos por Telegram con resultados en vivo",
  description:
    "Pegá tus líneas, elegí un gateway y mirá las respuestas ✅/❌ atribuidas al instante. Multi-tenant, pausable y con ritmo anti-ban.",
};

export default function LandingPage() {
  return (
    <main className="relative min-h-screen overflow-x-hidden">
      <RxBackdrop />
      <div className="relative z-[1]">
        <LandingNav />
        <Hero />
        <Features />
        <Pricing />
        <Gates />
        <CtaFooter />
      </div>
    </main>
  );
}
