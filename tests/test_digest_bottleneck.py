"""Tests for src/digest/bottleneck.py (R4-02)."""

from __future__ import annotations

from src.digest.bottleneck import (
    compute_bottleneck,
    compute_funnel,
    compute_trust_status_line,
)


def _funnel(**kw):
    base = dict(
        total_scraped=100,
        pre_scoring_filtered=20,
        scored_total=60,
        scored_but_skip=30,
        below_max_jobs_cap=5,
        apply_count=5,
        review_count=10,
    )
    base.update(kw)
    return compute_funnel(**base)


# ── compute_funnel ────────────────────────────────────────────────────

def test_funnel_adds_up_and_computes_percentages():
    f = _funnel()
    assert f["total_scraped"] == 100
    assert f["pre_scoring_filtered"] == 20
    assert f["scored_total"] == 60
    assert f["surfaced"] == 15  # 5 apply + 10 review
    assert f["not_surfaced_scored"] == 30  # scored_but_skip
    assert f["filtered_pct"] == 20  # 20/100
    assert f["skip_pct_of_scored"] == 50  # 30/60
    assert f["surfaced_pct"] == 15  # 15/100


def test_funnel_handles_zero_total_scraped():
    f = _funnel(total_scraped=0, pre_scoring_filtered=0, scored_total=0, scored_but_skip=0, apply_count=0, review_count=0)
    assert f["filtered_pct"] == 0
    assert f["skip_pct_of_scored"] == 0


# ── compute_bottleneck ────────────────────────────────────────────────

def test_bottleneck_nothing_surfaced_all_filtered():
    f = _funnel(total_scraped=100, pre_scoring_filtered=100, scored_total=0, scored_but_skip=0, apply_count=0, review_count=0)
    assert "all rows filtered or missing JD" in compute_bottleneck(f)


def test_bottleneck_nothing_surfaced_all_skip():
    f = _funnel(total_scraped=100, pre_scoring_filtered=10, scored_total=60, scored_but_skip=60, apply_count=0, review_count=0)
    assert "every scored row Skip-tier" in compute_bottleneck(f)


def test_bottleneck_most_filtered_before_scoring():
    f = _funnel(total_scraped=100, pre_scoring_filtered=70, scored_total=20, scored_but_skip=5, apply_count=5, review_count=10)
    msg = compute_bottleneck(f)
    assert "filtered before scoring" in msg
    assert "70/100" in msg


def test_bottleneck_low_hard_skill():
    f = _funnel()
    msg = compute_bottleneck(f, avg_hard_skill=6.0)
    assert "Low hard-skill fit" in msg
    assert "6.0" in msg


def test_bottleneck_most_scored_skip():
    f = _funnel(scored_total=20, scored_but_skip=18, apply_count=1, review_count=1)
    msg = compute_bottleneck(f)
    assert "Skip-tier" in msg


def test_bottleneck_no_apply_only_review():
    f = _funnel(apply_count=0, review_count=8, scored_but_skip=10, scored_total=30)
    assert compute_bottleneck(f) == "No Apply-tier matches today — 8 Review only"


def test_bottleneck_healthy_day_returns_none():
    f = _funnel(total_scraped=100, pre_scoring_filtered=15, scored_total=60, scored_but_skip=30, apply_count=10, review_count=20)
    assert compute_bottleneck(f, avg_hard_skill=18.0) is None


# ── compute_trust_status_line ─────────────────────────────────────────

def test_trust_line_includes_borderline_when_present():
    line = compute_trust_status_line(apply_count=8, review_count=14, borderline_skip_count=5)
    assert line == "8 Apply, 14 Review, 5 borderline skips worth audit"


def test_trust_line_omits_borderline_when_zero():
    line = compute_trust_status_line(apply_count=2, review_count=3, borderline_skip_count=0)
    assert line == "2 Apply, 3 Review"


def test_trust_line_singular_borderline():
    line = compute_trust_status_line(apply_count=0, review_count=0, borderline_skip_count=1)
    assert "1 borderline skip worth audit" in line
