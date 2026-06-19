// Client chrome (Story 2.2 / Ranger-X handoff): ambient circuit backdrop +
// sticky header + bottom nav (mobile) around every client surface. Content sits
// on z-[1] above the fixed backdrop; .client-shell (globals.css) is the fluid
// responsive container — clamp gutters, capped width, and bottom padding that
// clears the mobile bottom nav + safe-area inset.
// `rx-calm` scopes the cockpit to control-room calm: it lowers --glow for the
// whole authenticated subtree (live ring/pill/button stay the only energy) and
// the backdrop mounts its dimmed `--calm` variant. The louder default backdrop
// stays on landing/login/register.
import { ClientNav } from "@/components/client-nav";
import { RxBackdrop } from "@/components/ui/rx-backdrop";

export default function ClientLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="rx-calm relative flex min-h-screen flex-col">
      <RxBackdrop className="rx-backdrop--calm" />
      <ClientNav />
      <main className="relative z-[1] flex-1 client-shell">{children}</main>
    </div>
  );
}
