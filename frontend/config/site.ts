export type SiteConfig = typeof siteConfig;

export const siteConfig = {
  name: "cc",
  description: "Plataforma de envíos por Telegram.",
  // External reactivation channels shown on the blocked-account notice (AC4)
  // and, later, the /expired page (Story 1.4). PLACEHOLDERS — Richard swaps
  // these for the real links at deploy time.
  contact: {
    whatsapp: "https://wa.me/0000000000", // TODO(Richard): real WhatsApp link
    telegram: "https://t.me/your_handle", // TODO(Richard): real Telegram link
  },
};
