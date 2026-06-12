import {
  Fira_Code as FontMono,
  Public_Sans as FontSans,
} from "next/font/google";

// Public Sans is the brand body font (UX-DR2). The theme layer maps
// --font-sans: var(--font-public-sans), so expose that CSS variable here.
export const fontSans = FontSans({
  subsets: ["latin"],
  variable: "--font-public-sans",
});

// Fira Code is the data font (tabular figures, distinguishable 0/O). Like
// --font-public-sans above, expose a dedicated variable; the theme layer maps
// --font-mono: var(--font-fira-code) (a self-reference here broke the chain).
export const fontMono = FontMono({
  subsets: ["latin"],
  variable: "--font-fira-code",
});
