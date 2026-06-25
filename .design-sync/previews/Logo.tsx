import { Logo } from "rangerx";

const Frame = ({ children, pad = 28 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, display: "flex", alignItems: "center", background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Default = () => (
  <Frame><Logo height={44} /></Frame>
);

export const Compact = () => (
  <Frame><Logo height={30} sub={false} /></Frame>
);

export const Large = () => (
  <Frame pad={36}><Logo height={68} /></Frame>
);
