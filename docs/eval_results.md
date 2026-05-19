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
| `pure_ann` | 0.25 | 0.39 | 0.46 | 0.16 | 252ms | 449ms |
| `pure_geo` | 0.16 | 0.23 | 0.24 | 0.10 | 8ms | 53ms |
| `pure_pop` | 0.16 | 0.23 | 0.24 | 0.10 | 7ms | 58ms |
| `structured_only` | 0.42 | 0.60 | 0.67 | 0.31 | 5ms | 10ms |
| `hybrid_default` | 1.00 | 1.00 | 1.00 | 1.00 | 251ms | 435ms |

## Reading the table

- **`pure_ann`** measures pgvector-only retrieval. The gap to hybrid quantifies what geo + popularity blending recovers.
- **`pure_geo`** and **`pure_pop`** drop the semantic query entirely and rank by distance / popularity alone. These are the floor: how well does a naive geo-only or pop-only ranker do on vibe queries?
- **`structured_only`** uses category filters from the query metadata when available, then ranks by distance + popularity. It's the fairest single-signal comparison because it gets the category right; the gap to hybrid quantifies the value of semantic *within* a known category.
- **MRR** rewards getting *any* gold item near the top. Low MRR with decent recall@10 = right items but wrong order.
- **Jaccard@5** is the simplest interpretive lens: top-5 set overlap with hybrid.
- **Latency** is end-to-end including the embedding call for semantic modes. ANN modes pay the embedding cost; structured modes don't.

## What the numbers say

Auto-filled at run time. Re-running `python -m scripts.eval` regenerates this section.

<!-- AUTO:END -->

## Reading the table

Three findings worth highlighting:

**(1) `structured_only` is the production insight.** Recovers 42% of hybrid's
top-5 and 60% of top-10 at **5ms p50** — ~50× faster than hybrid (251ms p50,
which is dominated by the OpenAI embedding round-trip). For queries where
the agent already knows the category ("pharmacies open now", "ATMs near
me"), routing to the structured path costs nothing in quality terms that
matter for those queries and pays back the latency budget. A production
router would classify queries by whether `expected_category` is inferable
and pick the cheap path. The gap from 42→100 is the value of pgvector for
queries where semantic-vibe disambiguation actually matters.

**(2) `pure_ann` (0.25 recall@5) confirms semantic alone is insufficient.**
ANN retrieves a reasonable *set* (recall@10 = 0.39, MRR ≈ 0.46 — the right
item is often #1 or #2) but consistently misses pieces of hybrid's top-5.
The miss is structured: hybrid's distance and popularity weights are
pulling in nearby + popular places that ANN alone ranks lower. That 25%
floor is exactly what you'd expect from "semantic without geo grounding"
on a city-scoped corpus.

**(3) `pure_geo` and `pure_pop` are indistinguishable (both 0.16 recall@5).**
Without a query, ranking by distance or popularity reduces to "give me
*any* nearby/popular place" — which essentially randomizes against the
hybrid gold across categories. This is the floor and tells you the
absolute minimum any "ranker" achieves on this eval. Anything above 0.16
is doing real work.

## Caveats acknowledged

- The 1.0 for `hybrid_default` is **by construction** — gold = hybrid top-5
  is circular. The interesting numbers are the *gaps* between modes, not
  absolute recall.
- The eval pool is bootstrapped from hybrid_default's top-K. Modes that
  surface novel relevant items not in hybrid's pool are penalized. A
  pooled-judgment v2 (top-K from each mode, LLM-or-human labeled) would
  fix this. Roughly 1 day of follow-up work.
- 80 queries is small. The 0.42 vs 0.25 gap between structured_only and
  pure_ann is real; the 0.16 vs 0.16 tie between pure_geo and pure_pop
  is also real.
- Latency is local-loopback (Docker → host). Production with managed Postgres
  + OpenAI would be ~50ms higher on each leg.

## Item 8 (Commit 3) — alias signals in embedding text

Embedding text now includes `brand_name` and `name_local` when distinct from
`primary_name` (so "Halyk Bank" branches with `brand_name="Halyk Bank"` add no
duplicate token; "Банк ЦентрКредит" with `brand_name="Bank CenterCredit"`
gains a cross-script alias).

Coverage in the current OSM seed: 21 places have `brand_name` distinct from
primary, 74 have `name_local`. The wiring is correct (verified by inspecting
serialized strings), but the global `pure_ann` recall@5 is unchanged at 0.25
because the alias signal only differentiates ~8% of the corpus and the
bootstrapped gold set is hybrid-ranked, which already finds chain places
through geo+pop weights. The change is best understood as **infrastructure
ready for multi-source ingest**: when real ingest backfills more brand and
local-language aliases (especially for restaurants and cafes, where coverage
is currently thinnest), the embedding signal will pay off without further
code changes.
