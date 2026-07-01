import type { MetadataRoute } from "next";

import { siteConfig } from "@/config/site";

// Web app manifest (PWA). Next 16 serves this at /manifest.webmanifest and
// auto-injects <link rel="manifest"> into <head> — do NOT also set
// metadata.manifest in layout.tsx. No offline/caching by design: the cockpit is
// a live WebSocket relay, so this only makes the app INSTALLABLE (icon +
// standalone window), nothing more.
export default function manifest(): MetadataRoute.Manifest {
  // ?v=<app version> (inlined from package.json) busts the browser favicon
  // cache AND changes the manifest icon URLs, so an installed PWA re-mints its
  // home-screen icon on the next update check. Bump package.json when the icon
  // bytes change. Keep in sync with the same-purpose ?v= in app/layout.tsx.
  const v = process.env.NEXT_PUBLIC_APP_VERSION ?? "1";

  return {
    // Matches start_url. Android keys its WebAPK registry on `id`; the old
    // `/app` value got a poisoned mint entry from the earlier redirecting
    // start_url, and clearing site data does NOT clear that registry — so the
    // phone kept reusing the stuck "installing…" mint. A fresh id (== start_url,
    // the conventional single-entry config) forces a clean mint. Trade-off: the
    // laptop's existing `/app`-id install is orphaned (keeps working, won't
    // auto-update); reinstall to adopt the new identity.
    id: "/",
    name: siteConfig.name,
    short_name: siteConfig.shortName,
    description: siteConfig.description,
    // Must return 200 to an UNAUTHENTICATED fetch: Android's WebAPK minting
    // server fetches start_url without the session cookie, and a redirect there
    // hangs the install ("installing…" forever). "/" is the public landing
    // (200); middleware bounces a logged-in launch on to /app, a logged-out one
    // shows the landing → login.
    start_url: "/",
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
        src: `/brand/favicon-192.png?v=${v}`,
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        // Reuses the existing 512×512 brand mark — no generated assets.
        src: `/brand/ranger-x-mark.png?v=${v}`,
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
    ],
  };
}
