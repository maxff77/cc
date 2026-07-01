"use client";

import { useEffect, useState } from "react";

import { Btn } from "@/components/ui/btn";
import { Icon } from "@/components/ui/icon";
import { Notice } from "@/components/ui/notice";

// `beforeinstallprompt` isn't in the TS DOM lib yet.
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

// Time-boxed dismissal, not permanent: we store the dismiss timestamp and
// re-nudge after DISMISS_TTL_MS. This also RESETS legacy installs for free —
// the old code stored "1", which Number()s to epoch 1ms (1970), always older
// than the TTL → the banner reappears once for everyone who ever closed it.
// The browser's own install affordance stays available regardless.
const DISMISS_KEY = "rx-install-dismissed";
const DISMISS_TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

// localStorage can throw (Safari Lockdown, blocked storage, some webviews). A
// banner must never crash the cockpit, so both paths swallow failures.
function wasDismissed(): boolean {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    if (raw === null) return false;
    const at = Number(raw); // legacy "1" / junk → 1 or NaN → treated as expired
    return Number.isFinite(at) && Date.now() - at < DISMISS_TTL_MS;
  } catch {
    return false;
  }
}
function rememberDismissed(): void {
  try {
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
  } catch {
    // storage blocked — the banner just reappears next load. Acceptable.
  }
}

type Mode = "hidden" | "installable" | "ios";

function isStandalone(): boolean {
  if (window.matchMedia("(display-mode: standalone)").matches) return true;

  // iOS Safari exposes navigator.standalone (non-standard, not in TS types).
  return (
    (window.navigator as Navigator & { standalone?: boolean }).standalone ===
    true
  );
}

function isIosSafari(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  const ua = nav.userAgent;
  const isIos =
    /iPad|iPhone|iPod/.test(ua) ||
    // iPadOS 13+ masquerades as a Mac; a touch-capable "Mac" is an iPad.
    (nav.platform === "MacIntel" && nav.maxTouchPoints > 1);
  // "Add to Home Screen" exists only in real mobile Safari, which defines
  // navigator.standalone (false in a tab). In-app WKWebViews (Telegram/IG/FB)
  // and Chrome/Firefox on iOS leave it undefined — filter them out so we never
  // tell a webview user to "tap Share in Safari" where that option isn't there.
  const canAddToHomeScreen = typeof nav.standalone === "boolean";

  return isIos && canAddToHomeScreen && !/CriOS|FxiOS|EdgiOS/.test(ua);
}

// In-app PWA install nudge for the cockpit. On Chrome/Edge (desktop + Android)
// it intercepts `beforeinstallprompt` and offers a real Install button; on iOS
// Safari — which has no programmatic install — it shows the manual hint.
// Hidden when already installed or previously dismissed.
export function InstallPrompt() {
  const [mode, setMode] = useState<Mode>("hidden");
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(
    null,
  );

  useEffect(() => {
    if (isStandalone()) return;
    if (wasDismissed()) return;

    const onPrompt = (e: Event) => {
      e.preventDefault(); // suppress Chrome's mini-infobar; we render our own.
      setDeferred(e as BeforeInstallPromptEvent);
      setMode("installable");
    };
    const onInstalled = () => {
      setDeferred(null);
      setMode("hidden");
    };

    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);

    // iOS never fires beforeinstallprompt and can't be triggered → show a hint.
    if (isIosSafari()) setMode("ios");

    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
    // ponytail: if beforeinstallprompt fired before this effect mounted (rare —
    // Chrome gates it behind an engagement heuristic that lands post-mount) we
    // miss it for this load; the browser's address-bar install icon still works.
  }, []);

  if (mode === "hidden") return null;

  const dismiss = () => {
    rememberDismissed();
    setMode("hidden");
  };

  const install = async () => {
    const evt = deferred;

    if (!evt) return;
    setDeferred(null); // consume synchronously — guards against double-click.
    try {
      await evt.prompt();
      await evt.userChoice; // accepted or dismissed — retire it either way.
    } catch {
      // prompt() rejects if already consumed or the platform refuses; nothing
      // to recover — appinstalled is the source of truth for a real install.
    } finally {
      setMode("hidden");
    }
  };

  return (
    <div className="mx-auto w-full max-w-[1400px] px-[clamp(1rem,4vw,4rem)] pt-3">
      <Notice className="flex items-center gap-3" status="accent">
        <Icon className="shrink-0 text-accent" name="download" size={20} />
        <div className="min-w-0 flex-1">
          <p className="font-display font-semibold leading-tight">
            Instalá Ranger-X
          </p>
          <p className="text-xs leading-snug text-muted">
            {mode === "installable"
              ? "Abrila en su propia ventana, como una app de escritorio o móvil."
              : "Tocá Compartir en Safari y luego “Añadir a pantalla de inicio”."}
          </p>
        </div>
        {mode === "installable" && (
          <Btn icon="download" size="sm" variant="primary" onClick={install}>
            Instalar
          </Btn>
        )}
        <button
          aria-label="Cerrar"
          className="rx-focus shrink-0 rounded-md p-1 text-muted transition-colors hover:text-foreground"
          type="button"
          onClick={dismiss}
        >
          <Icon name="close" size={18} />
        </button>
      </Notice>
    </div>
  );
}
