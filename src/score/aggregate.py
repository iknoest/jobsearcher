"""Aggregate scorer + Apply/Review/Skip decision (R3-04) — PRD §15 + §16.

Combines the 6 sub-scorer outputs into a final 0-100 MatchScore and
deterministically assigns one of three recommendation labels.

User-locked routing rules (2026-04-22):

Skip (hard gates — highest precedence):
    - rejected_by_forbidden_core (from hard_skill)
    - role_hard_reject            (from role_family classifier; safety net)
    - seniority_hard_reject       (from seniority classifier; safety net)
    - final_score < 50

Review demoters (fire even when score >= 70):
    - seniority_mismatch_flag                              (junior / head-of / VP / director)
    - role_rescued                                         (excluded role_family rescued by JD signal)
    - forbidden_optional_hits >= 2                         (2+ optional forbidden skills)
    - forbidden_optional_hits == 1 AND hard_skill <= 8     (weak evidence + 1 optional forbidden)
    - hard_skill <= 8 (independent guardrail)              (low skill coverage never goes straight to Apply)

Review (score band):
    - 50 <= final_score <= 69

Apply:
    - final_score >= 70 AND no Review demoters fire.

Notes:
    - industry == "unknown" alone does NOT demote (informational only, per
      user rule 2026-04-22).
    - PRD thresholds are retained as-is. Re-calibration deferred until
      R3-02b closes the skills.yaml coverage gap (would introduce churn
      otherwise).
"""

# Sub-score maxima per PRD §15
_MAX_ROLE_FIT = 25
_MAX_HARD_SKILL = 30
_MAX_SENIORITY_FIT = 15
_MAX_INDUSTRY_PROX = 10
_MAX_DESIRABILITY = 10
_MAX_EVIDENCE = 10
_MAX_TOTAL = 100

# User-locked thresholds (2026-04-22)
_APPLY_THRESHOLD = 70
_REVIEW_THRESHOLD = 50

# User-locked guardrail (2026-04-22)
_HARD_SKILL_GUARDRAIL = 8

# Display thresholds for top_reasons / top_risks
_POSITIVE_RATIO = 0.7
_NEGATIVE_RATIO = 0.3


def _ratio(score: int, max_val: int) -> float:
    return (score / max_val) if max_val else 0.0


def _format_subscore_reason(label: str, score: int, max_val: int) -> str:
    return f"{label}: {score}/{max_val}"


