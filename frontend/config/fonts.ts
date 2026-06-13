import {
  JetBrains_Mono as FontMono,
  Public_Sans as FontSans,
  Saira as FontDisplay,
} from "next/font/google";

// Public Sans is the brand body font. The theme layer maps
// --font-sans: var(--font-public-sans), so expose that CSS variable here.
export const fontSans = FontSans({
  subsets: ["latin"],
  variable: "--font-public-sans",
});

// JetBrains Mono is the data font of the Ranger-X neon identity (tabular
// figures, distinguishable 0/O). The theme layer maps
// --font-mono: var(--font-jetbrains-mono) (a self-reference here broke the chain).
export const fontMono = FontMono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
});

// Saira is the display font (italic wordmark + screen headings) of the Ranger-X
// neon identity. Weights 700/800, italic + normal cover the logo and headings.
// Like --font-public-sans / --font-jetbrains-mono above, expose a DISTINCT
// variable name: the theme layer maps --font-display: var(--font-saira). A
// self-reference (--font-display: var(--font-display)) breaks the chain.
export const fontDisplay = FontDisplay({
  subsets: ["latin"],
  weight: ["700", "800"],
  style: ["normal", "italic"],
  variable: "--font-saira",
});
