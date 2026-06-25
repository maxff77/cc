import { MonoChip } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Default = () => (
  <Frame>
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <MonoChip>/fa</MonoChip>
      <MonoChip>14:47:06</MonoChip>
      <MonoChip>#1042</MonoChip>
      <MonoChip>v3.1.0</MonoChip>
    </div>
  </Frame>
);
