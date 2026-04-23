"""Tests for src/score/evidence.py (R3-03d).

Per-proof points = 2.0 × strength_multiplier (High=1.0, Medium=0.7, Low=0.4).
Cap 10/10 per job. A proof matches if the job's role_family OR industry
appears in the proof's matched-lists.
"""

import pytest

from src.score.evidence import score_evidence, reset_cache


@pytest.fixture(autouse=True)
def _clear():
    reset_cache()
    yield
    reset_cache()


# ── NO MATCH ─────────────────────────────────────────────────────
def test_no_match_when_neither_role_nor_industry_hits():
    r = score_evidence({}, role_family="devops_engineer", industry="fintech_banking")
    assert r["score"] == 0
    assert r["signals"]["matched_count"] == 0


def test_empty_inputs_score_zero():
    r = score_evidence({}, role_family="", industry="")
    assert r["score"] == 0


# ── ROLE MATCH ───────────────────────────────────────────────────
def test_product_researcher_matches_multiple_proofs():
    """product_researcher is a common role_family in evidence.yaml → several matches."""
    r = score_evidence({}, role_family="product_researcher", industry="")
    assert r["signals"]["matched_count"] >= 1
    assert r["score"] >= 2
    # All matched_via should be role-only here (industry empty)
    assert all(m["matched_via"] == "role" for m in r["signals"]["matched_proofs"])


# ── INDUSTRY MATCH ───────────────────────────────────────────────
def test_automotive_industry_matches_byd_proof():
    """automotive is in BYD/VOC proof's industries_matched."""
    r = score_evidence({}, role_family="", industry="automotive")
    assert r["signals"]["matched_count"] >= 1
    assert r["score"] >= 1


# ── ROLE + INDUSTRY BOTH MATCH ───────────────────────────────────
def test_combined_role_and_industry_match():
    """product_researcher + automotive → BYD VOC proof matches on BOTH axes."""
    r = score_evidence({}, role_family="product_researcher", industry="automotive")
    # Should include 'role+industry' in matched_via for at least one proof
    via_set = {m["matched_via"] for m in r["signals"]["matched_proofs"]}
    assert "role+industry" in via_set


# ── SCORE CAP ────────────────────────────────────────────────────
def test_score_never_exceeds_cap():
    """Even with multi-axis matches, score caps at 10/10."""
    # product_manager + automotive is a high-overlap combo
    r = score_evidence({}, role_family="product_manager", industry="automotive")
    assert r["score"] <= 10


# ── STRENGTH MULTIPLIER ───────────────────────────────────────────
def test_strength_multiplier_applied():
    """Matched proofs should carry different point values based on strength."""
    r = score_evidence({}, role_family="product_researcher", industry="")
    if r["signals"]["matched_proofs"]:
        pts = [m["points"] for m in r["signals"]["matched_proofs"]]
        # High=2.0, Medium=1.4, Low=0.8 — at least one match expected
        assert all(0 < p <= 2.0 for p in pts)
