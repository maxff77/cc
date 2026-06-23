"use client";

// Shared gift-key claim form (gift-keys feature). Mounted in the cockpit (an
// active client) and on /expired (a just-registered / lapsed client) — those two
// mounts are wired in a coordinated step with the registration session that owns
// those files. The claim endpoint bypasses the plan-expiry gate, so an EXPIRED
// client can redeem here to recover access (the api wrapper won't bounce a 200).
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { Btn } from "@/components/ui/btn";
import { Field } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";

export interface ClaimResult {
  expires_at: string | null;
  plan_id: number | null;
  days_added: number;
  credits_added: number;
}

export function ClaimKey({
  onClaimed,
}: {
  // Fired after a successful claim — the cockpit invalidates /me; /expired sends
  // the recovered client back into the app.
  onClaimed?: (result: ClaimResult) => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (pending) return;
    const trimmed = code.trim();

    if (!trimmed) {
      setError("Pega tu key.");

      return;
    }
    setPending(true);
    setError(null);
    setOk(null);
    try {
      const result = await api.post<ClaimResult>("/api/keys/claim", {
        code: trimmed,
      });

      setCode("");
      // Compose what the key granted — days, credits, or both.
      const grants: string[] = [];

      if (result.days_added > 0) grants.push(`+${result.days_added} días`);
      if (result.credits_added > 0)
        grants.push(`+${result.credits_added} créditos`);
      setOk(`Key canjeada: ${grants.join(" y ") || "sin cambios"}.`);
      onClaimed?.(result);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={submit}>
      {ok && <Notice status="success">{ok}</Notice>}
      {error && <Notice status="danger">{error}</Notice>}
      <Field
        label="¿Tienes una key?"
        name="gift_key"
        placeholder="RangerX-XXXX-XXXX-XXXX"
        value={code}
        onChange={(v) => setCode(v)}
      />
      <Btn full disabled={pending} type="submit" variant="primary">
        {pending ? "Canjeando…" : "Canjear key"}
      </Btn>
    </form>
  );
}
