"""Tests for src/digest/render.py (R4-02) — smoke-level HTML shape."""

from __future__ import annotations

import pandas as pd
import pytest

from src.digest.bottleneck import compute_funnel
from src.digest.render import render_trust_digest


def _row(**extra):
    base = {
        "job_url": "https://www.linkedin.com/jobs/view/4400000001",
        "title": "Senior Product Manager",
        "company": "Acme",
        "location": "Amsterdam, NL",
        "work_mode": "hybrid",
        "travel_minutes": 45,
        "recommendation": "Apply",
        "final_score": 82,
        "score_role_fit": 22,
        "score_hard_skill": 24,
        "score_seniority_fit": 14,
        "score_industry_proximity": 9,
        "score_desirability": 7,
        "score_evidence": 6,
        "top_reasons": "Role fit: 22/25; Hard skills: 24/30",
        "top_risks": "",
        "review_flags": "",
        "blockers": "",
    }
    base.update(extra)
    return base


def _sample_df():
    return pd.DataFrame([
        _row(),
        _row(
            job_url="https://www.linkedin.com/jobs/view/4400000002",
            title="Innovation PM",
            company="Robotics BV",
            recommendation="Review",
            final_score=63,
            score_hard_skill=8,
            top_risks="Hard skills: 8/30",
            review_flags="low hard_skill_guardrail (8/30 ≤ 8)",
        ),
        _row(
            job_url="https://www.linkedin.com/jobs/view/4400000003",
            title="Simulation Engineer",
            company="SimCorp",
            recommendation="Skip",
            final_score=35,
            score_role_fit=5, score_hard_skill=0, score_seniority_fit=4,
            score_industry_proximity=3, score_desirability=5, score_evidence=2,
            top_reasons="",
            blockers="forbidden_core_skill: CFD simulation",
        ),
        _row(
            job_url="https://www.linkedin.com/jobs/view/4400000004",
            title="Edge Case Borderline",
            company="Fringe Co",
            recommendation="Skip",
            final_score=47,
        ),
    ])


def _funnel_default():
    return compute_funnel(
        total_scraped=100,
        pre_scoring_filtered=20,
        scored_total=60,
        scored_but_skip=30,
        below_max_jobs_cap=5,
        apply_count=1,
        review_count=1,
    )


# ── Smoke tests ───────────────────────────────────────────────────────

def test_render_returns_html_string():
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="")
    assert isinstance(html, str)
    assert html.lstrip().startswith("<!DOCTYPE html>") or html.lstrip().startswith("<!doctype")


def test_render_includes_trust_status_line():
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="")
    # Header renders the counts line directly (no "Trust status today:" prefix)
    assert "1 Apply" in html
    assert "1 Review" in html


def test_render_includes_bottleneck_when_provided():
    html = render_trust_digest(
        _sample_df(),
        funnel=_funnel_default(),
        bottleneck_headline="Low hard-skill fit across the board (avg 7.5/30)",
        tg_bot_username="",
    )
    assert "Main bottleneck today" in html
    assert "Low hard-skill fit" in html


def test_render_apply_section_has_card():
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="")
    assert "Apply (1)" in html
    assert "Senior Product Manager" in html
    # New copy: "Why it may fit"
    assert "Why it may fit" in html


def test_render_review_section_has_holding_back():
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="")
    assert "Review (1)" in html
    assert "holding this back" in html.lower()


def test_render_skip_section_collapsed_with_top_reasons_and_borderline():
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="")
    assert "Skip (2)" in html or "Skip (2) — collapsed" in html
    assert "Top skip reasons" in html
    assert "Borderline skips worth audit" in html
    # The 47-score Skip row should appear in the borderline block
    assert "Edge Case Borderline" in html


def test_render_funnel_block_numbers_visible():
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="")
    assert "FILTERED (before scoring)" in html
    # Plain-language: "SCORED BUT NOT SHOWN" instead of "NOT SURFACED"
    assert "NOT SHOWN" in html
    assert "SHOWN IN TODAY" in html


