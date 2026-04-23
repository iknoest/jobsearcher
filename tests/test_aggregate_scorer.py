"""Tests for src/score/aggregate.py (R3-04).

Thresholds (user-locked 2026-04-22):
    Apply  >= 70
    Review  50-69
    Skip   < 50 or any hard blocker

Review demoters: seniority_mismatch_flag, role_rescued,
forbidden_optional_hits >= 2, (optional==1 AND hard_skill <= 8),
hard_skill <= 8 (independent guardrail).

industry == unknown alone does NOT demote.
"""

import pytest

from src.score.aggregate import aggregate_score


def _sub(score: int, signals: dict | None = None) -> dict:
    return {"score": score, "signals": signals or {}, "reasoning": "test"}


def _hs(score: int, *, rejected_core=False, forbidden_core=None,
        forbidden_optional_count=0):
    return {
        "score": score,
        "signals": {
            "rejected_by_forbidden_core": rejected_core,
            "forbidden_core_hits": forbidden_core or [],
            "forbidden_optional_hits": [
                {"canonical_name": f"fo{i}"} for i in range(forbidden_optional_count)
            ],
        },
    }


def _role(bucket="desired", hard=False, display="Test Role"):
    return {"bucket": bucket, "would_hard_reject": hard, "display_name": display, "family": "f"}


def _sen(level="senior", hard=False, mismatch=False):
    return {
        "seniority_level": level,
        "seniority_hard_reject": hard,
        "seniority_mismatch_flag": mismatch,
    }


# ════════════════════════════════════════════════════════════════
# APPLY PATH
# ════════════════════════════════════════════════════════════════
def test_strong_apply_path():
    """All sub-scores high, no flags → Apply."""
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(25),
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(9),
        evidence=_sub(10),
        role_classified=_role(),
        seniority_classified=_sen(),
    )
    assert r["final_score"] == 89
    assert r["recommendation"] == "Apply"
    assert r["blockers"] == []
    assert r["review_flags"] == []


def test_apply_threshold_boundary_70_is_apply():
    """Exactly 70 → Apply."""
    r = aggregate_score(
        role_fit=_sub(15),
        hard_skill=_hs(20),
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(5),
        evidence=_sub(5),
        role_classified=_role(),
        seniority_classified=_sen(),
    )
    assert r["final_score"] == 70
    assert r["recommendation"] == "Apply"


# ════════════════════════════════════════════════════════════════
# REVIEW BAND (50-69)
# ════════════════════════════════════════════════════════════════
def test_score_in_50_69_is_review():
    r = aggregate_score(
        role_fit=_sub(12),
        hard_skill=_hs(15),
        seniority_fit=_sub(10),
        industry_proximity=_sub(7),
        desirability=_sub(6),
        evidence=_sub(5),
    )
    assert r["final_score"] == 55
    assert r["recommendation"] == "Review"


def test_review_floor_boundary_50_is_review():
    r = aggregate_score(
        role_fit=_sub(10),
        hard_skill=_hs(15),
        seniority_fit=_sub(10),
        industry_proximity=_sub(5),
        desirability=_sub(5),
        evidence=_sub(5),
    )
    assert r["final_score"] == 50
    assert r["recommendation"] == "Review"


# ════════════════════════════════════════════════════════════════
# SKIP PATH
# ════════════════════════════════════════════════════════════════
def test_score_below_50_is_skip():
    r = aggregate_score(
        role_fit=_sub(5),
        hard_skill=_hs(10),
        seniority_fit=_sub(5),
        industry_proximity=_sub(5),
        desirability=_sub(5),
        evidence=_sub(5),
    )
    assert r["final_score"] == 35
    assert r["recommendation"] == "Skip"


def test_forbidden_core_always_skips_even_with_high_score():
    """Forbidden CORE skill → Skip regardless of total score."""
    r = aggregate_score(
        role_fit=_sub(25),
        hard_skill=_hs(
            0,
            rejected_core=True,
            forbidden_core=[{"canonical_name": "firmware engineering"}],
        ),
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(10),
        evidence=_sub(10),
    )
    assert r["recommendation"] == "Skip"
    assert any("forbidden_core_skill" in b for b in r["blockers"])


def test_role_hard_reject_skips():
    r = aggregate_score(
        role_fit=_sub(0),
        hard_skill=_hs(20),
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(10),
        evidence=_sub(5),
        role_classified=_role(bucket="excluded", hard=True, display="DevOps"),
    )
    assert r["recommendation"] == "Skip"
    assert any("role_hard_reject" in b for b in r["blockers"])


def test_seniority_hard_reject_skips():
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(20),
        seniority_fit=_sub(0),
        industry_proximity=_sub(10),
        desirability=_sub(5),
        evidence=_sub(5),
        seniority_classified=_sen(level="entry_level", hard=True),
    )
    assert r["recommendation"] == "Skip"
    assert any("seniority_hard_reject" in b for b in r["blockers"])


# ════════════════════════════════════════════════════════════════
# REVIEW DEMOTERS (score >= 70 but routed to Review)
# ════════════════════════════════════════════════════════════════
def test_seniority_mismatch_demotes_apply_to_review():
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(25),
        seniority_fit=_sub(5),  # head_of
        industry_proximity=_sub(10),
        desirability=_sub(8),
        evidence=_sub(10),
        role_classified=_role(),
        seniority_classified=_sen(level="head_of", mismatch=True),
    )
    assert r["final_score"] >= 70
    assert r["recommendation"] == "Review"
    assert any("seniority_mismatch" in f for f in r["review_flags"])


