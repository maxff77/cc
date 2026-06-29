"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { Btn } from "@/components/ui/btn";
import { Field } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";

// Local shape mirrors the backend SupportContactsResponse (snake_case n/a here —
// just a handle string per contact).
interface SupportContactsResponse {
  contacts: { handle: string }[];
}

const CONTACTS_KEY = ["admin-support-contacts"] as const;
// Mirror of the backend MAX_SUPPORT_CONTACTS bound — soft-block in the UI so the
// owner gets a hint instead of a 400.
const MAX_CONTACTS = 8;

export default function AdminContactosPage() {
  const queryClient = useQueryClient();

  const contacts = useQuery({
    queryKey: CONTACTS_KEY,
    queryFn: () =>
      api.get<SupportContactsResponse>("/api/admin/support-contacts"),
  });

  // Editable working copy. Seeded once from the query, then re-synced only on an
  // explicit save (so a background refetch never clobbers an in-progress edit).
  const [rows, setRows] = useState<string[]>([]);
  const [banner, setBanner] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const seeded = useRef(false);

  useEffect(() => {
    if (!seeded.current && contacts.data) {
      setRows(contacts.data.contacts.map((c) => c.handle));
      seeded.current = true;
    }
  }, [contacts.data]);

  const save = useMutation({
    mutationFn: () =>
      api.put<SupportContactsResponse>("/api/admin/support-contacts", {
        handles: rows,
      }),
    onSuccess: (res) => {
      // Adopt the canonical (normalized + deduped) list the backend returned.
      setRows(res.contacts.map((c) => c.handle));
      setBanner(null);
      setSaved(true);
      queryClient.setQueryData(CONTACTS_KEY, res);
    },
    onError: (err) => {
      setSaved(false);
      // Backend sends user-facing Spanish (invalid_contact / empty / too many)
      // in `message` — surface it verbatim.
      setBanner(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  const setRow = (index: number, value: string) => {
    setSaved(false);
    setRows((prev) => prev.map((r, i) => (i === index ? value : r)));
  };

  const removeRow = (index: number) => {
    setSaved(false);
    setRows((prev) => prev.filter((_, i) => i !== index));
  };

  const addRow = () => {
    setSaved(false);
    setRows((prev) => [...prev, ""]);
  };

  const hasContent = rows.some((r) => r.trim() !== "");

  return (
    // Owner-only (backend guard + middleware) → owner nav.
    <AdminShell gatesVisible title="Contactos de soporte">
      <div className="mx-auto w-full max-w-[560px]">
        <SectionCard legend="CONTACTOS DE SOPORTE">
          {contacts.isLoading && <PanelSkeleton rows={3} />}

          {contacts.isError && (
            <Notice status="danger">
              No pudimos cargar los contactos. Recarga la página.
            </Notice>
          )}

          {contacts.data && (
            <div className="flex flex-col gap-4">
              <p className="m-0 text-[13px] text-muted">
                Los handles de Telegram que ven los clientes en el inicio de
                sesión, la pantalla de plan vencido y el menú “Soporte”. El
                primero es el principal (el que abre el menú móvil).
              </p>

              {banner && <Notice status="danger">{banner}</Notice>}
              {saved && <Notice status="success">Contactos guardados.</Notice>}

              <div className="flex flex-col gap-3">
                {rows.map((handle, index) => (
                  <div key={index} className="flex items-end gap-2">
                    <div className="flex-1">
                      <Field
                        label={
                          index === 0 ? "Principal" : `Contacto ${index + 1}`
                        }
                        name={`contact-${index}`}
                        placeholder="@usuario"
                        value={handle}
                        onChange={(v) => setRow(index, v)}
                      />
                    </div>
                    <Btn
                      aria-label="Quitar contacto"
                      disabled={rows.length <= 1 || save.isPending}
                      icon="trash"
                      size="sm"
                      variant="danger"
                      onClick={() => removeRow(index)}
                    />
                  </div>
                ))}
              </div>

              <div className="flex flex-wrap gap-2">
                <Btn
                  disabled={rows.length >= MAX_CONTACTS || save.isPending}
                  icon="plus"
                  variant="secondary"
                  onClick={addRow}
                >
                  Agregar contacto
                </Btn>
                <Btn
                  className="ml-auto"
                  disabled={!hasContent || save.isPending}
                  icon="check"
                  variant="primary"
                  onClick={() => save.mutate()}
                >
                  {save.isPending ? "Guardando…" : "Guardar"}
                </Btn>
              </div>

              {rows.length >= MAX_CONTACTS && (
                <p className="m-0 text-[12px] text-muted">
                  Máximo {MAX_CONTACTS} contactos.
                </p>
              )}
            </div>
          )}
        </SectionCard>
      </div>
    </AdminShell>
  );
}
