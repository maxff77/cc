import "@/styles/globals.css";
import { Metadata, Viewport } from "next";
import clsx from "clsx";

import { Providers } from "./providers";

import { siteConfig } from "@/config/site";
import { fontMono, fontSans } from "@/config/fonts";

export const metadata: Metadata = {
  title: {
    default: siteConfig.name,
    template: `%s - ${siteConfig.name}`,
  },
  description: siteConfig.description,
  icons: {
    icon: "/favicon.ico",
  },
};

export const viewport: Viewport = {
  // Hex approximations of the real --background tokens (globals.css).
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f6f8" }, // ≈ oklch(97.02% 0.0026 243)
    { media: "(prefers-color-scheme: dark)", color: "#15181b" }, // ≈ oklch(12% 0.0026 243)
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
        )}
      >
        <Providers themeProps={{ attribute: "class", defaultTheme: "dark" }}>
          {children}
        </Providers>
      </body>
    </html>
  );
}
