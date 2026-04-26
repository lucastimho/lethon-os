/**
 * Wire-format mirror of the Python `MemoryShard`. Keep in sync with
 * `lethon_os/schemas.py` — if you add a field there, add it here too.
 */
export type Tier = "L0_CORE" | "L1" | "L2" | "L3";

/** UI-friendly tier names + descriptions. Single source of truth for vocab. */
export const TIER_LABEL: Record<Tier, { name: string; abbrev: string; description: string }> = {
  L0_CORE: {
    name: "Constitution",
    abbrev: "L0",
    description: "Immutable safety rules. Never pruned, never demoted.",
  },
  L1: {
    name: "Working",
    abbrev: "L1",
    description: "Hot context. Sub-10ms reads. Decays over time.",
  },
  L2: {
    name: "Episodic",
    abbrev: "L2",
    description: "Vector-searchable warm memory. Promoted on access.",
  },
  L3: {
    name: "Archive",
    abbrev: "L3",
    description: "Compressed cold storage. Auditable, restorable on lookup.",
  },
};

export interface MemoryShard {
  id: string;
  content: string;
  tier: Tier;
  utility_score: number;
  access_count: number;
  last_accessed_at: string; // ISO 8601
  created_at: string;
  goal_context: string | null;
  metadata?: Record<string, unknown>;
}

/**
 * Derived link between shards — e.g. semantic neighbors surfaced by the
 * backend. For the scaffold we don't render links yet; the type is here
 * so the D3 component can adopt it without a second refactor.
 */
export interface ShardLink {
  source: string; // shard id
  target: string;
  weight: number; // cosine sim, 0..1
}

/** SSE envelope streamed from the backend at `/api/stream`. */
export type StreamEvent =
  | { type: "snapshot"; shards: MemoryShard[] }
  | { type: "upsert"; shard: MemoryShard }
  | { type: "demote"; id: string; from: Tier; to: Tier }
  | { type: "archive"; id: string }
  | { type: "prune_stats"; scanned: number; demoted_l1_l2: number; demoted_l2_l3: number; wall_ms: number };
