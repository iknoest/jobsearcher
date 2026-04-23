"""Tests for src/score/seniority_fit.py (R3-03a).

User-locked scoring curve (2026-04-22):
    senior=15, lead=14, principal=12, mid=8, junior=4,
    head_of=5, entry_level=0, unknown=8

Years-required adjustment (bounded, never overpowers level):
    <3 → -3, 3-4 → -1, 5-10 → 0, 11-12 → -1, >12 → -3, None → 0
"""

import pytest

from src.score.seniority_fit import score_seniority_fit


def _cls(level, years_required=None, hard_reject=False, mismatch_flag=False):
    return {
        "seniority_level": level,
        "seniority_years_required": years_required,
        "seniority_hard_reject": hard_reject,
        "seniority_mismatch_flag": mismatch_flag,
        "seniority_confidence": 0.85,
        "seniority_reasoning": "test",
    }


# ── LEVEL SCORES ─────────────────────────────────────────────────
def test_senior_with_sweet_spot_years():
    """senior + 8 yrs → 15 + 0 = 15 (max)."""
    r = score_seniority_fit({"title": "Senior PM", "description": ""}, _cls("senior", 8))
    assert r["score"] == 15
    assert r["signals"]["level_score"] == 15
    assert r["signals"]["years_adjustment"] == 0


def test_lead_level_scores_14():
    r = score_seniority_fit({"title": "Lead", "description": ""}, _cls("lead", 8))
    assert r["score"] == 14


def test_mid_level_scores_8():
    r = score_seniority_fit({"title": "PM", "description": ""}, _cls("mid", 4))
    # mid=8, years 4 → -1 = 7
    assert r["score"] == 7


def test_junior_scores_4():
    r = score_seniority_fit({"title": "Junior PM", "description": ""}, _cls("junior", 2))
    # 4 + (-3 for <3 yrs) = 1
    assert r["score"] == 1


def test_head_of_scores_5():
    r = score_seniority_fit({"title": "Head of Product", "description": ""},
                            _cls("head_of", 10))
    assert r["score"] == 5


def test_unknown_is_neutral_8():
    r = score_seniority_fit({"title": "Product", "description": ""}, _cls("unknown", None))
    assert r["score"] == 8


# ── YEARS-REQUIRED ADJUSTMENTS ───────────────────────────────────
def test_years_too_few_penalizes_3():
    """Senior title but 2 yrs required → senior-inappropriate → 15 − 3 = 12."""
    r = score_seniority_fit({"title": "Senior", "description": ""}, _cls("senior", 2))
    assert r["score"] == 12


def test_years_3_to_4_penalty_1():
    r = score_seniority_fit({"title": "Senior", "description": ""}, _cls("senior", 4))
    assert r["score"] == 14


def test_years_sweet_spot_no_adjustment():
    r = score_seniority_fit({"title": "Senior", "description": ""}, _cls("senior", 7))
    assert r["score"] == 15


def test_years_11_to_12_penalty_1():
    r = score_seniority_fit({"title": "Senior", "description": ""}, _cls("senior", 12))
    assert r["score"] == 14


def test_years_over_12_penalty_3():
    r = score_seniority_fit({"title": "Senior", "description": ""}, _cls("senior", 15))
    assert r["score"] == 12


def test_years_none_no_adjustment():
    r = score_seniority_fit({"title": "Senior", "description": ""}, _cls("senior", None))
    assert r["score"] == 15
    assert r["signals"]["years_adjustment"] == 0


# ── GUARDRAIL: adjustment bounded, never overpowers level ────────
def test_years_penalty_cannot_drop_senior_below_junior_score():
    """Even with extreme years penalty, senior (15 - 3 = 12) remains above junior (4)."""
    bad = score_seniority_fit({"title": "Senior", "description": ""}, _cls("senior", 20))
    good = score_seniority_fit({"title": "Junior", "description": ""}, _cls("junior", 6))
    assert bad["score"] > good["score"]


def test_years_penalty_cannot_go_below_zero():
    """entry_level=0 + any penalty still clips to 0."""
    r = score_seniority_fit({"title": "Intern", "description": ""},
                            _cls("entry_level", 1))
    assert r["score"] == 0


# ── HARD-REJECT SAFETY NET ───────────────────────────────────────
def test_hard_reject_zeroes_score():
    """hard_reject=True short-circuits to 0 regardless of level/years."""
    r = score_seniority_fit({"title": "Senior Intern", "description": ""},
                            _cls("senior", 7, hard_reject=True))
    assert r["score"] == 0
    assert r["signals"]["hard_reject_safety_net"] is True
