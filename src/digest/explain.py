"""Deterministic per-card explanation generator (R4-02).

Reads a row dict containing R3-05 score + audit columns and returns
human-readable text for:
    - why_apply   : "✅ Why Apply" bullets (for Apply tier)
    - watch_outs  : "⚠ Watch-outs" bullets (for Apply tier)
    - holding_back: "What's holding this back" bullets (for Review tier)
    - still_worth : "Still worth a look because..." bullets (for Review tier)
    - skip_reason : 1-line reason (for Skip tier)

NO LLM. All text is mechanical from the sub-score + aggregate output.
User rule 2026-04-22: digest explanations must be deterministic so the
user can trust the surface without second-guessing an LLM narrative.
"""

from __future__ import annotations

from typing import Any


# Sub-score maxima (mirror src/score/aggregate.py) for ratio math.
_SUBSCORES = [
    ("role_fit", "score_role_fit", "Role fit", 25),
    ("hard_skill", "score_hard_skill", "Hard skills", 30),
    ("seniority_fit", "score_seniority_fit", "Seniority fit", 15),
    ("industry_proximity", "score_industry_proximity", "Industry proximity", 10),
    ("desirability", "score_desirability", "Desirability", 10),
    ("evidence", "score_evidence", "Evidence", 10),
]

_STRONG_RATIO = 0.70
_WEAK_RATIO = 0.40


def _sub(row: dict, col: str, max_val: int) -> tuple[int, float]:
    """Return (score, ratio) for a sub-score col; defaults to (0, 0.0) if absent."""
    raw = row.get(col, 0)
    try:
        score = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        score = 0
    ratio = (score / max_val) if max_val else 0.0
    return score, ratio


def _strong_subscores(row: dict) -> list[tuple[str, int, int]]:
    """Sub-scores at ≥70% of max, sorted high→low."""
    out = []
    for _, col, label, max_val in _SUBSCORES:
        score, ratio = _sub(row, col, max_val)
        if ratio >= _STRONG_RATIO:
            out.append((label, score, max_val))
    out.sort(key=lambda x: -(x[1] / x[2]))
    return out


def _weak_subscores(row: dict) -> list[tuple[str, int, int]]:
    """Sub-scores at ≤40% of max, sorted low→high."""
    out = []
    for _, col, label, max_val in _SUBSCORES:
        score, ratio = _sub(row, col, max_val)
        if ratio <= _WEAK_RATIO:
            out.append((label, score, max_val))
    out.sort(key=lambda x: x[1] / x[2])
    return out


def _split_semi(val: Any) -> list[str]:
    """Split a semicolon-joined audit string into clean parts; drop blanks."""
    if not val:
        return []
    if isinstance(val, list):
        return [str(p).strip() for p in val if str(p).strip()]
    return [p.strip() for p in str(val).split(";") if p.strip()]


# ── Public API ────────────────────────────────────────────────────────

def explain_row(row: dict) -> dict:
    """Return all explanation strings for this row, keyed by recommendation context.

    The renderer picks the fields it needs:
        Apply tier  → why_apply + watch_outs
        Review tier → holding_back + still_worth
        Skip tier   → skip_reason
    """
    rec = str(row.get("recommendation", "") or "").strip() or "Skip"
    return {
        "recommendation": rec,
        "why_apply": _why_apply(row),
        "watch_outs": _watch_outs(row),
        "holding_back": _holding_back(row),
        "still_worth": _still_worth(row),
        "skip_reason": _skip_reason(row),
        "subscore_bars": _subscore_bars(row),
    }


# ── Apply tier ────────────────────────────────────────────────────────

def _why_apply(row: dict) -> list[str]:
    """2-3 bullets describing the strongest positive signals.

    Prefers aggregate-surfaced top_reasons; falls back to strong sub-scores
    when top_reasons is empty.
    """
    bullets = _split_semi(row.get("top_reasons"))
    # Surface concrete skill matches when available
    hs_skills = (row.get("_score_raw_hard_skill_signals") or {})
    verified = hs_skills.get("verified_matches", []) if isinstance(hs_skills, dict) else []
    if verified:
        names = [m.get("canonical_name", "") for m in verified[:3] if m.get("canonical_name")]
        if names:
            bullets.append(f"Skills matched: {', '.join(names)}")
    if not bullets:
        for label, score, max_val in _strong_subscores(row)[:3]:
            bullets.append(f"{label} strong ({score}/{max_val})")
    return _dedupe(bullets)[:3]


