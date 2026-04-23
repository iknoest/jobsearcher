"""Desirability sub-scorer (R3-03c) — PRD §15.6, weight 10.

Composite of work-mode, commute, salary transparency, and KM-visa
sponsor flag. All components capped so no single factor dominates.

Score pool (max 10):
    base               3   (neutral anchor)
    + work_mode bonus  0 to 4   (remote=4, hybrid=2, onsite=1, unknown=2)
    − commute penalty  0 to 3   (-1 per 15min over threshold; onsite=90min, hybrid=60min)
    + salary signal    0 or 2   (+2 if salary_min or salary_max present)
    + km_visa signal   0 or 2   (+2 if KM-sponsor flag set)

Final clipped to [0, 10].
"""

_MAX_SCORE = 10
_BASE = 3
_WORK_MODE_BONUS = {"remote": 4, "hybrid": 2, "onsite": 1, "unknown": 2}
_ONSITE_COMMUTE_THRESHOLD_MIN = 90
_HYBRID_COMMUTE_THRESHOLD_MIN = 60
_COMMUTE_CAP = 3
_SALARY_BONUS = 2
_KM_VISA_BONUS = 2


def _detect_work_mode(job) -> str:
    mode = str(job.get("work_mode", "") or "").lower().strip()
    if mode in ("remote", "hybrid", "onsite"):
        return mode
    if job.get("is_remote") is True:
        return "remote"
    loc = str(job.get("location", "") or "").lower()
    if "remote" in loc:
        return "remote"
    if "hybrid" in loc:
        return "hybrid"
    return "unknown"


def _commute_penalty(work_mode: str, travel_minutes) -> int:
    """Return penalty 0..3 based on commute overage."""
    if work_mode == "remote" or travel_minutes is None:
        return 0
    try:
        minutes = int(travel_minutes)
    except (TypeError, ValueError):
        return 0
    if minutes <= 0:
        return 0
    threshold = (
        _ONSITE_COMMUTE_THRESHOLD_MIN
        if work_mode == "onsite"
        else _HYBRID_COMMUTE_THRESHOLD_MIN
    )
    over = max(0, minutes - threshold)
    if over == 0:
        return 0
    # −1 per 15min over, capped at -3
    return min(_COMMUTE_CAP, (over + 14) // 15)


def _has_salary(job) -> bool:
    for k in ("salary_min", "salary_max", "min_amount", "max_amount"):
        v = job.get(k)
        if v not in (None, 0, "", "0"):
            return True
    return False


def _has_km_visa_sponsor(job) -> bool:
    return bool(job.get("km_visa_sponsor") or job.get("km_visa_eligible"))


def score_desirability(job) -> dict:
    """Score a job's desirability from work_mode, commute, salary, and KM visa.

    Args:
        job: dict-like. Expected fields (all optional): work_mode, is_remote,
             location, travel_minutes, salary_min, salary_max, km_visa_sponsor.

    Returns:
        {score: int 0-10, signals, reasoning}.
    """
    work_mode = _detect_work_mode(job)
    travel_minutes = job.get("travel_minutes")

    work_mode_bonus = _WORK_MODE_BONUS.get(work_mode, _WORK_MODE_BONUS["unknown"])
    commute_penalty = _commute_penalty(work_mode, travel_minutes)
    salary_bonus = _SALARY_BONUS if _has_salary(job) else 0
    km_bonus = _KM_VISA_BONUS if _has_km_visa_sponsor(job) else 0

    raw = _BASE + work_mode_bonus - commute_penalty + salary_bonus + km_bonus
    final = max(0, min(_MAX_SCORE, raw))

    parts = [f"base {_BASE}", f"work_mode={work_mode} +{work_mode_bonus}"]
    if commute_penalty:
        parts.append(f"commute ~{travel_minutes}min −{commute_penalty}")
    if salary_bonus:
        parts.append(f"salary +{salary_bonus}")
    if km_bonus:
        parts.append(f"KM visa +{km_bonus}")
    parts.append(f"→ {final}/{_MAX_SCORE}")

    return {
        "score": final,
        "signals": {
            "work_mode": work_mode,
            "travel_minutes": travel_minutes,
            "has_salary": salary_bonus > 0,
            "km_visa_sponsor": km_bonus > 0,
            "work_mode_bonus": work_mode_bonus,
            "commute_penalty": commute_penalty,
            "salary_bonus": salary_bonus,
            "km_visa_bonus": km_bonus,
        },
        "reasoning": "; ".join(parts),
    }
