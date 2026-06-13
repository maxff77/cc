"use client";

import { useId } from "react";

// RANGER-X brand marks — ported from the Claude Design handoff
// (ux-designs/ranger-x-handoff/lib.jsx). Gradient fill works on light + dark via
// the brand-spectrum tokens; useId() gives each instance a stable, SSR-safe
// gradient id (Math.random would hydrate-mismatch). The wordmark references the
// display font through var(--font-display) because next/font hashes the family
// name — a literal "Saira" would not resolve.

interface LogoProps {
  /** Pixel height of the wordmark glyphs. */
  height?: number;
  /** Render the "CHECK" sub-lockup with circuit ticks. */
  sub?: boolean;
}

export function Logo({ height = 40, sub = true }: LogoProps) {
  const raw = useId();
  const id = `rxg-${raw.replace(/[^a-zA-Z0-9]/g, "")}`;
  const w = sub ? height * 6.0 : height * 5.2;
  const h = sub ? height * 1.42 : height;

  return (
    <svg
      aria-label="Ranger-X Check"
      height={h}
      role="img"
      style={{ display: "block", overflow: "visible" }}
      viewBox="0 0 600 142"
      width={w}
    >
      <defs>
        <linearGradient id={id} x1="0" x2="1" y1="0" y2="0.25">
          <stop offset="0%" stopColor="var(--cyan)" />
          <stop offset="34%" stopColor="var(--blue)" />
          <stop offset="64%" stopColor="var(--accent)" />
          <stop offset="100%" stopColor="var(--magenta)" />
        </linearGradient>
        <filter height="220%" id={`${id}-glow`} width="160%" x="-30%" y="-60%">
          <feGaussianBlur result="b" stdDeviation="3.2" />
          <feComponentTransfer in="b" result="bb">
            <feFuncA slope="0.55" type="linear" />
          </feComponentTransfer>
          <feMerge>
            <feMergeNode in="bb" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <g filter={`url(#${id}-glow)`}>
        {/* leading lightning slash */}
        <path
          d="M58 18 L34 74 L52 74 L40 122 L92 56 L70 56 L86 18 Z"
          fill={`url(#${id})`}
          opacity="0.95"
        />
        {/* wordmark */}
        <text
          fill={`url(#${id})`}
          fontFamily="var(--font-display)"
          fontSize="86"
          fontStyle="italic"
          fontWeight="800"
          letterSpacing="1"
          style={{ fontStretch: "condensed" }}
          x="96"
          y="92"
        >
          RANGER-X
        </text>
        {/* trailing accent slash through the X */}
        <path
          d="M560 12 L600 12 L556 130 L516 130 Z"
          fill={`url(#${id})`}
          opacity="0.9"
        />
      </g>
      {sub && (
        <g>
          {/* circuit ticks left */}
          <g opacity="0.8" stroke="var(--accent)" strokeWidth="3">
            <line x1="150" x2="200" y1="126" y2="126" />
            <line x1="206" x2="218" y1="120" y2="120" />
            <line x1="206" x2="226" y1="126" y2="126" />
          </g>
          <text
            fill={`url(#${id})`}
            fontFamily="var(--font-display)"
            fontSize="30"
            fontWeight="700"
            letterSpacing="14"
            x="244"
            y="134"
          >
            CHECK
          </text>
          <g opacity="0.8" stroke="var(--magenta)" strokeWidth="3">
            <line x1="452" x2="502" y1="126" y2="126" />
            <line x1="430" x2="442" y1="120" y2="120" />
            <line x1="424" x2="444" y1="126" y2="126" />
          </g>
        </g>
      )}
    </svg>
  );
}

interface MarkProps {
  /** Pixel height of the shield. */
  size?: number;
}

// Compact shield-X mark for nav / favicon.
export function Mark({ size = 30 }: MarkProps) {
  const raw = useId();
  const id = `rxm-${raw.replace(/[^a-zA-Z0-9]/g, "")}`;

  return (
    <svg
      aria-hidden="true"
      height={size}
      style={{ display: "block", overflow: "visible" }}
      viewBox="0 0 48 56"
      width={size * 0.86}
    >
      <defs>
        <linearGradient id={id} x1="0" x2="1" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--cyan)" />
          <stop offset="55%" stopColor="var(--accent)" />
          <stop offset="100%" stopColor="var(--magenta)" />
        </linearGradient>
      </defs>
      <path
        d="M24 2 L45 10 V28 C45 42 36 50 24 54 C12 50 3 42 3 28 V10 Z"
        fill="none"
        stroke={`url(#${id})`}
        strokeWidth="2.6"
        style={{
          filter: "drop-shadow(0 0 calc(5px * var(--glow)) var(--accent))",
        }}
      />
      <path
        d="M15 18 L33 40 M33 18 L15 40"
        stroke={`url(#${id})`}
        strokeLinecap="round"
        strokeWidth="4.4"
      />
    </svg>
  );
}