def _watch_outs(row: dict) -> list[str]:
    """2-3 bullets flagging weak sub-scores, review flags, and optional-forbidden hits."""
    bullets = []
    # Aggregate-surfaced risks first
    risks = _split_semi(row.get("top_risks"))
    bullets.extend(risks)
    # Review flags from aggregate (seniority mismatch, role_rescued, optional-forbidden)
    flags = _split_semi(row.get("review_flags"))
    bullets.extend(flags)
    # Finally, any weak sub-score not already mentioned
    for label, score, max_val in _weak_subscores(row)[:2]:
        bullets.append(f"Low {label.lower()} ({score}/{max_val})")
    return _dedupe(bullets)[:3]


# ── Review tier ───────────────────────────────────────────────────────

def _holding_back(row: dict) -> list[str]:
    """Main reasons the score landed in Review band, not Apply.

    Priority: explicit review_flags from aggregate, then weakest 2 sub-scores.
    """
    bullets = _split_semi(row.get("review_flags"))
    if not bullets:
        bullets = _split_semi(row.get("top_risks"))
    for label, score, max_val in _weak_subscores(row)[:2]:
        note = f"Weak {label.lower()} ({score}/{max_val})"
        if note not in bullets:
            bullets.append(note)
    return _dedupe(bullets)[:3]


def _still_worth(row: dict) -> list[str]:
    """Why a Review-tier role is still worth a look — strongest sub-scores."""
    bullets = _split_semi(row.get("top_reasons"))
    if not bullets:
        for label, score, max_val in _strong_subscores(row)[:2]:
            bullets.append(f"{label} strong ({score}/{max_val})")
    # Surface a single verified skill if present and not already in top_reasons
    hs_skills = (row.get("_score_raw_hard_skill_signals") or {})
    verified = hs_skills.get("verified_matches", []) if isinstance(hs_skills, dict) else []
    if verified:
        names = [m.get("canonical_name", "") for m in verified[:2] if m.get("canonical_name")]
        if names:
            skill_line = f"Verified skills: {', '.join(names)}"
            if skill_line not in bullets:
                bullets.append(skill_line)
    return _dedupe(bullets)[:3]


# ── Skip tier ─────────────────────────────────────────────────────────

def _skip_reason(row: dict) -> str:
    """One-line reason for Skip rows; fed into the aggregated skip section."""
    # Prefer blockers when present (hard gates)
    blockers = _split_semi(row.get("blockers"))
    if blockers:
        return blockers[0]
    # Drop-reason from pipeline audit (e.g. missing_jd_body)
    drop_reason = str(row.get("drop_reason", "") or "").strip()
    if drop_reason:
        return drop_reason
    # Score-band Skip
    final = int(row.get("final_score", 0) or 0)
    if final > 0:
        return f"Score {final}/100 below Review floor"
    return "Skipped"


# ── Sub-score bars (data for render.py to draw) ───────────────────────

def _subscore_bars(row: dict) -> list[dict]:
    """Ordered sub-score list for rendering bars.

    PRD weight order: role_fit → hard_skill → seniority → industry → desirability → evidence.
    Each entry: {key, label, score, max, ratio, band} where band ∈ {strong, ok, weak}.
    """
    out = []
    for _, col, label, max_val in _SUBSCORES:
        score, ratio = _sub(row, col, max_val)
        if ratio >= _STRONG_RATIO:
            band = "strong"
        elif ratio <= _WEAK_RATIO:
            band = "weak"
        else:
            band = "ok"
        out.append({
            "key": col,
            "label": label,
            "score": score,
            "max": max_val,
            "ratio": round(ratio, 2),
            "band": band,
        })
    return out


# ── Helpers ───────────────────────────────────────────────────────────

def _dedupe(items: list[str]) -> list[str]:
    """Stable de-dup preserving first occurrence."""
    seen = set()
    out = []
    for i in items:
        k = i.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(i.strip())
    return out
