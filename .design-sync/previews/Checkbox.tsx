import { Checkbox } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const States = () => (
  <Frame>
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Checkbox checked onChange={() => {}}>Ocultar respuestas declinadas</Checkbox>
      <Checkbox checked={false} onChange={() => {}}>Mostrar solo nuevas</Checkbox>
    </div>
  </Frame>
);
