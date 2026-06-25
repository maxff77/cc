import { SectionCard, StatePill, LabelCaps } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Default = () => (
  <Frame>
    <div style={{ maxWidth: 420 }}>
      <SectionCard legend="Envío">
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 14, lineHeight: 1.5 }}>
          Pega tus líneas y elige un gate para comenzar el envío al destino.
        </p>
      </SectionCard>
    </div>
  </Frame>
);

export const WithRail = () => (
  <Frame>
    <div style={{ maxWidth: 420 }}>
      <SectionCard legend="Watchdog" rail="warning" legendRight={<StatePill tone="warning" dot>Pausado</StatePill>}>
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 14, lineHeight: 1.5 }}>
          Pausa global activa por pérdida de sesión de Telegram.
        </p>
      </SectionCard>
    </div>
  </Frame>
);
