"use client";

import * as d3 from "d3";
import * as React from "react";

import { clamp } from "@/lib/utils";
import type { MemoryShard, Tier } from "@/lib/types";

/* -----------------------------------------------------------------------------
 * Live Memory Graph — the page's hero.
 *
 * Visual grammar (no glow, no chrome):
 *   • Radius      — utility_score, range 3–11px
 *   • Fill alpha  — utility_score, range 0.18–1.0
 *   • Stroke      — high-utility nodes get a 1px ring of the tier hue;
 *                   low-utility nodes have none. Replaces the ex-glow.
 *   • Position    — radial pull: high-utility orbits the center,
 *                   low-utility drifts toward the rim.
 *   • Hue         — tier (L0 amber, L1 cyan, L2 teal, L3 grey)
 *
 * No drop-shadow filters anywhere. The glow has been removed — utility
 * is encoded via brightness and stroke instead.
 * ---------------------------------------------------------------------------*/

type SimNode = d3.SimulationNodeDatum & {
  id: string;
  utility: number;
  tier: Tier;
  label: string;
  selected: boolean;
};

interface MemoryGraphProps {
  shards: MemoryShard[];
  selectedId?: string | null;
  onSelect?: (shard: MemoryShard) => void;
  className?: string;
}

const TIER_COLOR: Record<Tier, string> = {
  L0_CORE: "var(--color-l0)",
  L1: "var(--color-l1)",
  L2: "var(--color-l2)",
  L3: "var(--color-l3)",
};

export function MemoryGraph({
  shards,
  selectedId,
  onSelect,
  className,
}: MemoryGraphProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const svgRef = React.useRef<SVGSVGElement>(null);
  const simRef = React.useRef<d3.Simulation<SimNode, undefined> | null>(null);
  const [size, setSize] = React.useState({ w: 800, h: 600 });

  const shardIndex = React.useMemo(
    () => new Map(shards.map((s) => [s.id, s])),
    [shards],
  );

  /* ------------------------------------------------- Resize observer  */
  React.useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setSize({ w: Math.max(320, rect.width), h: Math.max(320, rect.height) });
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  /* ------------------------------------------------- Simulation setup */
  React.useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const root = svg.append("g").attr("class", "viewport");

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 4])
      .on("zoom", (e) => root.attr("transform", e.transform.toString()));
    svg.call(zoom);

    root.append("g").attr("class", "nodes");

    const sim = d3
      .forceSimulation<SimNode>()
      .force("charge", d3.forceManyBody<SimNode>().strength(-22))
      .force("collide", d3.forceCollide<SimNode>((d) => nodeRadius(d.utility) + 1.5))
      .force("center", d3.forceCenter(size.w / 2, size.h / 2))
      .alphaDecay(0.035);

    simRef.current = sim;

    sim.on("tick", () => {
      root
        .select<SVGGElement>("g.nodes")
        .selectAll<SVGGElement, SimNode>("g.node")
        .attr("transform", (d) => `translate(${d.x ?? 0}, ${d.y ?? 0})`);
    });

    return () => {
      sim.stop();
      simRef.current = null;
    };
  }, [size.w, size.h]);

  /* ------------------------------------------------- Data binding    */
  React.useEffect(() => {
    const sim = simRef.current;
    if (!sim || !svgRef.current) return;

    const nodes: SimNode[] = shards.map((s) => ({
      id: s.id,
      utility: clamp(s.utility_score, 0, 1),
      tier: s.tier,
      label: s.content,
      selected: s.id === selectedId,
    }));

    // Preserve positions across data updates so the graph breathes.
    const prev = new Map((sim.nodes() as SimNode[]).map((n) => [n.id, n]));
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

    // Radial pull: utility maps to centripetal distance.
    const radius = Math.min(size.w, size.h) / 2 - 60;
    sim.force(
      "radial",
      d3
        .forceRadial<SimNode>(
          (d) => (1 - d.utility) * radius,
          size.w / 2,
          size.h / 2,
        )
        .strength(0.22),
    );

    /* ------ Render ------ */
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
      .attr("fill-opacity", (d) => 0.18 + d.utility * 0.82)
      .attr("stroke", (d) => TIER_COLOR[d.tier])
      .attr("stroke-opacity", (d) => (d.utility > 0.55 ? 0.9 : 0))
      .attr("stroke-width", (d) => (d.selected ? 2 : 1));

    enter.append("title").text((d) =>
      `${d.label}\nutility ${d.utility.toFixed(2)} · tier ${d.tier}`,
    );

    join
      .select("circle")
      .transition()
      .duration(550)
      .ease(d3.easeCubicOut)
      .attr("r", (d) => nodeRadius(d.utility) * (d.selected ? 1.4 : 1))
      .attr("fill", (d) => TIER_COLOR[d.tier])
      .attr("fill-opacity", (d) => 0.18 + d.utility * 0.82)
      .attr("stroke", (d) =>
        d.selected ? "var(--color-foreground)" : TIER_COLOR[d.tier],
      )
      .attr("stroke-opacity", (d) =>
        d.selected ? 1 : d.utility > 0.55 ? 0.9 : 0,
      )
      .attr("stroke-width", (d) => (d.selected ? 2 : 1));

    join
      .select("title")
      .text((d) =>
        `${d.label}\nutility ${d.utility.toFixed(2)} · tier ${d.tier}`,
      );

    join.exit().transition().duration(400).style("opacity", 0).remove();

    sim.alpha(0.32).restart();
  }, [shards, shardIndex, size.w, size.h, onSelect, selectedId]);

  return (
    <div
      ref={containerRef}
      className={`relative h-full w-full ${className ?? ""}`}
    >
      <svg
        ref={svgRef}
        width={size.w}
        height={size.h}
        className="block"
        role="img"
        aria-label="Semantic memory graph — node position and brightness encode utility score"
      />
    </div>
  );
}

function nodeRadius(utility: number): number {
  // Floor at 3px so low-utility nodes stay clickable.
  return 3 + utility * 8;
}
