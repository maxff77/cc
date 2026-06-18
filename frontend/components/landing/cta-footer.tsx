import { LinkBtn } from "./link-btn";

import { Mark } from "@/components/ui/logo";
import { siteConfig, telegramHref } from "@/config/site";

// Closing conversion band + footer. The band is the one drenched moment on the
// page (brand-gradient-soft wash + glow); the footer stays calm.
export function CtaFooter() {
  return (
    <>
      <section className="mx-auto w-full max-w-[1180px] px-5 pb-16 sm:px-8 lg:pb-24">
        <div
          className="glow-soft relative overflow-hidden rounded-[24px] border border-border bg-surface px-6 py-12 text-center sm:px-12"
          style={{ backgroundImage: "var(--brand-gradient-soft)" }}
        >
          <h2
            className="mx-auto max-w-[20ch] font-display text-[clamp(1.8rem,4.5vw,2.8rem)] font-extrabold leading-[1.08] tracking-[-0.02em] text-foreground"
            style={{ textWrap: "balance" }}
          >
            Empezá a revisar hoy.
          </h2>
          <p className="mx-auto mt-4 max-w-[44ch] text-[15px] text-muted">
            Creá tu cuenta en minutos. Si no tenés plan todavía, te lo armamos
            por Telegram.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <LinkBtn
              href="/register"
              iconRight="arrow"
              size="lg"
              variant="primary"
            >
              Crear cuenta
            </LinkBtn>
            <LinkBtn href="/login" size="lg" variant="secondary">
              Iniciar sesión
            </LinkBtn>
          </div>
        </div>
      </section>

      <footer className="border-t border-border">
        <div className="mx-auto flex w-full max-w-[1180px] flex-col items-center justify-between gap-5 px-5 py-8 sm:flex-row sm:px-8">
          <div className="flex items-center gap-2.5">
            <Mark size={28} />
            <span className="font-mono text-[12px] tracking-[0.1em] text-muted">
              RANGER-X CHECK © 2026
            </span>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-faint">
              Soporte
            </span>
            {siteConfig.contacts.map((c) => (
              <a
                key={c.handle}
                className="rounded-[var(--radius-sm)] border border-[var(--field-border)] px-2.5 py-0.5 font-mono text-[12px] text-accent no-underline transition-colors hover:border-accent"
                href={telegramHref(c.handle)}
                rel="noopener noreferrer"
                target="_blank"
              >
                @{c.handle}
              </a>
            ))}
          </div>
        </div>
      </footer>
    </>
  );
}
