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

export const fontMono = FontMono({
  subsets: ["latin"],
  variable: "--font-mono",
});
