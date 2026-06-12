// Client chrome (Story 2.2): header strip + bottom nav (mobile) around every
// client surface. Mobile order per DESIGN.md: header → page content (ring →
// controls slot → data panel) → bottom nav; pb-24 keeps content clear of the
// fixed bottom nav.
import { ClientNav } from "@/components/client-nav";

export default function ClientLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <ClientNav />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-24 pt-6 lg:px-6 lg:pb-10">
        {children}
      </main>
    </div>
  );
}
