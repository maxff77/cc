/* global React */
// ============================================================================
// RANGER-X CHECK — Logo + UI primitives (rack-plate system, neon identity)
// ============================================================================
const { useState, useRef, useEffect } = React;

// ---------------------------------------------------------------------------
// LOGO — built in SVG, gradient fill works on light & dark
// ---------------------------------------------------------------------------
function Logo({ height = 40, sub = true, gid }) {
  // unique gradient id per instance so multiple logos don't collide
  const id = gid || ("rxg-" + Math.random().toString(36).slice(2, 8));
  const w = sub ? height * 6.0 : height * 5.2;
  const h = sub ? height * 1.42 : height;
  return (
    <svg viewBox="0 0 600 142" width={w} height={h} role="img" aria-label="Ranger-X Check" style={{ display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="0.25">
          <stop offset="0%"  stopColor="var(--cyan)" />
          <stop offset="34%" stopColor="var(--blue)" />
          <stop offset="64%" stopColor="var(--accent)" />
          <stop offset="100%" stopColor="var(--magenta)" />
        </linearGradient>
        <filter id={id + "-glow"} x="-30%" y="-60%" width="160%" height="220%">
          <feGaussianBlur stdDeviation="3.2" result="b" />
          <feComponentTransfer in="b" result="bb">
            <feFuncA type="linear" slope={`${0.55}`} />
          </feComponentTransfer>
          <feMerge><feMergeNode in="bb" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      <g filter={`url(#${id}-glow)`}>
        {/* leading lightning slash */}
        <path d="M58 18 L34 74 L52 74 L40 122 L92 56 L70 56 L86 18 Z" fill={`url(#${id})`} opacity="0.95" />
        {/* wordmark */}
        <text x="96" y="92" fontFamily="Saira, sans-serif" fontWeight="800" fontSize="86"
              fontStyle="italic" letterSpacing="1" fill={`url(#${id})`}
              style={{ fontStretch: "condensed" }}>RANGER-X</text>
        {/* trailing accent slash through the X */}
        <path d="M560 12 L600 12 L556 130 L516 130 Z" fill={`url(#${id})`} opacity="0.9" />
      </g>
      {sub && (
        <g>
          {/* circuit ticks left */}
          <g stroke="var(--accent)" strokeWidth="3" opacity="0.8">
            <line x1="150" y1="126" x2="200" y2="126" />
            <line x1="206" y1="120" x2="218" y2="120" /><line x1="206" y1="126" x2="226" y2="126" />
          </g>
          <text x="244" y="134" fontFamily="Saira, sans-serif" fontWeight="700" fontSize="30"
                letterSpacing="14" fill={`url(#${id})`}>CHECK</text>
          <g stroke="var(--magenta)" strokeWidth="3" opacity="0.8">
            <line x1="452" y1="126" x2="502" y2="126" />
            <line x1="430" y1="120" x2="442" y2="120" /><line x1="424" y1="126" x2="444" y2="126" />
          </g>
        </g>
      )}
    </svg>
  );
}

// compact shield-X mark for nav / favicon
function Mark({ size = 30 }) {
  const id = "rxm-" + Math.random().toString(36).slice(2, 8);
  return (
    <svg viewBox="0 0 48 56" width={size * 0.86} height={size} aria-hidden="true" style={{ display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--cyan)" />
          <stop offset="55%" stopColor="var(--accent)" />
          <stop offset="100%" stopColor="var(--magenta)" />
        </linearGradient>
      </defs>
      <path d="M24 2 L45 10 V28 C45 42 36 50 24 54 C12 50 3 42 3 28 V10 Z"
            fill="none" stroke={`url(#${id})`} strokeWidth="2.6"
            style={{ filter: "drop-shadow(0 0 calc(5px * var(--glow)) var(--accent))" }} />
      <path d="M15 18 L33 40 M33 18 L15 40" stroke={`url(#${id})`} strokeWidth="4.4" strokeLinecap="round" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// ICONS (inline, currentColor)
// ---------------------------------------------------------------------------
const I = {
  user:  <path d="M12 12a4 4 0 100-8 4 4 0 000 8zm0 2c-4 0-7 2-7 5v1h14v-1c0-3-3-5-7-5z" />,
  lock:  <path d="M6 10V8a6 6 0 1112 0v2h1a1 1 0 011 1v9a1 1 0 01-1 1H5a1 1 0 01-1-1v-9a1 1 0 011-1h1zm2 0h8V8a4 4 0 10-8 0v2z" />,
  eye:   <path d="M12 5c-5 0-9 4.5-10 7 1 2.5 5 7 10 7s9-4.5 10-7c-1-2.5-5-7-10-7zm0 11a4 4 0 110-8 4 4 0 010 8z" />,
  eyeOff:<path d="M3 4l17 17-1.4 1.4-3-3A11 11 0 0112 19C7 19 3 14.5 2 12a13 13 0 014-5L1.6 5.4 3 4zm9 5a3 3 0 013 3l-3-3zm0-4c5 0 9 4.5 10 7a13 13 0 01-3 4l-3-3a4 4 0 00-5-5L9.6 6.4A10 10 0 0112 5z" />,
  arrow: <path d="M5 12h12m0 0l-5-5m5 5l-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
  chevron:<path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
  pause: <path d="M7 5h3v14H7zM14 5h3v14h-3z" />,
  play:  <path d="M7 4l13 8-13 8z" />,
  stop:  <rect x="6" y="6" width="12" height="12" rx="1.5" />,
  plus:  <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" />,
  send:  <path d="M3 11l18-8-8 18-2-7z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />,
  download:<path d="M12 3v10m0 0l-4-4m4 4l4-4M5 19h14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
  sun:   <g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" /></g>,
  moon:  <path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z" />,
  trash: <path d="M6 7h12l-1 13a1 1 0 01-1 1H8a1 1 0 01-1-1L6 7zm3-3h6l1 2H8l1-2zM4 6h16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />,
  refresh:<path d="M4 12a8 8 0 0113.7-5.7L20 8m0 0V3m0 5h-5M20 12a8 8 0 01-13.7 5.7L4 16m0 0v5m0-5h5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />,
  search:<g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="6" /><path d="M20 20l-3.5-3.5" /></g>,
  check: <path d="M5 12l5 5L20 6" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />,
};
function Icon({ name, size = 18, style }) {
  return <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" style={{ flexShrink: 0, ...style }}>{I[name]}</svg>;
}

// ---------------------------------------------------------------------------
// BUTTON
// ---------------------------------------------------------------------------
function Btn({ variant = "secondary", size = "md", icon, iconRight, children, full, style, ...p }) {
  const pad = size === "sm" ? "6px 12px" : size === "lg" ? "13px 22px" : "9px 16px";
  const fs = size === "sm" ? 13 : size === "lg" ? 15 : 14;
  const base = {
    display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8,
    font: `600 ${fs}px/1 "Saira", sans-serif`, letterSpacing: ".02em",
    padding: pad, borderRadius: "var(--radius-field)", cursor: "pointer",
    border: "1px solid transparent", whiteSpace: "nowrap",
    transition: "transform .12s, box-shadow .2s, background .2s, border-color .2s",
    width: full ? "100%" : undefined,
  };
  const v = {
    primary: { background: "var(--brand-gradient)", color: "#fff", border: "none",
               boxShadow: "0 6px 22px oklch(64% 0.21 295 / calc(.35 * var(--glow)))" },
    secondary: { background: "var(--surface-secondary)", color: "var(--foreground)", borderColor: "var(--border)" },
    ghost: { background: "transparent", color: "var(--muted)" },
    danger: { background: "transparent", color: "var(--danger)", borderColor: "color-mix(in oklch, var(--danger) 40%, transparent)" },
    success: { background: "var(--success)", color: "var(--success-foreground)", border: "none" },
    warning: { background: "transparent", color: "var(--warning)", borderColor: "color-mix(in oklch, var(--warning) 40%, transparent)" },
  }[variant];
  return (
    <button className="rx-focus rx-btn" style={{ ...base, ...v, ...style }} {...p}>
      {icon && <Icon name={icon} size={size === "sm" ? 15 : 17} />}
      {children}
      {iconRight && <Icon name={iconRight} size={size === "sm" ? 15 : 17} />}
    </button>
  );
}

// ---------------------------------------------------------------------------
// LABELS / CHIPS / PILLS
// ---------------------------------------------------------------------------
function LabelCaps({ children, as, style }) {
  const Tag = as || "span";
  return <Tag style={{ font: '700 10px/1 "Saira", sans-serif', textTransform: "uppercase", letterSpacing: ".16em", color: "var(--muted)", ...style }}>{children}</Tag>;
}

function MonoChip({ children, style }) {
  return <span className="font-mono" style={{ border: "1px solid var(--border)", background: "var(--surface-secondary)", borderRadius: "var(--radius-sm)", padding: "2px 7px", fontSize: 11, fontVariantNumeric: "tabular-nums", color: "var(--foreground)", ...style }}>{children}</span>;
}

const PILL_TONE = {
  accent:  { bg: "var(--accent-soft)", fg: "var(--accent)" },
  cyan:    { bg: "oklch(80% 0.135 216 / .16)", fg: "var(--cyan)" },
  warning: { bg: "oklch(82% 0.15 78 / .16)", fg: "var(--warning)" },
  danger:  { bg: "oklch(64% 0.215 25 / .16)", fg: "var(--danger)" },
  success: { bg: "oklch(76% 0.19 150 / .16)", fg: "var(--success)" },
  muted:   { bg: "var(--surface-tertiary)", fg: "var(--muted)" },
};
function StatePill({ tone = "muted", dot, children, style }) {
  const t = PILL_TONE[tone];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, background: t.bg, color: t.fg,
      borderRadius: 99, padding: "3px 9px", font: '700 10px/1 "Saira", sans-serif', textTransform: "uppercase",
      letterSpacing: ".12em", flexShrink: 0, whiteSpace: "nowrap",
      boxShadow: tone !== "muted" ? `0 0 calc(12px * var(--glow)) ${t.bg}` : "none", ...style }}>
      {dot && <span style={{ width: 6, height: 6, borderRadius: 99, background: "currentColor",
        animation: dot === "pulse" ? "rx-pulse 1.4s ease-in-out infinite" : "none" }} />}
      {children}
    </span>
  );
}

function CountBadge({ value, tone }) {
  return <span className="font-mono" style={{ background: "var(--surface-secondary)", borderRadius: "var(--radius-sm)",
    padding: "1px 8px", fontSize: 12, lineHeight: "20px", fontVariantNumeric: "tabular-nums",
    color: tone === "success" ? "var(--success)" : "var(--foreground)" }}>{value}</span>;
}

// ---------------------------------------------------------------------------
// SECTION CARD — engraved legend over the top border (signature element)
// ---------------------------------------------------------------------------
function SectionCard({ legend, legendRight, rail, padding = "gutter", children, style }) {
  const railColor = rail === "accent" ? "var(--accent)" : rail === "warning" ? "var(--warning)" : null;
  return (
    <section style={{ position: "relative", borderRadius: "var(--radius)", border: "1px solid var(--border)",
      background: "var(--surface)", padding: padding === "gutter" ? 14 : 0,
      borderLeft: railColor ? `2px solid ${railColor}` : undefined,
      boxShadow: railColor ? `-8px 0 24px -16px ${railColor}` : "none", ...style }}>
      {legend && (
        <span className="legend-mask" style={{ position: "absolute", top: -8, left: 12, height: 16,
          display: "flex", alignItems: "center", padding: "0 6px", whiteSpace: "nowrap" }}>
          <LabelCaps>{legend}</LabelCaps>
        </span>
      )}
      {legendRight && (
        <span className="legend-mask" style={{ position: "absolute", top: -10, right: 12, height: 20,
          display: "flex", alignItems: "center", padding: "0 6px", whiteSpace: "nowrap" }}>{legendRight}</span>
      )}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// FORM FIELDS
// ---------------------------------------------------------------------------
function Field({ label, icon, type = "text", value, onChange, placeholder, rightSlot, mono, style }) {
  const [focus, setFocus] = useState(false);
  return (
    <label style={{ display: "block", ...style }}>
      {label && <div style={{ marginBottom: 6 }}><LabelCaps>{label}</LabelCaps></div>}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 13px",
        background: "var(--field-background)", borderRadius: "var(--radius-field)",
        border: `1px solid ${focus ? "var(--focus)" : "var(--field-border)"}`,
        boxShadow: focus ? "0 0 0 3px var(--accent-soft)" : "none", transition: "border-color .15s, box-shadow .15s" }}>
        {icon && <Icon name={icon} size={17} style={{ color: focus ? "var(--accent)" : "var(--muted)" }} />}
        <input type={type} value={value} placeholder={placeholder}
          onChange={(e) => onChange && onChange(e.target.value)}
          onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
          className={mono ? "font-mono" : ""}
          style={{ flex: 1, minWidth: 0, background: "transparent", border: "none", outline: "none",
            color: "var(--field-foreground)", fontSize: 14, fontFamily: mono ? '"JetBrains Mono", monospace' : "inherit" }} />
        {rightSlot}
      </div>
    </label>
  );
}

function Area({ label, value, onChange, placeholder, rows = 6, style }) {
  const [focus, setFocus] = useState(false);
  return (
    <label style={{ display: "block", ...style }}>
      {label && <div style={{ marginBottom: 6 }}><LabelCaps>{label}</LabelCaps></div>}
      <textarea value={value} placeholder={placeholder} rows={rows} className="font-mono rx-scroll"
        onChange={(e) => onChange && onChange(e.target.value)}
        onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
        style={{ width: "100%", resize: "vertical", padding: "11px 13px", background: "var(--field-background)",
          borderRadius: "var(--radius-field)", border: `1px solid ${focus ? "var(--focus)" : "var(--field-border)"}`,
          boxShadow: focus ? "0 0 0 3px var(--accent-soft)" : "none", color: "var(--field-foreground)",
          fontSize: 13, lineHeight: 1.5, outline: "none", transition: "border-color .15s, box-shadow .15s" }} />
    </label>
  );
}

function Select({ label, value, placeholder, options = [], onChange, disabled, style }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    function h(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, []);
  const sel = options.find((o) => (o.id ?? o) === value);
  return (
    <label ref={ref} style={{ display: "block", position: "relative", ...style }}>
      {label && <div style={{ marginBottom: 6 }}><LabelCaps>{label}</LabelCaps></div>}
      <button type="button" disabled={disabled} onClick={() => !disabled && setOpen((o) => !o)}
        className="rx-focus"
        style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
          padding: "11px 13px", background: "var(--field-background)", borderRadius: "var(--radius-field)",
          border: `1px solid ${open ? "var(--focus)" : "var(--field-border)"}`,
          boxShadow: open ? "0 0 0 3px var(--accent-soft)" : "none",
          color: sel ? "var(--field-foreground)" : "var(--field-placeholder)", fontSize: 14, cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.55 : 1, fontFamily: "inherit", textAlign: "left" }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sel ? (sel.label ?? sel) : placeholder}</span>
        <Icon name="chevron" size={16} style={{ color: "var(--muted)", transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
      </button>
      {open && (
        <div className="rx-scroll glow-soft" style={{ position: "absolute", zIndex: 40, top: "100%", left: 0, right: 0, marginTop: 6,
          background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius-field)",
          padding: 5, maxHeight: 240, overflowY: "auto" }}>
          {options.map((o) => {
            const oid = o.id ?? o, ol = o.label ?? o, active = oid === value;
            return (
              <button key={oid} type="button" onClick={() => { onChange && onChange(oid); setOpen(false); }}
                style={{ width: "100%", display: "flex", alignItems: "center", gap: 8, padding: "9px 10px",
                  background: active ? "var(--accent-soft)" : "transparent", border: "none", borderRadius: "var(--radius-sm)",
                  color: active ? "var(--accent)" : "var(--foreground)", fontSize: 14, cursor: "pointer", textAlign: "left",
                  fontFamily: "inherit" }}
                onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "var(--surface-secondary)"; }}
                onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}>
                {ol}{o.mono && <span className="font-mono" style={{ color: "var(--muted)", fontSize: 12 }}>{o.mono}</span>}
              </button>
            );
          })}
        </div>
      )}
    </label>
  );
}

