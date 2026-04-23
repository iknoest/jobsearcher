"""Tests for src/classify/industry.py (R2-02)."""

import pytest

from src.classify.industry import classify_industry, reset_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


# ── ANCHOR: 1.0 proximity ─────────────────────────────────────────
def test_anchor_automotive_byd():
    r = classify_industry("BYD Europe", "Product research for EV/PHEV lines.")
    assert r["industry"] == "automotive"
    assert r["proximity_to_ava"] == 1.0
    assert r["source"] == "company"
    assert r["evidence"]["company_match"].lower().startswith("byd")


def test_anchor_consumer_electronics_logitech():
    r = classify_industry("Logitech", "Develop peripherals and consumer devices.")
    assert r["industry"] == "consumer_electronics"
    assert r["proximity_to_ava"] == 1.0


def test_anchor_industrial_hardware_asml():
    r = classify_industry("ASML", "Work on lithography systems.")
    assert r["industry"] == "industrial_hardware_manufacturing"
    assert r["proximity_to_ava"] == 1.0


# ── CLOSE: 0.7-0.8 proximity ──────────────────────────────────────
def test_close_smart_home_signify():
    r = classify_industry("Signify", "Smart lighting and connected home.")
    assert r["industry"] == "smart_home_appliances"
    assert r["proximity_to_ava"] == pytest.approx(0.8)


def test_close_mobility_tomtom():
    r = classify_industry("TomTom", "Data visualisation for mobility.")
    assert r["industry"] == "mobility_platforms"
    assert r["proximity_to_ava"] == pytest.approx(0.7)


# ── FAR: 0.2 proximity ────────────────────────────────────────────
def test_far_fintech_rabobank():
    r = classify_industry("Rabobank", "Data-driven banking products.")
    assert r["industry"] == "fintech_banking"
    assert r["proximity_to_ava"] == pytest.approx(0.2)


def test_far_clinical_device_stryker():
    r = classify_industry("Stryker", "Medical device product work.")
    assert r["industry"] == "clinical_medical_device"
    assert r["proximity_to_ava"] == pytest.approx(0.2)


def test_far_enterprise_saas_salesforce():
    r = classify_industry("Salesforce", "Platform features.")
    assert r["industry"] == "enterprise_saas_general"
    assert r["proximity_to_ava"] == pytest.approx(0.2)


# ── VERY FAR: 0.0-0.1 proximity ───────────────────────────────────
def test_very_far_management_consulting_mckinsey():
    r = classify_industry("McKinsey & Company", "Strategy engagement with clients.")
    assert r["industry"] == "management_consulting"
    assert r["proximity_to_ava"] == pytest.approx(0.1)


def test_very_far_oil_gas_aramco():
    r = classify_industry("Aramco", "Engineering role.")
    assert r["industry"] == "oil_gas_heavy_industry"
    assert r["proximity_to_ava"] == 0.0


# ── JD-ONLY: no company match ────────────────────────────────────
def test_jd_only_automotive():
    r = classify_industry(
        "Obscure Mobility Startup BV",
        "Develop powertrain software for electric vehicle platforms at our OEM.",
    )
    assert r["industry"] == "automotive"
    assert r["source"] == "jd"
    assert r["evidence"]["jd_match"] is not None
    assert r["proximity_to_ava"] == 1.0


# ── COMPANY + JD DISAGREE ────────────────────────────────────────
def test_company_primary_jd_as_secondary():
    """Databricks mentions automotive customer segment — primary should stay cloud_infra, secondary should surface automotive."""
    r = classify_industry(
        "Databricks",
        "Build products for automotive customers and electric vehicle fleets.",
    )
    assert r["industry"] == "cloud_infra_devtools"
    assert r["source"] == "company"
    secondary_names = [c["industry"] for c in r["secondary_candidates"]]
    assert "automotive" in secondary_names


# ── UNKNOWN ──────────────────────────────────────────────────────
def test_unknown_both_empty():
    r = classify_industry("", "")
    assert r["industry"] == "unknown"
    assert r["proximity_to_ava"] is None
    assert r["source"] == "unknown"
    assert r["secondary_candidates"] == []


def test_unknown_no_match():
    r = classify_industry("Some Obscure Co", "Generic role description with no industry hints.")
    assert r["industry"] == "unknown"
    assert r["proximity_to_ava"] is None


# ── CASE INSENSITIVITY ──────────────────────────────────────────
def test_case_insensitive_company():
    r = classify_industry("TESLA", "Some JD.")
    assert r["industry"] == "automotive"
    assert r["proximity_to_ava"] == 1.0


# ── WORD BOUNDARY SAFETY ────────────────────────────────────────
def test_no_false_positive_on_substring():
    """A company name containing 'lever' (from 'Unilever') must NOT match HR-tech 'lever.co'."""
    r = classify_industry("Unilever", "Consumer goods product work.")
    # Should NOT classify as hr_tech; should be unknown (no CPG industry defined) or consumer_electronics via JD
    assert r["industry"] != "hr_tech"


def test_no_false_positive_on_bare_ev():
    """Random JD mentioning 'development' must not match automotive via bare 'ev'."""
    r = classify_industry(
        "Some Software Shop",
        "Software development lifecycle with CI/CD; you develop features every day.",
    )
    # Should not classify as automotive on 'every' / 'development'
    assert r["industry"] != "automotive"
