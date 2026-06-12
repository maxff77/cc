// STUB (Story 2.2): exists so Historial doesn't 404. Story 3.3 builds the
// real session list — keep this minimal.
import Link from "next/link";

export default function SessionsPage() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
      <p className="text-muted">
        Todavía no tienes sesiones. Tu primer lote crea una.
      </p>
      <Link className="text-accent underline" href="/">
        Ir a Envío
      </Link>
    </div>
  );
}
