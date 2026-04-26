"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { MagnifyingGlass } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass";
import { ArrowRight } from "@phosphor-icons/react/dist/ssr/ArrowRight";
import { cn } from "@/lib/utils";
import { Kbd } from "@/components/hud";
import type { MemoryShard } from "@/lib/types";

/* -----------------------------------------------------------------------------
 * Command Bar — `/`-triggered search palette.
 *
 * Centered overlay (the one centered element on the page; warranted because
 * it's a transient command surface, not a permanent fixture). Filters
 * memories by content, accent-keys by tier abbrev, jumps to selection.
 *
 * Keyboard contract:
 *   Enter   — open the highlighted result
 *   Esc     — close
 *   ↑ / ↓   — move highlight
 * ---------------------------------------------------------------------------*/

const SPRING = { type: "spring", stiffness: 240, damping: 24 } as const;

interface CommandBarProps {
  open: boolean;
  shards: MemoryShard[];
  onSelect: (shard: MemoryShard) => void;
  onClose: () => void;
}

export function CommandBar({ open, shards, onSelect, onClose }: CommandBarProps) {
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const results = React.useMemo(() => {
    if (!query.trim()) return shards.slice(0, 8);
    const q = query.toLowerCase();
    return shards
      .filter((s) => s.content.toLowerCase().includes(q))
      .slice(0, 8);
  }, [query, shards]);

  React.useEffect(() => {
    if (!open) {
      setQuery("");
      setActive(0);
      return;
    }
    // Defer focus until after mount.
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  // Clamp active index when results change.
  React.useEffect(() => {
    setActive((i) => Math.min(i, Math.max(0, results.length - 1)));
  }, [results.length]);

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && results[active]) {
      onSelect(results[active]);
      onClose();
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            className="fixed inset-0 z-40 bg-background/80"
            onClick={onClose}
            aria-hidden
          />
          <motion.div
            role="dialog"
            aria-modal
            aria-label="Search memories"
            initial={{ opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={SPRING}
            className="fixed left-1/2 top-[18vh] -translate-x-1/2 z-50
                       w-[min(640px,calc(100vw-3rem))]
                       border border-border-strong bg-background"
          >
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
              <MagnifyingGlass size={16} weight="regular" className="text-muted-foreground shrink-0" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleKey}
                placeholder="Search memories by content"
                className="flex-1 bg-transparent text-sm placeholder:text-muted-foreground/60
                           focus:outline-none"
              />
              <Kbd>esc</Kbd>
            </div>

            <ul className="max-h-[40vh] overflow-y-auto py-1">
              {results.length === 0 && (
                <li className="px-4 py-6 text-xs text-muted-foreground text-center">
                  No memories match &quot;{query}&quot;.
                </li>
              )}
              {results.map((s, i) => (
                <li key={s.id}>
                  <button
                    onMouseEnter={() => setActive(i)}
                    onClick={() => {
                      onSelect(s);
                      onClose();
                    }}
                    className={cn(
                      "w-full text-left px-4 py-2 flex items-center gap-3 text-xs",
                      "transition-colors",
                      i === active
                        ? "bg-white/[0.04] text-foreground"
                        : "text-muted-foreground hover:bg-white/[0.02]",
                    )}
                  >
                    <span
                      className="inline-block h-1.5 w-1.5 rounded-full shrink-0"
                      style={{
                        background: `var(--color-${s.tier === "L0_CORE" ? "l0" : s.tier.toLowerCase()})`,
                      }}
                      aria-hidden
                    />
                    <span className="flex-1 truncate">{s.content}</span>
                    <span className="font-mono tabular-nums text-[10px] text-muted-foreground/70">
                      {s.utility_score.toFixed(2)}
                    </span>
                    {i === active && (
                      <ArrowRight size={12} weight="regular" className="text-accent" />
                    )}
                  </button>
                </li>
              ))}
            </ul>

            <div className="flex items-center justify-between px-4 py-2 border-t border-border text-[10px] text-muted-foreground">
              <span className="flex items-center gap-2">
                <Kbd>↑</Kbd> <Kbd>↓</Kbd> navigate
              </span>
              <span className="flex items-center gap-2">
                <Kbd>↵</Kbd> open
              </span>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
