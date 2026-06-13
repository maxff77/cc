/* global React, Icon, Btn, LabelCaps, MonoChip, StatePill, SectionCard, Field, Select, ResultPanel, COMPLETA, FILTRADA_CON, FILTRADA_SIN, SESSIONS, USERS, CATEGORIES, GATES */
const { useState: useStateS2 } = React;

// shared page header
function PageHeader({ title, mono, back, actions, onBack }) {
  return (
    <header style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
        {back && (
          <button onClick={onBack} className="rx-focus" style={{ background: "none", border: "none", cursor: "pointer",
            font: '700 10px/1 "Saira", sans-serif', textTransform: "uppercase", letterSpacing: ".16em",
            color: "var(--muted)", padding: 0, alignSelf: "flex-start", display: "flex", alignItems: "center", gap: 5 }}>
            ← {back}
          </button>
        )}
        <h1 className="display" style={{ margin: 0, fontSize: 26, fontWeight: 800, letterSpacing: "-.01em",
          color: "var(--foreground)" }}>{title}</h1>
        {mono && <span className="font-mono" style={{ fontSize: 12, color: "var(--muted)" }}>{mono}</span>}
      </div>
      {actions && <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>{actions}</div>}
    </header>
  );
}

// ===========================================================================
// HISTORIAL
// ===========================================================================
function HistorialScreen({ onOpen }) {
  return (
    <div className="rx-enter" style={{ maxWidth: 780, margin: "0 auto", display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader title="Historial" />
      <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
        {SESSIONS.map((g) => (
          <SectionCard key={g.gateValue} legend={g.group} legendRight={<MonoChip>{g.gateValue}</MonoChip>} padding="none">
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {g.items.map((s, i) => (
                <li key={s.id} style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "8px 12px",
                  padding: "13px 14px", borderTop: i ? "1px solid var(--separator)" : "none" }}>
                  <button onClick={() => onOpen(s, g)} className="rx-focus" style={{ flex: 1, minWidth: 160, textAlign: "left",
                    background: "none", border: "none", cursor: "pointer", padding: 0 }}>
                    <span style={{ display: "block", fontSize: 14, fontWeight: 600, color: "var(--foreground)" }}>{s.name || s.date}</span>
                    {s.name && <span className="font-mono" style={{ display: "block", fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{s.date}</span>}
                  </button>
                  <StatePill tone={s.active ? "accent" : "muted"} dot={s.active ? "pulse" : undefined}>{s.active ? "En curso" : "Cerrada"}</StatePill>
                  <div style={{ display: "flex", gap: 7, flexShrink: 0 }}>
                    {!s.active && <Btn size="sm" variant="secondary" icon="play">Continuar</Btn>}
                    <Btn size="sm" variant="secondary">Renombrar</Btn>
                    <Btn size="sm" variant="danger" icon="trash">Eliminar</Btn>
                  </div>
                </li>
              ))}
            </ul>
          </SectionCard>
        ))}
      </div>
    </div>
  );
}

// ===========================================================================
// DETALLE DE SESIÓN
// ===========================================================================
function DetalleScreen({ session, gate, onBack }) {
  const s = session || { name: "BridgeMind", date: "2026-06-12 13:11", active: false };
  const g = gate || { group: "Flujo Beta", gateValue: "/fb" };
  return (
    <div className="rx-enter" style={{ maxWidth: 1500, margin: "0 auto", display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader title={s.name || s.date} back="Historial" onBack={onBack}
        mono={`${g.group} · ${g.gateValue} · ${s.date}`}
        actions={<React.Fragment>
          {!s.active && <Btn size="md" variant="secondary" icon="play">Continuar</Btn>}
          <StatePill tone={s.active ? "accent" : "muted"} dot={s.active ? "pulse" : undefined}>{s.active ? "En curso" : "Cerrada"}</StatePill>
        </React.Fragment>} />
      <div className="detalle-grid" style={{ display: "grid", gap: 20, gridTemplateColumns: "repeat(3, minmax(0,1fr))" }}>
        <ResultPanel header="Completa" count={COMPLETA.length} rows={COMPLETA} empty="—" exportable maxH={560} />
        <ResultPanel header="Filtrada con respuesta" count={FILTRADA_CON.length} countTone="success" rows={FILTRADA_CON} empty="—" exportable maxH={560} />
        <ResultPanel header="Filtrada sin respuesta" count={FILTRADA_SIN.length} countTone="success" rows={FILTRADA_SIN} empty="—" exportable maxH={560} />
      </div>
    </div>
  );
}

// ===========================================================================
// USUARIOS (admin)
// ===========================================================================
const ROLE_TONE = { owner: "accent", admin: "cyan", client: "muted" };
function RolePill({ role }) {
  return <StatePill tone={ROLE_TONE[role] || "muted"}>{role}</StatePill>;
}

function UsuariosScreen() {
  const [tab, setTab] = useStateS2("client");
  const [email, setEmail] = useStateS2("");
  const [pass, setPass] = useStateS2("");
  const [days, setDays] = useStateS2("30");
  const [tg, setTg] = useStateS2("");

  return (
    <div className="rx-enter" style={{ maxWidth: 1180, margin: "0 auto", display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader title="Usuarios" />
      <div className="usuarios-grid" style={{ display: "grid", gap: 22, gridTemplateColumns: "360px minmax(0,1fr)", alignItems: "start" }}>

        {/* left: forms */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {/* segmented create switch */}
          <div style={{ display: "flex", gap: 6, padding: 5, background: "var(--surface-secondary)", borderRadius: "var(--radius-field)", border: "1px solid var(--border)" }}>
            {[["client", "Crear cliente"], ["admin", "Crear admin"]].map(([id, lbl]) => (
              <button key={id} onClick={() => setTab(id)} className="rx-focus"
                style={{ flex: 1, padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "none", cursor: "pointer",
                  font: '600 13px/1 "Saira", sans-serif', letterSpacing: ".02em",
                  background: tab === id ? "var(--brand-gradient)" : "transparent",
                  color: tab === id ? "#fff" : "var(--muted)" }}>{lbl}</button>
            ))}
          </div>

          <SectionCard legend={tab === "client" ? "Crear cliente" : "Crear admin"}>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <Field label="Correo *" value={email} onChange={setEmail} placeholder="cliente@correo.com" />
              <Field label="Contraseña *" type="password" value={pass} onChange={setPass} placeholder="••••••••" />
              {tab === "client" && <React.Fragment>
                <Field label="Días del plan *" value={days} onChange={setDays} mono />
                <Field label="Telegram (opcional)" value={tg} onChange={setTg} placeholder="@usuario" />
              </React.Fragment>}
              <Btn variant="primary" full icon="plus" style={{ marginTop: 2 }}>Crear</Btn>
            </div>
          </SectionCard>

          <SectionCard legend="Control de admisión">
            <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>
              Máximo de envíos activos a la vez; los lotes que excedan el límite esperan en cola.
              <strong style={{ color: "var(--foreground)" }}> 0</strong> desactiva el límite.
            </p>
            <Field label="Envíos activos máx." value="3" onChange={() => {}} mono />
          </SectionCard>
        </div>

        {/* right: users table */}
        <SectionCard legend="Usuarios" padding="none">
          <div style={{ overflowX: "auto" }} className="rx-scroll">
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 640 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {["Correo", "Rol", "Contacto", "Vence", "Estado", "Acciones"].map((h) => (
                    <th key={h} style={{ textAlign: h === "Acciones" ? "right" : "left", padding: "16px 14px 11px",
                      font: '700 10px/1 "Saira", sans-serif', textTransform: "uppercase", letterSpacing: ".14em", color: "var(--muted)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {USERS.map((u, i) => (
                  <tr key={u.email} style={{ borderTop: i ? "1px solid var(--separator)" : "none" }}>
                    <td style={{ padding: "13px 14px", fontSize: 13.5, fontWeight: 500 }}>{u.email}</td>
                    <td style={{ padding: "13px 14px" }}><RolePill role={u.role} /></td>
                    <td style={{ padding: "13px 14px", fontSize: 13, color: "var(--muted)" }} className="font-mono">{u.contact}</td>
                    <td style={{ padding: "13px 14px", fontSize: 13, color: "var(--muted)" }}>{u.vence}</td>
                    <td style={{ padding: "13px 14px" }}>
                      {u.estado === "Activo" ? <StatePill tone="success">Activo</StatePill>
                        : u.estado === "Vencido" ? <StatePill tone="danger">Vencido</StatePill>
                        : <span style={{ color: "var(--faint)" }}>—</span>}
                    </td>
                    <td style={{ padding: "13px 14px" }}>
                      <div style={{ display: "flex", gap: 7, justifyContent: "flex-end", flexWrap: "wrap" }}>
                        {u.role === "client" && <React.Fragment>
                          <Btn size="sm" variant="ghost">Sesiones</Btn>
                          <Btn size="sm" variant="secondary" icon="refresh">Renovar</Btn>
                        </React.Fragment>}
                        <Btn size="sm" variant="danger" icon="trash">Eliminar</Btn>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      </div>
    </div>
  );
}

Object.assign(window, { PageHeader, HistorialScreen, DetalleScreen, UsuariosScreen });
