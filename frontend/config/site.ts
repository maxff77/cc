export type SiteConfig = typeof siteConfig;

export const siteConfig = {
  name: "Ranger-X Check",
  description: "Plataforma de envíos por Telegram.",
  // Single seller/support Telegram contact, shown to clients on login, the
  // /expired lockout, and the persistent "Soporte" link in the client header.
  // One source of truth — change the handle here (redeploy) and every surface
  // follows. Telegram-only by decision (WhatsApp dropped).
  contact: {
    telegram: "https://t.me/yesterWhite", // full link for href
    handle: "yesterWhite", // bare handle; UI shows `@${handle}`
  },
};
