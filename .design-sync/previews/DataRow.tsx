import { DataRow } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Console = () => (
  <Frame>
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", background: "var(--surface)" }}>
      <DataRow left="14:47:06" text="Lorem ipsum dolor sit amet consectetur adipiscing" status="ok" />
      <DataRow left="14:47:17" text="Sed do eiusmod tempor incididunt ut labore" status="rejected" />
      <DataRow left="14:47:28" text="Ut enim ad minim veniam quis nostrud exercitation" status="ok" />
      <DataRow left="14:47:39" text="Duis aute irure dolor in reprehenderit voluptate" status="ok" nueva />
    </div>
  </Frame>
);
