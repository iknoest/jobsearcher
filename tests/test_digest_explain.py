"""Tests for src/digest/explain.py (R4-02)."""

from __future__ import annotations

from src.digest.explain import explain_row


def _apply_row(**extra):
    base = {
        "recommendation": "Apply",
        "title": "Senior Product Manager",
        "company": "Acme Robotics",
        "role_family": "product_manager",
        "industry": "robotics",
        "industry_display": "Robotics",
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


# ── One-liner ────────────────────────────────────────────────────────

def test_one_liner_has_role_industry_focus():
    row = _apply_row(_score_raw_hard_skill_signals={
        "verified_matches": [
            {"canonical_name": "Prototyping"},
            {"canonical_name": "User research synthesis"},
        ]
    })
    out = explain_row(row)
    ol = out["one_liner"]
    assert "Product role" in ol
    assert "Robotics" in ol
    assert "prototyping" in ol.lower() or "user research" in ol.lower()


def test_one_liner_falls_back_gracefully_when_signals_missing():
    row = _apply_row(role_family="", industry="", industry_display="",
                     _score_raw_hard_skill_signals={})
    out = explain_row(row)
    # Doesn't crash; has the shape "Role in ..., focused on ..."
    assert " in " in out["one_liner"]
    assert "focused on" in out["one_liner"]


# ── Apply tier ────────────────────────────────────────────────────────

def test_apply_row_produces_why_fits_and_empty_watch_outs():
    out = explain_row(_apply_row())
    assert out["recommendation"] == "Apply"
    assert len(out["why_fits"]) >= 1
    assert any("Role fit" in r or "Hard skills" in r or "Strong fit" in r for r in out["why_fits"])
    assert isinstance(out["watch_outs"], list)


def test_apply_row_surfaces_verified_skills_as_matched_chip_and_bullet():
    row = _apply_row(_score_raw_hard_skill_signals={
        "verified_matches": [
            {"canonical_name": "Product development for physical products"},
            {"canonical_name": "User research synthesis"},
        ]
    })
    out = explain_row(row)
    # Matched skill chip surface
    assert "Product development for physical products" in out["matched_skills"]
    # And surfaced in the "why_fits" bullet
    assert any("Strong fit" in r for r in out["why_fits"])


def test_apply_row_with_risks_populates_watch_outs():
    row = _apply_row(top_risks="Evidence: 2/10; Desirability: 3/10", score_desirability=3, score_evidence=2)
    out = explain_row(row)
    assert len(out["watch_outs"]) >= 1


def test_gap_skills_surface_forbidden_core_and_optional():
    row = _apply_row(_score_raw_hard_skill_signals={
        "forbidden_core_hits": [{"canonical_name": "CFD simulation"}],
        "forbidden_optional_hits": [{"canonical_name": "Java enterprise"}],
    })
    out = explain_row(row)
    assert "CFD simulation" in out["gap_skills"]
    assert "Java enterprise" in out["gap_skills"]


def test_evidence_anchors_surface_matched_proofs():
    row = _apply_row(_score_raw_evidence_signals={
        "matched_proofs": [
            {"topic": "BYD VOC", "strength": "High", "matched_via": "industry"},
            {"topic": "TomTom data viz", "strength": "Medium", "matched_via": "role"},
        ]
    })
    out = explain_row(row)
    assert "BYD VOC" in out["evidence_anchors"]
    assert any("BYD VOC" in b for b in out["why_fits"])


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
    # Softened label keeps the concrete skill name, drops the code prefix
    assert "CFD simulation" in out["skip_reason"]


def test_skip_row_score_band_skip_shows_score_in_plain_language():
    row = _skip_row(blockers="", drop_reason="", final_score=42)
    out = explain_row(row)
    assert "42/100" in out["skip_reason"]
    # Plain language ("just below the Review threshold"), NOT internal "Review floor"
    assert "below the Review threshold" in out["skip_reason"]
    assert "Review floor" not in out["skip_reason"]


def test_skip_row_softens_forbidden_core_label():
    row = _skip_row(blockers="forbidden_core_skill: CFD simulation")
    out = explain_row(row)
    # No raw "forbidden_core_skill:" prefix leaks to user
    assert "forbidden_core_skill" not in out["skip_reason"]
    assert "CFD simulation" in out["skip_reason"]


def test_skip_row_missing_jd_body_is_plain_language():
    row = _skip_row(blockers="", drop_reason="missing_jd_body")
    out = explain_row(row)
    assert "job description" in out["skip_reason"].lower()
    assert "missing_jd_body" not in out["skip_reason"]


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
    # "Role fit..." only appears once across why_fits bullets
    assert sum(1 for r in out["why_fits"] if "Role fit" in r) <= 1
