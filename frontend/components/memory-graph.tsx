"use client";

import * as d3 from "d3";
import * as React from "react";

import { clamp } from "@/lib/utils";
import type { MemoryShard, Tier } from "@/lib/types";

/* -----------------------------------------------------------------------------
 * Live Memory Graph
 *
 * A force-directed graph where every node = one MemoryShard.
 *
 * Visual grammar:
 *   • Node radius      — scales with utility_score (0.3 → 0.9 times base)
 *   • Node opacity     — scales with utility_score (0.25 → 1.0)
 *   • Radial distance  — (1 − utility) × maxRadius. High-utility shards
 *                        sit near the center; low-utility drift to the rim.
 *   • Hue              — tier (L1 sky, L2 violet, L3 cold grey)
 *
 * Design notes:
 *   – SVG renderer chosen for the scaffold. Fine up to ~2k nodes. Past
 *     that, switch to a Canvas 2D renderer (d3 exposes the same sim).
 *   – The simulation runs while the component is mounted; we cool alpha
 *     gently on every data update so node motion is smooth instead of
 *     snapping.
 *   – Zoom/pan is on the <svg> root via d3-zoom so shards stay clickable.
 * ---------------------------------------------------------------------------*/

type SimNode = d3.SimulationNodeDatum & {
  id: string;
  utility: number;
  tier: Tier;
  label: string;
};

interface MemoryGraphProps {
  shards: MemoryShard[];
  /** Optional click-through for the manual Pin / Prune controls upstream. */
  onSelect?: (shard: MemoryShard) => void;
  className?: string;
}

const TIER_COLOR: Record<Tier, string> = {
  L1: "var(--color-l1)",
  L2: "var(--color-l2)",
  L3: "var(--color-l3)",
};

