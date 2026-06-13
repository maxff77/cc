/* global React, Logo, Mark, Icon, Btn, LabelCaps, Field, SectionCard */
const { useState: useStateS3 } = React;

// shared centered auth layout (matches Login)
function AuthLayout({ title, subtitle, children, onBack }) {
  return (
    <div className="rx-enter" style={{ position: "relative", zIndex: 1, minHeight: "100vh", display: "flex",
      alignItems: "center", justifyContent: "center", padding: "40px 20px" }}>
      <div style={{ width: "100%", maxWidth: 420, display: "flex", flexDirection: "column", alignItems: "center", gap: 24 }}>
        <Logo height={44} />
        <div style={{ width: "100%", position: "relative", borderRadius: 18, border: "1px solid var(--border)",
          background: "var(--surface)", padding: "28px 26px", backgroundImage: "var(--brand-gradient-soft)" }}>
          <div style={{ textAlign: "center", marginBottom: 22 }}>
            <h1 className="display" style={{ margin: 0, fontSize: 21, fontWeight: 800, letterSpacing: ".01em", color: "var(--foreground)" }}>{title}</h1>
            {subtitle && <p style={{ margin: "8px 0 0", fontSize: 14, color: "var(--muted)", lineHeight: 1.5 }}>{subtitle}</p>}
          </div>
          {children}
        </div>
        {onBack && (
          <button onClick={onBack} className="rx-focus" style={{ background: "none", border: "none", cursor: "pointer",
            color: "var(--muted)", fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
            ← Volver a iniciar sesión
          </button>
        )}
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <Mark size={26} />
          <span className="font-mono" style={{ fontSize: 11, color: "var(--faint)", letterSpacing: ".1em" }}>RANGER-X CHECK © 2026</span>
        </div>
      </div>
    </div>
  );
}

// shared reactivation panel (login bloqueado + plan vencido)
function ContactPanel({ message }) {
  return (
    <div style={{ borderRadius: "var(--radius-field)", border: "1px solid color-mix(in oklch, var(--danger) 35%, transparent)",
      background: "oklch(64% 0.215 25 / .12)", padding: "16px 16px" }}>
      <p style={{ margin: 0, fontSize: 14, color: "var(--foreground)", lineHeight: 1.55 }}>{message}</p>
      <div style={{ display: "flex", gap: 8, marginTop: 13 }}>
        <Btn size="sm" variant="secondary">WhatsApp</Btn>
        <Btn size="sm" variant="secondary">Telegram</Btn>
      </div>
    </div>
  );
}

// 1) Cambiar contraseña (forzado)
function ChangePasswordScreen({ onBack, onDone }) {
  const [cur, setCur] = useStateS3("");
  const [nw, setNw] = useStateS3("");
  return (
    <AuthLayout title="Contraseña nueva" subtitle="Elige una contraseña nueva para continuar." onBack={onBack}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Field label="Contraseña temporal" icon="lock" type="password" value={cur} onChange={setCur} placeholder="••••••••" />
        <div>
          <Field label="Contraseña nueva" icon="lock" type="password" value={nw} onChange={setNw} placeholder="••••••••" />
          <p style={{ margin: "7px 2px 0", fontSize: 12, color: "var(--muted)" }}>Mínimo 8 caracteres.</p>
        </div>
        <Btn variant="primary" full icon="check" onClick={onDone} style={{ marginTop: 2 }}>Guardar</Btn>
      </div>
    </AuthLayout>
  );
}

// 2) Plan vencido (hard lockout)
function ExpiredScreen({ onBack }) {
  return (
    <AuthLayout title="Tu plan venció" onBack={onBack}>
      <ContactPanel message="Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos." />
    </AuthLayout>
  );
}

// 3) Cuenta bloqueada (estado del login)
function BlockedScreen({ onBack }) {
  return (
    <AuthLayout title="Cuenta bloqueada" onBack={onBack}>
      <ContactPanel message="Tu cuenta está bloqueada. Escríbenos por WhatsApp o Telegram para reactivarla." />
    </AuthLayout>
  );
}

// 4) Error genérico
function ErrorScreen({ onBack }) {
  return (
    <AuthLayout title="Algo salió mal" subtitle="Recarga la página o intenta de nuevo." onBack={onBack}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, borderRadius: "var(--radius-field)",
          border: "1px solid color-mix(in oklch, var(--danger) 35%, transparent)", background: "oklch(64% 0.215 25 / .12)", padding: "14px 16px" }}>
          <span style={{ color: "var(--danger)", display: "flex" }}><Icon name="refresh" size={20} /></span>
          <span style={{ fontSize: 14, color: "var(--foreground)" }}>No pudimos completar la última acción.</span>
        </div>
        <Btn variant="primary" full icon="refresh" onClick={onBack}>Reintentar</Btn>
      </div>
    </AuthLayout>
  );
}

Object.assign(window, { ChangePasswordScreen, ExpiredScreen, BlockedScreen, ErrorScreen });
