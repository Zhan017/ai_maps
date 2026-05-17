"""Evaluation harness for the hybrid retrieval layer.

For each query in tests/data/eval_queries.jsonl:
  1. Run the default hybrid config to establish a "gold" set (top-K_GOLD).
  2. Run each ablation mode (pure_ann, pure_geo, pure_pop, structured_only,
     hybrid_default) and compute recall@5, recall@10, MRR against the gold.
  3. Aggregate across queries; emit docs/eval_results.md.

Gold-set methodology: bootstrapped from the current shipping hybrid config.
This means:
  - `hybrid_default` is trivially 1.0 by construction — say so in the doc.
  - The interesting columns are the gaps: how much of hybrid's ranking
    does pure_ann recover? pure_geo? pure_pop?
  - A more rigorous eval would pool top-K from all modes and label by hand
    or LLM-as-judge. That's the natural next step.

Usage:
    OPENAI_API_KEY=... python -m scripts.eval
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from openai import OpenAI

from app.core.config import OPENAI_API_KEY
from app.db.session import make_pool
from app.services import search as search_svc

QUERIES_PATH = Path(__file__).resolve().parent.parent / "tests" / "data" / "eval_queries.jsonl"
DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "eval_results.md"

GOLD_K = 5         # top-K from default → gold
EVAL_KS = (5, 10)  # measure recall at these depths

MODES = ["pure_ann", "pure_geo", "pure_pop", "structured_only", "hybrid_default"]


def _load_queries() -> list[dict]:
    with QUERIES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _run_query(pool, client, query: dict, mode: str, limit: int) -> list[str]:
    """Run one search under one mode; return ranked place_ids."""
    q = query["query"]
    sq_kwargs = dict(
        lat=query["lat"], lng=query["lng"], radius_m=query["radius_m"],
        category=None, limit=limit, offset=0,
    )

    # Stash defaults so we can restore them
    saved = (
        search_svc.HYBRID_ANN_W,
        search_svc.HYBRID_DISTANCE_W,
        search_svc.HYBRID_POPULARITY_W,
        search_svc.STRUCTURED_DISTANCE_W,
        search_svc.STRUCTURED_POPULARITY_W,
    )

    try:
        if mode == "pure_ann":
            search_svc.HYBRID_ANN_W = 1.0
            search_svc.HYBRID_DISTANCE_W = 0.0
            search_svc.HYBRID_POPULARITY_W = 0.0
            sq = search_svc.SearchQuery(q=q, **sq_kwargs)
        elif mode == "pure_geo":
            # No semantic, no popularity — only distance ranking
            search_svc.STRUCTURED_DISTANCE_W = 1.0
            search_svc.STRUCTURED_POPULARITY_W = 0.0
            sq = search_svc.SearchQuery(q=None, **sq_kwargs)
        elif mode == "pure_pop":
            # No semantic, no distance — only popularity ranking
            search_svc.STRUCTURED_DISTANCE_W = 0.0
            search_svc.STRUCTURED_POPULARITY_W = 1.0
            sq = search_svc.SearchQuery(q=None, **sq_kwargs)
        elif mode == "structured_only":
            # Structured filters only; default weights for distance + popularity.
            # If the query has an expected_category, apply it as a filter.
            cat = query.get("expected_category")
            sq_kwargs["category"] = cat
            sq = search_svc.SearchQuery(q=None, **sq_kwargs)
        elif mode == "hybrid_default":
            sq = search_svc.SearchQuery(q=q, **sq_kwargs)
        else:
            raise ValueError(f"unknown mode {mode}")

        out = search_svc.search(pool, client, sq)
        return [r["place_id"] for r in out["results"]]
    finally:
        (search_svc.HYBRID_ANN_W,
         search_svc.HYBRID_DISTANCE_W,
         search_svc.HYBRID_POPULARITY_W,
         search_svc.STRUCTURED_DISTANCE_W,
         search_svc.STRUCTURED_POPULARITY_W) = saved


def _recall_at_k(predicted: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    return len(set(predicted[:k]) & gold) / len(gold)


def _mrr(predicted: list[str], gold: set[str]) -> float:
    for i, pid in enumerate(predicted, 1):
        if pid in gold:
            return 1.0 / i
    return 0.0


def _jaccard(a: list[str], b: list[str], k: int) -> float:
    sa, sb = set(a[:k]), set(b[:k])
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def evaluate() -> dict:
    if not OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — semantic modes require it")

    queries = _load_queries()
    print(f"Loaded {len(queries)} queries")
    pool = make_pool()
    client = OpenAI(api_key=OPENAI_API_KEY)

    # Per-query per-mode results
    per_query: list[dict] = []
    mode_times: dict[str, list[float]] = {m: [] for m in MODES}

    try:
        for i, q in enumerate(queries):
            # 1. gold
            gold_ids = _run_query(pool, client, q, "hybrid_default", GOLD_K)
            gold_set = set(gold_ids)
            if len(gold_set) == 0:
                print(f"[{i+1}/{len(queries)}] {q['id']}: NO RESULTS, skipping")
                continue

            row = {"id": q["id"], "query": q["query"], "gold": gold_ids}

            # 2. ablation modes
            for mode in MODES:
                t0 = time.time()
                preds = _run_query(pool, client, q, mode, max(EVAL_KS))
                mode_times[mode].append(time.time() - t0)

                metrics = {f"recall@{k}": _recall_at_k(preds, gold_set, k) for k in EVAL_KS}
                metrics["mrr"] = _mrr(preds, gold_set)
                metrics["jaccard@5"] = _jaccard(preds, gold_ids, 5)
                metrics["top5"] = preds[:5]
                row[mode] = metrics

            per_query.append(row)
            print(f"[{i+1}/{len(queries)}] {q['id']}: {q['query'][:50]}")
    finally:
        pool.close()

    # 3. aggregate
    summary: dict[str, dict] = {}
    for mode in MODES:
        cells = {
            "recall@5": statistics.mean(r[mode]["recall@5"] for r in per_query),
            "recall@10": statistics.mean(r[mode]["recall@10"] for r in per_query),
            "mrr": statistics.mean(r[mode]["mrr"] for r in per_query),
            "jaccard@5": statistics.mean(r[mode]["jaccard@5"] for r in per_query),
            "p50_latency_ms": statistics.median(mode_times[mode]) * 1000,
            "p95_latency_ms": (sorted(mode_times[mode])[int(0.95 * len(mode_times[mode]))] if mode_times[mode] else 0) * 1000,
        }
        summary[mode] = cells

    return {"per_query": per_query, "summary": summary, "n_queries": len(per_query)}


def render_markdown(result: dict) -> str:
    s = result["summary"]
    n = result["n_queries"]
    lines = [
        "# Retrieval eval — recall@k / MRR ablation",
        "",
        f"Eval set: `tests/data/eval_queries.jsonl` (30 hand-authored + 50 templated = 80 queries; "
        f"{n} returned at least 1 result against the current seed).",
        "",
        "## Methodology",
        "",
        "- Gold set per query = top-5 from the shipping **hybrid_default** config "
        "(`HYBRID_ANN_W=0.6`, `HYBRID_DISTANCE_W=0.25`, `HYBRID_POPULARITY_W=0.15`).",
        "- Each ablation mode runs against the same query and is scored against that gold.",
        "- `hybrid_default` is trivially 1.0 by construction — the comparison is the *gaps* "
        "between hybrid and the single-signal baselines.",
        "- This bootstrapped gold biases the eval toward the default. A pooled-judgment eval "
        "(top-K from each mode, hand- or LLM-labeled) is the natural next step; this "
        "harness gets us ablation deltas in minutes rather than days.",
        "",
        "## Results",
        "",
        "| mode | recall@5 | recall@10 | MRR | Jaccard@5 vs gold | p50 latency | p95 latency |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode in MODES:
        c = s[mode]
        lines.append(
            f"| `{mode}` | {c['recall@5']:.2f} | {c['recall@10']:.2f} | "
            f"{c['mrr']:.2f} | {c['jaccard@5']:.2f} | "
            f"{c['p50_latency_ms']:.0f}ms | {c['p95_latency_ms']:.0f}ms |"
        )

    lines += [
        "",
        "## Reading the table",
        "",
        "- **`pure_ann`** measures pgvector-only retrieval. The gap to hybrid quantifies "
        "what geo + popularity blending recovers.",
        "- **`pure_geo`** and **`pure_pop`** drop the semantic query entirely and rank by "
        "distance / popularity alone. These are the floor: how well does a naive geo-only "
        "or pop-only ranker do on vibe queries?",
        "- **`structured_only`** uses category filters from the query metadata when "
        "available, then ranks by distance + popularity. It's the fairest single-signal "
        "comparison because it gets the category right; the gap to hybrid quantifies the "
        "value of semantic *within* a known category.",
        "- **MRR** rewards getting *any* gold item near the top. Low MRR with decent "
        "recall@10 = right items but wrong order.",
        "- **Jaccard@5** is the simplest interpretive lens: top-5 set overlap with hybrid.",
        "- **Latency** is end-to-end including the embedding call for semantic modes. ANN "
        "modes pay the embedding cost; structured modes don't.",
        "",
        "## What the numbers say",
        "",
        "Auto-filled at run time. Re-running `python -m scripts.eval` regenerates this section.",
        "",
        "<!-- AUTO:END -->",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    result = evaluate()
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = DOC_PATH.read_text() if DOC_PATH.exists() else ""
    SENTINEL = "<!-- AUTO:END -->"
    # lstrip newlines so repeated runs don't accumulate blank lines below the sentinel.
    hand = existing.split(SENTINEL, 1)[1].lstrip("\n") if SENTINEL in existing else ""
    DOC_PATH.write_text(render_markdown(result) + ("\n" + hand if hand else ""))
    print(f"\nwrote {DOC_PATH}")
    print("\n== summary ==")
    for mode, c in result["summary"].items():
        print(f"  {mode:18s} recall@5={c['recall@5']:.2f} mrr={c['mrr']:.2f} "
              f"jaccard@5={c['jaccard@5']:.2f} p50={c['p50_latency_ms']:.0f}ms")


if __name__ == "__main__":
    main()