def test_render_llm_usage_block_visible():
    html = render_trust_digest(
        _sample_df(),
        funnel=_funnel_default(),
        llm_evaluated=12, llm_skipped_by_gates=30,
        sub_counters={"skill_llm_calls": 8, "skill_cache_hits": 4, "skill_extraction_failures": 0},
        tg_bot_username="",
    )
    assert "LLM usage today" in html
    assert "evaluated" in html
    assert "skipped by hard gates" in html
    assert "cache hits" in html


def test_render_scope_line_when_total_new_provided():
    html = render_trust_digest(
        _sample_df(),
        funnel=_funnel_default(),
        total_new=125, role_groups=7, hours_window=24,
        tg_bot_username="",
    )
    assert "125 new positions" in html
    assert "7 role groups" in html
    assert "past 24 hours" in html


def test_render_preview_banner_only_in_preview_mode():
    html_off = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="", preview_mode=False)
    assert "Preview:" not in html_off
    html_on = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="", preview_mode=True)
    assert "Preview:" in html_on
    assert "placeholders" in html_on


def test_render_pending_feedback_comes_after_review_section():
    pending = [{"job_url": "https://x.com/j/1", "title": "PrevJob", "company": "Co", "recommendation": "Apply", "final_score": 81}]
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), pending_feedback=pending, tg_bot_username="")
    review_idx = html.find("Review (1)")
    pending_idx = html.find("Feedback still pending")
    assert review_idx > 0 and pending_idx > 0
    assert pending_idx > review_idx, "Pending feedback must render AFTER the Review section"


def test_render_borderline_cards_have_full_actions_when_tg_enabled():
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="jobsearch_bot")
    # Borderline card section present
    assert "Borderline skips worth audit" in html
    # Our Edge Case Borderline (score 47) should be in there with action buttons
    assert "Edge Case Borderline" in html
    # Per-borderline actions: both Open JD and Keep/Reject/Note should be present
    bl_idx = html.find("Borderline skips worth audit")
    after_borderline = html[bl_idx:bl_idx + 2500]
    assert 'class="btn keep"' in after_borderline
    assert 'class="btn reject"' in after_borderline
    assert 'class="btn note"' in after_borderline


def test_render_tg_buttons_only_when_bot_configured():
    html_off = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="")
    assert "Keep" not in html_off or 'class="btn keep"' not in html_off

    html_on = render_trust_digest(_sample_df(), funnel=_funnel_default(), tg_bot_username="jobsearch_bot")
    assert 'class="btn keep"' in html_on
    assert 'class="btn reject"' in html_on
    assert 'class="btn note"' in html_on


def test_render_pending_feedback_block_appears_when_populated():
    pending = [
        {"job_url": "https://www.linkedin.com/jobs/view/4399999999", "title": "Prev Apply", "company": "Co", "recommendation": "Apply", "final_score": 81},
    ]
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), pending_feedback=pending, tg_bot_username="")
    assert "Feedback still pending" in html
    assert "Prev Apply" in html


def test_render_feedback_summary_block_appears_when_activity_exists():
    summary = {"kept": 2, "rejected": 3, "notes": 1, "themes": [{"label": "Too technical", "count": 2}]}
    html = render_trust_digest(_sample_df(), funnel=_funnel_default(), feedback_summary=summary, tg_bot_username="")
    assert "Your feedback since last digest" in html
    assert "Too technical" in html


def test_render_handles_empty_dataframe():
    empty = pd.DataFrame(columns=["job_url", "title", "company", "recommendation", "final_score"])
    html = render_trust_digest(empty, funnel=compute_funnel(0, 0, 0, 0, 0, 0, 0), tg_bot_username="")
    # Does not crash; header renders with 0s
    assert "0 Apply" in html and "0 Review" in html
    assert "Funnel today" in html
