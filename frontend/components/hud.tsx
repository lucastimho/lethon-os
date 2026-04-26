"use client";

import * as React from "react";
import { motion, type HTMLMotionProps } from "framer-motion";
import { cn } from "@/lib/utils";

/* -----------------------------------------------------------------------------
 * HUD primitives — thin floating panels anchored to the viewport corners.
 * No glass surfaces. No drop shadows. Just a 1px hairline border on a
 * dim solid background, like a cockpit instrument that doesn't compete
 * with the canvas behind it.
 *
 * Mounted with a subtle stagger via framer-motion so the chrome feels
 * intentional rather than dumped on the page.
 * ---------------------------------------------------------------------------*/

const SPRING = { type: "spring", stiffness: 90, damping: 20 } as const;

type Anchor =
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right";

const POSITION: Record<Anchor, string> = {
  "top-left": "top-6 left-6",
  "top-right": "top-6 right-6",
  "bottom-left": "bottom-6 left-6",
  "bottom-right": "bottom-6 right-6",
};

const SLIDE_FROM: Record<Anchor, { x: number; y: number }> = {
  "top-left": { x: -8, y: -8 },
  "top-right": { x: 8, y: -8 },
  "bottom-left": { x: -8, y: 8 },
  "bottom-right": { x: 8, y: 8 },
};

type HudProps = HTMLMotionProps<"div"> & {
  anchor: Anchor;
  /** Cascade order: HUD overlays mount with a small stagger. */
  delay?: number;
};

export function Hud({
  anchor,
  delay = 0,
  className,
  children,
  ...rest
}: HudProps) {
  const slide = SLIDE_FROM[anchor];
  return (
    <motion.div
      initial={{ opacity: 0, ...slide }}
      animate={{ opacity: 1, x: 0, y: 0 }}
      transition={{ ...SPRING, delay }}
      className={cn("absolute z-10 pointer-events-auto", POSITION[anchor], className)}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

/** A single HUD row — label on the left, value (mono) on the right. */
export function HudRow({
  label,
  value,
  className,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-baseline gap-3 text-xs", className)}>
      <span className="text-muted-foreground tracking-wide uppercase">{label}</span>
      <span className="font-mono tabular-nums text-foreground">{value}</span>
    </div>
  );
}

/** Connection-status indicator. No pinging dot — a small steady marker
 *  that uses color, not motion, to convey state. */
export function StatusDot({
  state,
  label,
}: {
  state: "live" | "stale" | "offline";
  label?: string;
}) {
  const colors = {
    live: "var(--color-accent)",
    stale: "var(--color-l3)",
    offline: "var(--color-danger)",
  } as const;

  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: colors[state] }}
        aria-hidden
      />
      <span className="text-muted-foreground tracking-wide uppercase">
        {label ?? state}
      </span>
    </div>
  );
}

/** A keystroke hint chip — uses real <kbd> for accessibility. */
export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className="inline-flex items-center justify-center min-w-[1.5rem] h-5 px-1
                 rounded border border-border-strong bg-background
                 font-mono text-[10px] text-muted-foreground"
    >
      {children}
    </kbd>
  );
}
