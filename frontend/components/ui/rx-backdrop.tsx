import clsx from "clsx";

// Ambient page backdrop: fixed circuit grid + corner neon bloom, behind all
// content (z-index:0, pointer-events:none — never intercepts clicks). All the
// work lives in the `.rx-backdrop` class (+ ::before) in styles/globals.css;
// the bloom intensity scales with the --glow tunable. Authenticated/auth
// surfaces mount one instance near the root of their layout (deferred screens).
export function RxBackdrop({ className }: { className?: string }) {
  return <div aria-hidden className={clsx("rx-backdrop", className)} />;
}
