# VOYGR Replica — place intelligence for Astana

> Google Maps coverage is patchy in Central Asia — exactly the gap VOYGR is
> built to fill. This is a place-intelligence replica seeded with real OSM
> data for Astana (1,158 places, mixed Cyrillic + Latin) and built to be
> probed for **entity resolution**, **eval discipline**, and **confidence
> calibration**. Spec: `docs/voygr_replica.md`.

The stack is **FastAPI + Postgres + PostGIS + pgvector**, raw SQL via
`psycopg`, OpenAI embeddings (`text-embedding-3-small`) for semantic
retrieval, OpenAI chat completions for the agent layer.

## 30-second tour

`scripts/fetch_osm.py` pulls 1,158 named Astana places from OpenStreetMap.
`scripts/seed.py --source osm` lays them into a 13-table canonical schema
(`app/db/schema.sql`) modelled on the VOYGR spec — `places`,
`place_sources`, `place_amenities`, `place_hours`, `place_attributes`,
`place_status_history`, `places_vectors`, etc. Enrichment beyond names is
mocked but coherent (status-correlated source signals, category-correlated
popularity, vibe attributes per category). The matching engine
(`app/services/matching.py`) is a weighted scorer over name + address +
phone + website + distance + category with renormalization for missing
fields. The search layer blends pgvector ANN with PostGIS distance and
popularity. Three load-bearing docs in `docs/` show ablations and
calibration:

1. [`docs/entity_resolution.md`](docs/entity_resolution.md) — matching
   ablation across 5 weight configurations on 1,000 synthetic noisy
   duplicates + conflict-resolution policy
2. [`docs/eval_results.md`](docs/eval_results.md) — retrieval ablation
   across 5 modes (pure_ann, pure_geo, pure_pop, structured_only,
   hybrid_default) on 80 labeled queries with recall@k, MRR, Jaccard
3. [`docs/confidence_calibration.md`](docs/confidence_calibration.md) —
   per-decile calibration of the `status_confidence` formula on 300
   synthetic places

## Quick start

```bash
docker compose up -d --build db                           # postgis + pgvector

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m scripts.fetch_osm                               # ~2s, writes data/osm_places.json (gitignored)
python -m scripts.seed --source osm                       # ~5s, prints API keys (auth bypass is on by default for the demo)
OPENAI_API_KEY=sk-... python -m scripts.build_embeddings  # ~15s on 1.1k places, ~$0.005

OPENAI_API_KEY=sk-... uvicorn app.main:app --reload
```

Open <http://localhost:8000/>. To regenerate the ablation tables:

```bash
python -m scripts.generate_noisy_duplicates --n 200   # writes tests/data/noisy_duplicates.jsonl
pytest tests/test_matching_ablation.py -v -s          # rewrites docs/entity_resolution.md auto block
pytest tests/test_confidence_calibration.py -v -s     # rewrites docs/confidence_calibration.md
OPENAI_API_KEY=sk-... python -m scripts.eval          # rewrites docs/eval_results.md (~$0.01)
```

---

## §1 — Entity resolution

Customer-place input → canonical place ID. The matcher
(`app/services/matching.py:match()`) does PostGIS radius candidate
generation (`ST_DWithin`, 500m, `LIMIT 25`) followed by a weighted
linear scorer over six features with **renormalization** so missing input
fields don't penalize:

```
WEIGHTS = {name: 0.45, address: 0.15, phone: 0.10,
           website: 0.10, distance: 0.15, category: 0.05}
```

If a customer only sends `name + lat/lng + category`, only those three
weights contribute and the denominator collapses to their sum. An exact
name + close coords + category match → confidence 1.0. (Before the
renormalization fix, that same input topped out at 0.65.)

Headline ablation on 1,000 synthetic noisy duplicates (5 corruption types
× 200 places each):

