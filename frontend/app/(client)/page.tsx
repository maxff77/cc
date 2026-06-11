// Client landing stub. Full Envío surface arrives in Story 2.2 — this exists so
// the post-login redirect (role "client" → "/") resolves to a real route.
export default function ClientHome() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <p className="text-lg text-muted">Envío — próximamente</p>
    </main>
  );
}
