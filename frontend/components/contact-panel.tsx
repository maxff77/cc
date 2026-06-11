"use client";

import { Button } from "@heroui/react";

import { siteConfig } from "@/config/site";

// External reactivation channels — the ONE panel shared by the blocked-account
// notice (login) and the /expired lockout page, so the two surfaces can't
// drift apart when the placeholder links or styling change.
const CHANNELS = [
  { label: "WhatsApp", href: siteConfig.contact.whatsapp },
  { label: "Telegram", href: siteConfig.contact.telegram },
] as const;

export function ContactPanel({
  message,
  className,
}: {
  message: string;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col gap-3 rounded-lg border border-danger/40 bg-danger/10 p-4 ${className ?? ""}`.trimEnd()}
    >
      <p className="text-sm">{message}</p>
      <div className="flex gap-2">
        {CHANNELS.map((channel) => (
          <Button
            key={channel.label}
            variant="secondary"
            onPress={() =>
              window.open(channel.href, "_blank", "noopener,noreferrer")
            }
          >
            {channel.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
