import { ProgressRing } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, display: "flex", gap: 28, alignItems: "center", background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Active = () => (
  <Frame>
    <ProgressRing percent={68} sent={68} total={100} />
    <ProgressRing percent={100} sent={120} total={120} />
  </Frame>
);

export const Warning = () => (
  <Frame><ProgressRing percent={42} sent={42} total={100} tone="warning" /></Frame>
);

export const Idle = () => (
  <Frame><ProgressRing idle /></Frame>
);
