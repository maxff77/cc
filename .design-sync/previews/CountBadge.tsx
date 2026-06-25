import { CountBadge, LabelCaps } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Default = () => (
  <Frame>
    <div style={{ display: "flex", gap: 18, alignItems: "center" }}>
      <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><LabelCaps>Completa</LabelCaps><CountBadge value={128} /></span>
      <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><LabelCaps>Capturadas</LabelCaps><CountBadge value={42} tone="success" /></span>
    </div>
  </Frame>
);
