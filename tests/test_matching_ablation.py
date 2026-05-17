"""Matching ablation harness.

Runs the synthetic noisy-duplicate dataset (tests/data/noisy_duplicates.jsonl)
through the matching engine under five weight configurations and writes the
results to docs/entity_resolution.md.

Usage:
    pytest tests/test_matching_ablation.py -v -s

The `-s` flag prints the table to stdout in addition to writing the doc.
Generate the dataset first:
    python -m scripts.generate_noisy_duplicates --n 200
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.session import make_pool
from app.services import matching

DATA_PATH = Path(__file__).parent / "data" / "noisy_duplicates.jsonl"
DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "entity_resolution.md"

ABLATIONS: dict[str, dict[str, float]] = {
    "default":       {"name": 0.45, "address": 0.15, "phone": 0.10, "website": 0.10, "distance": 0.15, "category": 0.05},
    "name_only":     {"name": 1.00, "address": 0.00, "phone": 0.00, "website": 0.00, "distance": 0.00, "category": 0.00},
    "name_geo":      {"name": 0.50, "address": 0.00, "phone": 0.00, "website": 0.00, "distance": 0.50, "category": 0.00},
    "name_address":  {"name": 0.50, "address": 0.50, "phone": 0.00, "website": 0.00, "distance": 0.00, "category": 0.00},
    "no_distance":   {"name": 0.55, "address": 0.20, "phone": 0.10, "website": 0.10, "distance": 0.00, "category": 0.05},
}


def _load_dataset() -> list[dict]:
    if not DATA_PATH.exists():
        pytest.skip("noisy_duplicates.jsonl missing — run scripts.generate_noisy_duplicates first")
    with DATA_PATH.open() as f:
        return [json.loads(line) for line in f]


def _evaluate(pool, rows: list[dict]) -> dict[str, dict[str, dict]]:
    """For each ablation, return per-corruption-type metrics."""
    out: dict[str, dict[str, dict]] = {}
    for ablation_name, weights in ABLATIONS.items():
        original = matching.WEIGHTS.copy()
        matching.WEIGHTS.clear()
        matching.WEIGHTS.update(weights)
        try:
            by_kind: dict[str, dict] = {}
            for r in rows:
                kind = r["corruption_type"]
                inp = matching.MatchInput(**r["customer_input"])
                result = matching.match(pool, inp)
                bucket = by_kind.setdefault(kind, {"n": 0, "correct": 0, "rejected": 0, "wrong_match": 0})
                bucket["n"] += 1
                expected_no_match = kind == "wrong_coords_far"
                if result.decision == "no_match":
                    if expected_no_match:
                        bucket["correct"] += 1
                    bucket["rejected"] += 1
                else:
                    if not expected_no_match and result.place_id == r["gold_place_id"]:
                        bucket["correct"] += 1
                    elif not expected_no_match:
                        bucket["wrong_match"] += 1
                    else:
                        bucket["wrong_match"] += 1  # false positive on a far-coords case
            out[ablation_name] = by_kind
        finally:
            matching.WEIGHTS.clear()
            matching.WEIGHTS.update(original)
    return out


def _render_markdown(results: dict[str, dict[str, dict]]) -> str:
    kinds = ["typo", "abbreviation", "wrong_coords_near", "wrong_coords_far", "missing_optional"]
    lines = [
        "# Entity resolution ablation",
        "",
        "Each cell is `correct / n` for the noisy-duplicate dataset",
        "(`tests/data/noisy_duplicates.jsonl`). For `wrong_coords_far`, correct = ",
        "rejected (decision == `no_match`); for everything else, correct = matched ",
        "to the gold place_id.",
        "",
        "## Per-corruption-type accuracy",
        "",
        "| ablation | " + " | ".join(kinds) + " | overall |",
        "|" + "---|" * (len(kinds) + 2),
    ]
    for name, by_kind in results.items():
        row = [name]
        total_correct = 0
        total_n = 0
        for k in kinds:
            b = by_kind.get(k, {"correct": 0, "n": 0})
            total_correct += b["correct"]
            total_n += b["n"]
            row.append(f"{b['correct']}/{b['n']} ({b['correct']/max(b['n'],1):.2f})")
        overall = total_correct / max(total_n, 1)
        row.append(f"**{total_correct}/{total_n} ({overall:.2f})**")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("## Reading the table")
    lines.append("")
    lines.append("- `typo` and `abbreviation` measure name-fuzz robustness.")
    lines.append("- `wrong_coords_near` (~100m perturbation) measures distance tolerance: a too-tight distance weight will reject these.")
    lines.append("- `wrong_coords_far` (~2km perturbation) inverts: correct = rejected. A pure-name matcher will falsely accept these against any similarly-named place.")
    lines.append("- `missing_optional` measures the renormalization fix (phone/website/category dropped from denominator in `app/services/matching.py:score()`).")
    return "\n".join(lines) + "\n"


def test_matching_ablation():
    rows = _load_dataset()
    pool = make_pool()
    try:
        results = _evaluate(pool, rows)
    finally:
        pool.close()

    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = DOC_PATH.read_text() if DOC_PATH.exists() else ""
    auto = _render_markdown(results)

    # Preserve any hand-written sections after the auto block, marked with a sentinel.
    SENTINEL = "<!-- AUTO:END -->"
    if SENTINEL in existing:
        # lstrip newlines so repeated test runs don't accumulate blank lines.
        hand_section = existing.split(SENTINEL, 1)[1].lstrip("\n")
    else:
        hand_section = ""
    full = auto + "\n<!-- AUTO:END -->\n\n" + hand_section
    DOC_PATH.write_text(full)
    print(f"\nwrote {DOC_PATH}")
    print(auto)

    # Smoke assertions — the default should beat name_only on wrong_coords_far rejection
    default_far = results["default"]["wrong_coords_far"]
    name_only_far = results["name_only"]["wrong_coords_far"]
    assert default_far["correct"] >= name_only_far["correct"], (
        "default config should reject wrong_coords_far at least as well as name_only"
    )
