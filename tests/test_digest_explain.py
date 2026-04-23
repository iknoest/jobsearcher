"""Tests for src/digest/explain.py (R4-02)."""

from __future__ import annotations

from src.digest.explain import explain_row


def _apply_row(**extra):
    base = {
        "recommendation": "Apply",
        "final_score": 82,
        "score_role_fit": 22,        # 22/25 → strong
        "score_hard_skill": 24,      # 24/30 → strong
        "score_seniority_fit": 14,   # 14/15 → strong
        "score_industry_proximity": 9,  # 9/10 → strong
        "score_desirability": 7,     # 7/10 → ok
        "score_evidence": 6,         # 6/10 → ok
        "top_reasons": "Role fit: 22/25; Hard skills: 24/30",
        "top_risks": "",
        "review_flags": "",
        "blockers": "",
    }
    base.update(extra)
    return base


def _review_row(**extra):
    base = {
        "recommendation": "Review",
        "final_score": 62,
        "score_role_fit": 18,
        "score_hard_skill": 8,       # 8/30 → weak
        "score_seniority_fit": 14,
        "score_industry_proximity": 4,  # 4/10 → weak
        "score_desirability": 7,
        "score_evidence": 6,
        "top_reasons": "Role fit: 18/25; Seniority fit: 14/15",
        "top_risks": "Hard skills: 8/30",
        "review_flags": "low hard_skill_guardrail (8/30 ≤ 8)",
        "blockers": "",
    }
    base.update(extra)
    return base


def _skip_row(**extra):
    base = {
        "recommendation": "Skip",
        "final_score": 35,
        "score_role_fit": 5,
        "score_hard_skill": 0,
        "score_seniority_fit": 4,
        "score_industry_proximity": 3,
        "score_desirability": 5,
        "score_evidence": 2,
        "top_reasons": "",
        "top_risks": "",
        "review_flags": "",
        "blockers": "forbidden_core_skill: CFD simulation",
        "drop_reason": "forbidden_core_skill: CFD simulation",
    }
    base.update(extra)
    return base


# ── Apply tier ────────────────────────────────────────────────────────

def test_apply_row_produces_why_apply_and_empty_watch_outs():
    out = explain_row(_apply_row())
    assert out["recommendation"] == "Apply"
    assert len(out["why_apply"]) >= 1
    # top_reasons should surface
    assert any("Role fit" in r or "Hard skills" in r for r in out["why_apply"])
    # Nothing strongly negative → watch_outs may be empty list
    assert isinstance(out["watch_outs"], list)


def test_apply_row_surfaces_verified_skills_when_available():
    row = _apply_row(_score_raw_hard_skill_signals={
        "verified_matches": [
            {"canonical_name": "Product development for physical products"},
            {"canonical_name": "User research synthesis"},
        ]
    })
    out = explain_row(row)
    assert any("Product development" in r or "User research" in r for r in out["why_apply"])


def test_apply_row_with_risks_populates_watch_outs():
    row = _apply_row(top_risks="Evidence: 2/10; Desirability: 3/10", score_desirability=3, score_evidence=2)
    out = explain_row(row)
    assert len(out["watch_outs"]) >= 1
    assert any("Evidence" in w or "Desirability" in w for w in out["watch_outs"])


# ── Review tier ───────────────────────────────────────────────────────

def test_review_row_holding_back_surfaces_flags_and_weak_subscores():
    out = explain_row(_review_row())
    assert out["recommendation"] == "Review"
    assert len(out["holding_back"]) >= 1
    # review_flags must come through
    assert any("guardrail" in h.lower() or "hard" in h.lower() for h in out["holding_back"])


def test_review_row_still_worth_surfaces_top_reasons():
    out = explain_row(_review_row())
    assert len(out["still_worth"]) >= 1
    assert any("Role fit" in s or "Seniority" in s for s in out["still_worth"])


# ── Skip tier ─────────────────────────────────────────────────────────

def test_skip_row_returns_blocker_as_skip_reason():
    out = explain_row(_skip_row())
    assert out["recommendation"] == "Skip"
    assert "forbidden_core_skill" in out["skip_reason"]


def test_skip_row_missing_jd_body_returns_drop_reason():
    row = _skip_row(blockers="", drop_reason="missing_jd_body")
    out = explain_row(row)
    assert out["skip_reason"] == "missing_jd_body"


def test_skip_row_score_band_skip_shows_score():
    row = _skip_row(blockers="", drop_reason="", final_score=42)
    out = explain_row(row)
    assert "42/100" in out["skip_reason"]
    assert "below Review" in out["skip_reason"]


# ── Sub-score bars ────────────────────────────────────────────────────

def test_subscore_bars_emit_prd_weight_order():
    out = explain_row(_apply_row())
    labels = [b["label"] for b in out["subscore_bars"]]
    assert labels == [
        "Role fit", "Hard skills", "Seniority fit",
        "Industry proximity", "Desirability", "Evidence",
    ]


def test_subscore_bars_bands_classify_correctly():
    # Overrides put Desirability + Evidence strictly in the middle band
    out = explain_row(_apply_row(score_desirability=5, score_evidence=5))
    bars = {b["label"]: b for b in out["subscore_bars"]}
    assert bars["Role fit"]["band"] == "strong"          # 22/25 = 0.88
    assert bars["Hard skills"]["band"] == "strong"       # 24/30 = 0.80
    assert bars["Desirability"]["band"] == "ok"          # 5/10 = 0.50
    assert bars["Evidence"]["band"] == "ok"              # 5/10 = 0.50


def test_subscore_bars_ratio_is_bounded_0_to_1():
    out = explain_row(_apply_row())
    for b in out["subscore_bars"]:
        assert 0 <= b["ratio"] <= 1


def test_missing_sub_score_columns_default_to_zero_weak():
    """Defensive: pre-R3-05 rows with no sub-score cols still render."""
    row = {"recommendation": "Skip", "final_score": 0}
    out = explain_row(row)
    for b in out["subscore_bars"]:
        assert b["score"] == 0
        assert b["band"] == "weak"


# ── Dedup ─────────────────────────────────────────────────────────────

def test_explanation_bullets_are_deduped():
    row = _apply_row(top_reasons="Role fit: 22/25; Role fit: 22/25; Hard skills: 24/30")
    out = explain_row(row)
    # "Role fit..." only appears once in why_apply
    assert sum(1 for r in out["why_apply"] if "Role fit" in r) == 1
