export type SiteConfig = typeof siteConfig;

// Telegram link from a bare handle — single derivation so the handle and the
// link can never drift. UI shows `@${handle}`, hrefs go through this.
export const telegramHref = (handle: string) => `https://t.me/${handle}`;

export const siteConfig = {
  name: "Ranger-X Check",
  // Short brand for tight spots (PWA manifest short_name, iOS home-screen
  // title). Deliberately shorter than `name`; single-sourced so they can't drift.
  shortName: "Ranger-X",
  description: "Plataforma de envíos por Telegram.",
  // Seller/support Telegram contacts, shown to clients on login, the /expired
  // lockout, and the in-app "Soporte" link. One source of truth — change the
  // handles here (redeploy) and every surface follows. Telegram-only by
  // decision (WhatsApp dropped). Order = priority: index 0 is the primary
  // contact (the mobile bottom-nav links to it).
  contacts: [
    { handle: "AionRanger" },
    { handle: "AionRangerOwner" },
  ],
};