def aggregate_score(
    role_fit: dict,
    hard_skill: dict,
    seniority_fit: dict,
    industry_proximity: dict,
    desirability: dict,
    evidence: dict,
    role_classified: dict | None = None,
    seniority_classified: dict | None = None,
    industry_classified: dict | None = None,
) -> dict:
    """Combine the 6 sub-scores, decide Apply/Review/Skip, and surface top signals.

    Args:
        role_fit / hard_skill / seniority_fit / industry_proximity / desirability / evidence:
            sub-scorer result dicts (each contains at minimum a 'score' key).
        role_classified / seniority_classified / industry_classified:
            optional classifier-output dicts; used to detect Review demoters
            (seniority_mismatch_flag, role_rescued). Safe to omit — the function
            falls back to hard-gate signals already present in sub-score dicts.

    Returns:
        dict with: final_score, recommendation, reason, top_reasons, top_risks,
        subscore_breakdown, blockers, review_flags.
    """
    # ── 1. Collect sub-score ints ────────────────────────────────
    rf = int(role_fit.get("score", 0))
    hs = int(hard_skill.get("score", 0))
    sn = int(seniority_fit.get("score", 0))
    ip = int(industry_proximity.get("score", 0))
    de = int(desirability.get("score", 0))
    ev = int(evidence.get("score", 0))
    total = rf + hs + sn + ip + de + ev
    total = max(0, min(_MAX_TOTAL, total))

    breakdown = {
        "role_fit": rf,
        "hard_skill": hs,
        "seniority_fit": sn,
        "industry_proximity": ip,
        "desirability": de,
        "evidence": ev,
        "total": total,
    }

    # ── 2. Detect hard gates (Skip) ──────────────────────────────
    blockers: list[str] = []
    hs_signals = hard_skill.get("signals", {}) or {}
    if hs_signals.get("rejected_by_forbidden_core"):
        names = ", ".join(
            h.get("canonical_name", "?")
            for h in hs_signals.get("forbidden_core_hits", [])
        )
        blockers.append(f"forbidden_core_skill: {names}")

    if role_classified and role_classified.get("would_hard_reject"):
        blockers.append(
            f"role_hard_reject: {role_classified.get('display_name', '?')}"
        )
    if seniority_classified and seniority_classified.get("seniority_hard_reject"):
        blockers.append(
            f"seniority_hard_reject: {seniority_classified.get('seniority_level', '?')}"
        )

    # ── 3. Detect Review demoters ────────────────────────────────
    review_flags: list[str] = []
    seniority_mismatch = bool(
        (seniority_classified or {}).get("seniority_mismatch_flag")
    )
    role_bucket = (role_classified or {}).get("bucket")
    role_hard_reject = (role_classified or {}).get("would_hard_reject", False)
    role_rescued = role_bucket == "excluded" and not role_hard_reject
    forbidden_optional_count = len(hs_signals.get("forbidden_optional_hits", []) or [])

    if seniority_mismatch:
        review_flags.append(
            f"seniority_mismatch: {seniority_classified.get('seniority_level', '?')}"
        )
    if role_rescued:
        review_flags.append(
            f"role_rescued: {(role_classified or {}).get('display_name', '?')}"
        )
    if forbidden_optional_count >= 2:
        names = ", ".join(
            h.get("canonical_name", "?")
            for h in hs_signals.get("forbidden_optional_hits", [])[:3]
        )
        review_flags.append(f"{forbidden_optional_count} optional-forbidden ({names})")
    elif forbidden_optional_count == 1 and hs <= _HARD_SKILL_GUARDRAIL:
        name = hs_signals["forbidden_optional_hits"][0].get("canonical_name", "?")
        review_flags.append(
            f"optional-forbidden '{name}' + weak hard_skill ({hs}/30)"
        )
    if hs <= _HARD_SKILL_GUARDRAIL:
        review_flags.append(
            f"low hard_skill_guardrail ({hs}/30 ≤ {_HARD_SKILL_GUARDRAIL})"
        )

    # ── 4. Decide tier ───────────────────────────────────────────
    if blockers:
        recommendation = "Skip"
        reason = f"Blocker(s): {'; '.join(blockers)}"
    elif total < _REVIEW_THRESHOLD:
        recommendation = "Skip"
        reason = f"Score {total}/100 below Review floor ({_REVIEW_THRESHOLD})"
    elif total < _APPLY_THRESHOLD:
        recommendation = "Review"
        reason = f"Score {total}/100 in Review band ({_REVIEW_THRESHOLD}-{_APPLY_THRESHOLD - 1})"
    elif review_flags:
        recommendation = "Review"
        reason = (
            f"Score {total}/100 would Apply, but demoted to Review by: "
            f"{'; '.join(review_flags)}"
        )
    else:
        recommendation = "Apply"
        reason = f"Score {total}/100 ≥ {_APPLY_THRESHOLD} with no mismatch flags"

    # ── 5. Build top_reasons (positive) + top_risks (negative) ───
    sub_entries = [
        ("Role fit", rf, _MAX_ROLE_FIT),
        ("Hard skills", hs, _MAX_HARD_SKILL),
        ("Seniority fit", sn, _MAX_SENIORITY_FIT),
        ("Industry proximity", ip, _MAX_INDUSTRY_PROX),
        ("Desirability", de, _MAX_DESIRABILITY),
        ("Evidence", ev, _MAX_EVIDENCE),
    ]

    positives = sorted(
        [(_ratio(s, m), label, s, m) for label, s, m in sub_entries if _ratio(s, m) >= _POSITIVE_RATIO],
        key=lambda x: -x[0],
    )
    top_reasons = [_format_subscore_reason(label, s, m) for _, label, s, m in positives[:3]]

    negatives = sorted(
        [(_ratio(s, m), label, s, m) for label, s, m in sub_entries if _ratio(s, m) <= _NEGATIVE_RATIO],
        key=lambda x: x[0],
    )
    score_risks = [_format_subscore_reason(label, s, m) for _, label, s, m in negatives[:3]]
    top_risks = review_flags + [r for r in score_risks if r not in top_reasons]
    top_risks = top_risks[:3]

    return {
        "final_score": total,
        "recommendation": recommendation,
        "reason": reason,
        "top_reasons": top_reasons,
        "top_risks": top_risks,
        "subscore_breakdown": breakdown,
        "blockers": blockers,
        "review_flags": review_flags,
    }