function Checkbox({ checked, onChange, children }) {
  return (
    <label style={{ display: "inline-flex", alignItems: "center", gap: 9, cursor: "pointer", color: "var(--muted)", fontSize: 14 }}>
      <span onClick={() => onChange && onChange(!checked)} className="rx-focus"
        style={{ width: 18, height: 18, borderRadius: 5, border: `1.5px solid ${checked ? "transparent" : "var(--border-strong)"}`,
          background: checked ? "var(--brand-gradient)" : "transparent", display: "inline-flex", alignItems: "center",
          justifyContent: "center", color: "#fff", flexShrink: 0, transition: "background .15s" }}>
        {checked && <Icon name="check" size={13} />}
      </span>
      {children}
    </label>
  );
}

// ---------------------------------------------------------------------------
// PROGRESS RING — gradient stroke + neon glow
// ---------------------------------------------------------------------------
function ProgressRing({ percent = 0, sent = 0, total = 0, tone = "accent", idle }) {
  const R = 58, C = 2 * Math.PI * R;
  const off = C - (percent / 100) * C;
  const stroke = idle ? "var(--surface-tertiary)" : tone === "warning" ? "var(--warning)" : `url(#ring-grad)`;
  return (
    <div style={{ position: "relative", width: 144, height: 144, flexShrink: 0 }}>
      <svg viewBox="0 0 144 144" width={144} height={144} style={{ transform: "rotate(-90deg)" }}>
        <defs>
          <linearGradient id="ring-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--cyan)" /><stop offset="55%" stopColor="var(--accent)" /><stop offset="100%" stopColor="var(--magenta)" />
          </linearGradient>
        </defs>
        <circle cx="72" cy="72" r={R} fill="none" stroke="var(--surface-tertiary)" strokeWidth="9" />
        {!idle && (
          <circle cx="72" cy="72" r={R} fill="none" stroke={stroke} strokeWidth="9" strokeLinecap="round"
            strokeDasharray={C} strokeDashoffset={off}
            style={{ transition: "stroke-dashoffset .6s cubic-bezier(.2,.7,.2,1)",
              filter: `drop-shadow(0 0 calc(7px * var(--glow)) var(--accent))` }} />
        )}
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
        <span className="font-mono" style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-.03em", lineHeight: 1,
          fontVariantNumeric: "tabular-nums", color: idle ? "var(--muted)" : "var(--foreground)" }}>{idle ? "—" : percent + "%"}</span>
        {!idle && <span className="font-mono" style={{ marginTop: 5, fontSize: 12, color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>{sent} / {total}</span>}
      </div>
    </div>
  );
}

