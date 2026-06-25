import { Area } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

export const Empty = () => (
  <Frame><div style={{ maxWidth: 420 }}><Area label="Líneas a enviar" placeholder="Pega aquí tus líneas — una por renglón" rows={5} /></div></Frame>
);

export const Filled = () => (
  <Frame>
    <div style={{ maxWidth: 420 }}>
      <Area label="Líneas a enviar" rows={5} onChange={() => {}}
        value={"Lorem ipsum dolor sit amet\nConsectetur adipiscing elit\nSed do eiusmod tempor incididunt\nUt enim ad minim veniam"} />
    </div>
  </Frame>
);
