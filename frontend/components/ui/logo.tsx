import Image from "next/image";
import clsx from "clsx";

// RANGER-X brand marks — the approved raster identity (horse-head + "RANGER-X
// CHECK" lockup). Two shapes:
//   • Logo — the full lockup on its light brand field. The wordmark is dark on
//     light by design, so it must keep that light background; we frame it in a
//     rounded bordered panel (border + a faint inner ring) so it reads as an
//     intentional brand card on dark surfaces and stays delineated on light
//     ones. Rendered by width — the lockup is wide, so a small fixed height
//     would make the text unreadable; the wrapper reserves the aspect ratio so
//     there is no load-time reflow.
//   • Mark — the horse head lifted off its background onto a dark brand disc,
//     so the chrome head pops at small sizes on BOTH light and dark chrome
//     (nav header, footers, favicon). Assets live in /public/brand.

interface LogoProps {
  /** Max rendered width in px (responsive: width caps here, height follows). */
  maxWidth?: number;
  /** Preload (set only where the lockup is above the fold, e.g. login/auth). */
  priority?: boolean;
  className?: string;
}

export function Logo({
  maxWidth = 340,
  priority = false,
  className,
}: LogoProps) {
  return (
    <div
      className={clsx(
        "glow-soft aspect-[1340/780] overflow-hidden rounded-2xl border border-border ring-1 ring-black/5",
        className,
      )}
      style={{ width: "100%", maxWidth }}
    >
      <Image
        alt="Ranger-X Check"
        className="block h-auto w-full"
        height={780}
        priority={priority}
        sizes={`(max-width: 480px) 90vw, ${maxWidth}px`}
        src="/brand/ranger-x-lockup.jpg"
        width={1340}
      />
    </div>
  );
}

interface MarkProps {
  /** Box size in px. */
  size?: number;
  className?: string;
}

export function Mark({ size = 30, className }: MarkProps) {
  return (
    <Image
      alt=""
      className={clsx("block rounded-[22%] ring-1 ring-white/25", className)}
      height={size}
      src="/brand/ranger-x-mark.png"
      width={size}
    />
  );
}

interface WordmarkProps {
  /** Rendered height in px (width follows the wordmark's aspect ratio). */
  height?: number;
  className?: string;
}

// "RANGER-X" wordmark lifted from the lockup. Its type is dark-on-light, so it
// rides a light plate (rounded + border) to stay legible on the dark nav.
export function Wordmark({ height = 18, className }: WordmarkProps) {
  return (
    <span
      className={clsx(
        "inline-flex h-[26px] items-center overflow-hidden rounded-[7px] border border-border px-[7px]",
        className,
      )}
      // Canvas brand plate: fixed light field so the dark-on-light wordmark stays
      // legible on the dark nav, with a faint outer ring.
      style={{ background: "#f4f4fb", boxShadow: "0 0 0 1px rgba(0,0,0,.05)" }}
    >
      <Image
        alt="Ranger-X"
        className="block"
        height={220}
        src="/brand/ranger-x-wordmark.png"
        style={{ height, width: "auto" }}
        width={1330}
      />
    </span>
  );
}
