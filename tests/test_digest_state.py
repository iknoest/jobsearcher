"""Tests for src/digest/state.py (R4-02)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.digest.state import (
    compute_pending_feedback,
    load_last_digest_snapshot,
    save_last_digest_snapshot,
    summarize_recent_feedback,
)


@pytest.fixture
def tmp_snapshot(tmp_path: Path) -> Path:
    return tmp_path / "last_digest.json"


# ── save / load snapshot ──────────────────────────────────────────────

def test_save_then_load_preserves_row_fields(tmp_snapshot):
    rows = [
        {"job_url": "u1", "title": "T1", "company": "C1", "recommendation": "Apply", "final_score": 82},
        {"job_url": "u2", "title": "T2", "company": "C2", "recommendation": "Review", "final_score": 63},
    ]
    save_last_digest_snapshot(rows, path=tmp_snapshot)
    loaded = load_last_digest_snapshot(path=tmp_snapshot)
    assert loaded is not None
    assert loaded["rows"][0]["job_url"] == "u1"
    assert loaded["rows"][0]["recommendation"] == "Apply"
    assert loaded["rows"][1]["final_score"] == 63


def test_load_snapshot_returns_none_when_missing(tmp_snapshot):
    assert load_last_digest_snapshot(path=tmp_snapshot) is None


def test_load_snapshot_returns_none_on_corrupt_file(tmp_snapshot):
    tmp_snapshot.write_text("{ not json", encoding="utf-8")
    assert load_last_digest_snapshot(path=tmp_snapshot) is None


def test_save_creates_parent_directory(tmp_path):
    nested = tmp_path / "nested" / "sub" / "last.json"
    save_last_digest_snapshot([{"job_url": "u", "title": "t", "company": "c", "recommendation": "Apply", "final_score": 80}], path=nested)
    assert nested.exists()


# ── Pending feedback ──────────────────────────────────────────────────

def test_pending_is_rows_without_active_verdict():
    last = {
        "rows": [
            {"job_url": "u1", "title": "T1", "company": "C1", "recommendation": "Apply", "final_score": 80},
            {"job_url": "u2", "title": "T2", "company": "C2", "recommendation": "Review", "final_score": 60},
        ]
    }
    feedback = {
        "user_verdicts": [
            {"job_url": "u1", "user_verdict": "Good Match", "superseded_by": None},
        ]
    }
    pending = compute_pending_feedback(last, feedback)
    assert [p["job_url"] for p in pending] == ["u2"]


def test_pending_ignores_superseded_verdicts():
    """A superseded verdict does not count as 'responded'."""
    last = {"rows": [{"job_url": "u1", "title": "T", "company": "C", "recommendation": "Apply", "final_score": 80}]}
    feedback = {
        "user_verdicts": [
            {"job_url": "u1", "user_verdict": "Good Match", "superseded_by": 1},
        ]
    }
    pending = compute_pending_feedback(last, feedback)
    assert len(pending) == 1


def test_pending_handles_missing_or_empty_state():
    assert compute_pending_feedback(None, {}) == []
    assert compute_pending_feedback({"rows": []}, {}) == []


# ── summarize_recent_feedback ─────────────────────────────────────────

def _now_iso(offset_hours: int = 0) -> str:
    return (datetime.now() + timedelta(hours=offset_hours)).isoformat(timespec="seconds")


def test_summary_counts_kept_rejected_notes():
    fb = {
        "user_verdicts": [
            {"user_verdict": "Good Match", "user_reason": "", "title": "T", "timestamp": _now_iso(-1)},
            {"user_verdict": "Disagree-Skip", "user_reason": "too technical", "title": "T", "timestamp": _now_iso(-2)},
            {"user_verdict": "Disagree-Skip", "user_reason": "wrong industry", "title": "T", "timestamp": _now_iso(-3)},
            {"user_verdict": "Note", "user_reason": "follow up later", "title": "T", "timestamp": _now_iso(-4)},
        ]
    }
    s = summarize_recent_feedback(fb)
    assert s["kept"] == 1
    assert s["rejected"] == 2
    assert s["notes"] == 1


def test_summary_clusters_reject_themes():
    fb = {
        "user_verdicts": [
            {"user_verdict": "Disagree-Skip", "user_reason": "too technical for me", "title": "Senior Systems Engineer", "timestamp": _now_iso(-1)},
            {"user_verdict": "Disagree-Skip", "user_reason": "heavy simulation work", "title": "R&D Engineer", "timestamp": _now_iso(-2)},
            {"user_verdict": "Disagree-Skip", "user_reason": "wrong industry", "title": "Building Materials PM", "timestamp": _now_iso(-3)},
        ]
    }
    s = summarize_recent_feedback(fb)
    labels = {t["label"] for t in s["themes"]}
    assert any("technical" in l.lower() for l in labels)
    assert any("industry" in l.lower() for l in labels)
    # Each reject produced one theme count; total should equal reject count
    assert sum(t["count"] for t in s["themes"]) == 3


def test_summary_respects_since_timestamp():
    cutoff = _now_iso(-2)
    fb = {
        "user_verdicts": [
            {"user_verdict": "Good Match", "user_reason": "", "title": "New", "timestamp": _now_iso(-1)},
            {"user_verdict": "Good Match", "user_reason": "", "title": "Old", "timestamp": _now_iso(-5)},
        ]
    }
    s = summarize_recent_feedback(fb, since_timestamp=cutoff)
    assert s["kept"] == 1
    assert s["since"] == cutoff


def test_summary_handles_empty_input():
    s = summarize_recent_feedback(None)
    assert s == {"kept": 0, "rejected": 0, "notes": 0, "themes": [], "since": None}