def test_role_rescued_demotes_apply_to_review():
    """Role was excluded but rescued by JD signal → still Review even at high score."""
    r = aggregate_score(
        role_fit=_sub(10),
        hard_skill=_hs(25),
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(8),
        evidence=_sub(5),
        role_classified=_role(bucket="excluded", hard=False, display="Software Engineer"),
    )
    assert r["final_score"] == 73
    assert r["recommendation"] == "Review"
    assert any("role_rescued" in f for f in r["review_flags"])


def test_two_forbidden_optional_hits_demote_to_review():
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(20, forbidden_optional_count=2),
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(8),
        evidence=_sub(5),
        role_classified=_role(),
        seniority_classified=_sen(),
    )
    assert r["final_score"] >= 70
    assert r["recommendation"] == "Review"
    assert any("optional-forbidden" in f for f in r["review_flags"])


def test_one_forbidden_optional_alone_does_not_demote():
    """1 optional-forbidden with strong hard_skill should NOT demote."""
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(25, forbidden_optional_count=1),  # hs=25 > 8 guardrail
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(8),
        evidence=_sub(5),
        role_classified=_role(),
        seniority_classified=_sen(),
    )
    assert r["recommendation"] == "Apply"


def test_one_forbidden_optional_plus_weak_hard_skill_demotes():
    """1 optional-forbidden + hard_skill <= 8 → Review (combined rule fires)."""
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(8, forbidden_optional_count=1),
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(10),
        evidence=_sub(8),
        role_classified=_role(),
        seniority_classified=_sen(),
    )
    assert r["final_score"] >= 70
    assert r["recommendation"] == "Review"
    # Both specific rule + guardrail should fire; at minimum the combined rule
    assert any("weak hard_skill" in f for f in r["review_flags"])


def test_low_hard_skill_guardrail_demotes_alone():
    """hard_skill <= 8 alone is enough to block Apply → Review."""
    r = aggregate_score(
        role_fit=_sub(25),
        hard_skill=_hs(8),  # forbidden_optional_count=0 by default
        seniority_fit=_sub(15),
        industry_proximity=_sub(10),
        desirability=_sub(10),
        evidence=_sub(10),
        role_classified=_role(),
        seniority_classified=_sen(),
    )
    assert r["final_score"] >= 70
    assert r["recommendation"] == "Review"
    assert any("low hard_skill_guardrail" in f for f in r["review_flags"])


# ════════════════════════════════════════════════════════════════
# industry=unknown alone does NOT demote (informational)
# ════════════════════════════════════════════════════════════════
def test_industry_unknown_alone_does_not_demote():
    """User rule: industry unknown is informational only."""
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(25),
        seniority_fit=_sub(15),
        industry_proximity=_sub(4),  # unknown fallback = 4
        desirability=_sub(8),
        evidence=_sub(3),
        role_classified=_role(),
        seniority_classified=_sen(),
        industry_classified={"industry": "unknown", "proximity_to_ava": None},
    )
    assert r["final_score"] >= 70
    assert r["recommendation"] == "Apply"


# ════════════════════════════════════════════════════════════════
# SUBSCORE BREAKDOWN + REASONS
# ════════════════════════════════════════════════════════════════
def test_subscore_breakdown_sum_matches_total():
    r = aggregate_score(
        role_fit=_sub(20),
        hard_skill=_hs(22),
        seniority_fit=_sub(14),
        industry_proximity=_sub(8),
        desirability=_sub(7),
        evidence=_sub(6),
    )
    sb = r["subscore_breakdown"]
    assert sb["total"] == sb["role_fit"] + sb["hard_skill"] + sb["seniority_fit"] + \
           sb["industry_proximity"] + sb["desirability"] + sb["evidence"]


def test_top_reasons_surface_high_subscores():
    r = aggregate_score(
        role_fit=_sub(25),       # 25/25 = 1.0 → positive
        hard_skill=_hs(30),      # 30/30 = 1.0 → positive
        seniority_fit=_sub(15),  # 15/15 = 1.0 → positive
        industry_proximity=_sub(3),  # 0.3 → below positive threshold
        desirability=_sub(3),
        evidence=_sub(3),
    )
    assert len(r["top_reasons"]) >= 2
    assert any("Role fit" in s for s in r["top_reasons"])
    assert any("Hard skills" in s for s in r["top_reasons"])


def test_top_risks_surface_review_flags_and_low_subscores():
    r = aggregate_score(
        role_fit=_sub(5),   # 0.2 → negative
        hard_skill=_hs(6),  # 0.2 → negative + guardrail
        seniority_fit=_sub(4),  # 0.27 → negative
        industry_proximity=_sub(10),
        desirability=_sub(10),
        evidence=_sub(10),
        role_classified=_role(),
        seniority_classified=_sen(),
    )
    # top_risks must include guardrail flag
    assert any("hard_skill_guardrail" in s for s in r["top_risks"])


# ════════════════════════════════════════════════════════════════
# BOUNDARY: total clamped to [0, 100]
# ════════════════════════════════════════════════════════════════
def test_total_clamped_to_100_if_sum_exceeds():
    """Defensive: if caller passes out-of-range values, clamp at 100."""
    r = aggregate_score(
        role_fit=_sub(30),   # over-max
        hard_skill=_hs(35),  # over-max
        seniority_fit=_sub(20),
        industry_proximity=_sub(15),
        desirability=_sub(15),
        evidence=_sub(15),
    )
    assert r["final_score"] == 100
    assert r["subscore_breakdown"]["total"] == 100
