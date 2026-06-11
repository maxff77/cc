// Admin landing stub. Full user-management surface arrives in Story 1.3 — this
// exists so the post-login redirect (role "admin"/"owner" → "/admin/users")
// resolves to a real route.
export default function AdminUsers() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <p className="text-lg text-muted">Gestión de usuarios — próximamente</p>
    </main>
  );
}
