"""Seniority-fit sub-scorer (R3-03a) — PRD §15.4, weight 15.

Scores detected seniority_level from R2-03 against Ava's target
(Senior → Lead → Principal). Years-required is a bounded modifier,
not an overriding signal (user rule 2026-04-22).
"""

from src.classify.seniority import classify_seniority

# User-locked 2026-04-22
_LEVEL_SCORES = {
    "senior": 15,
    "lead": 14,
    "principal": 12,  # classifier returns "lead" for Principal today;
    "mid": 8,         # kept here for forward-compat if classifier distinguishes.
    "junior": 4,
    "head_of": 5,
    "entry_level": 0,
    "unknown": 8,
}

_MAX_SCORE = 15


def _years_adjustment(years: int | None) -> int:
    """User-locked years-required modifier (bounded; refines but never overpowers level)."""
    if years is None:
        return 0
    if years < 3:
        return -3
    if years < 5:
        return -1
    if years <= 10:
        return 0
    if years <= 12:
        return -1
    return -3


def score_seniority_fit(job, classified: dict | None = None) -> dict:
    """Score a job's seniority fit for Ava (8+ yrs, target Senior→Lead).

    Args:
        job: dict-like with 'title' and 'description'.
        classified: optional pre-computed R2-03 classifier output.

    Returns:
        {score: int 0-15, signals, reasoning}.
    """
    title = str(job.get("title", "") or "")
    description = str(job.get("description", "") or "")

    if classified is None:
        classified = classify_seniority(title, description)

    level = classified.get("seniority_level", "unknown")
    years = classified.get("seniority_years_required")
    mismatch_flag = bool(classified.get("seniority_mismatch_flag", False))
    hard_reject = bool(classified.get("seniority_hard_reject", False))

    # Hard-reject safety net — shouldn't reach here after Stage 3b, but if it does, score 0.
    if hard_reject:
        return {
            "score": 0,
            "signals": {
                "level": level,
                "years_required": years,
                "level_score": 0,
                "years_adjustment": 0,
                "mismatch_flag": mismatch_flag,
                "hard_reject_safety_net": True,
            },
            "reasoning": "Seniority hard-reject → score 0 (safety net — should have been gated in Stage 3b)",
        }

    level_score = _LEVEL_SCORES.get(level, _LEVEL_SCORES["unknown"])
    years_adj = _years_adjustment(years)
    final = max(0, min(_MAX_SCORE, level_score + years_adj))

    reasoning_parts = [f"level={level} → {level_score}"]
    if years is not None:
        reasoning_parts.append(f"years_required={years} → {years_adj:+d}")
    if mismatch_flag:
        reasoning_parts.append("(mismatch_flag set)")
    reasoning_parts.append(f"final {final}/{_MAX_SCORE}")

    return {
        "score": final,
        "signals": {
            "level": level,
            "years_required": years,
            "level_score": level_score,
            "years_adjustment": years_adj,
            "mismatch_flag": mismatch_flag,
            "hard_reject_safety_net": False,
        },
        "reasoning": "; ".join(reasoning_parts),
    }
