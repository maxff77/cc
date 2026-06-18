import { ReadoutMock } from "./readout-mock";
import { LinkBtn } from "./link-btn";

const TRUST = [
  "Resultados en vivo",
  "Completa + Filtrada",
  "Listo cuando volvés",
];

// Hero: solid Saira headline (gradient energy carried by the rule + the CTA +
// the product readout, never clipped onto the prose), operator subcopy, the two
// CTAs, and the product mock as imagery. Two columns on desktop, stacked on
// mobile.
export function Hero() {
  return (
    <section className="relative mx-auto grid w-full max-w-[1180px] grid-cols-1 items-center gap-12 px-5 pb-10 pt-10 sm:px-8 lg:grid-cols-[1.05fr_0.95fr] lg:gap-8 lg:pb-20 lg:pt-16">
      <div className="rx-enter flex flex-col items-start">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-3 py-1 font-mono text-[12px] text-muted">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: "var(--success)" }}
          />
          Checker masivo
        </span>

        <h1
          className="mt-5 font-display text-[clamp(2.3rem,6vw,4rem)] font-extrabold leading-[1.04] tracking-[-0.02em] text-foreground"
          style={{ textWrap: "balance" }}
        >
          Elevate al siguiente nivel.
        </h1>

        {/* gradient rule — the energy moment, not the text */}
        <div className="brand-fill mt-5 h-1 w-28 rounded-full" />

        <p className="mt-5 max-w-[46ch] text-[15px] leading-relaxed text-muted">
          Enviá tus líneas y dejá el trabajo. Volvés por tu café y ya tenés los
          resultados <span className="font-semibold text-success">✅</span> /{" "}
          <span className="font-semibold text-danger">❌</span> listos.
        </p>

        <div className="mt-7 flex flex-wrap items-center gap-3">
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

        <ul className="mt-7 flex flex-wrap gap-x-5 gap-y-2">
          {TRUST.map((t) => (
            <li
              key={t}
              className="inline-flex items-center gap-2 font-mono text-[12px] text-faint"
            >
              <span className="text-accent">▸</span>
              {t}
            </li>
          ))}
        </ul>
      </div>

      <div
        className="rx-enter flex justify-center lg:justify-end"
        style={{ animationDelay: "90ms" }}
      >
        <ReadoutMock />
      </div>
    </section>
  );
}
