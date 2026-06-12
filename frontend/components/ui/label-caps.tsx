// Engraved-legend caps label (ui-polish-spec §2.3) — THE one tracked-caps
// style of the system (tracking-[0.1em], single source). Kills the divergent
// 0.08em/0.12em copies that lived in metric/send-form/sessions.
// (response-views/response-row deliberately keep their local copies for now:
// their §3.9 restyle renders inside Envío and is deferred until story 2-2
// lands.)
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
