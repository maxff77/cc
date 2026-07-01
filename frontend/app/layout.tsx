import "@/styles/globals.css";
import { Metadata, Viewport } from "next";
import clsx from "clsx";

import { Providers } from "./providers";
import { RegisterSW } from "./register-sw";

import { siteConfig } from "@/config/site";
import { fontDisplay, fontMono, fontSans } from "@/config/fonts";

// Cache-bust the icons by URL. Browsers keep a SEPARATE favicon cache that
// ignores the max-age=0 header Next serves for /public, so a byte swap at a
// stable path can show the old tab icon for weeks. Pinning ?v=<app version>
// (inlined from package.json via next.config.mjs) makes it a genuinely new URL
// on every release — bump package.json's version whenever the icon changes.
const V = process.env.NEXT_PUBLIC_APP_VERSION ?? "1";

export const metadata: Metadata = {
  title: {
    default: siteConfig.name,
    template: `%s - ${siteConfig.name}`,
  },
  description: siteConfig.description,
  icons: {
    icon: [
      { url: `/favicon.ico?v=${V}`, sizes: "any" },
      { url: `/brand/favicon-32.png?v=${V}`, type: "image/png", sizes: "32x32" },
      {
        url: `/brand/favicon-192.png?v=${V}`,
        type: "image/png",
        sizes: "192x192",
      },
    ],
    apple: { url: `/brand/favicon-180.png?v=${V}`, sizes: "180x180" },
  },
  // PWA install on iOS Safari (Add to Home Screen → standalone, no Safari
  // chrome). The apple-touch-icon comes from icons.apple above. The web app
  // manifest is auto-linked by app/manifest.ts — do not set metadata.manifest.
  // statusBarStyle "default" keeps content BELOW the status bar (no notch
  // overlap) and lets iOS pick readable glyphs in light OR dark theme.
  // "black-translucent" would float content under the bar and require
  // viewport-fit=cover + env(safe-area-inset-top) CSS we don't ship.
  appleWebApp: {
    capable: true,
    title: siteConfig.shortName,
    statusBarStyle: "default",
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
          <RegisterSW />
          {children}
        </Providers>
      </body>
    </html>
  );
}
