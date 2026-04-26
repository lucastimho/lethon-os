"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Command } from "@phosphor-icons/react/dist/ssr/Command";
import { Question } from "@phosphor-icons/react/dist/ssr/Question";

import { MemoryGraph } from "@/components/memory-graph";
import { CommandBar } from "@/components/command-bar";
import { ShardDetail } from "@/components/shard-detail";
import { TierLegend } from "@/components/tier-legend";
import { Hud, HudRow, Kbd, StatusDot } from "@/components/hud";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { makeSampleShards } from "@/lib/sample-data";
import { TIER_LABEL, type MemoryShard, type Tier } from "@/lib/types";
import { cn } from "@/lib/utils";

/* -----------------------------------------------------------------------------
 * Lethon-Vision dashboard.
 *
 * Layout philosophy: the graph IS the page.
 *   • Full-viewport memory graph as the canvas.
 *   • Four HUD overlays anchored to the corners — title, status,
 *     tier legend, command hint.
 *   • Slide-in shard detail on the right when a node is selected.
 *   • Centered command palette on `/` keypress.
 *
 * No glass surfaces. No starfield. No glow shadows. The sci-fi mood is
 * earned through restraint: dim solid background, single accent color,
 * mono numerals, and a graph that breathes via real physics.
 * ---------------------------------------------------------------------------*/

type LoadState = "loading" | "ready" | "empty" | "error";

