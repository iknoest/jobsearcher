"""Tests for src/score/role_fit.py (R3-01).

User-locked bands (2026-04-22):
    desired          15 → 20
    ambiguous        10 → 14
    excluded+rescued  6 → 10
    excluded+reject   0
    unknown           6
Bonus cap +5 (strong +1, weak +0.5). Penalty cap −3.
`roadmap alignment` fires ONLY when a hard anti-pattern also hits.
"""

import pytest

from src.score.role_fit import score_role_fit


def _cls(bucket, confidence=0.5, would_hard_reject=False,
         display_name="Test", title_match=None):
    return {
        "family": "test_family",
        "bucket": bucket,
        "confidence": confidence,
        "display_name": display_name,
        "evidence_spans": {
            "title_match": title_match or [],
            "confirming_jd": [],
            "rescuing_jd": [],
            "negative_jd": [],
        },
        "would_hard_reject": would_hard_reject,
        "secondary_candidates": [],
        "reasoning": "test",
    }


# ── BASE SCORES: bucket × confidence caps ────────────────────────
def test_desired_low_confidence_hits_floor():
    """desired + confidence=0.4 → base 15 (the floor)."""
    r = score_role_fit({"title": "PM", "description": ""}, _cls("desired", 0.4))
    assert r["score"] == 15
    assert r["signals"]["bucket"] == "desired"


def test_desired_high_confidence_hits_cap():
    """desired + confidence=1.0 → base 20 (the cap, not 25)."""
    r = score_role_fit({"title": "PM", "description": ""}, _cls("desired", 1.0))
    assert r["score"] == 20


def test_ambiguous_caps_at_14():
    r = score_role_fit({"title": "PMM", "description": ""}, _cls("ambiguous", 1.0))
    assert r["score"] == 14


def test_rescued_excluded_caps_at_10():
    r = score_role_fit(
        {"title": "Software Engineer", "description": ""},
        _cls("excluded", 1.0, would_hard_reject=False),
    )
    assert r["signals"]["rescued"] is True
    assert r["score"] == 10


def test_excluded_hard_reject_zeroes():
    """Safety net — a hard-reject excluded role that slipped through scores 0."""
    r = score_role_fit(
        {"title": "DevOps Engineer", "description": ""},
        _cls("excluded", 1.0, would_hard_reject=True),
    )
    assert r["score"] == 0


def test_unknown_returns_neutral_6():
    r = score_role_fit({"title": "Manager", "description": ""}, _cls("unknown", 0.0))
    assert r["score"] == 6


# ── DELIVERABLE BONUS ────────────────────────────────────────────
def test_strong_deliverables_add_bonus():
    """desired base 15 + 3 strong hits → 18. research/prototype/validate are all strong."""
    jd = "You will lead user research, run prototype sprints, and validate concepts."
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 0.4)
    )
    assert set(r["signals"]["deliverable_hits"]) >= {"research", "prototype", "validate"}
    assert r["score"] == 18  # 15 base + 3 bonus


def test_weak_deliverables_count_half():
    """desired base 15 + 2 weak hits → 15 + 1 → 16."""
    jd = "Hands-on role in a 0→1 environment."
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 0.4)
    )
    assert len(r["signals"]["weak_deliverable_hits"]) >= 2
    assert r["score"] == 16  # 15 base + 1 bonus (0.5 + 0.5)


def test_bonus_caps_at_5():
    """Many deliverable hits → bonus caps at +5, not +7."""
    jd = (
        "Own product research, prototype, validate, discovery, benchmark, "
        "requirements, feature definition, insight synthesis."
    )
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 0.4)
    )
    assert r["score"] == 20  # 15 base + 5 (capped)


def test_new_keywords_grounded_in_real_work():
    """'benchmark', 'requirements', 'feature definition', 'insight synthesis' must fire."""
    jd = (
        "Own the benchmark studies, drive requirements, feature definition, "
        "and insight synthesis."
    )
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 0.4)
    )
    hits = set(r["signals"]["deliverable_hits"])
    assert {"benchmark", "requirements", "feature definition", "insight synthesis"} <= hits


# ── ANTI-PATTERN PENALTY ─────────────────────────────────────────
def test_hard_anti_pattern_penalty():
    """desired base 20 − 2 hard anti-pattern hits → 18."""
    jd = "Deliver PPT decks in steering committee reviews."
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 1.0)
    )
    hits = set(h.lower() for h in r["signals"]["anti_pattern_hits"])
    assert {"ppt", "steering committee"} <= hits
    assert r["score"] == 18


def test_penalty_caps_at_3():
    """4 anti-patterns → penalty caps at −3, not −4."""
    jd = "Waterfall roadmap with PPT-only decks, slide deck reviews, steering committee."
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 1.0)
    )
    assert r["score"] == 17  # 20 base − 3 (capped)


# ── ROADMAP ALIGNMENT COMBO RULE ─────────────────────────────────
def test_roadmap_alignment_alone_does_not_penalize():
    """'roadmap alignment' in an otherwise clean JD must NOT trigger the penalty."""
    jd = "Drive roadmap alignment with a small product team."
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 1.0)
    )
    assert r["signals"]["roadmap_alignment_combo"] is False
    assert r["score"] == 20  # no penalty


def test_roadmap_alignment_combo_triggers_with_hard_anti_pattern():
    """'roadmap alignment' + 'waterfall' fires the combo penalty."""
    jd = "Drive roadmap alignment across a waterfall portfolio."
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 1.0)
    )
    assert r["signals"]["roadmap_alignment_combo"] is True
    # 20 base − (1 for waterfall + 1 for combo) = 18
    assert r["score"] == 18


def test_cross_functional_alignment_no_longer_penalizes():
    """User removed 'cross-functional alignment' and 'stakeholder alignment' from anti-patterns
    2026-04-22 — they appear in real good roles."""
    jd = (
        "Partner with cross-functional alignment across engineering and design. "
        "Stakeholder alignment is part of the role."
    )
    r = score_role_fit(
        {"title": "PM", "description": jd}, _cls("desired", 1.0)
    )
    assert r["signals"]["anti_pattern_hits"] == []
    assert r["score"] == 20


# ── INTEGRATION: live classifier path ───────────────────────────
def test_runs_classifier_when_classified_is_none():
    """When no classified dict is passed, scorer runs classify_role_family internally."""
    r = score_role_fit({
        "title": "Product Researcher",
        "description": "Run user research, prototyping, and validation studies.",
    })
    # Exact score depends on classifier output, but should be > 15 (desired + bonuses)
    assert r["score"] >= 15
    assert r["signals"]["bucket"] in ("desired", "ambiguous", "unknown")


# ── CLIP BOUNDARY ────────────────────────────────────────────────
def test_hard_reject_stays_zero_despite_deliverables():
    """A hard-reject excluded role must stay at 0 even if the JD is loaded with
    strong deliverable keywords — bonuses cannot override the safety net."""
    jd = "Research, prototype, validate, experiment, discovery, benchmark, requirements."
    r = score_role_fit(
        {"title": "Dev Ops Engineer", "description": jd},
        _cls("excluded", 1.0, would_hard_reject=True),
    )
    assert r["score"] == 0


def test_final_score_clips_to_25():
    """Even if somehow bonuses/base exceed cap, final stays ≤ 25."""
    r = score_role_fit(
        {"title": "PM", "description": "research prototype validate benchmark requirements"},
        _cls("desired", 1.0),
    )
    assert r["score"] <= 25
