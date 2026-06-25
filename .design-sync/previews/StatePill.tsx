import { StatePill } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Tones = () => (
  <Frame>
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <StatePill tone="success" dot>Completado</StatePill>
      <StatePill tone="danger" dot>Error</StatePill>
      <StatePill tone="warning" dot>Pendiente</StatePill>
      <StatePill tone="cyan">Enviando</StatePill>
      <StatePill tone="accent" dot="pulse">Sesión activa</StatePill>
      <StatePill tone="muted">Inactiva</StatePill>
    </div>
  </Frame>
);
