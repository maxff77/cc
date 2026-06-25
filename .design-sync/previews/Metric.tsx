import { Metric } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, display: "flex", alignItems: "center", background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Row = () => (
  <Frame>
    <div style={{ display: "flex", gap: 36 }}>
      <Metric label="Enviadas" value="1 284" />
      <Metric label="Capturadas" value="312" tone="success" />
      <Metric label="Errores" value="7" />
    </div>
  </Frame>
);
