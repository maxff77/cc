// Single source of truth for the role-gated nav links shared by ClientNav
// (cockpit chrome) and AdminShell (admin chrome) so the two never drift apart.
// Same role → identical ordered set on every page. Full union: each role adds
// its tier on top of the one below. Clients never see /admin/* links; middleware
// also gates those routes, so this is presentation, not authorization.
export type NavLink = { href: string; label: string };

const CLIENT: readonly NavLink[] = [
  { href: "/app", label: "Envío" },
  { href: "/app/historial", label: "Historial" },
];
const ADMIN: readonly NavLink[] = [
  { href: "/admin/users", label: "Usuarios" },
  { href: "/admin/keys", label: "Keys" },
];
const OWNER: readonly NavLink[] = [
  { href: "/admin/plans", label: "Planes" },
  { href: "/admin/gates", label: "Gateways" },
  { href: "/admin/destinos", label: "Destinos" },
  { href: "/admin/contactos", label: "Contactos" },
  { href: "/admin/monitor", label: "Monitoreo" },
];

export function navLinks(role: string | undefined): readonly NavLink[] {
  if (role === "owner") return [...CLIENT, ...ADMIN, ...OWNER];
  if (role === "admin") return [...CLIENT, ...ADMIN];

  return CLIENT; // client / unknown / loading → base only (no admin-link flash)
}
