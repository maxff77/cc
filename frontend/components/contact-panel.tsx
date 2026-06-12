"use client";

import { Alert, Button } from "@heroui/react";

import { siteConfig } from "@/config/site";

// External reactivation channels — the ONE panel shared by the blocked-account
// notice (login) and the /expired lockout page, so the two surfaces can't
// drift apart when the placeholder links or styling change. Restyled over
// HeroUI Alert (ui-polish-spec §3.2): one danger language across auth.
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
    <Alert className={className} status="danger">
      <Alert.Content>
        <Alert.Description>{message}</Alert.Description>
        <div className="mt-3 flex gap-2">
          {CHANNELS.map((channel) => (
            <Button
              key={channel.label}
              size="sm"
              variant="secondary"
              onPress={() =>
                window.open(channel.href, "_blank", "noopener,noreferrer")
              }
            >
              {channel.label}
            </Button>
          ))}
        </div>
      </Alert.Content>
    </Alert>
  );
}
