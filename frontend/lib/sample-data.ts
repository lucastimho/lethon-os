import type { MemoryShard, Tier } from "@/lib/types";

/**
 * Deterministic mock corpus for local dev before the SSE stream lands.
 * Seeded RNG so the graph layout is reproducible between renders.
 */
function seeded(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
}

const GOAL_PHRASES = [
  "planning flight itinerary",
  "debugging memory leak",
  "analyzing user feedback",
  "researching vector indexes",
  "drafting project proposal",
];

const CONTENT_TEMPLATES = [
  "User asked about {}",
  "Retrieved doc on {}",
  "Tool call result for {}",
  "LLM reasoning step: {}",
  "Web search hit: {}",
  "Summary of prior turn: {}",
];

export function makeSampleShards(count = 120, seed = 42): MemoryShard[] {
  const rand = seeded(seed);
  const now = Date.now();
  const shards: MemoryShard[] = [];

  for (let i = 0; i < count; i++) {
    const utility = Math.pow(rand(), 1.6); // skewed so most are low-utility
    const tier: Tier = utility > 0.6 ? "L1" : utility > 0.25 ? "L2" : "L3";
    const ageMinutes = Math.floor((1 - utility) * 60 * 48); // older → lower utility

    const templateIdx = Math.floor(rand() * CONTENT_TEMPLATES.length);
    const phraseIdx = Math.floor(rand() * GOAL_PHRASES.length);
    const content = CONTENT_TEMPLATES[templateIdx].replace(
      "{}",
      GOAL_PHRASES[phraseIdx],
    );

    shards.push({
      id: `shard-${i.toString().padStart(4, "0")}`,
      content,
      tier,
      utility_score: utility,
      access_count: Math.floor(rand() * 30),
      last_accessed_at: new Date(now - ageMinutes * 60_000).toISOString(),
      created_at: new Date(now - ageMinutes * 60_000 - 3600_000).toISOString(),
      goal_context: GOAL_PHRASES[phraseIdx],
    });
  }

  return shards;
}
