// Worker HTTP. Ingreso = webhook de ForwardEmail (catch-all *@mail.lohari.com.mx).
// ForwardEmail manda el correo ya parseado (mailparser JSON), no MIME crudo.
//
// Seguridad del webhook (plan free de ForwardEmail no da firma HMAC): se verifica
// que el POST venga de las IPs de mx1/mx2.forwardemail.net, resueltas en vivo por DoH.
// ponytail: allowlist por IP de ForwardEmail; si pasas a plan pago, cambia a verificar
// el header X-Webhook-Signature (HMAC con tu Webhook Key) que es mas fuerte.

async function dohIPs(name, type) {
  const r = await fetch(`https://cloudflare-dns.com/dns-query?name=${name}&type=${type}`,
    { headers: { accept: "application/dns-json" } });
  if (!r.ok) return [];
  const j = await r.json();
  // type 1 = A, type 28 = AAAA (ignora CNAME u otros)
  return (j.Answer || []).filter(a => a.type === 1 || a.type === 28).map(a => a.data);
}

async function fromForwardEmail(ip) {
  if (!ip) return false;
  const lists = await Promise.all([
    dohIPs("mx1.forwardemail.net", "A"), dohIPs("mx1.forwardemail.net", "AAAA"),
    dohIPs("mx2.forwardemail.net", "A"), dohIPs("mx2.forwardemail.net", "AAAA"),
  ]);
  // ponytail: compara IPv6 como string canonico (CF y CF-DNS usan el mismo formato);
  // si algun webhook v6 diera 403, normaliza ambos lados.
  return new Set(lists.flat()).has(ip);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);

    // Webhook de ForwardEmail (POST /inbound) — autenticado por IP de origen
    if (req.method === "POST" && url.pathname === "/inbound") {
      if (!(await fromForwardEmail(req.headers.get("CF-Connecting-IP"))))
        return new Response("forbidden", { status: 403 });
      const m = await req.json();
      const from = m.from?.value?.[0]?.address ?? m.from?.text ?? "";
      const to = m.session?.recipient ?? (Array.isArray(m.recipients) ? m.recipients.join(",") : "");
      await env.EMAILS_DB.prepare(
        "INSERT INTO emails (from_addr,to_addr,subject,text,html) VALUES (?,?,?,?,?)"
      ).bind(from, to, m.subject ?? "", m.text ?? "", m.html ?? "").run();
      return new Response("ok");
    }

    // Lectura autenticada (GET /emails?to=&limit=, Bearer token)
    if (req.method === "GET" && url.pathname === "/emails") {
      if (req.headers.get("Authorization") !== `Bearer ${env.AUTH_TOKEN}`)
        return new Response("unauthorized", { status: 401 });
      const to = url.searchParams.get("to");
      const limit = Math.min(Number(url.searchParams.get("limit")) || 20, 100);
      const q = to
        ? env.EMAILS_DB.prepare("SELECT * FROM emails WHERE to_addr=? ORDER BY id DESC LIMIT ?").bind(to, limit)
        : env.EMAILS_DB.prepare("SELECT * FROM emails ORDER BY id DESC LIMIT ?").bind(limit);
      const { results } = await q.all();
      return Response.json(results);
    }

    return new Response("not found", { status: 404 });
  },
};
