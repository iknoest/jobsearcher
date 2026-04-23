"""Bottleneck detection + 3-band funnel (R4-02).

Pure functions over aggregate counters — no DataFrame mutation, no IO.
The digest template calls `compute_funnel(...)` for the funnel block
and `compute_bottleneck(...)` for the one-line headline.

Funnel bands (user-locked 2026-04-22, refined 2026-04-23):
    FILTERED               — dropped before scoring (lang/location/enrich/classify)
    SCORED BUT NOT SURFACED — scored, then Skip by aggregate OR below --max-jobs cap
    SCORED & SURFACED       — Apply + Review cards shown in the digest

Bottleneck heuristics (fire in priority order; first match wins):
    1. apply_count == 0 AND review_count == 0       → "No jobs surfaced at all today"
    2. pre_scoring_filtered / total_scraped > 0.6    → "Most jobs filtered before scoring"
    3. avg_hard_skill < 10 (of 30) on scored rows    → "Low hard-skill fit across the board"
    4. skip_by_score / scored > 0.7                  → "Most scored jobs Skip-tier"
    5. apply_count == 0                              → "No Apply candidates — only Review"
    6. otherwise: None
"""

from __future__ import annotations

from typing import Any


# ── Funnel ────────────────────────────────────────────────────────────

def compute_funnel(
    total_scraped: int,
    pre_scoring_filtered: int,
    scored_total: int,
    scored_but_skip: int,
    below_max_jobs_cap: int,
    apply_count: int,
    review_count: int,
) -> dict:
    """Return a dict with the 3-band funnel numbers + derived percentages.

    Args:
        total_scraped: row count emerging from scraping + Inbox merge.
        pre_scoring_filtered: dropped by language / location / enrich / classify gates
            (sum across all pre-R3-05 reject streams).
        scored_total: rows that reached the sub-scorer stage.
        scored_but_skip: scored rows whose aggregate recommendation was Skip.
        below_max_jobs_cap: Apply/Review rows that were below the --max-jobs cap,
            so they did not receive heavy-LLM narrative (still shown, but counted
            here for completeness — bypassed rows still surface if Apply/Review).
        apply_count / review_count: cards that made it to the digest.
    """
    surfaced = apply_count + review_count
    not_surfaced_scored = scored_but_skip  # below_max_jobs_cap still surfaces
    return {
        "total_scraped": int(total_scraped),
        "pre_scoring_filtered": int(pre_scoring_filtered),
        "scored_total": int(scored_total),
        "scored_but_skip": int(scored_but_skip),
        "below_max_jobs_cap": int(below_max_jobs_cap),
        "apply_count": int(apply_count),
        "review_count": int(review_count),
        "surfaced": int(surfaced),
        "not_surfaced_scored": int(not_surfaced_scored),
        "filtered_pct": _pct(pre_scoring_filtered, total_scraped),
        "skip_pct_of_scored": _pct(scored_but_skip, scored_total),
        "surfaced_pct": _pct(surfaced, total_scraped),
    }


def _pct(num: int, denom: int) -> int:
    if denom <= 0:
        return 0
    return int(round(100 * num / denom))


# ── Bottleneck headline ───────────────────────────────────────────────

_HARD_SKILL_LOW_THRESHOLD = 10  # out of 30
_FILTERED_HEAVY_RATIO = 0.6
_SKIP_HEAVY_RATIO = 0.7


def compute_bottleneck(
    funnel: dict,
    avg_hard_skill: float | None = None,
) -> str | None:
    """Return a single-sentence bottleneck headline, or None if nothing is off.

    The string is ready to splice into the header — no leading/trailing
    punctuation. Callers can wrap it (e.g. "Main bottleneck today: ...")
    at render time.
    """
    apply_count = int(funnel.get("apply_count", 0))
    review_count = int(funnel.get("review_count", 0))
    total_scraped = int(funnel.get("total_scraped", 0))
    pre_filtered = int(funnel.get("pre_scoring_filtered", 0))
    scored_total = int(funnel.get("scored_total", 0))
    scored_skip = int(funnel.get("scored_but_skip", 0))

    # 1. Nothing surfaced
    if apply_count == 0 and review_count == 0:
        if scored_total == 0 and total_scraped > 0:
            return "Nothing reached scoring — all rows filtered or missing JD"
        return "No jobs surfaced — every scored row Skip-tier"

    # 2. Most of the pool filtered out before scoring
    if total_scraped > 0 and pre_filtered / total_scraped > _FILTERED_HEAVY_RATIO:
        return (
            f"Most jobs filtered before scoring "
            f"({pre_filtered}/{total_scraped}, "
            f"{_pct(pre_filtered, total_scraped)}%) — "
            f"check filter settings"
        )

    # 3. Hard-skill weakness across the board
    if (
        avg_hard_skill is not None
        and scored_total > 0
        and float(avg_hard_skill) < _HARD_SKILL_LOW_THRESHOLD
    ):
        return (
            f"Low hard-skill fit across the board "
            f"(avg {float(avg_hard_skill):.1f}/30 on scored rows) — "
            f"skills.yaml coverage may be stale"
        )

    # 4. High skip-rate among scored rows
    if scored_total > 0 and scored_skip / scored_total > _SKIP_HEAVY_RATIO:
        return (
            f"Most scored jobs ended Skip-tier "
            f"({scored_skip}/{scored_total}, "
            f"{_pct(scored_skip, scored_total)}%) — "
            f"scoring is strict today"
        )

    # 5. Apply-tier empty, only Review
    if apply_count == 0 and review_count > 0:
        return f"No Apply-tier matches today — {review_count} Review only"

    return None


# ── Trust-status header line (used by render.py) ──────────────────────

def compute_trust_status_line(
    apply_count: int,
    review_count: int,
    borderline_skip_count: int,
) -> str:
    """Return the one-line "Trust status today: ..." string.

    borderline_skip_count is the count of rows in the 45-49 final_score
    band — surfaced in the Skip section as "worth audit" so the user
    can spot false negatives without reading every Skip reason.
    """
    parts = [f"{apply_count} Apply", f"{review_count} Review"]
    if borderline_skip_count > 0:
        parts.append(f"{borderline_skip_count} borderline skip{'s' if borderline_skip_count != 1 else ''} worth audit")
    return ", ".join(parts)
