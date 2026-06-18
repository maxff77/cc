"use client";

import type { PublicPlansResponse } from "@/types/api";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";

import { InfinityGlyph } from "./infinity-glyph";
import { LinkBtn } from "./link-btn";

import { api } from "@/lib/api";
import { siteConfig, telegramHref } from "@/config/site";

type Plan = PublicPlansResponse["items"][number];

function formatUsd(v: number | string): string {
  const n = Number(v);

  if (!Number.isFinite(n)) return "—";

  return `$${Number.isInteger(n) ? n : n.toFixed(2)}`;
}

function CheckRow({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2.5 text-[14px] text-foreground">
      <svg
        className="mt-0.5 shrink-0 text-success"
        fill="none"
        height="16"
        viewBox="0 0 24 24"
        width="16"
      >
        <path
          d="M5 12l5 5L20 6"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2.4"
        />
      </svg>
      <span>{children}</span>
    </li>
  );
}

function PlanCard({ plan, featured }: { plan: Plan; featured: boolean }) {
  return (
    <div
      className={clsx(
        "relative flex w-full max-w-[320px] flex-col rounded-[18px] border bg-surface p-6 sm:w-[300px]",
        featured
          ? "glow-accent border-[color-mix(in_oklch,var(--accent)_55%,var(--border))] lg:-translate-y-2 lg:scale-[1.03]"
          : "border-border",
      )}
      style={
        featured ? { backgroundImage: "var(--brand-gradient-soft)" } : undefined
      }
    >
      {featured && (
        <>
          <div className="brand-fill absolute inset-x-0 top-0 h-1 rounded-t-[18px]" />
          <span className="brand-fill absolute -top-3 left-1/2 -translate-x-1/2 rounded-full px-3 py-1 font-display text-[11px] font-bold uppercase tracking-[0.12em] text-white">
            Recomendado
          </span>
        </>
      )}

      <h3 className="font-display text-[19px] font-extrabold tracking-[-0.01em] text-foreground">
        {plan.name}
      </h3>

      <div className="mt-3 flex items-end gap-2">
        <span className="font-mono text-[40px] font-extrabold leading-none tracking-[-0.03em] text-foreground">
          {formatUsd(plan.price_usd)}
        </span>
        <span className="pb-1 text-[13px] text-muted">
          / {plan.duration_days} días
        </span>
      </div>

      <ul className="mt-6 flex flex-col gap-3 border-t border-border pt-5">
        <CheckRow>
          {plan.credits_unlimited ? (
            <span className="inline-flex items-center gap-2 font-semibold">
              <InfinityGlyph size={30} />
              Créditos ilimitados
            </span>
          ) : plan.credits > 0 ? (
            <>
              <span className="font-mono font-semibold text-foreground">
                {plan.credits.toLocaleString("es-MX")}
              </span>{" "}
              créditos incluidos
            </>
          ) : (
            "Sin créditos incluidos"
          )}
        </CheckRow>
        <CheckRow>
          Hasta{" "}
          <span className="font-mono font-semibold text-foreground">
            {plan.max_lines_per_batch.toLocaleString("es-MX")}
          </span>{" "}
          líneas por lote
        </CheckRow>
        <CheckRow>{plan.duration_days} días de vigencia</CheckRow>
        <CheckRow>Soporte por Telegram</CheckRow>
      </ul>

      <div className="mt-6 pt-1">
        <LinkBtn
          full
          href="/register"
          variant={featured ? "primary" : "secondary"}
        >
          Crear cuenta
        </LinkBtn>
      </div>
    </div>
  );
}

function PricingFallback() {
  return (
    <div className="mx-auto max-w-[480px] rounded-[18px] border border-border bg-surface p-7 text-center">
      <p className="text-[15px] text-muted">
        Escribinos por Telegram y armamos el plan que necesitás.
      </p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {siteConfig.contacts.map((c) => (
          <a
            key={c.handle}
            className="rounded-[var(--radius-sm)] border border-[var(--field-border)] px-3 py-1 font-mono text-[13px] text-accent no-underline transition-colors hover:border-accent"
            href={telegramHref(c.handle)}
            rel="noopener noreferrer"
            target="_blank"
          >
            @{c.handle}
          </a>
        ))}
      </div>
    </div>
  );
}

function PricingSkeleton() {
  return (
    <div className="flex w-full flex-wrap items-stretch justify-center gap-5">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-[360px] w-full max-w-[320px] animate-pulse rounded-[18px] border border-border bg-surface sm:w-[300px]"
        />
      ))}
    </div>
  );
}

export function Pricing() {
  const plans = useQuery({
    queryKey: ["public-plans"],
    queryFn: () => api.get<PublicPlansResponse>("/api/public/plans"),
  });

  const anyUnlimited =
    plans.data?.items.some((p) => p.credits_unlimited) ?? false;

  let body: React.ReactNode;

  if (plans.isPending) {
    body = <PricingSkeleton />;
  } else if (plans.isError || !plans.data || plans.data.items.length === 0) {
    body = <PricingFallback />;
  } else {
    const items = [...plans.data.items].sort(
      (a, b) => Number(a.price_usd) - Number(b.price_usd),
    );
    // Exactly ONE featured card: the unlimited (∞) tier, else the priciest. A
    // single object reference avoids ties flagging multiple "Recomendado" cards.
    const featuredPlan: Plan | undefined = items.some(
      (p) => p.credits_unlimited,
    )
      ? items.find((p) => p.credits_unlimited)
      : items.reduce((top, p) =>
          Number(p.price_usd) > Number(top.price_usd) ? p : top,
        );

    body = (
      <div className="flex w-full flex-wrap items-stretch justify-center gap-6">
        {items.map((p) => (
          <PlanCard key={p.name} featured={p === featuredPlan} plan={p} />
        ))}
      </div>
    );
  }

  return (
    <section
      className="mx-auto w-full max-w-[1180px] px-5 py-16 sm:px-8 lg:py-24"
      id="planes"
    >
      <div className="mb-10 text-center">
        <h2 className="font-display text-[clamp(1.7rem,4vw,2.5rem)] font-extrabold tracking-[-0.02em] text-foreground">
          Planes
        </h2>
        <p className="mx-auto mt-3 max-w-[48ch] text-[15px] text-muted">
          {anyUnlimited
            ? "Elegí por días, líneas y créditos. El tope llega con créditos ilimitados."
            : "Elegí por días, líneas y créditos según lo que necesités."}
        </p>
      </div>
      {body}
    </section>
  );
}
