"use client";

// External reactivation channels — the ONE panel shared by the blocked-account
// notice (login) and the /expired lockout page, so the two surfaces can't drift
// apart. Native Notice + Btn (Ranger-X handoff `ContactPanel`); one danger
// language across auth.
import { siteConfig, telegramHref } from "@/config/site";
import { Notice } from "@/components/ui/notice";
import { Btn } from "@/components/ui/btn";

// One button per configured Telegram contact. Label = the handle so the two
// are distinguishable (no role label by decision).
const CHANNELS = siteConfig.contacts.map((c) => ({
  label: `@${c.handle}`,
  href: telegramHref(c.handle),
}));

export function ContactPanel({
  message,
  className,
}: {
  message: string;
  className?: string;
}) {
  return (
    <Notice className={className} status="danger">
      <p className="m-0">{message}</p>
      <div className="mt-3 flex gap-2">
        {CHANNELS.map((channel) => (
          <Btn
            key={channel.label}
            size="sm"
            variant="secondary"
            onClick={() =>
              window.open(channel.href, "_blank", "noopener,noreferrer")
            }
          >
            {channel.label}
          </Btn>
        ))}
      </div>
    </Notice>
  );
}
