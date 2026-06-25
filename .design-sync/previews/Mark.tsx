import { Mark } from "rangerx";

const Frame = ({ children, pad = 28 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, display: "flex", alignItems: "center", background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Sizes = () => (
  <Frame>
    <div style={{ display: "flex", gap: 22, alignItems: "center" }}>
      <Mark size={28} />
      <Mark size={40} />
      <Mark size={56} />
    </div>
  </Frame>
);
