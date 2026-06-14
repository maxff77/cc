// Inline icon set (Ranger-X handoff lib.jsx `I`/`Icon`) — currentColor SVGs so
// every icon inherits the surrounding text color. One 24×24 viewBox; callers
// pass `name` + optional `size`. Native (no icon library): keeps the neon
// identity self-contained and tree-shakeable.
import type { CSSProperties } from "react";

export type IconName =
  | "user"
  | "lock"
  | "eye"
  | "eyeOff"
  | "arrow"
  | "chevron"
  | "pause"
  | "play"
  | "stop"
  | "plus"
  | "send"
  | "download"
  | "sun"
  | "moon"
  | "trash"
  | "refresh"
  | "search"
  | "check";

const PATHS: Record<IconName, React.ReactNode> = {
  user: (
    <path d="M12 12a4 4 0 100-8 4 4 0 000 8zm0 2c-4 0-7 2-7 5v1h14v-1c0-3-3-5-7-5z" />
  ),
  lock: (
    <path d="M6 10V8a6 6 0 1112 0v2h1a1 1 0 011 1v9a1 1 0 01-1 1H5a1 1 0 01-1-1v-9a1 1 0 011-1h1zm2 0h8V8a4 4 0 10-8 0v2z" />
  ),
  eye: (
    <path d="M12 5c-5 0-9 4.5-10 7 1 2.5 5 7 10 7s9-4.5 10-7c-1-2.5-5-7-10-7zm0 11a4 4 0 110-8 4 4 0 010 8z" />
  ),
  eyeOff: (
    <path d="M3 4l17 17-1.4 1.4-3-3A11 11 0 0112 19C7 19 3 14.5 2 12a13 13 0 014-5L1.6 5.4 3 4zm9 5a3 3 0 013 3l-3-3zm0-4c5 0 9 4.5 10 7a13 13 0 01-3 4l-3-3a4 4 0 00-5-5L9.6 6.4A10 10 0 0112 5z" />
  ),
  arrow: (
    <path
      d="M5 12h12m0 0l-5-5m5 5l-5 5"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
    />
  ),
  chevron: (
    <path
      d="M6 9l6 6 6-6"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
    />
  ),
  pause: <path d="M7 5h3v14H7zM14 5h3v14h-3z" />,
  play: <path d="M7 4l13 8-13 8z" />,
  stop: <rect height="12" rx="1.5" width="12" x="6" y="6" />,
  plus: <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" />,
  send: (
    <path
      d="M3 11l18-8-8 18-2-7z"
      fill="none"
      stroke="currentColor"
      strokeLinejoin="round"
      strokeWidth="2"
    />
  ),
  download: (
    <path
      d="M12 3v10m0 0l-4-4m4 4l4-4M5 19h14"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
    />
  ),
  sun: (
    <g fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" />
    </g>
  ),
  moon: <path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z" />,
  trash: (
    <path
      d="M6 7h12l-1 13a1 1 0 01-1 1H8a1 1 0 01-1-1L6 7zm3-3h6l1 2H8l1-2zM4 6h16"
      fill="none"
      stroke="currentColor"
      strokeLinejoin="round"
      strokeWidth="1.8"
    />
  ),
  refresh: (
    <path
      d="M4 12a8 8 0 0113.7-5.7L20 8m0 0V3m0 5h-5M20 12a8 8 0 01-13.7 5.7L4 16m0 0v5m0-5h5"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
    />
  ),
  search: (
    <g fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2">
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-3.5-3.5" />
    </g>
  ),
  check: (
    <path
      d="M5 12l5 5L20 6"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2.4"
    />
  ),
};

export function Icon({
  name,
  size = 18,
  className,
  style,
}: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      aria-hidden
      className={className}
      fill="currentColor"
      height={size}
      style={{ flexShrink: 0, ...style }}
      viewBox="0 0 24 24"
      width={size}
    >
      {PATHS[name]}
    </svg>
  );
}
