/* global React, Logo, Mark, Icon, Btn, LabelCaps, MonoChip, StatePill, CountBadge, SectionCard, Field, Area, Select, Checkbox, ProgressRing, Metric, ResultPanel, COMPLETA, FILTRADA_CON, FILTRADA_SIN, GATES, CATEGORIES */
const { useState, useEffect, useRef } = React;

// ===========================================================================
// LOGIN
// ===========================================================================
function LoginScreen({ onLogin }) {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [show, setShow] = useState(false);
  const [remember, setRemember] = useState(true);

  return (
    <div className="rx-enter" style={{ position: "relative", zIndex: 1, minHeight: "100vh", display: "flex",
      alignItems: "center", justifyContent: "center", padding: "40px 20px" }}>
      <div style={{ width: "100%", maxWidth: 460, display: "flex", flexDirection: "column", alignItems: "center", gap: 26 }}>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
          <Logo height={52} />
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ height: 1, width: 20, background: "var(--accent)", opacity: .6 }} />
            <LabelCaps style={{ letterSpacing: ".22em", color: "var(--muted)", whiteSpace: "nowrap" }}>Seguridad · Control · Rendimiento</LabelCaps>
            <span style={{ height: 1, width: 20, background: "var(--magenta)", opacity: .6 }} />
          </div>
        </div>

        {/* card */}
        <div className="glow-soft" style={{ width: "100%", position: "relative", borderRadius: 18,
          border: "1px solid var(--border)", background: "var(--surface)", padding: "30px 28px 28px",
          backgroundImage: "var(--brand-gradient-soft)" }}>
          {/* corner ticks */}
          <CornerTicks />

          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12, marginBottom: 26 }}>
            <span style={{ flex: 1, height: 1, background: "linear-gradient(90deg,transparent,var(--accent))" }} />
            <h1 className="display" style={{ margin: 0, fontSize: 19, fontWeight: 800, letterSpacing: ".18em",
              textTransform: "uppercase", color: "var(--foreground)", whiteSpace: "nowrap" }}>Iniciar sesión</h1>
            <span style={{ flex: 1, height: 1, background: "linear-gradient(90deg,var(--magenta),transparent)" }} />
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Field label="Usuario" icon="user" value={user} onChange={setUser} placeholder="Ingresa tu usuario" />
            <Field label="Contraseña" icon="lock" type={show ? "text" : "password"} value={pass} onChange={setPass}
              placeholder="Ingresa tu contraseña"
              rightSlot={<button onClick={() => setShow((s) => !s)} className="rx-focus"
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted)", display: "flex", padding: 0 }}>
                <Icon name={show ? "eyeOff" : "eye"} size={18} /></button>} />

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
              <Checkbox checked={remember} onChange={setRemember}>Recordarme</Checkbox>
              <a href="#" onClick={(e) => e.preventDefault()} style={{ fontSize: 13, color: "var(--accent)", textDecoration: "none" }}>¿Olvidaste tu contraseña?</a>
            </div>

            <Btn variant="primary" size="lg" full iconRight="arrow" onClick={onLogin} style={{ marginTop: 4, letterSpacing: ".14em", textTransform: "uppercase" }}>
              Iniciar sesión
            </Btn>

            <p style={{ textAlign: "center", fontSize: 13, color: "var(--muted)", margin: "6px 0 0" }}>
              ¿Problemas para entrar? Escríbenos por{" "}
              <a href="#" onClick={(e) => e.preventDefault()} style={{ color: "var(--accent)" }}>WhatsApp</a> o{" "}
              <a href="#" onClick={(e) => e.preventDefault()} style={{ color: "var(--accent)" }}>Telegram</a>.
            </p>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <Mark size={30} />
          <span className="font-mono" style={{ fontSize: 11, color: "var(--faint)", letterSpacing: ".1em" }}>RANGER-X CHECK © 2026</span>
        </div>
      </div>
    </div>
  );
}

function CornerTicks() {
  const c = { position: "absolute", width: 22, height: 22, pointerEvents: "none" };
  const line = { position: "absolute", background: "var(--accent)", opacity: .5 };
  return (
    <React.Fragment>
      <span style={{ ...c, top: -1, left: -1 }}>
        <span style={{ ...line, top: 0, left: 0, width: 22, height: 2 }} /><span style={{ ...line, top: 0, left: 0, width: 2, height: 22 }} />
      </span>
      <span style={{ ...c, top: -1, right: -1 }}>
        <span style={{ ...line, top: 0, right: 0, width: 22, height: 2, background: "var(--magenta)" }} /><span style={{ ...line, top: 0, right: 0, width: 2, height: 22, background: "var(--magenta)" }} />
      </span>
      <span style={{ ...c, bottom: -1, left: -1 }}>
        <span style={{ ...line, bottom: 0, left: 0, width: 22, height: 2, background: "var(--cyan)" }} /><span style={{ ...line, bottom: 0, left: 0, width: 2, height: 22, background: "var(--cyan)" }} />
      </span>
      <span style={{ ...c, bottom: -1, right: -1 }}>
        <span style={{ ...line, bottom: 0, right: 0, width: 22, height: 2, background: "var(--magenta)" }} /><span style={{ ...line, bottom: 0, right: 0, width: 2, height: 22, background: "var(--magenta)" }} />
      </span>
    </React.Fragment>
  );
}

