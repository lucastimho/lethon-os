"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";
import { TIER_LABEL, type Tier } from "@/lib/types";

/**
 * Tier legend — bottom-left HUD. Shows English name as primary,
 * abbreviation in mono, and (on hover) the full description.
 *
 * Click-to-filter: tapping a tier dims all other nodes in the graph.
 * Wires up via the `activeTier` prop / `onChange` callback.
 */
interface TierLegendProps {
  counts: Record<Tier, number>;
  activeTier: Tier | "all";
  onChange: (t: Tier | "all") => void;
}

const TIERS: Tier[] = ["L0_CORE", "L1", "L2", "L3"];

export function TierLegend({ counts, activeTier, onChange }: TierLegendProps) {
  return (
    <div className="flex flex-col gap-1 min-w-[200px]">
      <div className="text-[10px] tracking-widest uppercase text-muted-foreground mb-1">
        Memory tiers
      </div>
      <button
        onClick={() => onChange("all")}
        className={cn(
          "flex items-center justify-between text-xs py-1 -mx-1 px-1 rounded transition-colors",
          activeTier === "all"
            ? "text-foreground"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <span>All</span>
        <span className="font-mono tabular-nums">
          {Object.values(counts).reduce((a, b) => a + b, 0)}
        </span>
      </button>
      {TIERS.map((t) => (
        <TierRow
          key={t}
          tier={t}
          count={counts[t]}
          active={activeTier === t}
          onClick={() => onChange(t)}
        />
      ))}
    </div>
  );
}

function TierRow({
  tier,
  count,
  active,
  onClick,
}: {
  tier: Tier;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  const [hover, setHover] = React.useState(false);
  const meta = TIER_LABEL[tier];
  const swatch = `var(--color-${tier === "L0_CORE" ? "l0" : tier.toLowerCase()})`;

  return (
    <div className="relative">
      <button
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onClick={onClick}
        className={cn(
          "w-full flex items-center justify-between gap-3 text-xs py-1 -mx-1 px-1 rounded",
          "transition-colors",
          active
            ? "text-foreground bg-white/[0.03]"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <span className="flex items-center gap-2">
          <span
            className="inline-block h-2 w-2 rounded-full shrink-0"
            style={{ background: swatch }}
            aria-hidden
          />
          <span>{meta.name}</span>
          <span className="font-mono text-[10px] text-muted-foreground/70 uppercase">
            {meta.abbrev}
          </span>
        </span>
        <span className="font-mono tabular-nums">{count}</span>
      </button>

      {/* Hover tooltip — describes the tier in plain English so first-timers
          don't need to memorize the L1/L2/L3 vocabulary. */}
      <AnimatePresence>
        {hover && (
          <motion.div
            initial={{ opacity: 0, x: -4 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -4 }}
            transition={{ duration: 0.12 }}
            className="absolute left-full top-0 ml-3 w-56 z-30
                       border border-border bg-background p-3 text-xs text-muted-foreground"
          >
            {meta.description}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
