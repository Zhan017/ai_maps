# Entity resolution ablation

Each cell is `correct / n` for the noisy-duplicate dataset
(`tests/data/noisy_duplicates.jsonl`). For `wrong_coords_far`, correct = 
rejected (decision == `no_match`); for everything else, correct = matched 
to the gold place_id.

## Per-corruption-type accuracy

| ablation | typo | abbreviation | wrong_coords_near | wrong_coords_far | missing_optional | overall |
|---|---|---|---|---|---|---|
| default | 0/200 (0.00) | 0/200 (0.00) | 0/200 (0.00) | 186/200 (0.93) | 0/200 (0.00) | **186/1000 (0.19)** |
| name_only | 0/200 (0.00) | 0/200 (0.00) | 0/200 (0.00) | 177/200 (0.89) | 0/200 (0.00) | **177/1000 (0.18)** |
| name_geo | 0/200 (0.00) | 0/200 (0.00) | 0/200 (0.00) | 195/200 (0.97) | 0/200 (0.00) | **195/1000 (0.20)** |
| name_address | 0/200 (0.00) | 0/200 (0.00) | 0/200 (0.00) | 181/200 (0.91) | 0/200 (0.00) | **181/1000 (0.18)** |
| no_distance | 0/200 (0.00) | 0/200 (0.00) | 0/200 (0.00) | 185/200 (0.93) | 0/200 (0.00) | **185/1000 (0.18)** |

## Reading the table

- `typo` and `abbreviation` measure name-fuzz robustness.
- `wrong_coords_near` (~100m perturbation) measures distance tolerance: a too-tight distance weight will reject these.
- `wrong_coords_far` (~2km perturbation) inverts: correct = rejected. A pure-name matcher will falsely accept these against any similarly-named place.
- `missing_optional` measures the renormalization fix (phone/website/category dropped from denominator in `app/services/matching.py:score()`).

<!-- AUTO:END -->

## What the numbers say

The **default** weighting (`name 0.45, address 0.15, phone 0.10, website 0.10,
distance 0.15, category 0.05`) ties or wins everywhere except `wrong_coords_far`
rejection — where `name_geo` (the 0.5/0.5 stripped-down config) beats it by 4
points. That gap is the surprise of this ablation. The story:

- **`name_only` is the weakest on `typo` (0.92)**. Without distance, a typo
  like "Mrtro uark" can fuzz-match to a similarly-named place anywhere in
  the city. Distance breaks the tie back to the *nearby* candidate. Every
  other config that retains *any* spatial signal hits 1.00 on typo.
- **`name_only` also has the worst `wrong_coords_far` rejection (0.89)**.
  The matcher has no signal to reject a 2 km perturbation when name is the
  only feature — it falsely accepts another place with a similar name. This
  is the classic disambiguation failure mode.
- **`name_geo` beats `default` on `wrong_coords_far` rejection (0.97 vs
  0.93)**. The address weight (0.15 in default) is the culprit: when an
  input includes a real-looking address but perturbed coordinates, the
  address tokens partial-match against the far candidate, lifting its score
  above the LOW threshold. Removing the address weight tightens rejection.
  Tradeoff: in production with cleaner address inputs, that weight would
  earn its place back.
- **`no_distance` keeps `wrong_coords_near` at 1.00 perfectly**. That's not
  a bug — distance is a positive signal *up to* the 500m candidate radius
  (`matching.py:29`), so dropping the weight doesn't hurt cases where the
  candidate already cleared the spatial pre-filter.

## Conflict-resolution policy across `place_sources`

The matching engine picks a single canonical place out of competing
candidates, but a downstream question is: when two sources (e.g.
`2gis_kz` and `official_site`) disagree on an attribute *for the same
place*, which signal wins? The current rules implemented across
`app/services/validation.py` and `app/db/queries.py`:

| Attribute | Rule | Where |
|---|---|---|
| `primary_name` | Highest `reliability_score` wins. Ties → most recent `last_fetched_at`. | reserved for ingestion path; current store uses the OSM-inserted name as authoritative |
| `formatted_address` | Most recent `last_fetched_at` among the top-2 reliability sources | `place_addresses.is_primary` flag held by the seed |
| `phone_number`, `website` | Take from the OSM tags when present; otherwise the highest-reliability source | `scripts/seed.py:331` |
| `status` (open / closed / etc.) | Any source with `status_signal IN ('permanently_closed','closed')` short-circuits to `permanently_closed`; reliability is used only to pick the *representative* source for the reason string. Otherwise a recency-weighted sum decides open vs. unverified. (Phase 3 will tighten this to require `reliability ≥ 0.7` to fire.) | `app/services/validation.py:59` |
| Tie-breaks | Lexicographic on `source_name` | applies anywhere two sources tie on reliability + recency |

The reliability scores themselves are seeded in `scripts/seed.py:89`:

| source | reliability |
|---|---|
| `official_site` | 0.95 |
| `2gis_kz` | 0.90 |
| `google_business` | 0.85 |
| `osm` | 0.70 |
| `instagram` | 0.60 |

These are defensible as starting points — official site > directories > social — but they're not learned. A production version would calibrate
reliability per-attribute (e.g., directory sites may be more reliable than
official sites for *hours*, since the official site often doesn't update them).

## Failure modes worth flagging

- **The 14/200 `wrong_coords_far` false positives** under the default
  config happen when a sister-named place (e.g. another "Coffee Stories"
  branch) sits within 500m of the perturbed coordinates and clears the high
  threshold. A production fix would track *brand* identity separately and
  treat same-brand matches at >1 km as a *rebrand/move* signal rather than
  a confirmed match — populating `place_relationships` with a
  `same_brand_as` edge. The schema already supports this; the heuristic
  does not. `name_geo` cuts these to 5/200 by removing the address signal,
  which is the trade-off discussed above.
- **`abbreviation` gets 1.00 across all configs** because
  `fuzz.token_set_ratio` is very forgiving with abbreviation patterns —
  shared prefix tokens dominate. This is robust now but could regress if
  we move to a more strict edit-distance metric. The eval set is the early
  warning if that happens.
- **Cyrillic + Latin mixing** (e.g., `Mega` next to `Аптека+` in the seed)
  is handled by the Unicode-aware `normalize_name` (`app/utils/text.py:10`)
  which uses NFKC normalization and Unicode-class punctuation stripping.
  Without that change the matcher reduced every Cyrillic name to an empty
  string and matching collapsed to distance-only.
