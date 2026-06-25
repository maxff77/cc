import { Icon } from "rangerx";

const Frame = ({ children, pad = 24 }: any) => (
  <div style={{ margin: -24, padding: pad, minHeight: 150, background: "var(--background)", color: "var(--foreground)", fontFamily: '"Public Sans", system-ui, sans-serif' }}>
    {children}
  </div>
);

const NAMES = ["user", "lock", "eye", "eyeOff", "arrow", "chevron", "pause", "play", "stop", "plus", "send", "download", "sun", "moon", "trash", "refresh", "search", "check"] as const;

export const AllGlyphs = () => (
  <Frame>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(9, 1fr)", gap: 18, color: "var(--foreground)" }}>
      {NAMES.map((n) => <Icon key={n} name={n} size={22} />)}
    </div>
  </Frame>
);

export const Sizes = () => (
  <Frame>
    <div style={{ display: "flex", gap: 16, alignItems: "center", color: "var(--accent)" }}>
      <Icon name="send" size={16} />
      <Icon name="send" size={22} />
      <Icon name="send" size={30} />
    </div>
  </Frame>
);
