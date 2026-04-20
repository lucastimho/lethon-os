# Lethon-OS — Memory Controller Architecture

The Memory Controller is the core subsystem of Lethon-OS. It manages the
lifecycle of *memory shards* across three tiers, scoring each shard by a
time-varying **Utility** function and actively pruning "cold" shards to
prevent context-window entropy.

## 1. System Diagram

```
                        ┌─────────────────────────────┐
                        │      Agent (LangGraph)      │
                        │   stateless reasoning loop  │
                        └──────────────┬──────────────┘
                                       │  async
                     retrieve(query, goal, top_k)
                                       │
                        ┌──────────────▼──────────────┐
                        │      MemoryController       │
                        │  (Cache-Aside orchestrator) │
                        └──┬──────────┬──────────┬────┘
                           │ L1 hit   │ L2 hit   │ L3 restore
                           │ <10ms    │ ~30ms    │ ~150ms
              ┌────────────▼──┐   ┌───▼─────┐  ┌─▼────────────┐
              │     Redis     │   │ Qdrant  │  │   SQLite     │
              │   L1 Hot      │   │ L2 Warm │  │  L3 Archive  │
              │ shard:{id}    │   │ vector  │  │ gzip'd JSON  │
              │ zset by util  │   │ search  │  │ cold store   │
              └───────┬───────┘   └────┬────┘  └──────┬───────┘
                      │                │              ▲
                      │                │              │ demote
                      │   demote       │   demote     │
                      └────────────────┴──────────────┘
                                       ▲
                                       │
                        ┌──────────────┴──────────────┐
                        │     Utility Pruner Task     │
                        │   asyncio background loop   │
                        │   U(m,t) = αR + βC − γD     │
                        └─────────────────────────────┘
```

## 2. Utility Function

For a shard `m` at time `t`, against the current goal embedding `G`:

```
U(m, t) = α · Relevance(m, G)        # cosine(m.embedding, G)
        + β · Recency(m, t)          # exp(-λ · Δt_hours)
        − γ · Redundancy(m)          # max cosine vs. newer shards
```

Defaults: `α=0.55, β=0.25, γ=0.20, λ=0.08`. `α+β=0.8` keeps signal dominant
over decay; `γ` is small but sufficient to suppress near-duplicates.

Redundancy is computed against the `K=64` newest L1 shards (a moving
reference window), so duplicates of *recent* thought are penalised while
genuinely novel old shards survive.

## 3. Pruning Algorithm

The pruner is an `asyncio` task that runs every `prune_interval` seconds
(default 30s). It is a pure demotion pipeline — it never promotes:

```
PRUNE CYCLE:
  goal_vec ← current goal embedding (or zero-vec if idle)
  ref_set  ← newest K shards in L1             # redundancy reference

  for tier in (L1, L2):
      shards ← tier.scan(batch_size=256)
      for m in shards:
          R ← cosine(m.embedding, goal_vec)
          C ← exp(-λ · hours_since(m.last_accessed))
          D ← max_cosine(m.embedding, ref_set ∖ {m})
          U ← α·R + β·C − γ·D
          m.utility_score ← U
          if U < threshold[tier]:
              demote(m, tier → tier+1)

  archive_stats.record(evicted, retained, wall_time)
```

Thresholds: `L1→L2 at U<0.35`, `L2→L3 at U<0.15`. Demotion is
idempotent — if the shard already exists in the target tier, its payload
is overwritten with the newer `last_accessed_at` and score.

### Why demote-only

Promotion happens on the **read path** (Cache-Aside), not in the pruner.
That keeps the pruner's work bounded by the cold tail, while hot access
patterns naturally lift shards back up without a second scan.

## 4. Cache-Aside Read Path

```
controller.get(shard_id):
    shard ← L1.get(shard_id)
    if shard: touch(shard); return shard

    shard ← L2.get(shard_id)
    if shard: L1.put(shard); touch(shard); return shard

    shard ← L3.get(shard_id)          # context-restoration
    if shard: L2.put(shard); L1.put(shard); touch(shard); return shard

    return None

controller.search(query_vec, goal_vec, top_k):
    hits ← L1.top_k_by_util(goal_vec, top_k)       # in-memory scoring
    if len(hits) < top_k:
        hits += L2.vector_search(query_vec, top_k - len(hits))
    return rerank(hits, goal_vec)
```

`touch(shard)` updates `last_accessed_at` and bumps `access_count`. It is
the only write on the read path and is `fire-and-forget` (scheduled via
`asyncio.create_task`) so retrieval latency is unaffected.

## 5. Concurrency Model

- **Read path**: fully `async`, never awaits the pruner.
- **Pruner**: single background task per process, holds no locks on the
  stores — demotion uses atomic per-shard operations (Redis `DEL` after
  Qdrant `upsert`, Qdrant `delete` after SQLite `INSERT OR REPLACE`).
- **Horizontal scaling**: multiple agent instances share Redis + Qdrant +
  SQLite. The pruner runs at-most-one via a Redis lock (`SET NX PX`);
  other instances skip the cycle. State is entirely in the tiered stores,
  so agents are stateless.

## 6. Schemas

See `lethon_os/schemas.py`. Every shard carries:
- `id` (UUID), `content`, `embedding` (list[float])
- `created_at`, `last_accessed_at`, `access_count`
- `utility_score`, `tier` (L1/L2/L3)
- `goal_context` (goal active at creation, for traceability)
- `metadata` (free-form dict)

Pydantic enforces the contract at every tier boundary — a shard that
cannot round-trip through `MemoryShard.model_validate(...)` is rejected
before it touches a store.
