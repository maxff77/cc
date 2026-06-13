// Engraved-legend caps label (ui-polish-spec §2.3) — THE one tracked-caps
// style of the system (tracking-[0.1em], single source). Kills the divergent
// 0.08em/0.12em copies that lived in metric/send-form/sessions and the
// 0.12em panel-header copy that lived in response-views (now adopted here).
import clsx from "clsx";

export function LabelCaps({
  children,
  className,
  as: Tag = "span",
}: {
  children: React.ReactNode;
  className?: string;
  as?: "span" | "h2" | "label";
}) {
  return (
    <Tag
      className={clsx(
        "text-[10px] font-bold uppercase tracking-[0.1em] text-muted",
        className,
      )}
    >
      {children}
    </Tag>
  );
}
