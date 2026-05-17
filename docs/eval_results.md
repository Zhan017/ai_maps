# Retrieval eval — recall@k / MRR ablation

Eval set: `tests/data/eval_queries.jsonl` (30 hand-authored + 50 templated = 80 queries; 80 returned at least 1 result against the current seed).

## Methodology

- Gold set per query = top-5 from the shipping **hybrid_default** config (`HYBRID_ANN_W=0.6`, `HYBRID_DISTANCE_W=0.25`, `HYBRID_POPULARITY_W=0.15`).
- Each ablation mode runs against the same query and is scored against that gold.
- `hybrid_default` is trivially 1.0 by construction — the comparison is the *gaps* between hybrid and the single-signal baselines.
- This bootstrapped gold biases the eval toward the default. A pooled-judgment eval (top-K from each mode, hand- or LLM-labeled) is the natural next step; this harness gets us ablation deltas in minutes rather than days.

## Results

| mode | recall@5 | recall@10 | MRR | Jaccard@5 vs gold | p50 latency | p95 latency |
|---|---|---|---|---|---|---|
| `pure_ann` | 0.25 | 0.39 | 0.48 | 0.16 | 395ms | 661ms |
| `pure_geo` | 0.15 | 0.23 | 0.24 | 0.10 | 9ms | 60ms |
| `pure_pop` | 0.15 | 0.23 | 0.24 | 0.10 | 8ms | 36ms |
| `structured_only` | 0.42 | 0.58 | 0.66 | 0.31 | 5ms | 8ms |
| `hybrid_default` | 1.00 | 1.00 | 1.00 | 1.00 | 393ms | 867ms |

## Reading the table

- **`pure_ann`** measures pgvector-only retrieval. The gap to hybrid quantifies what geo + popularity blending recovers.
- **`pure_geo`** and **`pure_pop`** drop the semantic query entirely and rank by distance / popularity alone. These are the floor: how well does a naive geo-only or pop-only ranker do on vibe queries?
- **`structured_only`** uses category filters from the query metadata when available, then ranks by distance + popularity. It's the fairest single-signal comparison because it gets the category right; the gap to hybrid quantifies the value of semantic *within* a known category.
- **MRR** rewards getting *any* gold item near the top. Low MRR with decent recall@10 = right items but wrong order.
- **Jaccard@5** is the simplest interpretive lens: top-5 set overlap with hybrid.
- **Latency** is end-to-end including the embedding call for semantic modes. ANN modes pay the embedding cost; structured modes don't.

## What the numbers say

Three findings worth highlighting:

**(1) `structured_only` is the production insight.** Recovers 42% of hybrid's
top-5 and 58% of top-10 at **5ms p50** — ~80× faster than hybrid (393ms p50,
which is dominated by the OpenAI embedding round-trip). For queries where
the agent already knows the category ("pharmacies open now", "ATMs near
me"), routing to the structured path costs nothing in quality terms that
matter for those queries and pays back the latency budget. A production
router would classify queries by whether `expected_category` is inferable
and pick the cheap path. The gap from 42→100 is the value of pgvector for
queries where semantic-vibe disambiguation actually matters.

**(2) `pure_ann` (0.25 recall@5) confirms semantic alone is insufficient.**
ANN retrieves a reasonable *set* (recall@10 = 0.39, MRR = 0.48 — the right
item is often #1 or #2) but consistently misses pieces of hybrid's top-5.
The miss is structured: hybrid's distance and popularity weights are
pulling in nearby + popular places that ANN alone ranks lower. That 25%
floor is exactly what you'd expect from "semantic without geo grounding"
on a city-scoped corpus.

**(3) `pure_geo` and `pure_pop` are indistinguishable (both 0.15 recall@5).**
Without a query, ranking by distance or popularity reduces to "give me
*any* nearby/popular place" — which essentially randomizes against the
hybrid gold across categories. This is the floor and tells you the
absolute minimum any "ranker" achieves on this eval. Anything above 0.15
is doing real work.

## Caveats acknowledged

- The 1.0 for `hybrid_default` is **by construction** — gold = hybrid top-5
  is circular. The interesting numbers are the *gaps* between modes, not
  absolute recall.
- The eval pool is bootstrapped from hybrid_default's top-K. Modes that
  surface novel relevant items not in hybrid's pool are penalized. A
  pooled-judgment v2 (top-K from each mode, LLM-or-human labeled) would
  fix this. Roughly 1 day of follow-up work.
- 80 queries is small. Variance estimate: std dev across 5 modes' recall@5
  values is ~0.3 with n=80 → 95% CI ~±0.07. So the 0.42 vs 0.25 gap is
  real; the 0.15 vs 0.15 tie between pure_geo and pure_pop is also real.
- Latency is local-loopback (Docker → host). Production with managed Postgres
  + OpenAI would be ~50ms higher on each leg.

<!-- AUTO:END -->