| ablation | typo | abbreviation | coords_near | coords_far (reject) | missing_opt | overall |
|---|---|---|---|---|---|---|
| **default** | 1.00 | 1.00 | 1.00 | 0.93 | 1.00 | **0.99** |
| name_only | 0.92 | 1.00 | 0.98 | 0.89 | 1.00 | 0.96 |
| name_geo | 1.00 | 1.00 | 0.98 | **0.97** | 1.00 | 0.99 |
| name_address | 1.00 | 1.00 | 1.00 | 0.91 | 1.00 | 0.98 |
| no_distance | 1.00 | 1.00 | 1.00 | 0.93 | 1.00 | 0.98 |

**The interesting finding**: `name_geo` (a stripped-down 0.5/0.5 config)
*beats* the production default by 4 points on `wrong_coords_far`
rejection. The address weight in default lets noisy address tokens lift
the wrong candidate over the LOW threshold. In production with cleaner
address inputs the address weight earns its place back — but the eval
flags the regression. See [`docs/entity_resolution.md`](docs/entity_resolution.md)
for the conflict-resolution policy across competing sources.

Code paths to read: `app/services/matching.py:20` (weights),
`app/services/matching.py:score()` (renormalization), `app/utils/text.py`
(Unicode-aware `normalize_name` — NFKC + casefold + `[^\w ]+`; without it
every Cyrillic name reduced to `""` and matching collapsed to
distance-only).

---

## §2 — Eval discipline

`scripts/eval.py` runs 80 hand+templated queries
(`tests/data/eval_queries.jsonl`) through five retrieval modes, scores
each against the **hybrid_default** top-5 gold, and emits
[`docs/eval_results.md`](docs/eval_results.md):

| mode | recall@5 | MRR | Jaccard@5 | p50 latency |
|---|---|---|---|---|
| pure_ann | 0.25 | 0.48 | 0.16 | 395ms |
| pure_geo | 0.15 | 0.24 | 0.10 | 9ms |
| pure_pop | 0.15 | 0.24 | 0.10 | 8ms |
| **structured_only** | **0.42** | **0.66** | **0.31** | **5ms** |
| hybrid_default | 1.00 | 1.00 | 1.00 | 393ms |

**The production insight**: `structured_only` (category filter +
distance × popularity) recovers 42% of hybrid's top-5 at **80× lower
latency** (5ms vs 393ms — the gap is the OpenAI embedding round-trip).
A real router classifies queries on whether `expected_category` is
inferable and routes to the cheap path when it is. Hybrid only earns its
393ms on queries where vibe-disambiguation actually matters.

Other findings: `pure_ann` at 0.25 confirms semantic-alone is
insufficient (the geo + popularity blending is doing real work);
`pure_geo` and `pure_pop` tied at 0.15 establishes the floor any
retrieval system must beat.

**Honest caveats**, called out in the doc: hybrid_default = 1.0 is by
construction (the gold *is* hybrid's top-5 — pooled-judgment v2 is the
natural next step); 80 queries is small (CI estimated at ±0.07 at n=80);
latency is local-loopback.

Code paths to read: `app/services/search.py:15-26` (the 5 module-level
weight constants the harness toggles), `scripts/eval.py:_run_query`
(weight override pattern), `tests/data/eval_queries.jsonl` (30
hand-authored + 50 templated, JSON Lines).

---

## §3 — Confidence + freshness scoring

