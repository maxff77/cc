import clsx from "clsx";

import { Icon, type IconName } from "@/components/ui/icon";

// "Qué ofrecemos" — a bento (varied tile sizes), not a uniform icon-card grid.
// The lead capability gets the wide tile; the rest fill a 3-up row.
interface Feature {
  icon: IconName;
  title: string;
  desc: string;
  span?: string;
  lead?: boolean;
}

const FEATURES: Feature[] = [
  {
    icon: "send",
    title: "Envío masivo en vivo",
    desc: "Pegá miles de líneas y mirá el progreso real: enviando, en pausa, flood o terminado. Sin ambigüedad, sin adivinar.",
    span: "lg:col-span-2",
    lead: true,
  },
  {
    icon: "check",
    title: "Captura ✅/❌ atribuida",
    desc: "Cada respuesta del checker vuelve a su línea exacta, al instante.",
  },
  {
    icon: "search",
    title: "Filtrada automática",
    desc: "Los CC se extraen y deduplican por sesión. Solo lo que sirve.",
  },
  {
    icon: "user",
    title: "Multi-tenant justo",
    desc: "Round-robin entre clientes: a nadie se le adelanta la cola.",
  },
  {
    icon: "refresh",
    title: "Ritmo anti-ban",
    desc: "Intervalo adaptativo y watchdog que protegen la cuenta compartida.",
  },
];

export function Features() {
  return (
    <section className="mx-auto w-full max-w-[1180px] px-5 py-16 sm:px-8 lg:py-24">
      <h2 className="font-display text-[clamp(1.7rem,4vw,2.5rem)] font-extrabold tracking-[-0.02em] text-foreground">
        Qué ofrecemos
      </h2>
      <p className="mt-3 max-w-[52ch] text-[15px] text-muted">
        Un instrumento de precisión para revisar a escala — no una caja negra.
      </p>

      <div className="mt-9 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <article
            key={f.title}
            className={clsx(
              "group rounded-[16px] border border-border bg-surface p-6 transition-colors hover:border-border-strong",
              f.span,
            )}
          >
            <span className="inline-flex h-11 w-11 items-center justify-center rounded-[12px] bg-surface-secondary text-accent transition-transform group-hover:-translate-y-px">
              <Icon name={f.icon} size={22} />
            </span>
            <h3
              className={clsx(
                "mt-4 font-display font-bold tracking-[-0.01em] text-foreground",
                f.lead ? "text-[20px]" : "text-[17px]",
              )}
            >
              {f.title}
            </h3>
            <p
              className={clsx(
                "mt-2 text-muted",
                f.lead
                  ? "max-w-[44ch] text-[15px] leading-relaxed"
                  : "text-[14px] leading-relaxed",
              )}
            >
              {f.desc}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}
