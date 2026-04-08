import json
import os
import tempfile
from src.feedback import detect_patterns, record_verdict

def test_detect_pattern_below_threshold():
    """Should not trigger adjustment with fewer than 5 signals."""
    verdicts = [
        {"user_verdict": "Disagree-Skip", "user_reason": "too SaaS", "system_decision": "Apply"},
        {"user_verdict": "Disagree-Skip", "user_reason": "pure SaaS role", "system_decision": "Maybe"},
    ]
    patterns = detect_patterns(verdicts)
    assert patterns.get("saas_penalty_boost", 0) == 0

def test_detect_pattern_above_threshold():
    """Should trigger SaaS penalty boost after 5 similar signals."""
    verdicts = [
        {"user_verdict": "Disagree-Skip", "user_reason": "too SaaS", "system_decision": "Apply"},
        {"user_verdict": "Disagree-Skip", "user_reason": "pure SaaS", "system_decision": "Apply"},
        {"user_verdict": "Disagree-Skip", "user_reason": "SaaS company", "system_decision": "Maybe"},
        {"user_verdict": "Disagree-Skip", "user_reason": "software only SaaS", "system_decision": "Apply"},
        {"user_verdict": "Disagree-Skip", "user_reason": "saas platform", "system_decision": "Maybe"},
    ]
    patterns = detect_patterns(verdicts)
    assert patterns.get("saas_penalty_boost", 0) == -5

def test_detect_phygital_underweight():
    """Should boost phygital weight if user repeatedly says Disagree-Fit for phygital jobs."""
    verdicts = [
        {"user_verdict": "Disagree-Fit", "user_reason": "has hardware", "system_decision": "Skip"},
        {"user_verdict": "Disagree-Fit", "user_reason": "actually phygital", "system_decision": "Skip"},
        {"user_verdict": "Disagree-Fit", "user_reason": "IoT hardware", "system_decision": "Skip"},
        {"user_verdict": "Disagree-Fit", "user_reason": "sensor product", "system_decision": "Skip"},
        {"user_verdict": "Disagree-Fit", "user_reason": "physical product", "system_decision": "Skip"},
    ]
    patterns = detect_patterns(verdicts)
    assert patterns.get("phygital_bonus_boost", 0) == 5

def test_record_verdict_supersedes():
    """Recording a new verdict for same URL should supersede the old one."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"negative_feedback": [], "positive_signals": [], "skipped_job_ids": [],
                    "user_verdicts": [], "weight_adjustments": [], "pattern_counts": {}}, f)
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
