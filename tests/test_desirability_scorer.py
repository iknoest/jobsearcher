"""Tests for src/score/desirability.py (R3-03c).

Score pool (max 10): base 3 + work_mode (0-4) − commute (0-3) + salary (0|2) + KM visa (0|2).
"""

from src.score.desirability import score_desirability


# ── WORK MODE BONUSES ────────────────────────────────────────────
def test_remote_no_extras():
    r = score_desirability({"work_mode": "remote"})
    # base 3 + remote 4 = 7
    assert r["score"] == 7
    assert r["signals"]["work_mode"] == "remote"


def test_onsite_no_extras():
    r = score_desirability({"work_mode": "onsite", "location": "Amsterdam"})
    # base 3 + onsite 1 = 4
    assert r["score"] == 4


def test_hybrid_no_extras():
    r = score_desirability({"work_mode": "hybrid"})
    assert r["score"] == 5  # 3 + 2


def test_unknown_work_mode_neutral():
    r = score_desirability({"location": "Amsterdam"})
    # unknown = 2 → 3 + 2 = 5
    assert r["score"] == 5


def test_is_remote_flag_detected():
    r = score_desirability({"is_remote": True})
    assert r["signals"]["work_mode"] == "remote"


def test_remote_detected_from_location_string():
    r = score_desirability({"location": "Remote, Europe"})
    assert r["signals"]["work_mode"] == "remote"


# ── COMMUTE PENALTIES ────────────────────────────────────────────
def test_onsite_within_threshold_no_penalty():
    r = score_desirability({"work_mode": "onsite", "travel_minutes": 45})
    assert r["signals"]["commute_penalty"] == 0


def test_onsite_over_threshold_penalizes():
    """onsite 120min (30 over 90 threshold) → −2 penalty."""
    r = score_desirability({"work_mode": "onsite", "travel_minutes": 120})
    # 30 over / 15 per step = 2 penalty
    assert r["signals"]["commute_penalty"] == 2


def test_commute_penalty_caps_at_3():
    """Massive overage still capped at −3."""
    r = score_desirability({"work_mode": "onsite", "travel_minutes": 300})
    assert r["signals"]["commute_penalty"] == 3


def test_hybrid_has_lower_threshold():
    """hybrid threshold 60min — 120min → 60 over → 4 steps → cap 3."""
    r = score_desirability({"work_mode": "hybrid", "travel_minutes": 120})
    assert r["signals"]["commute_penalty"] == 3


def test_remote_ignores_travel_minutes():
    """Remote role with travel_minutes set (weird) → no penalty."""
    r = score_desirability({"work_mode": "remote", "travel_minutes": 300})
    assert r["signals"]["commute_penalty"] == 0


# ── SALARY + KM VISA BONUSES ─────────────────────────────────────
def test_salary_transparency_bonus():
    r = score_desirability({"work_mode": "remote", "salary_min": 60000})
    # 3 + 4 + 2 = 9
    assert r["score"] == 9


def test_km_visa_bonus():
    r = score_desirability({"work_mode": "hybrid", "km_visa_sponsor": True})
    # 3 + 2 + 2 = 7
    assert r["score"] == 7


def test_all_bonuses_cap_at_10():
    r = score_desirability({
        "work_mode": "remote",
        "salary_min": 90000,
        "km_visa_sponsor": True,
    })
    # 3 + 4 + 2 + 2 = 11 → clipped to 10
    assert r["score"] == 10


# ── EDGE: onsite + long commute + no bonuses → low score ─────────
def test_bad_combo_floors_at_low_score():
    r = score_desirability({"work_mode": "onsite", "travel_minutes": 120})
    # 3 + 1 − 2 = 2
    assert r["score"] == 2


def test_salary_zero_treated_as_absent():
    r = score_desirability({"work_mode": "remote", "salary_min": 0, "salary_max": 0})
    # 3 + 4 = 7 (no salary bonus)
    assert r["score"] == 7
