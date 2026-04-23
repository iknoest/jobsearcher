"""Tests for src/score/industry_proximity.py (R3-03b).

Unknown industry → neutral-low fallback 4/10 (per R1-03 scorer_defaults).
Anchor industries (proximity 1.0) → 10/10. Never hard-rejects.
"""

import pytest

from src.score.industry_proximity import score_industry_proximity, reset_cache


@pytest.fixture(autouse=True)
def _clear():
    reset_cache()
    yield
    reset_cache()


def _cls(industry, proximity, source="company"):
    return {
        "industry": industry,
        "display_name": industry.replace("_", " ").title(),
        "nace_code": None,
        "proximity_to_ava": proximity,
        "evidence": {"company_match": None, "jd_match": None},
        "source": source,
        "secondary_candidates": [],
        "reasoning": "test",
    }


# ── ANCHOR (1.0) → 10 ────────────────────────────────────────────
def test_anchor_scores_10():
    r = score_industry_proximity({"company": "BYD"}, _cls("automotive", 1.0))
    assert r["score"] == 10
    assert r["signals"]["proximity_to_ava"] == 1.0


# ── CLOSE / MEDIUM / FAR bands ──────────────────────────────────
def test_close_08_scores_8():
    r = score_industry_proximity({"company": "Signify"}, _cls("smart_home_appliances", 0.8))
    assert r["score"] == 8


def test_medium_05_scores_5():
    r = score_industry_proximity({"company": "X"}, _cls("pharma_life_sciences", 0.5))
    assert r["score"] == 5


def test_far_02_scores_2():
    r = score_industry_proximity({"company": "Rabobank"}, _cls("fintech_banking", 0.2))
    assert r["score"] == 2


def test_very_far_0_scores_0():
    r = score_industry_proximity({"company": "Aramco"}, _cls("oil_gas_heavy_industry", 0.0))
    assert r["score"] == 0


# ── UNKNOWN → fallback 4 ─────────────────────────────────────────
def test_unknown_proximity_none_scores_fallback_4():
    r = score_industry_proximity(
        {"company": "Mystery Co"},
        _cls("unknown", None, source="unknown"),
    )
    assert r["score"] == 4
    assert "fallback" in r["reasoning"].lower()


# ── EDGE: proximity > 1.0 or < 0 clamps ─────────────────────────
def test_proximity_above_1_clamps_to_10():
    r = score_industry_proximity({"company": "X"}, _cls("weird", 1.5))
    assert r["score"] == 10


def test_proximity_negative_clamps_to_0():
    r = score_industry_proximity({"company": "X"}, _cls("weird", -0.5))
    assert r["score"] == 0


# ── LIVE CLASSIFIER PATH ─────────────────────────────────────────
def test_runs_classifier_when_not_supplied():
    """When classified=None, scorer runs classify_industry internally."""
    r = score_industry_proximity({"company": "BYD Europe", "description": "EV work"})
    assert r["score"] == 10  # BYD company-match → automotive anchor
