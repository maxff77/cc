import { Btn } from "rangerx";

// Full-bleed dark frame — the DS is dark-principal; bleeds past the card's
// default white body so cards read as the real app surface.
const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Primary = () => (
  <Frame><Btn variant="primary" icon="send">Enviar lote</Btn></Frame>
);

export const Variants = () => (
  <Frame>
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <Btn variant="primary">Primary</Btn>
      <Btn variant="secondary">Secondary</Btn>
      <Btn variant="ghost">Ghost</Btn>
      <Btn variant="danger">Danger</Btn>
      <Btn variant="success">Success</Btn>
      <Btn variant="warning">Warning</Btn>
    </div>
  </Frame>
);

export const Sizes = () => (
  <Frame>
    <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
      <Btn size="sm">Small</Btn>
      <Btn size="md">Medium</Btn>
      <Btn size="lg">Large</Btn>
    </div>
  </Frame>
);

export const WithIcons = () => (
  <Frame>
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <Btn variant="primary" icon="send">Enviar</Btn>
      <Btn variant="secondary" iconRight="chevron">Más opciones</Btn>
      <Btn variant="danger" icon="stop">Detener</Btn>
    </div>
  </Frame>
);

export const Disabled = () => (
  <Frame><Btn variant="primary" disabled>Enviando…</Btn></Frame>
);
