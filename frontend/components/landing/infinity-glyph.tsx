"use client";

import { useId } from "react";

// Gradient ∞ — the "unlimited credits" mark for the premium plan. Rendered as a
// real SVG icon with a gradient STROKE (not background-clip:text on prose), so
// it's a deliberate brand moment and stays crisp at any size. Always paired with
// a text label ("Ilimitados") at the call site — never color/shape alone. The
// gradient id is per-instance (useId) so multiple ∞ cards never collide on a
// shared DOM id (which would drop the stroke on the 2nd+ instance).
export function InfinityGlyph({
  size = 34,
  className,
}: {
  size?: number;
  className?: string;
}) {
  const gradientId = useId();

  return (
    <svg
      aria-hidden
      className={className}
      fill="none"
      height={(size * 32) / 56}
      role="img"
      viewBox="0 0 56 32"
      width={size}
    >
      <defs>
        <linearGradient
          gradientUnits="userSpaceOnUse"
          id={gradientId}
          x1="0"
          x2="56"
          y1="0"
          y2="0"
        >
          <stop offset="0" stopColor="oklch(80% 0.135 216)" />
          <stop offset="0.5" stopColor="oklch(64% 0.21 295)" />
          <stop offset="1" stopColor="oklch(67% 0.255 332)" />
        </linearGradient>
      </defs>
      <path
        d="M28 16 C 22 6, 6 8, 6 16 C 6 24, 22 26, 28 16 C 34 6, 50 8, 50 16 C 50 24, 34 26, 28 16 Z"
        stroke={`url(#${gradientId})`}
        strokeLinecap="round"
        strokeWidth="3.4"
      />
    </svg>
  );
}
