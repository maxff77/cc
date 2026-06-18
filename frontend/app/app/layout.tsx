// Client chrome (Story 2.2 / Ranger-X handoff): ambient circuit backdrop +
// sticky header + bottom nav (mobile) around every client surface. Content sits
// on z-[1] above the fixed backdrop; .client-shell (globals.css) is the fluid
// responsive container — clamp gutters, capped width, and bottom padding that
// clears the mobile bottom nav + safe-area inset.
import { ClientNav } from "@/components/client-nav";
import { RxBackdrop } from "@/components/ui/rx-backdrop";

export default function ClientLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen flex-col">
      <RxBackdrop />
      <ClientNav />
      <main className="relative z-[1] flex-1 client-shell">{children}</main>
    </div>
  );
}
