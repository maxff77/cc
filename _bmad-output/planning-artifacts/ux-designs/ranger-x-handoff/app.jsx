/* global React, ReactDOM, Logo, Mark, Icon, Btn, LabelCaps, MonoChip, StatePill, SectionCard, Field, PageHeader, LoginScreen, EnvioScreen, HistorialScreen, DetalleScreen, UsuariosScreen, GATES, CATEGORIES, useTweaks, TweaksPanel, TweakSection, TweakRadio, TweakSlider, TweakToggle */
const { useState, useEffect } = React;

// ---- Flujos screen (light, for nav completeness) ----
function GatesScreen() {
  const groups = CATEGORIES.map((c) => ({
    name: c.label,
    gates: GATES.filter((_, i) => i % CATEGORIES.length === CATEGORIES.indexOf(c)),
  })).filter((g) => g.gates.length);
  // simple deterministic split so each category has at least one
  const byCat = [
    { name: "Categoría A", gates: [GATES[0], GATES[2]] },
    { name: "Categoría B", gates: [GATES[1]] },
    { name: "Categoría C", gates: [GATES[3]] },
  ];
  return (
    <div className="rx-enter" style={{ maxWidth: 780, margin: "0 auto", display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader title="Flujos" actions={<React.Fragment>
        <Btn size="md" variant="secondary" icon="plus">Categoría</Btn>
        <Btn size="md" variant="primary" icon="plus">Nuevo flujo</Btn>
      </React.Fragment>} />
      {byCat.map((c) => (
        <SectionCard key={c.name} legend={c.name} padding="none">
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {c.gates.map((g, i) => (
              <li key={g.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "13px 14px",
                borderTop: i ? "1px solid var(--separator)" : "none" }}>
                <span style={{ flex: 1, fontSize: 14, fontWeight: 600 }}>{g.label}</span>
                <MonoChip>{g.mono}</MonoChip>
                <Btn size="sm" variant="secondary">Renombrar</Btn>
                <Btn size="sm" variant="danger" icon="trash">Eliminar</Btn>
              </li>
            ))}
          </ul>
        </SectionCard>
      ))}
    </div>
  );
}

// ---- Chrome (header nav) ----
function Chrome({ screen, go, theme, toggleTheme, children }) {
  const items = [
    { id: "envio", label: "Envío" },
    { id: "historial", label: "Historial" },
    { id: "usuarios", label: "Usuarios" },
    { id: "gates", label: "Flujos" },
  ];
  const active = screen === "detalle" ? "historial" : screen;
  return (
    <div style={{ position: "relative", zIndex: 1, minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header style={{ position: "sticky", top: 0, zIndex: 30, display: "flex", alignItems: "center",
        justifyContent: "space-between", gap: 16, padding: "12px 22px",
        borderBottom: "1px solid var(--border)", background: "color-mix(in oklch, var(--background) 82%, transparent)",
        backdropFilter: "blur(12px)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 22, minWidth: 0 }}>
          <button onClick={() => go("envio")} className="rx-focus" style={{ background: "none", border: "none", cursor: "pointer",
            display: "flex", alignItems: "center", gap: 10, padding: 0 }}>
            <Mark size={28} />
            <span className="font-display gradient-text" style={{ fontSize: 21, fontWeight: 800, fontStyle: "italic",
              letterSpacing: ".01em", lineHeight: 1.1, paddingRight: "0.18em" }}>RANGER-X</span>
          </button>
          <nav className="rx-nav" style={{ display: "flex", alignItems: "center", gap: 3 }}>
            {items.map((it) => {
              const on = active === it.id;
              return (
                <button key={it.id} onClick={() => go(it.id)} className="rx-focus"
                  style={{ position: "relative", padding: "8px 13px", borderRadius: "var(--radius-sm)", border: "none", cursor: "pointer",
                    font: '600 14px/1 "Saira", sans-serif', letterSpacing: ".01em",
                    background: on ? "var(--surface-tertiary)" : "transparent", color: on ? "var(--foreground)" : "var(--muted)",
                    transition: "color .15s, background .15s" }}>
                  {it.label}
                  {on && <span style={{ position: "absolute", left: 13, right: 13, bottom: -13, height: 2,
                    background: "var(--brand-gradient)", borderRadius: 2 }} />}
                </button>
              );
            })}
          </nav>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button onClick={toggleTheme} className="rx-focus" title="Cambiar tema"
            style={{ width: 38, height: 38, borderRadius: "var(--radius-field)", border: "1px solid var(--border)",
              background: "var(--surface-secondary)", color: "var(--foreground)", cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
          </button>
          <Btn size="sm" variant="secondary" onClick={() => go("login")}>Cerrar sesión</Btn>
        </div>
      </header>
      <main style={{ flex: 1, padding: "28px 22px 60px" }}>{children}</main>
    </div>
  );
}

// ---- Auth demo switcher (auth screens have no chrome) ----
function AuthSwitcher({ screen, go, theme, toggleTheme }) {
  const views = [
    { id: "login", label: "Login" },
    { id: "change-password", label: "Cambiar contraseña" },
    { id: "expired", label: "Plan vencido" },
    { id: "blocked", label: "Bloqueada" },
    { id: "error", label: "Error" },
  ];
  return (
    <div style={{ position: "fixed", left: "50%", bottom: 18, transform: "translateX(-50%)", zIndex: 50,
      display: "flex", alignItems: "center", gap: 6, padding: 6, borderRadius: 99,
      border: "1px solid var(--border)", background: "color-mix(in oklch, var(--surface) 86%, transparent)",
      backdropFilter: "blur(12px)", boxShadow: "0 10px 30px oklch(0% 0 0 / .3)", maxWidth: "94vw", flexWrap: "wrap", justifyContent: "center" }}>
      <span className="font-display" style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".14em",
        color: "var(--faint)", padding: "0 6px" }}>Demo</span>
      {views.map((v) => {
        const on = screen === v.id;
        return (
          <button key={v.id} onClick={() => go(v.id)} className="rx-focus"
            style={{ padding: "6px 12px", borderRadius: 99, border: "none", cursor: "pointer",
              font: '600 12px/1 "Saira", sans-serif', whiteSpace: "nowrap",
              background: on ? "var(--brand-gradient)" : "transparent", color: on ? "#fff" : "var(--muted)" }}>{v.label}</button>
        );
      })}
      <span style={{ width: 1, height: 18, background: "var(--border)", margin: "0 2px" }} />
      <button onClick={toggleTheme} className="rx-focus" title="Cambiar tema"
        style={{ width: 30, height: 30, borderRadius: 99, border: "none", background: "transparent", color: "var(--foreground)",
          cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
      </button>
    </div>
  );
}

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "accent": "violet",
  "glow": 1,
  "radius": 12,
  "density": 1
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [screen, setScreen] = useState("envio");
  const [sel, setSel] = useState(null);

  // apply tweaks → CSS
  useEffect(() => {
    const r = document.documentElement;
    r.setAttribute("data-theme", t.theme);
    r.setAttribute("data-accent", t.accent);
    r.style.setProperty("--glow", String(t.glow));
    r.style.setProperty("--radius", t.radius + "px");
    r.style.setProperty("--radius-field", Math.max(4, t.radius - 3) + "px");
    r.style.setProperty("--radius-sm", Math.max(3, t.radius - 5) + "px");
    r.style.setProperty("--density", String(t.density));
  }, [t]);

  function go(s) { setScreen(s); window.scrollTo(0, 0); }
  function openSession(s, g) { setSel({ s, g }); go("detalle"); }
  function toggleTheme() { setTweak("theme", t.theme === "dark" ? "light" : "dark"); }

  const AUTH = ["login", "change-password", "expired", "blocked", "error"];
  const isAuth = AUTH.includes(screen);

  let body;
  if (screen === "envio") body = <EnvioScreen />;
  else if (screen === "historial") body = <HistorialScreen onOpen={openSession} />;
  else if (screen === "detalle") body = <DetalleScreen session={sel?.s} gate={sel?.g} onBack={() => go("historial")} />;
  else if (screen === "usuarios") body = <UsuariosScreen />;
  else if (screen === "gates") body = <GatesScreen />;

  let authBody = null;
  if (screen === "login") authBody = <LoginScreen onLogin={() => go("envio")} />;
  else if (screen === "change-password") authBody = <ChangePasswordScreen onBack={() => go("login")} onDone={() => go("envio")} />;
  else if (screen === "expired") authBody = <ExpiredScreen onBack={() => go("login")} />;
  else if (screen === "blocked") authBody = <BlockedScreen onBack={() => go("login")} />;
  else if (screen === "error") authBody = <ErrorScreen onBack={() => go("login")} />;

  return (
    <React.Fragment>
      <div className="rx-backdrop" />
      {isAuth
        ? authBody
        : <Chrome screen={screen} go={go} theme={t.theme} toggleTheme={toggleTheme}>{body}</Chrome>}
      {isAuth && <AuthSwitcher screen={screen} go={go} theme={t.theme} toggleTheme={toggleTheme} />}

      <TweaksPanel title="Tweaks">
        <TweakSection label="Tema" />
        <TweakRadio label="Modo" value={t.theme} options={["dark", "light"]} onChange={(v) => setTweak("theme", v)} />
        <TweakRadio label="Acento" value={t.accent} options={["violet", "cyan", "magenta"]} onChange={(v) => setTweak("accent", v)} />
        <TweakSection label="Identidad" />
        <TweakSlider label="Neón / glow" value={t.glow} min={0} max={1.6} step={0.1} onChange={(v) => setTweak("glow", v)} />
        <TweakSlider label="Radio de esquinas" value={t.radius} min={2} max={20} step={1} unit="px" onChange={(v) => setTweak("radius", v)} />
        <TweakSection label="Densidad" />
        <TweakSlider label="Padding de filas" value={t.density} min={0.7} max={1.6} step={0.1} unit="×" onChange={(v) => setTweak("density", v)} />
      </TweaksPanel>
    </React.Fragment>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
