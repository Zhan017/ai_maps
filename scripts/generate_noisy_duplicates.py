"""Generate a synthetic noisy-duplicate dataset for the matching ablation.

Picks N=200 random open places from the DB; for each, emits K controlled
corruptions covering five realistic failure modes:

  - typo                : 1-2 char substitutions/transpositions in the name
  - abbreviation        : last word -> first 2-3 chars + '.'
  - wrong_coords_near   : lat/lng perturbed by ~100m (should still match)
  - wrong_coords_far    : lat/lng perturbed by ~2km (should NOT match)
  - missing_optional    : phone/website/category dropped

Output JSONL at tests/data/noisy_duplicates.jsonl with rows:
  {customer_input: {name, address, lat, lng, category, phone, website},
   gold_place_id: <uuid>,
   corruption_type: <str>}

Run after `scripts.seed --source osm`.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import psycopg

from app.core.config import DSN

OUT_PATH = Path(__file__).resolve().parent.parent / "tests" / "data" / "noisy_duplicates.jsonl"

CORRUPTIONS = [
    "typo", "abbreviation", "wrong_coords_near", "wrong_coords_far", "missing_optional",
]


def _meters_to_deg(meters: float, lat_deg: float) -> tuple[float, float]:
    """Approximate meters → (dlat, dlng) at a given latitude."""
    dlat = meters / 111_000.0
    dlng = meters / (111_000.0 * max(math.cos(math.radians(lat_deg)), 0.01))
    return dlat, dlng


def _typo(name: str, rnd: random.Random) -> str:
    if len(name) < 4:
        return name
    chars = list(name)
    n_edits = rnd.choice([1, 2])
    for _ in range(n_edits):
        op = rnd.choice(["sub", "swap"])
        i = rnd.randrange(0, len(chars))
        if op == "sub":
            chars[i] = rnd.choice("abcdefghijklmnopqrstuvwxyz")
        elif op == "swap" and i + 1 < len(chars):
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def _abbreviate(name: str) -> str:
    words = name.split()
    if len(words) < 2:
        return name + "."  # one-word names get a trailing dot
    last = words[-1]
    keep = max(2, min(3, len(last) - 1))
    return " ".join(words[:-1] + [last[:keep] + "."])


def _perturb(lat: float, lng: float, meters: float, rnd: random.Random) -> tuple[float, float]:
    dlat, dlng = _meters_to_deg(meters, lat)
    angle = rnd.uniform(0, 2 * math.pi)
    return lat + dlat * math.sin(angle), lng + dlng * math.cos(angle)


def make_corruption(row: dict, kind: str, rnd: random.Random) -> dict:
    name, lat, lng = row["name"], row["lat"], row["lng"]
    address, category = row["address"], row["category"]
    phone, website = row["phone"], row["website"]

    inp = {"name": name, "address": address, "lat": lat, "lng": lng,
           "category": category, "phone": phone, "website": website}

    if kind == "typo":
        inp["name"] = _typo(name, rnd)
    elif kind == "abbreviation":
        inp["name"] = _abbreviate(name)
    elif kind == "wrong_coords_near":
        inp["lat"], inp["lng"] = _perturb(lat, lng, 100.0, rnd)
    elif kind == "wrong_coords_far":
        inp["lat"], inp["lng"] = _perturb(lat, lng, 2000.0, rnd)
    elif kind == "missing_optional":
        inp["phone"] = None
        inp["website"] = None
        inp["category"] = None
    return inp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=200,
                        help="number of base places to pick (default 200)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    rnd = random.Random(args.seed)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id::text, p.primary_name, p.phone_number, p.primary_website_url,
                   pa.formatted_address, pc.code,
                   ST_Y(p.location::geometry), ST_X(p.location::geometry)
            FROM places p
            JOIN place_categories pc ON pc.id = p.category_id
            LEFT JOIN place_addresses pa ON pa.place_id = p.id AND pa.is_primary
            WHERE p.status = 'open'
              AND char_length(p.primary_name) >= 4
            ORDER BY random()
            LIMIT %s
            """,
            (args.n,),
        )
        rows = [
            {
                "id": r[0], "name": r[1], "phone": r[2], "website": r[3],
                "address": r[4], "category": r[5], "lat": r[6], "lng": r[7],
            }
            for r in cur.fetchall()
        ]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_per_kind = {k: 0 for k in CORRUPTIONS}
    with out_path.open("w") as f:
        for row in rows:
            for kind in CORRUPTIONS:
                customer_input = make_corruption(row, kind, rnd)
                f.write(json.dumps({
                    "customer_input": customer_input,
                    "gold_place_id": row["id"],
                    "corruption_type": kind,
                }, ensure_ascii=False) + "\n")
                n_per_kind[kind] += 1

    print(f"wrote {sum(n_per_kind.values())} rows to {out_path}")
    for k, n in n_per_kind.items():
        print(f"  {k:24s} {n}")


if __name__ == "__main__":
    main()
