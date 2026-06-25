import { Select } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

const GATES = [
  { id: "1", label: "Flujo Alpha", mono: "/fa" },
  { id: "2", label: "Flujo Beta", mono: "/fb" },
  { id: "3", label: "Flujo Gamma", mono: "/fg" },
];

export const Selected = () => (
  <Frame><div style={{ maxWidth: 340 }}><Select label="Gate" placeholder="Elige un gate…" options={GATES} value="2" onChange={() => {}} /></div></Frame>
);

export const Placeholder = () => (
  <Frame><div style={{ maxWidth: 340 }}><Select label="Categoría" placeholder="Selecciona una categoría…" options={[{ id: "a", label: "Categoría A" }, { id: "b", label: "Categoría B" }]} /></div></Frame>
);