export default function DashboardPage() {
  // TODO(sse): swap for useMemoryStream() once /api/stream is live.
  const [shards, setShards] = React.useState<MemoryShard[]>(() =>
    makeSampleShards(120),
  );
  const [loadState, setLoadState] = React.useState<LoadState>("ready");

  const [selected, setSelected] = React.useState<MemoryShard | null>(null);
  const [activeTier, setActiveTier] = React.useState<Tier | "all">("all");
  const [pinned, setPinned] = React.useState<Set<string>>(new Set());
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [helpOpen, setHelpOpen] = React.useState(false);

  /* ------ Derived state ------ */

  const counts = React.useMemo(
    () =>
      shards.reduce(
        (acc, s) => ({ ...acc, [s.tier]: (acc[s.tier] ?? 0) + 1 }),
        { L0_CORE: 0, L1: 0, L2: 0, L3: 0 } as Record<Tier, number>,
      ),
    [shards],
  );

  const visibleShards = React.useMemo(
    () => (activeTier === "all" ? shards : shards.filter((s) => s.tier === activeTier)),
    [shards, activeTier],
  );

  /* ------ Sample-data jitter (delete once SSE is wired) ------ */
  React.useEffect(() => {
    if (loadState !== "ready") return;
    const id = window.setInterval(() => {
      setShards((prev) =>
        prev.map((s) => {
          // Pinned + L0 shards never decay.
          if (pinned.has(s.id) || s.tier === "L0_CORE") return s;
          const next = s.utility_score + (Math.random() - 0.52) * 0.04;
          return {
            ...s,
            utility_score: Math.max(0, Math.min(1, next)),
          };
        }),
      );
    }, 1400);
    return () => window.clearInterval(id);
  }, [pinned, loadState]);

  /* ------ Mutations ------ */

  function togglePin(id: string) {
    setPinned((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function prune(id: string) {
    setShards((prev) =>
      prev.map((s) => (s.id === id ? { ...s, tier: "L3" } : s)),
    );
    if (selected?.id === id) setSelected(null);
  }

  /* ------ Keyboard shortcuts ------ */

  useKeyboardShortcuts([
    { key: "/", handler: (e) => { e.preventDefault(); setPaletteOpen(true); } },
    { key: "?", shift: true, handler: () => setHelpOpen((v) => !v) },
    { key: "Escape", alwaysFire: true, handler: () => {
      if (paletteOpen) setPaletteOpen(false);
      else if (helpOpen) setHelpOpen(false);
      else if (selected) setSelected(null);
    } },
    { key: "p", handler: () => { if (selected) togglePin(selected.id); } },
  ]);

  /* ------ Render: state-aware ------ */

  if (loadState === "loading") return <LoadingState />;
  if (loadState === "error") return <ErrorState onRetry={() => setLoadState("ready")} />;
  if (loadState === "empty" || shards.length === 0) return <EmptyState />;

  return (
    <main className="relative h-[100dvh] w-full overflow-hidden">
      {/* Full-viewport canvas — the graph is the hero. */}
      <div className="absolute inset-0">
        <MemoryGraph
          shards={visibleShards}
          selectedId={selected?.id ?? null}
          onSelect={setSelected}
        />
      </div>

      {/* HUD overlays — corner-anchored, hairline-bordered, no fills. */}

      <Hud anchor="top-left" delay={0.05}>
        <div className="flex flex-col gap-2 max-w-[320px]">
          <div className="flex items-center gap-2">
            <span className="text-[10px] tracking-[0.3em] uppercase text-muted-foreground">
              Lethon-Vision
            </span>
          </div>
          <h1 className="text-base font-medium tracking-tight text-foreground">
            Agent memory observatory
          </h1>
          <p className="text-xs text-muted-foreground/80 leading-relaxed">
            Live view of {shards.length} memories.{" "}
            Position is utility, color is tier.
          </p>
        </div>
      </Hud>

      <Hud anchor="top-right" delay={0.1}>
        <div className="flex flex-col items-end gap-2 min-w-[220px]">
          <StatusDot state="live" label="connected" />
          <div className="flex flex-col items-end gap-1.5">
            <HudRow label="Pruner lag" value="6.2s" />
            <HudRow label="Pinned" value={pinned.size} />
          </div>
        </div>
      </Hud>

      <Hud anchor="bottom-left" delay={0.15}>
        <TierLegend
          counts={counts}
          activeTier={activeTier}
          onChange={setActiveTier}
        />
      </Hud>

      <Hud anchor="bottom-right" delay={0.2}>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <button
            onClick={() => setPaletteOpen(true)}
            className="flex items-center gap-2 hover:text-foreground transition-colors"
          >
            <Command size={12} weight="regular" />
            <span>Search</span>
            <Kbd>/</Kbd>
          </button>
          <span className="h-3 w-px bg-border" aria-hidden />
          <button
            onClick={() => setHelpOpen(true)}
            className="flex items-center gap-2 hover:text-foreground transition-colors"
          >
            <Question size={12} weight="regular" />
            <span>Shortcuts</span>
            <Kbd>?</Kbd>
          </button>
        </div>
      </Hud>

      <ShardDetail
        shard={selected}
        pinned={pinned}
        onClose={() => setSelected(null)}
        onTogglePin={togglePin}
        onConfirmPrune={prune}
      />

      <CommandBar
        open={paletteOpen}
        shards={shards}
        onSelect={setSelected}
        onClose={() => setPaletteOpen(false)}
      />

      <HelpOverlay open={helpOpen} onClose={() => setHelpOpen(false)} />
    </main>
  );
}

/* -----------------------------------------------------------------------------
 * State variants
 * ---------------------------------------------------------------------------*/

function LoadingState() {
  return (
    <main className="h-[100dvh] w-full grid place-items-center">
      <div className="flex flex-col items-center gap-4">
        <div className="relative h-32 w-32">
          {/* Skeletal nodes appearing — same layout silhouette as the real
              graph, so the page doesn't reflow when data arrives. */}
          {Array.from({ length: 9 }).map((_, i) => (
            <span
              key={i}
              className="absolute h-2 w-2 rounded-full skeleton"
              style={{
                top: `${50 + Math.cos(i * 0.7) * 35}%`,
                left: `${50 + Math.sin(i * 0.7) * 35}%`,
                animationDelay: `${i * 120}ms`,
              }}
            />
          ))}
        </div>
        <p className="text-xs text-muted-foreground tracking-wide uppercase">
          Connecting to controller
        </p>
      </div>
    </main>
  );
}

function EmptyState() {
  return (
    <main className="h-[100dvh] w-full grid place-items-center px-6">
      <div className="flex flex-col items-center gap-5 max-w-md text-center">
        <div className="h-12 w-12 rounded-full border border-border-strong" aria-hidden />
        <div className="flex flex-col gap-2">
          <h2 className="text-base font-medium tracking-tight text-foreground">
            No memories yet
          </h2>
          <p className="text-sm text-muted-foreground leading-relaxed">
            Run an agent or write to{" "}
            <code className="font-mono text-foreground/80">/api/shards</code>{" "}
            to populate the graph. The first memory will appear at the center.
          </p>
        </div>
      </div>
    </main>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <main className="h-[100dvh] w-full grid place-items-center px-6">
      <div className="flex flex-col items-center gap-5 max-w-md text-center">
        <div
          className="h-12 w-12 rounded-full border-2 border-[var(--color-danger)]/60"
          aria-hidden
        />
        <div className="flex flex-col gap-2">
          <h2 className="text-base font-medium tracking-tight text-foreground">
            Lost connection to the controller
          </h2>
          <p className="text-sm text-muted-foreground leading-relaxed">
            The SSE stream from{" "}
            <code className="font-mono text-foreground/80">/api/stream</code>{" "}
            stopped responding. Check that the FastAPI gateway is running on
            port 8000.
          </p>
        </div>
        <button
          onClick={onRetry}
          className="text-xs uppercase tracking-wider text-accent hover:text-foreground
                     transition-colors active:translate-y-[1px]"
        >
          Retry
        </button>
      </div>
    </main>
  );
}

/* -----------------------------------------------------------------------------
 * Help overlay — keyboard reference. Triggered by `?`.
 * ---------------------------------------------------------------------------*/

function HelpOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
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
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 200, damping: 22 }}
            className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50
                       border border-border-strong bg-background
                       w-[min(420px,calc(100vw-3rem))] p-5"
            role="dialog"
            aria-modal
            aria-label="Keyboard shortcuts"
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium tracking-tight">Keyboard shortcuts</h3>
              <Kbd>esc</Kbd>
            </div>
            <ul className="flex flex-col gap-2 text-xs">
              {SHORTCUTS.map(({ keys, label }) => (
                <li key={label} className="flex items-center justify-between">
                  <span className="text-muted-foreground">{label}</span>
                  <span className="flex items-center gap-1">
                    {keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
            <div className="mt-5 pt-4 border-t border-border">
              <h4 className="text-[10px] tracking-widest uppercase text-muted-foreground mb-2">
                Memory tiers
              </h4>
              <ul className="flex flex-col gap-2 text-xs">
                {(["L0_CORE", "L1", "L2", "L3"] as const).map((t) => (
                  <li key={t} className={cn("flex items-baseline justify-between gap-3")}>
                    <span className="text-foreground">{TIER_LABEL[t].name}</span>
                    <span className="text-muted-foreground/80 leading-snug text-right max-w-[260px]">
                      {TIER_LABEL[t].description}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

const SHORTCUTS = [
  { keys: ["/"], label: "Search memories" },
  { keys: ["?"], label: "Toggle this help" },
  { keys: ["P"], label: "Pin selected" },
  { keys: ["esc"], label: "Close panel / palette" },
];
