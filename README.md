# Lethon-OS

Self-governing memory for long-horizon LLM agents. Memory shards live across
three tiers (Redis → Qdrant → SQLite) and are scored by a time-varying utility
function. Low-utility memories are pruned; the agent's safety constitution
sits in a separate, immutable tier and is never touched.

The repo ships two pieces:

- **`lethon_os/`** — the Python backend: tiered storage, async pruner,
  utility scoring, security middleware, FastAPI-facing facade.
- **`frontend/`** — Lethon-Vision, a real-time dashboard for watching the
  memory state evolve.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the design rationale and
diagram.

## Backend

### Memory tiering

| Tier      | Store    | Role                        | Access target |
| --------- | -------- | --------------------------- | ------------- |
| `L0_CORE` | Redis    | Immutable safety constitution. Never pruned. | <10 ms |
| `L1`      | Redis    | Working memory, hot path. Decays over time. | <10 ms |
| `L2`      | Qdrant   | Episodic memory, vector-searchable. | ~30 ms |
| `L3`      | SQLite   | Compressed cold archive. Restorable on lookup. | ~150 ms |

Reads are Cache-Aside: `MemoryController.get()` walks `L1 → L2 → L3` and
rehydrates the shard back up the stack on a hit. The pruner only ever
*demotes* — promotion happens organically on the read path.

### Utility scoring

Every shard `m` at time `t` carries a score:

```
U(m, t) = α · Relevance(m, G) + β · Recency(m, t) − γ · Redundancy(m)
```

- **Relevance** — cosine similarity to the current goal embedding `G`.
- **Recency** — exponential decay since `last_accessed_at`.
- **Redundancy** — max cosine against a moving window of newer L1 shards
  (with a self-mask so a shard does not penalize itself).

Defaults: `α=0.55, β=0.25, γ=0.20, λ=0.08`. Shards under the
per-tier threshold demote on the next pruner cycle. The scoring loop is
vectorized via NumPy (`score_batch` in `lethon_os/utility.py`).

### Asynchronous pruner

`UtilityPruner` runs every `interval_seconds` (default 30 s; the API
facade defaults to 60 s). At-most-one execution across horizontally
scaled agents is enforced with a Redis `SET NX` lock. The demote pipeline
is ordered `archive → delete` so a shard never exists in zero tiers
during a transition.

An optional `on_action` callback fires for every demote / archive — this
is the hook the security layer uses to write signed audit receipts.

### Security middleware

Application-layer hardening, layered as defense-in-depth:

- **`Ed25519Signer` / `Ed25519Verifier`** — wrappers with stable
  `key_id` so verifiers route by identity. Helpers for shard and audit
  receipt sign/verify.
- **`KeyRegistry`** — `key_id → Verifier` map. Rejects re-registration
  with a different public key (impersonation defense). Keeps retired
  keys around so historical receipts stay verifiable.
- **`SignedAuditLog`** — append-only SQLite log. Verifies signature and
  chain link on every append. `verify_chain()` replays the log; tamper
  detection is end-to-end. `merkle_root()` produces a BLAKE2b root
  suitable for external notarization.
- **`MemoryScrubber`** — regex-layered indirect prompt-injection defense
  with an optional async semantic classifier hook. Two FLAG hits on the
  same payload escalate to QUARANTINE. A rolling-window counter raises
  `ScrubberAlert` on adversarial spikes so the API can return HTTP 503
  and halt autonomous tool calls.
- **`SecureMemoryController`** — facade that owns its own Cache-Aside
  walk so the scrubber runs *before* L2/L3 hits get promoted to L1.
  Every lifecycle action emits a chained, signed receipt.
- **L0_CORE guards** — pruner scans filter by `Tier.is_prunable`;
  `archive.put` and `vector.put` reject `L0_CORE`; `cache.put` preserves
  it. The constitution is structurally exempt from utility decay.
- **Pydantic bounds** — content capped at 64 KB, embedding at 4096
  dimensions. Buffer-overflow defense at the application layer.

### FastAPI facade (in progress)

`lethon_os/api/` exposes a `UtilityController` over `MemoryController`
that adds back-pressure tracking (HTTP 503 when the pruner falls
behind), Logfire span instrumentation (optional, no-ops if Logfire is
absent), and HTTP-shaped projections of internal state. The HTTP routes
themselves are the next layer to land.

### Out of scope (called out for honesty)

The original blueprint mentions Confidential Computing / TEEs,
`mprotect`-based memory isolation, hand-written AVX-512 bounds, and
CVE-2025-6660. Those are infrastructure or kernel concerns and are not
addressable in Python application code. The security surface in this
repo is everything that *is* feasible at the app layer.

## Frontend (Lethon-Vision)

Real-time dashboard for the memory state. The graph IS the page —
everything else is corner-anchored HUD chrome.

- **Stack** — Next.js 15 (App Router, RSC), React 19, Tailwind CSS 4
  with `@theme` tokens, D3 for the force graph, framer-motion for HUD
  springs, Geist Sans + Geist Mono via `next/font`, Phosphor icons.
- **Memory graph** — D3 force simulation; node radius and brightness
  encode utility, hue encodes tier, position is centripetal (high
  utility orbits the center, low utility drifts to the rim). No
  drop-shadow glow.
- **Keyboard contract** — `/` opens the search palette, `?` opens
  shortcut help, `p` pins the selected memory, `Esc` closes any
  overlay. Arrow keys navigate the palette.
- **Destructive actions** — Prune is two-stage with a 4-second Undo
  countdown; never an immediate-fire button.
- **States** — explicit loading, empty, and error states. The loading
  skeleton matches the graph silhouette so the layout doesn't reflow on
  data arrival.

### Status

The dashboard runs against deterministic sample data while the SSE
backend (`/api/stream`) is still being wired. Replace `makeSampleShards`
with the real stream once the FastAPI route lands.

## Project layout

```
.
├── ARCHITECTURE.md            Design diagram + pruning algorithm
├── lethon_os/
│   ├── controller.py          MemoryController (Cache-Aside)
│   ├── pruner.py              UtilityPruner + audit hook
│   ├── schemas.py             MemoryShard, Tier, UtilityWeights
│   ├── utility.py             NumPy scoring + score_batch
│   ├── api/                   UtilityController facade
│   ├── security/              Signing, audit log, scrubber, secure controller
│   └── tiers/                 Redis / Qdrant / SQLite adapters
├── tests/                     120+ tests, fully offline (fakeredis,
│                              in-memory Qdrant, :memory: SQLite)
└── frontend/
    ├── app/                   Layout + dashboard page + theme
    ├── components/            HUD primitives, graph, palette, detail
    ├── hooks/                 Keyboard shortcuts
    └── lib/                   Types, utils, sample data
```

## Local development

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest -q              # 120 tests, runs in <1s, no external infra
```

The test suite is fully offline: `fakeredis` for Redis,
`AsyncQdrantClient(location=":memory:")` for Qdrant, and `aiosqlite`
with `:memory:` for SQLite. No services to spin up.

### Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
```

Routes at `/api/*` are rewritten to `LETHON_BACKEND_URL` (defaults to
`http://localhost:8000`) so the SSE stream is same-origin from the
browser's perspective.

## Reference

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — full design, tier diagram,
  utility derivation, pruning algorithm, concurrency model.
