import type { MetadataRoute } from "next";

import { siteConfig } from "@/config/site";

// Web app manifest (PWA). Next 16 serves this at /manifest.webmanifest and
// auto-injects <link rel="manifest"> into <head> — do NOT also set
// metadata.manifest in layout.tsx. No offline/caching by design: the cockpit is
// a live WebSocket relay, so this only makes the app INSTALLABLE (icon +
// standalone window), nothing more.
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/app",
    name: siteConfig.name,
    short_name: siteConfig.shortName,
    description: siteConfig.description,
    // The cockpit IS "the app". Logged-out launches hit auth middleware and
    // land on /login inside the standalone window.
    start_url: "/app",
    scope: "/",
    display: "standalone",
    // Match the dark default shell (layout renders className="dark") so the
    // standalone window/splash doesn't flash a light frame. Keep in sync with
    // the dark --background approximation in layout.tsx's viewport.themeColor.
    background_color: "#16141d",
    theme_color: "#16141d",
    lang: "es",
    dir: "ltr",
    icons: [
      {
        src: "/brand/favicon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        // Reuses the existing 512×512 brand mark — no generated assets.
        src: "/brand/ranger-x-mark.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
    ],
  };
}
