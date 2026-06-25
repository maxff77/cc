import { ResultPanel } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

const ROWS = [
  { key: "1", left: "14:47:06", text: "Lorem ipsum dolor sit amet consectetur", status: "ok" as const },
  { key: "2", left: "14:47:17", text: "Sed do eiusmod tempor incididunt labore", status: "rejected" as const },
  { key: "3", left: "14:47:28", text: "Ut enim ad minim veniam quis nostrud", status: "ok" as const },
  { key: "4", left: "14:47:39", text: "Duis aute irure dolor in reprehenderit", status: "ok" as const, nueva: true },
];

export const Completa = () => (
  <Frame>
    <div style={{ maxWidth: 460 }}>
      <ResultPanel header="Completa" count={4} rows={ROWS} empty="Sin respuestas aún" exportable />
    </div>
  </Frame>
);

export const Empty = () => (
  <Frame>
    <div style={{ maxWidth: 460 }}>
      <ResultPanel header="Filtrada" count={0} rows={[]} empty="Sin capturas todavía" />
    </div>
  </Frame>
);
