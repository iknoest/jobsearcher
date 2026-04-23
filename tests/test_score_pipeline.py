"""Tests for src/score/pipeline.py (R3-05).

The pipeline orchestrator runs the 6 sub-scorers + aggregate on a
DataFrame and attaches score + audit columns. It never drops rows —
a row that cannot be scored (no JD body) emerges with recommendation
"Skip" and drop_stage="scored_missing_jd".
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.score import pipeline as sp


# ────────────────────────────────────────────────────────────────
# FIXTURES
# ────────────────────────────────────────────────────────────────

def _row(
    *,
    title="Senior Product Manager",
    company="Acme Robotics",
    description="Lead product discovery and prototyping for our new robotics platform. "
                "Work with R&D, run user testing, validate product-market fit. "
                "5+ years experience in hardware product management required.",
    role_family="product_management",
    role_bucket="desired",
    role_confidence=0.9,
    role_hard_reject=False,
    industry="robotics",
    industry_proximity=0.9,
    industry_source="yaml",
    industry_display="Robotics",
    seniority_level="senior",
    seniority_years_required=5,
    seniority_hard_reject=False,
    seniority_mismatch_flag=False,
    work_mode="hybrid",
    travel_minutes=45,
    salary_min=80000,
    km_visa_sponsor=True,
    job_url="https://example.com/job/1",
    **extra,
):
    base = {
        "title": title,
        "company": company,
        "description": description,
        "role_family": role_family,
        "role_bucket": role_bucket,
        "role_confidence": role_confidence,
        "role_hard_reject": role_hard_reject,
        "industry": industry,
        "industry_proximity": industry_proximity,
        "industry_source": industry_source,
        "industry_display": industry_display,
        "seniority_level": seniority_level,
        "seniority_years_required": seniority_years_required,
        "seniority_hard_reject": seniority_hard_reject,
        "seniority_mismatch_flag": seniority_mismatch_flag,
        "work_mode": work_mode,
        "travel_minutes": travel_minutes,
        "salary_min": salary_min,
        "km_visa_sponsor": km_visa_sponsor,
        "job_url": job_url,
    }
    base.update(extra)
    return base


@pytest.fixture(autouse=True)
def _no_llm_calls(monkeypatch):
    """Force score_hard_skills to use a stubbed jd_skills extractor.

    The real score_hard_skills would hit an LLM or on-disk cache. We
    swap in a deterministic no-op so tests are hermetic — the skill
    extract source is reported as "llm" (because jd_skills was not
    cached and we pretended to call it) so observability counters
    still flow.
    """
    import src.score.pipeline as pipeline_module

    def _fake_score_hard_skills(job, **_kwargs):
        desc = str(job.get("description", "") or "").lower()
        jd_skills = []
        # Crude keyword detection for test determinism
        if "prototype" in desc or "prototyping" in desc:
            jd_skills.append({"name": "prototyping", "importance": "core"})
        if "user testing" in desc:
            jd_skills.append({"name": "user research", "importance": "core"})
        if "validate" in desc or "validation" in desc:
            jd_skills.append({"name": "validation", "importance": "optional"})
        return {
            "score": 20,
            "signals": {
                "jd_skills_count": len(jd_skills),
                "jd_skills_extracted": jd_skills,
                "verified_matches": [],
                "adjacent_matches": [],
                "forbidden_core_hits": [],
                "forbidden_optional_hits": [],
                "negative_hits": [],
                "rejected_by_forbidden_core": False,
                "extraction_source": "llm",
            },
            "reasoning": "fake",
        }

    monkeypatch.setattr(pipeline_module, "score_hard_skills", _fake_score_hard_skills)


# ────────────────────────────────────────────────────────────────
# HAPPY PATH
# ────────────────────────────────────────────────────────────────

def test_run_sub_scorers_attaches_all_columns():
    df = pd.DataFrame([_row()])
    scored, counters = sp.run_sub_scorers(df)

    required = [
        "score_role_fit", "score_hard_skill", "score_seniority_fit",
        "score_industry_proximity", "score_desirability", "score_evidence",
        "final_score", "recommendation", "recommendation_reason",
        "top_reasons", "top_risks", "blockers", "review_flags",
        "drop_stage", "drop_reason",
        "skill_extract_source", "scored_by_llm", "used_skill_cache",
        "llm_stage_skipped_reason",
    ]
    for col in required:
        assert col in scored.columns, f"missing column: {col}"

    assert counters["total"] == 1
    assert counters["scored_ok"] == 1
    assert counters["missing_jd"] == 0


def test_strong_row_lands_in_apply_or_review():
    """A desired role with hard_skill=20, senior, etc. should not be Skip."""
    df = pd.DataFrame([_row()])
    scored, _ = sp.run_sub_scorers(df)
    rec = scored.iloc[0]["recommendation"]
    assert rec in ("Apply", "Review"), f"expected Apply/Review, got {rec}"
    assert scored.iloc[0]["drop_stage"] == "scored"


# ────────────────────────────────────────────────────────────────
# MISSING JD BODY
# ────────────────────────────────────────────────────────────────

def test_empty_description_is_auto_skip_not_scored():
    df = pd.DataFrame([_row(description="")])
    scored, counters = sp.run_sub_scorers(df)

    assert counters["missing_jd"] == 1
    assert counters["scored_ok"] == 0
    row = scored.iloc[0]
    assert row["recommendation"] == "Skip"
    assert row["drop_stage"] == "scored_missing_jd"
    assert row["drop_reason"] == "missing_jd_body"
    assert row["skill_extract_source"] == "skipped_missing_jd"
    assert bool(row["scored_by_llm"]) is False


def test_whitespace_only_description_counts_as_missing():
    df = pd.DataFrame([_row(description="   \n \t ")])
    scored, counters = sp.run_sub_scorers(df)
    assert counters["missing_jd"] == 1
    assert scored.iloc[0]["recommendation"] == "Skip"


# ────────────────────────────────────────────────────────────────
# OBSERVABILITY COUNTERS
# ────────────────────────────────────────────────────────────────

def test_counters_tally_per_recommendation():
    """3 rows, each routing to a different recommendation tier."""
    rows = [
        _row(),  # strong → Apply or Review
        _row(description=""),  # missing JD → Skip (missing_jd)
        _row(  # weak hard_skill (we fake it below), but still scored
            title="Product Manager Mid",
            description="Typical manager role focused on stakeholder alignment and execution.",
            seniority_level="mid",
        ),
    ]
    df = pd.DataFrame(rows)
    scored, counters = sp.run_sub_scorers(df)

    assert counters["total"] == 3
    assert counters["missing_jd"] == 1
    assert counters["scored_ok"] == 2
    assert counters["apply"] + counters["review"] + counters["skip"] == 2
    # skill_llm_calls only increments for rows the fake was actually called on
    assert counters["skill_llm_calls"] == 2


# ────────────────────────────────────────────────────────────────
# COLUMN TYPE SANITY
# ────────────────────────────────────────────────────────────────

def test_score_columns_are_numeric_recommendation_is_string():
    import numbers
    df = pd.DataFrame([_row()])
    scored, _ = sp.run_sub_scorers(df)
    r = scored.iloc[0]
    for col in ("score_role_fit", "score_hard_skill", "score_seniority_fit",
                "score_industry_proximity", "score_desirability",
                "score_evidence", "final_score"):
        assert isinstance(r[col], numbers.Integral), (
            f"{col} should be integer-typed, got {type(r[col])}"
        )
    assert isinstance(r["recommendation"], str)
    assert r["recommendation"] in ("Apply", "Review", "Skip")


def test_empty_dataframe_returns_empty_scored_plus_zero_counters():
    df = pd.DataFrame(columns=["title", "description"])
    scored, counters = sp.run_sub_scorers(df)
    assert len(scored) == 0
    assert counters["total"] == 0
    assert counters["scored_ok"] == 0


# ────────────────────────────────────────────────────────────────
# format_counters helper
# ────────────────────────────────────────────────────────────────

def test_format_counters_renders_human_readable():
    counters = {
        "total": 10, "scored_ok": 8, "missing_jd": 2,
        "apply": 2, "review": 3, "skip": 3,
        "skill_llm_calls": 5, "skill_cache_hits": 3,
        "skill_extraction_failures": 0,
    }
    out = sp.format_counters(counters, elapsed_seconds=1.2)
    assert "Scored: 8/10" in out
    assert "Apply=2" in out
    assert "Review=3" in out
    assert "calls=5" in out
    assert "cache_hits=3" in out
    assert "Elapsed: 1.2s" in out
