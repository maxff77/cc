import "@/styles/globals.css";
import { Metadata, Viewport } from "next";
import clsx from "clsx";

import { Providers } from "./providers";

import { VersionBadge } from "@/components/ui/version-badge";

import { siteConfig } from "@/config/site";
import { fontDisplay, fontMono, fontSans } from "@/config/fonts";

export const metadata: Metadata = {
  title: {
    default: siteConfig.name,
    template: `%s - ${siteConfig.name}`,
  },
  description: siteConfig.description,
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/brand/favicon-32.png", type: "image/png", sizes: "32x32" },
      { url: "/brand/favicon-192.png", type: "image/png", sizes: "192x192" },
    ],
    apple: { url: "/brand/favicon-180.png", sizes: "180x180" },
  },
};

export const viewport: Viewport = {
  // Hex approximations of the real --background tokens (globals.css). A meta tag
  // cannot read a CSS var — keep these in sync if --background is retuned.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f5fa" }, // ≈ oklch(97.6% 0.006 280)
    { media: "(prefers-color-scheme: dark)", color: "#16141d" }, // ≈ oklch(14% 0.022 280)
  ],
};

// Root layout intentionally carries NO product chrome (navbar/footer). The
// authenticated surfaces ship their own chrome in later stories (1.3 / 2.2);
// the login page renders on the bare unauthenticated surface.
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html suppressHydrationWarning className="dark" lang="es">
      <head />
      <body
        className={clsx(
          "min-h-screen text-foreground bg-background font-sans antialiased",
          fontSans.variable,
          fontMono.variable,
          fontDisplay.variable,
        )}
      >
        <Providers themeProps={{ attribute: "class", defaultTheme: "dark" }}>
          {children}
          <VersionBadge />
        </Providers>
      </body>
    </html>
  );
}