// ===========================================================================
// ENVÍO — the cockpit
// ===========================================================================
function EnvioScreen() {
  // state machine: idle | sending | paused — default 'idle' = sesión activa con
  // resultados pero sin lote corriendo (la vista de aterrizaje real).
  const [state, setState] = useState("idle");
  const [sent, setSent] = useState(78);
  const [text, setText] = useState("");
  const [cat, setCat] = useState(null);
  const [gate, setGate] = useState(null);
  const total = 99, ccNew = 6;
  // gentle live tick while sending
  useEffect(() => {
    if (state !== "sending") return;
    const id = setInterval(() => setSent((s) => (s >= total ? s : s + 1)), 900);
    return () => clearInterval(id);
  }, [state]);

  const percent = Math.round((sent / total) * 100);
  const ringTone = state === "sending" ? "accent" : "warning";
  const idle = state === "idle";

  function enviar() {
    if (idle) { setState("sending"); setSent(0); }
    setText("");
  }

  return (
    <div className="rx-enter envio-grid" style={{ maxWidth: 1640, margin: "0 auto", display: "grid", gap: 22,
      gridTemplateColumns: "320px minmax(0,1fr) minmax(0,1fr) minmax(0,1fr)", alignItems: "start" }}>

      {/* COCKPIT COLUMN */}
      <div className="envio-cockpit" style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 22 }}>
        {/* ring + metrics */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: idle ? "center" : "space-between",
          gap: 16, padding: "8px 4px" }}>
          <ProgressRing percent={percent} sent={sent} total={total} tone={ringTone} idle={idle} />
          {!idle && (
            <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
              <Metric label="Enviadas · En cola" value={`${sent} · ${total - sent}`} />
              <Metric label={state === "paused" ? "ETA al reanudar" : "ETA"} value={state === "paused" ? "~1m 40s" : "1m 12s"} />
              <Metric label="Nuevas" value={ccNew} tone="success" />
            </div>
          )}
        </div>
        {idle && <p style={{ textAlign: "center", fontSize: 13.5, color: "var(--muted)", margin: "-6px 0 2px" }}>Pega tus entradas y elige un flujo.</p>}

        {/* sesión activa */}
        <SectionCard legend="Sesión activa" rail="accent">
          <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ flex: 1, fontSize: 14, fontWeight: 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Lote nocturno · MX</span>
              <StatePill tone="accent" dot="pulse">En curso</StatePill>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--muted)" }}>
              <span>Flujo Gamma</span><MonoChip>/fg</MonoChip>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Btn size="sm" variant="secondary">Renombrar</Btn>
              <Btn size="sm" variant="secondary" disabled={!idle}>Nueva sesión</Btn>
            </div>
          </div>
        </SectionCard>

        {/* controles */}
        {!idle && (
          <SectionCard legend="Controles" rail={state === "sending" ? "accent" : "warning"}>
            <div style={{ display: "flex", gap: 10 }}>
              {state === "paused"
                ? <Btn size="md" variant="success" full icon="play" onClick={() => setState("sending")}>Reanudar</Btn>
                : <Btn size="md" variant="warning" full icon="pause" onClick={() => setState("paused")}>Pausar</Btn>}
              <Btn size="md" variant="danger" full icon="stop" onClick={() => setState("idle")}>Detener</Btn>
            </div>
          </SectionCard>
        )}

        {/* nuevo lote */}
        <SectionCard legend="Nuevo lote">
          <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
            {!idle ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <LabelCaps>Flujo activo</LabelCaps>
                <MonoChip>Flujo Gamma · /fg</MonoChip>
              </div>
            ) : (
              <React.Fragment>
                <Select label="Categoría" placeholder="Elige una categoría" options={CATEGORIES} value={cat} onChange={setCat} />
                <Select label="Flujo" placeholder="Elige un flujo" options={GATES} value={gate} onChange={setGate} disabled={!cat} />
              </React.Fragment>
            )}
            <Area label="Entradas" value={text} onChange={setText} placeholder="Pega tus entradas" rows={5} />
            <Btn variant="primary" full icon="send" onClick={enviar}>Enviar</Btn>
          </div>
        </SectionCard>
      </div>

      {/* RESULT PANELS — la sesión activa conserva sus resultados aunque no
          haya lote corriendo; los totales son del snapshot (capado server-side). */}
      <ResultPanel header="Completa" count={99} rows={COMPLETA}
        empty="Aún no hay respuestas." exportable maxH="calc(100vh - 150px)" />
      <ResultPanel header="Filtrada con respuesta" count={10} countTone="success"
        rows={FILTRADA_CON} empty="Aún no hay respuestas con ✅." exportable maxH="calc(100vh - 150px)" />
      <ResultPanel header="Filtrada sin respuesta" count={10} countTone="success"
        rows={FILTRADA_SIN} empty="Aún no hay datos capturados." exportable maxH="calc(100vh - 150px)" />
    </div>
  );
}

Object.assign(window, { LoginScreen, EnvioScreen });
