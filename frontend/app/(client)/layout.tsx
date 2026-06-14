// Client chrome (Story 2.2 / Ranger-X handoff): ambient circuit backdrop +
// sticky header + bottom nav (mobile) around every client surface. Content sits
// on z-[1] above the fixed backdrop; pb-24 keeps it clear of the mobile bottom
// nav.
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
      <main className="relative z-[1] mx-auto w-full max-w-[1640px] flex-1 px-4 pb-24 pt-6 lg:px-6 lg:pb-10">
        {children}
      </main>
    </div>
  );
}
