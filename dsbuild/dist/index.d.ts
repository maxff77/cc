import type { CSSProperties, ReactNode, ButtonHTMLAttributes } from "react";

/** Icon glyph names available in the Ranger-X icon set. */
export type IconName =
  | "user" | "lock" | "eye" | "eyeOff" | "arrow" | "chevron"
  | "pause" | "play" | "stop" | "plus" | "send" | "download"
  | "sun" | "moon" | "trash" | "refresh" | "search" | "check";

export type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "success" | "warning";
export type BtnSize = "sm" | "md" | "lg";
export type PillTone = "accent" | "cyan" | "warning" | "danger" | "success" | "muted";

export interface LogoProps {
  /** Pixel height of the wordmark. */
  height?: number;
  /** Show the "CHECK" sub-label with circuit ticks. */
  sub?: boolean;
  /** Fixed gradient id (omit for a random unique id). */
  gid?: string;
}
/** Full Ranger-X Check wordmark — gradient lightning slash + Saira italic logotype. */
export declare function Logo(props: LogoProps): JSX.Element;

export interface MarkProps {
  /** Pixel height of the shield mark. */
  size?: number;
}
/** Compact shield-X mark for nav / favicon. */
export declare function Mark(props: MarkProps): JSX.Element;

export interface IconProps {
  name: IconName;
  size?: number;
  style?: CSSProperties;
}
/** Inline `currentColor` SVG icon. */
export declare function Icon(props: IconProps): JSX.Element;

export interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: BtnSize;
  /** Leading icon glyph name. */
  icon?: IconName;
  /** Trailing icon glyph name. */
  iconRight?: IconName;
  /** Stretch to full container width. */
  full?: boolean;
}
/** Primary action button. `primary` wears the brand gradient + neon glow. */
export declare function Btn(props: BtnProps): JSX.Element;

export interface LabelCapsProps {
  children?: ReactNode;
  /** Element tag to render (default `span`). */
  as?: keyof JSX.IntrinsicElements;
  style?: CSSProperties;
}
/** Uppercase tracked micro-label in Saira. */
export declare function LabelCaps(props: LabelCapsProps): JSX.Element;

export interface MonoChipProps {
  children?: ReactNode;
  style?: CSSProperties;
}
/** Small monospace chip for codes / values. */
export declare function MonoChip(props: MonoChipProps): JSX.Element;

export interface StatePillProps {
  tone?: PillTone;
  /** Show a leading dot; `"pulse"` animates it. */
  dot?: boolean | "pulse";
  children?: ReactNode;
  style?: CSSProperties;
}
/** Status pill with toned background, optional pulsing dot. */
export declare function StatePill(props: StatePillProps): JSX.Element;

export interface CountBadgeProps {
  value: ReactNode;
  /** `"success"` tints the number green. */
  tone?: "success" | string;
}
/** Monospace count badge. */
export declare function CountBadge(props: CountBadgeProps): JSX.Element;

export interface SectionCardProps {
  /** Engraved legend rendered over the top border (signature element). */
  legend?: ReactNode;
  /** Right-aligned legend slot (e.g. a pill). */
  legendRight?: ReactNode;
  /** Accent / warning left rail with matching bloom. */
  rail?: "accent" | "warning";
  /** `"gutter"` pads 14px; `"none"` removes padding. */
  padding?: "gutter" | "none" | string;
  children?: ReactNode;
  style?: CSSProperties;
}
/** Surface panel with an engraved legend over its top border. */
export declare function SectionCard(props: SectionCardProps): JSX.Element;

export interface FieldProps {
  label?: string;
  icon?: IconName;
  type?: string;
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  /** Content rendered at the field's right edge. */
  rightSlot?: ReactNode;
  /** Render the input value in JetBrains Mono. */
  mono?: boolean;
  style?: CSSProperties;
}
/** Labeled single-line input with optional leading icon + focus glow. */
export declare function Field(props: FieldProps): JSX.Element;

export interface AreaProps {
  label?: string;
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  rows?: number;
  style?: CSSProperties;
}
/** Labeled monospace textarea for pasted line input. */
export declare function Area(props: AreaProps): JSX.Element;

export interface SelectOption {
  id?: string | number;
  label?: string;
  /** Trailing monospace hint shown in the dropdown row. */
  mono?: string;
}
export interface SelectProps {
  label?: string;
  value?: string | number;
  placeholder?: string;
  options?: Array<SelectOption | string>;
  onChange?: (value: string | number) => void;
  disabled?: boolean;
  style?: CSSProperties;
}
/** Custom popover select with focus glow and keyboard-dismiss. */
export declare function Select(props: SelectProps): JSX.Element;

export interface CheckboxProps {
  checked?: boolean;
  onChange?: (checked: boolean) => void;
  children?: ReactNode;
}
/** Gradient-filled checkbox with label. */
export declare function Checkbox(props: CheckboxProps): JSX.Element;

export interface ProgressRingProps {
  /** 0–100 completion percentage. */
  percent?: number;
  sent?: number;
  total?: number;
  tone?: "accent" | "warning";
  /** Idle state — dashed muted ring, "—" readout. */
  idle?: boolean;
}
/** Circular progress ring with gradient stroke + neon glow. */
export declare function ProgressRing(props: ProgressRingProps): JSX.Element;

export interface MetricProps {
  label?: ReactNode;
  value?: ReactNode;
  /** `"success"` tints the value green. */
  tone?: "success" | string;
}
/** Label + monospace value metric block. */
export declare function Metric(props: MetricProps): JSX.Element;

export interface DataRowProps {
  /** Left gutter (timestamp or index). */
  left?: ReactNode;
  text?: ReactNode;
  /** `"ok"` → ✅, `"rejected"` → ❌. */
  status?: "ok" | "rejected";
  /** Highlight as a freshly-captured row. */
  nueva?: boolean;
}
/** Console-density data row with status glyph. */
export declare function DataRow(props: DataRowProps): JSX.Element;

export interface ResultPanelRow extends DataRowProps {
  key?: string;
}
export interface ResultPanelProps {
  header?: ReactNode;
  count?: ReactNode;
  countTone?: string;
  rows: ResultPanelRow[];
  /** Empty-state copy when `rows` is empty. */
  empty?: ReactNode;
  /** Show a `.txt` export footer. */
  exportable?: boolean;
  /** Max scroll-list height in px (default 460). */
  maxH?: number;
  style?: CSSProperties;
}
/** Result panel — header + scrolling DataRow list + optional export footer. */
export declare function ResultPanel(props: ResultPanelProps): JSX.Element;
