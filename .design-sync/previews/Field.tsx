import { Field } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Default = () => (
  <Frame><div style={{ maxWidth: 340 }}><Field label="Correo" icon="user" placeholder="cliente@rangerx.mx" /></div></Frame>
);

export const Password = () => (
  <Frame><div style={{ maxWidth: 340 }}><Field label="Contraseña" icon="lock" type="password" placeholder="••••••••" /></div></Frame>
);

export const Mono = () => (
  <Frame><div style={{ maxWidth: 340 }}><Field label="Token de acceso" mono placeholder="rx_live_8f2a4c…" /></div></Frame>
);