export function MemoryGraph({ shards, onSelect, className }: MemoryGraphProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const svgRef = React.useRef<SVGSVGElement>(null);
  const simRef = React.useRef<d3.Simulation<SimNode, undefined> | null>(null);
  const [size, setSize] = React.useState({ w: 800, h: 600 });

  // Index the source shards by id so we can hand real objects to callbacks
  // without round-tripping through simulation state.
  const shardIndex = React.useMemo(
    () => new Map(shards.map((s) => [s.id, s])),
    [shards],
  );

  /* ------------------------------------------------- Resize observation */
  React.useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setSize({ w: Math.max(320, rect.width), h: Math.max(320, rect.height) });
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  /* ------------------------------------------------- Simulation setup   */
  React.useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const root = svg.append("g").attr("class", "viewport");

    // Zoom & pan — keep nodes clickable by applying transform to the group.
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 4])
      .on("zoom", (e) => root.attr("transform", e.transform.toString()));
    svg.call(zoom);

    const nodeG = root.append("g").attr("class", "nodes");

    // Hot-swappable simulation — we rebuild nodes on data updates but keep
    // the sim reference alive across renders so positions persist.
    const sim = d3
      .forceSimulation<SimNode>()
      .force("charge", d3.forceManyBody<SimNode>().strength(-28))
      .force("collide", d3.forceCollide<SimNode>((d) => nodeRadius(d.utility) + 2))
      .force("center", d3.forceCenter(size.w / 2, size.h / 2))
      .alphaDecay(0.035);

    simRef.current = sim;

    sim.on("tick", () => {
      nodeG
        .selectAll<SVGGElement, SimNode>("g.node")
        .attr("transform", (d) => `translate(${d.x ?? 0}, ${d.y ?? 0})`);
    });

    return () => {
      sim.stop();
      simRef.current = null;
    };
    // Only rebuild the simulation when the viewport changes size.
  }, [size.w, size.h]);

  /* ------------------------------------------------- Data binding      */
  React.useEffect(() => {
    const sim = simRef.current;
    if (!sim || !svgRef.current) return;

    const nodes: SimNode[] = shards.map((s) => ({
      id: s.id,
      utility: clamp(s.utility_score, 0, 1),
      tier: s.tier,
      label: s.content,
    }));

    // Preserve positions across updates: copy over x/y from current nodes
    // when the id matches so the graph doesn't snap on every SSE frame.
    const prev = new Map(
      (sim.nodes() as SimNode[]).map((n) => [n.id, n]),
    );
    for (const n of nodes) {
      const prior = prev.get(n.id);
      if (prior) {
        n.x = prior.x;
        n.y = prior.y;
        n.vx = prior.vx;
        n.vy = prior.vy;
      }
    }

    sim.nodes(nodes);

    // Radial force: utility → centripetal pull. High utility → near center.
    const radius = Math.min(size.w, size.h) / 2 - 40;
    sim.force(
      "radial",
      d3
        .forceRadial<SimNode>((d) => (1 - d.utility) * radius, size.w / 2, size.h / 2)
        .strength(0.25),
    );

    /* ------ Node render ------ */
    const svg = d3.select(svgRef.current);
    const nodeG = svg.select<SVGGElement>("g.viewport g.nodes");

    const join = nodeG
      .selectAll<SVGGElement, SimNode>("g.node")
      .data(nodes, (d) => d.id);

    const enter = join
      .enter()
      .append("g")
      .attr("class", "node")
      .style("cursor", "pointer")
      .on("click", (_, d) => {
        const full = shardIndex.get(d.id);
        if (full && onSelect) onSelect(full);
      });

    enter
      .append("circle")
      .attr("r", (d) => nodeRadius(d.utility))
      .attr("fill", (d) => TIER_COLOR[d.tier])
      .attr("fill-opacity", (d) => 0.25 + d.utility * 0.75)
      .attr("stroke", (d) => TIER_COLOR[d.tier])
      .attr("stroke-opacity", 0.9)
      .attr("stroke-width", 1)
      .style("filter", (d) =>
        d.utility > 0.6
          ? `drop-shadow(0 0 ${4 + d.utility * 10}px ${TIER_COLOR[d.tier]})`
          : "none",
      );

    enter.append("title").text((d) => `${d.label}\nutility: ${d.utility.toFixed(2)}`);

    // Update existing nodes in place — this is what makes decay feel alive.
    join
      .select("circle")
      .transition()
      .duration(600)
      .attr("r", (d) => nodeRadius(d.utility))
      .attr("fill", (d) => TIER_COLOR[d.tier])
      .attr("fill-opacity", (d) => 0.25 + d.utility * 0.75)
      .attr("stroke", (d) => TIER_COLOR[d.tier]);

    join
      .select("title")
      .text((d) => `${d.label}\nutility: ${d.utility.toFixed(2)}`);

    join.exit().transition().duration(400).style("opacity", 0).remove();

    // Gentle reheat so new/changed nodes settle without jarring the rest.
    sim.alpha(0.35).restart();
  }, [shards, shardIndex, size.w, size.h, onSelect]);

  return (
    <div
      ref={containerRef}
      className={`relative h-full w-full overflow-hidden rounded-[var(--radius)] ${className ?? ""}`}
    >
      {/* Decorative starfield behind the graph — fits the cyber-glass theme. */}
      <div className="absolute inset-0 stardust opacity-60 pointer-events-none" />
      <svg
        ref={svgRef}
        width={size.w}
        height={size.h}
        className="relative block"
        role="img"
        aria-label="Semantic memory graph"
      />
      <GraphLegend />
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function nodeRadius(utility: number): number {
  // Base radius scaled by utility. Floor at 3px so low-utility nodes stay
  // visible (and clickable) as they drift to the periphery.
  return 3 + utility * 9;
}

function GraphLegend() {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 text-[11px] font-mono text-muted-foreground/80">
      {(["L1", "L2", "L3"] as const).map((tier) => (
        <div key={tier} className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: TIER_COLOR[tier] }}
          />
          <span>{tier}</span>
        </div>
      ))}
    </div>
  );
}
