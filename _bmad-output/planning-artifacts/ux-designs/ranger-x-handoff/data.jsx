/* global window */
// ============================================================================
// RANGER-X CHECK — sample data for the prototype
// ============================================================================

// placeholder sample lines — neutral lorem entries so the console reads true
const SAMPLE_TEXT = [
  "Lorem ipsum dolor sit amet  OK — Completado",
  "Consectetur adipiscing elit  OK — Completado",
  "Sed do eiusmod tempor labore  Error — Reintentar",
  "Ut enim ad minim veniam  OK — Completado",
  "Quis nostrud exercitation  Omitido — Sin datos",
  "Ullamco laboris nisi aliquip  Pendiente — En cola",
  "Ex ea commodo consequat  OK — Completado",
  "Duis aute irure dolor  Error — Reintentar",
  "In reprehenderit voluptate  OK — Completado",
  "Velit esse cillum fugiat  Omitido — Sin datos",
  "Nulla pariatur excepteur  OK — Completado",
  "Sint occaecat cupidatat  Error — Reintentar",
];

function tStamp(i) {
  const base = 14 * 3600 + 47 * 60 + 6; // 14:47:06
  const s = base + i * 11;
  const hh = String(Math.floor(s / 3600) % 24).padStart(2, "0");
  const mm = String(Math.floor(s / 60) % 60).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

// COMPLETA — every response, with status glyph
const COMPLETA = SAMPLE_TEXT.map((text, i) => {
  const ok = /OK —/.test(text);
  return { key: "c" + i, left: tStamp(i), text, status: ok ? "ok" : "rejected", nueva: i >= 10 };
});

// FILTRADA CON RESPONSE — only the ✅ ones, full text
const FILTRADA_CON = COMPLETA.filter((r) => r.status === "ok").map((r, i) => ({ ...r, key: "fc" + i, nueva: i >= 4 }));

// FILTRADA SIN RESPONSE — captured entries, numbered index, no glyph
const FILTRADA_SIN = COMPLETA.filter((r) => r.status === "ok").map((r, i, arr) => ({
  key: "fs" + i,
  left: String(i + 1).padStart(3, "0"),
  text: r.text.split("  ")[0],
  nueva: i >= arr.length - 2,
}));

const GATES = [
  { id: "1", label: "Flujo Alpha", mono: "/fa" },
  { id: "2", label: "Flujo Beta", mono: "/fb" },
  { id: "3", label: "Flujo Gamma", mono: "/fg" },
  { id: "4", label: "Flujo Delta", mono: "/fd" },
];
const CATEGORIES = [
  { id: "auth", label: "Categoría A" },
  { id: "cvv", label: "Categoría B" },
  { id: "charge", label: "Categoría C" },
];

const SESSIONS = [
  { group: "Flujo Gamma", gateValue: "/fg", items: [
    { id: 5, name: "Lote nocturno · MX", date: "2026-06-12 14:30", active: true },
  ]},
  { group: "Flujo Beta", gateValue: "/fb", items: [
    { id: 2, name: "BridgeMind", date: "2026-06-12 13:11", active: false },
    { id: 1, name: null, date: "2026-06-12 11:48", active: false },
  ]},
  { group: "Flujo Alpha", gateValue: "/fa", items: [
    { id: 4, name: "Jarvis AI", date: "2026-06-12 07:51", active: false },
    { id: 3, name: "Pruebas rápidas", date: "2026-06-11 23:02", active: false },
  ]},
];

const USERS = [
  { email: "owner@rangerx.mx", role: "owner", contact: "@maxff", vence: "—", estado: "—" },
  { email: "admin@rangerx.mx", role: "admin", contact: "—", vence: "—", estado: "—" },
  { email: "cliente@rangerx.mx", role: "client", contact: "@cli_mx", vence: "13 jun 2026", estado: "Activo" },
  { email: "nadia.r@gmail.com", role: "client", contact: "@nadia", vence: "28 jun 2026", estado: "Activo" },
  { email: "vega.ops@proton.me", role: "client", contact: "—", vence: "02 jun 2026", estado: "Vencido" },
];

Object.assign(window, { COMPLETA, FILTRADA_CON, FILTRADA_SIN, GATES, CATEGORIES, SESSIONS, USERS });
