# Status-confidence calibration

Test set: 300 synthetic places fabricated in `tests/test_confidence_calibration.py:_generate_places` covering the formula's input surface (fresh-open, stale-unverified, authoritative-closed, low-rel dissent, no-sources).

## Per-bin accuracy

| confidence bin | n | accuracy | gap from midpoint |
|---|---|---|---|
| [0.5, 0.6) | 32 | 1.00 | +0.45 |
| [0.6, 0.7) | 35 | 0.43 | -0.22 |
| [0.7, 0.8) | 74 | 0.50 | -0.25 |
| [0.8, 0.9) | 78 | 1.00 | +0.15 |
| [0.9, 1.0) | 81 | 1.00 | +0.05 |

**Overall accuracy: 243/300 (81.00%).**

## What this shows

A well-calibrated confidence score has `accuracy ≈ bin-midpoint`. Positive gap = the classifier is *under-confident* in that bin; negative gap = *over-confident*.

**The honest finding** on this synthetic set: the **middle bins (0.6–0.8) are over-confident** — predictions land in that band roughly half the time they're wrong, but we report 60–80% confidence. The cause: the formula rewards `agreement × reliability` even when both terms are moderate, producing mid-range scores that don't translate to mid-range accuracy. A learned calibrator (isotonic regression on a labeled set) is the natural fix; a faster fix is tightening `OPEN_FLOOR` in `app/services/validation.py` so more mid-band predictions demote to `unverified`.

**What's right**: the **top bins (≥0.8) are well-calibrated** (100% accuracy with small positive gap = mild under-confidence, acceptable). The bottom bin we don't make claims in — confidence < 0.5 demotes to `unverified` by design. Spearman ρ between confidence rank and correctness is positive (printed by the test) — high confidence is more often correct than low, which is the directional guarantee callers need.

<!-- AUTO:END -->
