"use client";

import * as React from "react";

import { MemoryGraph } from "@/components/memory-graph";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { makeSampleShards } from "@/lib/sample-data";
import type { MemoryShard, Tier } from "@/lib/types";

/* -----------------------------------------------------------------------------
 * Dashboard layout
 *
 * Three-column grid on wide viewports:
 *
 *   ┌──────────────────────────────┬─────────────────────┐
 *   │                              │  Tier overview      │
 *   │     Live Memory Graph        │  (L1 / L2 / L3)     │
 *   │     (D3 force-directed)      │                     │
 *   │                              ├─────────────────────┤
 *   │                              │  Utility decay      │
 *   │                              │  (Recharts) [todo]  │
 *   └──────────────────────────────┴─────────────────────┘
 *
 * The SSE hook, Recharts chart, and virtualised tier lists land in
 * follow-up commits — see the TODO markers.
 * ---------------------------------------------------------------------------*/

export default function DashboardPage() {
  // TODO(sse): replace with useMemoryStream(). The sample corpus is
  // deterministic so the layout is reviewable before the backend lands.
  const [shards, setShards] = React.useState<MemoryShard[]>(() =>
    makeSampleShards(120),
  );
  const [selected, setSelected] = React.useState<MemoryShard | null>(null);
  const [activeTier, setActiveTier] = React.useState<Tier>("L1");

  const counts = React.useMemo(
    () => ({
      L1: shards.filter((s) => s.tier === "L1").length,
      L2: shards.filter((s) => s.tier === "L2").length,
      L3: shards.filter((s) => s.tier === "L3").length,
    }),
    [shards],
  );

  // Quick local animation until SSE is wired — nudges utilities slightly so
  // the graph visibly breathes. Delete once useMemoryStream is in place.
  React.useEffect(() => {
    const id = setInterval(() => {
      setShards((prev) =>
        prev.map((s) => ({
          ...s,
          utility_score: Math.max(
            0,
            Math.min(1, s.utility_score + (Math.random() - 0.52) * 0.04),
          ),
        })),
      );
    }, 1200);
    return () => clearInterval(id);
  }, []);

  return (
    <main className="flex-1 p-5 lg:p-8">
      <Header />

      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
        {/* ---------- Memory graph ---------- */}
        <Card className="min-h-[640px] flex flex-col overflow-hidden">
          <CardHeader>
            <div className="flex items-start justify-between">
              <div>
                <CardTitle>Semantic memory map</CardTitle>
                <CardDescription>
                  {shards.length} shards — position encodes utility, color
                  encodes tier
                </CardDescription>
              </div>
              <LiveBadge />
            </div>
          </CardHeader>
          <CardContent className="flex-1 p-0 pb-0">
            <div className="h-full w-full px-3 pb-3">
              <MemoryGraph shards={shards} onSelect={setSelected} />
            </div>
          </CardContent>
        </Card>

        {/* ---------- Right column ---------- */}
        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader>
              <CardTitle>Context tiers</CardTitle>
              <CardDescription>L1 hot, L2 warm, L3 archived</CardDescription>
            </CardHeader>
            <CardContent>
              <TierPills
                counts={counts}
                active={activeTier}
                onChange={setActiveTier}
              />
              <TierPreview shards={shards} tier={activeTier} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Utility decay</CardTitle>
              <CardDescription>
                Avg L1 utility, rolling 5 min — Recharts chart in next commit
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-32 rounded-md border border-dashed border-border/60 flex items-center justify-center text-xs text-muted-foreground/80">
                Recharts &lt;LineChart /&gt; placeholder
              </div>
            </CardContent>
          </Card>

          <SelectedShard shard={selected} onClear={() => setSelected(null)} />
        </div>
      </div>
    </main>
  );
}

/* -------------------------------------------------------------------------- */

function Header() {
  return (
    <header className="flex items-end justify-between">
      <div>
        <div className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
          Lethon-Vision
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Agent Memory Observatory
        </h1>
      </div>
      <div className="hidden md:flex items-center gap-2">
        <Button variant="outline" size="sm">
          Time travel
        </Button>
        <Button variant="default" size="sm">
          Pause stream
        </Button>
      </div>
    </header>
  );
}

function LiveBadge() {
  return (
    <div className="flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full rounded-full bg-[var(--color-accent)] opacity-60 animate-ping" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--color-accent)]" />
      </span>
      LIVE
    </div>
  );
}

function TierPills({
  counts,
  active,
  onChange,
}: {
  counts: Record<Tier, number>;
  active: Tier;
  onChange: (t: Tier) => void;
}) {
  const tiers: Tier[] = ["L1", "L2", "L3"];
  return (
    <div className="flex gap-1.5 mb-3">
      {tiers.map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={`flex-1 glass-subtle rounded-md px-3 py-2 text-xs font-medium transition
            ${
              active === t
                ? "border-[var(--color-accent)]/60 shadow-[inset_0_0_0_1px_var(--color-accent)]"
                : "hover:border-border"
            }`}
        >
          <div className="flex items-center justify-between">
            <span className="font-mono">{t}</span>
            <span className="text-muted-foreground">{counts[t]}</span>
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground uppercase tracking-wide">
            {t === "L1" ? "working" : t === "L2" ? "episodic" : "archive"}
          </div>
        </button>
      ))}
    </div>
  );
}

function TierPreview({ shards, tier }: { shards: MemoryShard[]; tier: Tier }) {
  const filtered = React.useMemo(
    () =>
      shards
        .filter((s) => s.tier === tier)
        .sort((a, b) => b.utility_score - a.utility_score)
        .slice(0, 20),
    [shards, tier],
  );

  // TODO(virtual): swap for TanStack Virtual once the list can grow unbounded.
  return (
    <div className="flex flex-col gap-1 max-h-64 overflow-y-auto pr-1">
      {filtered.length === 0 && (
        <div className="text-xs text-muted-foreground/80 py-4 text-center">
          No shards in {tier} yet
        </div>
      )}
      {filtered.map((s) => (
        <div
          key={s.id}
          className="glass-subtle rounded-md px-3 py-2 text-xs flex items-center gap-2"
        >
          <span
            className="inline-block h-1.5 w-1.5 rounded-full shrink-0"
            style={{
              background: `var(--color-${tier.toLowerCase()})`,
              opacity: 0.4 + s.utility_score * 0.6,
            }}
          />
          <span className="truncate flex-1">{s.content}</span>
          <span className="font-mono text-muted-foreground shrink-0">
            {s.utility_score.toFixed(2)}
          </span>
        </div>
      ))}
    </div>
  );
}

function SelectedShard({
  shard,
  onClear,
}: {
  shard: MemoryShard | null;
  onClear: () => void;
}) {
  if (!shard) return null;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div>
            <CardTitle>Selected shard</CardTitle>
            <CardDescription className="font-mono">{shard.id}</CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={onClear}>
            ×
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm">{shard.content}</p>
        <div className="flex gap-2 text-[11px] font-mono text-muted-foreground">
          <span>U {shard.utility_score.toFixed(3)}</span>
          <span>·</span>
          <span>tier {shard.tier}</span>
          <span>·</span>
          <span>{shard.access_count} reads</span>
        </div>
        <div className="flex gap-2 pt-1">
          <Button size="sm" variant="outline" className="flex-1">
            Pin
          </Button>
          <Button size="sm" variant="danger" className="flex-1">
            Prune
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
