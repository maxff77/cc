import { LabelCaps } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Stacked = () => (
  <Frame>
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <LabelCaps>Destino · Gate</LabelCaps>
      <LabelCaps>Respuestas · Completa</LabelCaps>
      <LabelCaps>Sesión activa</LabelCaps>
    </div>
  </Frame>
);