function Metric({ label, value, tone }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <LabelCaps style={{ letterSpacing: ".07em", whiteSpace: "nowrap" }}>{label}</LabelCaps>
      <span className="font-mono" style={{ fontSize: 17, fontWeight: 700, fontVariantNumeric: "tabular-nums",
        color: tone === "success" ? "var(--success)" : "var(--foreground)" }}>{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DATA ROW (console density)
// ---------------------------------------------------------------------------
function DataRow({ left, text, status, nueva }) {
  return (
    <div className="font-mono" style={{ display: "flex", alignItems: "flex-start", gap: 8,
      borderBottom: "1px solid var(--separator)", padding: `calc(4px * var(--density)) 12px`,
      fontSize: 11.5, lineHeight: 1.45, background: nueva ? "oklch(76% 0.19 150 / .12)" : "transparent",
      color: nueva ? "var(--success)" : "var(--foreground)" }}>
      <span style={{ flexShrink: 0, color: nueva ? "var(--success)" : "var(--muted)", fontVariantNumeric: "tabular-nums" }}>{left}</span>
      <span style={{ flex: 1, minWidth: 0, wordBreak: "break-word" }}>{text}</span>
      {nueva && <span style={{ flexShrink: 0, background: "oklch(76% 0.19 150 / .2)", borderRadius: 5, padding: "0 5px",
        fontSize: 9, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--success)" }}>nueva</span>}
      {status && <span style={{ flexShrink: 0, color: status === "ok" ? "var(--success)" : "var(--danger)" }}>{status === "ok" ? "✅" : "❌"}</span>}
    </div>
  );
}

// result panel (header + scroll list + optional export footer)
function ResultPanel({ header, count, countTone, rows, empty, exportable, maxH = 460, style }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0, borderRadius: "var(--radius)",
      border: "1px solid var(--border)", background: "var(--surface)", overflow: "hidden", ...style }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
        borderBottom: "1px solid var(--border)", padding: "10px 12px" }}>
        <LabelCaps style={{ letterSpacing: ".12em", whiteSpace: "nowrap" }}>{header}</LabelCaps>
        <CountBadge value={count} tone={countTone} />
      </div>
      <div className="rx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", maxHeight: maxH }}>
        {rows.length === 0
          ? <p style={{ padding: "16px 12px", fontSize: 13, color: "var(--muted)", margin: 0 }}>{empty}</p>
          : rows.map((r, i) => <DataRow key={r.key || i} {...r} />)}
      </div>
      {exportable && (
        <div style={{ borderTop: "1px solid var(--border)", padding: "8px 12px" }}>
          <button className="font-mono rx-focus" style={{ background: "none", border: "none", cursor: "pointer",
            color: "var(--accent)", fontSize: 11.5, display: "inline-flex", alignItems: "center", gap: 5 }}>
            <Icon name="download" size={13} /> .txt
          </button>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { Logo, Mark, Icon, Btn, LabelCaps, MonoChip, StatePill, CountBadge, SectionCard, Field, Area, Select, Checkbox, ProgressRing, Metric, DataRow, ResultPanel });
