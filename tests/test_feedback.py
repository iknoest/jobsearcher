import json
import os
import tempfile
from src.feedback import detect_patterns, record_verdict, NUDGE_STEP, PATTERN_STEP, PATTERN_THRESHOLD

def test_nudge_from_first_signal():
    """1-sample nudge kicks in immediately from the first skip signal."""
    verdicts = [
        {"user_verdict": "Disagree-Skip", "user_reason": "too SaaS", "system_decision": "Apply", "title": ""},
    ]
    patterns = detect_patterns(verdicts)
    assert patterns.get("saas_penalty_boost", 0) == -NUDGE_STEP

def test_below_pattern_threshold():
    """With fewer than PATTERN_THRESHOLD signals, only nudge applies (no pattern bonus)."""
    verdicts = [
        {"user_verdict": "Disagree-Skip", "user_reason": "too SaaS", "system_decision": "Apply", "title": ""},
        {"user_verdict": "Disagree-Skip", "user_reason": "pure SaaS role", "system_decision": "Maybe", "title": ""},
    ]
    patterns = detect_patterns(verdicts)
    # 2 < PATTERN_THRESHOLD (3) → nudge only, no pattern bonus
    assert patterns.get("saas_penalty_boost", 0) == -NUDGE_STEP

def test_pattern_bonus_at_threshold():
    """At exactly PATTERN_THRESHOLD signals, nudge + pattern bonus applies."""
    verdicts = [
        {"user_verdict": "Disagree-Skip", "user_reason": "too SaaS", "system_decision": "Apply", "title": ""},
        {"user_verdict": "Disagree-Skip", "user_reason": "pure SaaS", "system_decision": "Apply", "title": ""},
        {"user_verdict": "Disagree-Skip", "user_reason": "SaaS company", "system_decision": "Maybe", "title": ""},
    ]
    patterns = detect_patterns(verdicts)
    assert patterns.get("saas_penalty_boost", 0) == -(NUDGE_STEP + PATTERN_STEP)

def test_frozen_pattern_skipped():
    """Frozen patterns are excluded from adjustments."""
    verdicts = [
        {"user_verdict": "Disagree-Skip", "user_reason": "saas", "system_decision": "Apply", "title": ""},
        {"user_verdict": "Disagree-Skip", "user_reason": "saas", "system_decision": "Apply", "title": ""},
        {"user_verdict": "Disagree-Skip", "user_reason": "saas", "system_decision": "Apply", "title": ""},
    ]
    patterns = detect_patterns(verdicts, frozen_patterns=["saas"])
    assert "saas_penalty_boost" not in patterns

def test_phygital_nudge_from_good_match():
    """Good Match verdict with phygital keywords produces positive nudge."""
    verdicts = [
        {"user_verdict": "Good Match", "user_reason": "hardware product", "system_decision": "Skip", "title": ""},
    ]
    patterns = detect_patterns(verdicts)
    assert patterns.get("phygital_bonus_boost", 0) == NUDGE_STEP

def test_phygital_pattern_bonus():
    """PATTERN_THRESHOLD+ good signals triggers nudge + pattern bonus."""
    verdicts = [
        {"user_verdict": "Disagree-Fit", "user_reason": "has hardware", "system_decision": "Skip", "title": ""},
        {"user_verdict": "Disagree-Fit", "user_reason": "actually phygital", "system_decision": "Skip", "title": ""},
        {"user_verdict": "Disagree-Fit", "user_reason": "IoT hardware", "system_decision": "Skip", "title": ""},
    ]
    patterns = detect_patterns(verdicts)
    assert patterns.get("phygital_bonus_boost", 0) == NUDGE_STEP + PATTERN_STEP

def test_record_verdict_supersedes():
    """Recording a new verdict for same URL should supersede the old one."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"negative_feedback": [], "positive_signals": [], "skipped_job_ids": [],
                    "user_verdicts": [], "weight_adjustments": [], "pattern_counts": {},
                    "notified_milestones": []}, f)
        path = f.name

    try:
        record_verdict("http://example.com/job1", "Job1", "Apply", 80,
                       "Disagree-Skip", "too SaaS", feedback_path=path)
        record_verdict("http://example.com/job1", "Job1", "Apply", 80,
                       "Agree", "changed my mind", feedback_path=path)

        with open(path) as f:
            data = json.load(f)
        verdicts = data["user_verdicts"]
        assert len(verdicts) == 2
        assert verdicts[1]["supersedes"] is not None
        active = [v for v in verdicts if v.get("superseded_by") is None]
        assert len(active) == 1
        assert active[0]["user_verdict"] == "Agree"
    finally:
        os.unlink(path)
