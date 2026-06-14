// Inline notice / alert (replaces HeroUI <Alert>) — a tinted, token-bordered
// strip used for operation banners and lockout messages. One status per tone;
// color-mix tints keep it legible in both themes. Body is free-form children so
// callers can embed links/buttons (e.g. the WhatsApp/Telegram contact CTAs).
import clsx from "clsx";

export type NoticeStatus = "danger" | "warning" | "success" | "accent";

const TONE: Record<NoticeStatus, string> = {
  danger:
    "border-[color-mix(in_oklch,var(--danger)_35%,transparent)] bg-[color-mix(in_oklch,var(--danger)_12%,transparent)]",
  warning:
    "border-[color-mix(in_oklch,var(--warning)_35%,transparent)] bg-[color-mix(in_oklch,var(--warning)_12%,transparent)]",
  success:
    "border-[color-mix(in_oklch,var(--success)_35%,transparent)] bg-[color-mix(in_oklch,var(--success)_12%,transparent)]",
  accent:
    "border-[color-mix(in_oklch,var(--accent)_35%,transparent)] bg-[var(--accent-soft)]",
};

export function Notice({
  status = "danger",
  children,
  className,
}: {
  status?: NoticeStatus;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "rounded-[var(--radius-field)] border px-4 py-3.5 text-sm leading-relaxed text-foreground",
        TONE[status],
        className,
      )}
      role={status === "danger" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}
