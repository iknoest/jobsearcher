"""Tests for src/classify/seniority.py (R2-03).

User-locked rules:
- HARD reject only for explicit early-career (intern / trainee / graduate program / stagiair).
- FLAG-only for junior / associate / head_of / mid-level — no hard reject.
- Soft penalties are deferred to R3, not this classifier.
"""

import pytest

from src.classify.seniority import (
    classify_seniority,
    _extract_years_required,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


# ── HARD REJECT: explicit early-career ───────────────────────────
def test_hard_reject_intern_title():
    r = classify_seniority("Product Management Intern", "Summer program.")
    assert r["seniority_hard_reject"] is True
    assert r["seniority_level"] == "entry_level"
    assert "seniority_entry_level_role" in r["matched_rules"]


def test_hard_reject_stagiair_title():
    r = classify_seniority("Stagiair Productontwikkeling", "Onderdeel van ons team.")
    assert r["seniority_hard_reject"] is True
    assert r["seniority_level"] == "entry_level"


def test_hard_reject_trainee_title():
    r = classify_seniority("Graduate Trainee Engineer", "18-month rotation program.")
    assert r["seniority_hard_reject"] is True


def test_hard_reject_graduate_program_title():
    r = classify_seniority("Graduate Program Associate", "2-year rotational program.")
    assert r["seniority_hard_reject"] is True


def test_hard_reject_via_jd_phrase():
    """Even without an entry-level title, an explicit graduate-program JD phrase rejects."""
    r = classify_seniority(
        "Rotation Associate",
        "This role is part of our internship program — recent graduates welcome.",
    )
    assert r["seniority_hard_reject"] is True
    assert "seniority_entry_level_role" in r["matched_rules"]


# ── FLAG ONLY: junior does NOT hard-reject (per user rule 2026-04-20) ────
def test_junior_title_flags_not_rejects():
    r = classify_seniority("Junior Product Manager", "We need someone with 2+ years.")
    assert r["seniority_hard_reject"] is False
    assert r["seniority_mismatch_flag"] is True
    assert r["seniority_level"] == "junior"
    assert "seniority_junior_ambiguous" in r["matched_rules"]


def test_jr_abbreviation_flags():
    r = classify_seniority("Jr. UX Researcher", "Responsible for research activities.")
    assert r["seniority_hard_reject"] is False
    assert r["seniority_mismatch_flag"] is True
    assert r["seniority_level"] == "junior"


# ── FLAG ONLY: head-of / VP / director ───────────────────────────
def test_head_of_flags_not_rejects():
    r = classify_seniority(
        "Head of Product", "Lead a team of 15 product managers across the org."
    )
    assert r["seniority_hard_reject"] is False
    assert r["seniority_mismatch_flag"] is True
    assert r["seniority_level"] == "head_of"


def test_vp_of_flags():
    r = classify_seniority("VP of Engineering", "Report to the CTO.")
    assert r["seniority_hard_reject"] is False
    assert r["seniority_mismatch_flag"] is True
    assert r["seniority_level"] == "head_of"


def test_director_of_flags():
    r = classify_seniority("Director of Product Strategy", "Own the portfolio roadmap.")
    assert r["seniority_hard_reject"] is False
    assert r["seniority_level"] == "head_of"


# ── NORMAL: senior / lead / principal (no mismatch flag) ─────────
def test_senior_title_no_flag():
    r = classify_seniority(
        "Senior Product Manager",
        "7+ years of experience required. Own the product roadmap.",
    )
    assert r["seniority_hard_reject"] is False
    assert r["seniority_mismatch_flag"] is False
    assert r["seniority_level"] == "senior"
    assert r["seniority_years_required"] == 7


def test_lead_title():
    r = classify_seniority(
        "Lead UX Researcher", "10+ years of experience in research."
    )
    assert r["seniority_level"] == "lead"
    assert r["seniority_mismatch_flag"] is False


def test_principal_title():
    r = classify_seniority(
        "Principal Product Manager", "Minimum 12 years of experience."
    )
    assert r["seniority_level"] == "lead"


# ── MID-LEVEL cue (flag) ─────────────────────────────────────────
def test_mid_level_phrase_flags():
    r = classify_seniority(
        "Product Manager",
        "We're looking for a mid-level PM with 3-5 years experience.",
    )
    assert r["seniority_hard_reject"] is False
    assert r["seniority_mismatch_flag"] is True
    assert r["seniority_level"] == "mid"


def test_medior_phrase_flags():
    r = classify_seniority("Product Owner", "Medior role, 2-4 years experience.")
    assert r["seniority_level"] == "mid"
    assert r["seniority_mismatch_flag"] is True


# ── YEARS EXTRACTION ─────────────────────────────────────────────
def test_extract_years_plus():
    assert _extract_years_required("We need 8+ years of experience.") == 8


def test_extract_years_minimum():
    assert _extract_years_required("Minimum 10 years of experience.") == 10


def test_extract_years_range():
    assert _extract_years_required("3-5 years of relevant experience.") == 3


def test_extract_years_at_least():
    assert _extract_years_required("At least 5 years of work experience.") == 5


def test_extract_years_none_when_absent():
    assert _extract_years_required("Exciting role with great benefits.") is None


def test_extract_years_ignores_bogus_large_number():
    """Text like '100 years' is a red herring — we cap at 30."""
    assert _extract_years_required("Founded 100 years of experience ago.") is None


# ── FALLBACK: years_required infers level when title is neutral ──
def test_years_based_senior_inference():
    r = classify_seniority(
        "Product Manager",
        "8+ years of professional experience required.",
    )
    assert r["seniority_level"] == "senior"
    assert r["seniority_years_required"] == 8
    assert r["seniority_mismatch_flag"] is False


def test_years_based_junior_inference():
    r = classify_seniority(
        "Product Researcher",
        "1+ years of relevant experience required.",
    )
    assert r["seniority_level"] == "junior"
    assert r["seniority_hard_reject"] is False
    assert r["seniority_years_required"] == 1


# ── UNKNOWN ──────────────────────────────────────────────────────
def test_unknown_empty_inputs():
    r = classify_seniority("", "")
    assert r["seniority_level"] == "unknown"
    assert r["seniority_hard_reject"] is False
    assert r["seniority_mismatch_flag"] is False


def test_unknown_no_seniority_signal():
    r = classify_seniority("Product Manager", "Work on our exciting platform.")
    assert r["seniority_level"] == "unknown"
    assert r["seniority_mismatch_flag"] is False


# ── CASE INSENSITIVITY + WORD BOUNDARY SAFETY ───────────────────
def test_case_insensitive_senior():
    r = classify_seniority("SENIOR Product Manager", "7+ years experience.")
    assert r["seniority_level"] == "senior"


def test_word_boundary_no_false_positive_on_intern():
    """'international' must NOT match 'intern' via substring."""
    r = classify_seniority(
        "International Product Manager",
        "Work with international customers.",
    )
    assert r["seniority_hard_reject"] is False
    assert r["seniority_level"] != "entry_level"


def test_word_boundary_no_false_positive_on_trainee():
    """'trainer' should NOT match 'trainee'."""
    r = classify_seniority(
        "Product Manager for Trainer Platform",
        "Help trainers build their courses.",
    )
    assert r["seniority_hard_reject"] is False
