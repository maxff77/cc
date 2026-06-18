"use client";

import type { PublicGatesResponse } from "@/types/api";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

function GatesSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      {[0, 1].map((i) => (
        <div
          key={i}
          className="rounded-[16px] border border-border bg-surface p-6"
        >
          <div className="h-4 w-32 animate-pulse rounded bg-surface-secondary" />
          <div className="mt-4 flex flex-wrap gap-2">
            {[0, 1, 2, 3, 4].map((j) => (
              <div
                key={j}
                className="h-7 w-24 animate-pulse rounded-full bg-surface-secondary"
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Gates() {
  const gates = useQuery({
    queryKey: ["public-gates"],
    queryFn: () => api.get<PublicGatesResponse>("/api/public/gates"),
  });

  let body: React.ReactNode;

  if (gates.isPending) {
    body = <GatesSkeleton />;
  } else if (
    gates.isError ||
    !gates.data ||
    gates.data.categories.length === 0
  ) {
    body = (
      <div className="rounded-[16px] border border-border bg-surface p-7 text-center text-[15px] text-muted">
        Catálogo en preparación. Consultá los gates disponibles al crear tu
        cuenta.
      </div>
    );
  } else {
    const { categories, total } = gates.data;

    body = (
      <>
        <p className="mb-7 font-mono text-[13px] text-faint">
          {total} {total === 1 ? "gate" : "gates"} · {categories.length}{" "}
          {categories.length === 1 ? "categoría" : "categorías"}
        </p>
        <div className="flex flex-col gap-5">
          {categories.map((cat) => (
            <div
              key={cat.name}
              className="rounded-[16px] border border-border bg-surface p-6"
            >
              <h3 className="font-display text-[15px] font-bold uppercase tracking-[0.1em] text-accent">
                {cat.name}
              </h3>
              <div className="mt-4 flex flex-wrap gap-2">
                {cat.gates.map((name) => (
                  <span
                    key={name}
                    className="rounded-full border border-border bg-surface-secondary px-3 py-1 font-mono text-[13px] text-foreground"
                  >
                    {name}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </>
    );
  }

  return (
    <section
      className="mx-auto w-full max-w-[1180px] px-5 py-16 sm:px-8 lg:py-24"
      id="gates"
    >
      <h2 className="font-display text-[clamp(1.7rem,4vw,2.5rem)] font-extrabold tracking-[-0.02em] text-foreground">
        Nuestros gates
      </h2>
      <p className="mb-9 mt-3 max-w-[52ch] text-[15px] text-muted">
        Un catálogo amplio, organizado por categoría. Elegís uno por lote desde
        el panel.
      </p>
      {body}
    </section>
  );
}