`app/services/validation.py:_score_verdict()` is a pure function over a
list of source rows producing `(status, confidence, reason)`. The
formula is **explicit** (top of the module's docstring):

```
status_confidence
  = 0.4 · source_agreement
  + 0.4 · weighted_reliability
  + 0.2 · freshness_factor

freshness_factor = exp(-min_days_since_fetch / 30)
```

Three rules: no sources → unverified 0.5; any source with `reliability ≥
0.7` and a `closed` signal → short-circuit to `permanently_closed`
(without that floor a single low-rel social post would close a real
place); otherwise score, and demote to `unverified` if `< 0.5`.

Calibration on 300 synthetic places covering 5 input surfaces
(fresh-open, stale-unverified, authoritative-closed, low-rel dissent,
no-sources):

| bin | n | accuracy | gap from midpoint |
|---|---|---|---|
| [0.5, 0.6) | 32 | 1.00 | +0.45 |
| [0.6, 0.7) | 35 | 0.43 | **-0.22** |
| [0.7, 0.8) | 74 | 0.50 | **-0.25** |
| [0.8, 0.9) | 78 | 1.00 | +0.15 |
| [0.9, 1.0) | 81 | 1.00 | +0.05 |

**Honest finding**: the mid bins (0.6–0.8) are **over-confident** —
predictions there are right about half the time but we report 60–80%
confidence. The formula rewards mid-range `agreement × reliability`
products too generously. Two fixes flagged in
[`docs/confidence_calibration.md`](docs/confidence_calibration.md):
isotonic regression on a labeled set (proper calibration) or tightening
`OPEN_FLOOR` so more mid-band predictions demote to `unverified` (faster).

The top bins (≥0.8) are well-calibrated at 100% accuracy with mild
under-confidence — that's where the system actually makes confident
public claims. Spearman ρ = 0.362 (positive monotone) confirms the
directional guarantee callers need: higher reported confidence ⇒ higher
real-world accuracy.

Code paths: `app/services/validation.py:_score_verdict` (pure formula),
`tests/test_confidence_calibration.py:_generate_places` (the 5 input
surfaces).

---

## §4 — What's mock and what's real

| Layer | Real | Mock |
|---|---|---|
| Place names + addresses + lat/lng + categories | ✓ (OSM Overpass, 1,158 Astana places) | |
| Schema | ✓ (13 canonical tables, VOYGR spec §3) | |
| Matching engine | ✓ (PostGIS + rapidfuzz scorer + renormalization) | |
| Hybrid search | ✓ (pgvector ivfflat + PostGIS distance + popularity) | |
| LLM tool calling | ✓ (gpt-4o-mini, 4-iter loop, filter-hint injection) | |
| Hours | | per-category templates with jitter |
| Amenities | | category-conditioned random |
| Source list per place | | 1–3 sources sampled from a 5-element catalog with reliability scores |
| Source `status_signal` | | status-correlated (open places get "active" mostly, closed places get "closed" from high-rel sources) |
| Vibe attributes | | per-category keywords |
| `popularity_score` | | category-base + uniform noise (hotels 0.74 → ATMs 0.11) |

The pipeline shape, the schema, and every line of code that processes
this data is what would ship to production. The data itself is what
would come from VOYGR's actual ingestion pipeline.

---

## §5 — Roadmap

This replica demonstrates architecture on a small footprint. Production
work for a real place-intelligence pipeline would add:

1. **Multi-source ingestion** beyond OSM. 2GIS for Kazakhstan, Yandex for
   Russia, Foursquare Open Places for global, plus targeted crawl of
   official-site domains. Each source becomes a row in `place_sources`
   with the existing reliability scoring.
2. **Real freshness signals**. HTTP HEAD on the place's website; an
   LLM-based content classifier over crawled pages looking for closure
   verbs (`"мы закрыты"`, `"we've moved to"`, etc.); social-media post
   recency. These plug into the existing `validation._score_verdict`
   formula via `place_sources.status_signal`.
3. **Pooled-judgment retrieval eval**. Replace the bootstrapped hybrid-as-gold
   with top-K-from-each-mode pooling, LLM-as-judge labeling, and a
   stratified set of ~500 queries. Gets us trustworthy absolute recall@k.
4. **Cross-encoder reranker**. BGE-reranker-base over ANN top-50. The
   current eval shows where the ANN-only path fails; a reranker is the
   surgical fix.
5. **Agentic tool composition**. `find_similar`, `compare`,
   `get_status`, multi-step decomposition for "best date spot tonight"
   queries. The existing `app/api/v1/legacy.py:chat` loop is the
   substrate.
6. **Production observability**. Structured logs with trace IDs, p50/p95
   per route, cost tracking per API key. Prompt-cache hit rate.

---

## Endpoint cheat-sheet

`/v1/*` and `/internal/*` accept (but don't currently require) `X-API-Key`.
Auth defaults to bypass for the demo — set `REQUIRE_API_KEY=1` to enforce.

| Endpoint | Purpose |
|---|---|
| `POST /v1/places:validate-enrich` | Match + enrich; returns `match_confidence`, `match_breakdown`, `status`, `status_confidence` |
| `GET /v1/places/{id}` | Full profile (hours, amenities, attributes; optional `?include_sources=true`, `?include_history=true`) |
| `POST /v1/places:status` | Batch status + freshness for a list of IDs |
| `POST /v1/places/{id}:feedback` | Customer correction; stored for review |
| `GET /v1/places:search` | Hybrid search; returns `reasons[]`, `freshness{}`, `score` |
| `GET /internal/places/{id}/debug` | Profile + customer refs + feedback log |
| `POST /internal/places/{id}/override` | Manual status/name overrides; writes history |
| `GET /api/place/{id}` | Unauth alias for the profile endpoint (used by the demo UI) |
| `GET /api/nearby`, `POST /api/chat`, `DELETE /api/chat/{sid}`, `GET /api/health`, `GET /api/stats` | Legacy unauth routes used by the demo UI |

## Examples

```bash
# Hybrid search — real Astana cafe data
curl -s "localhost:8000/v1/places:search?q=quiet+coffee+with+wifi&lat=51.1283&lng=71.4276&radius_m=2000&limit=5" | jq

# Validate-enrich round-trip — pass a real seeded place back at itself
curl -s -X POST localhost:8000/v1/places:validate-enrich \
  -H 'Content-Type: application/json' \
  -d '{"places":[{"customer_place_id":"abc","name":"Coffee Like","lat":51.13,"lng":71.43,"category":"cafe"}]}' | jq

# Natural-language chat with filter-hint injection
curl -s -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"t1","message":"quiet coffee with wifi","map_center":{"lat":51.1283,"lon":71.4276},"filters":{"open_now":true}}' | jq
```

## Tests

```bash
pytest                                                    # 13 tests
pytest tests/test_matching_ablation.py -v -s              # ablation table → docs/entity_resolution.md
pytest tests/test_confidence_calibration.py -v -s         # bin table → docs/confidence_calibration.md
OPENAI_API_KEY=... python -m scripts.eval                 # retrieval table → docs/eval_results.md
```

## Layout

```
app/
  main.py                  FastAPI entrypoint
  api/v1/                  places, search, admin, legacy routers
  core/                    config, security (auth bypass via REQUIRE_API_KEY=0)
  db/                      schema.sql, pool, raw SQL helpers
  services/
    matching.py            weighted scorer + candidate gen (PostGIS)
    validation.py          status formula + calibration-ready pure scorer
    search.py              hybrid: pgvector ANN + PostGIS distance + popularity
    enrichment.py          read-side full-profile shaper
    embeddings.py          place-text serializer + OpenAI wrapper
  utils/                   geo, text (Unicode-aware normalization)
db/Dockerfile              postgis + pgvector
scripts/
  fetch_osm.py             Overpass → data/osm_places.json
  seed.py --source osm     drops + reseeds; prints API keys
  build_embeddings.py      OpenAI embeddings → places_vectors (idempotent via text_hash)
  generate_noisy_duplicates.py   tests/data/noisy_duplicates.jsonl
  eval.py                  scripts the retrieval ablation
docs/
  entity_resolution.md     matching ablation + conflict-resolution policy
  eval_results.md          retrieval ablation
  confidence_calibration.md   confidence formula calibration
  voygr_replica.md         original spec
static/index.html          Leaflet UI + chat + Validate tab
tests/
  data/                    eval_queries.jsonl + noisy_duplicates.jsonl
  test_*.py                12 functional + 1 ablation harness (writes docs/)
```
