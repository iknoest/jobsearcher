"""Tests for src/generator.py — CV tailoring engine."""
import pytest


def test_extract_job_id_linkedin():
    from src.generator import extract_job_id
    assert extract_job_id("https://www.linkedin.com/jobs/view/4400368636") == "4400368636"


def test_extract_job_id_other_url():
    from src.generator import extract_job_id
    result = extract_job_id("https://example.com/jobs/123")
    assert isinstance(result, str)
    assert len(result) > 0


def test_select_angle_engineering():
    from src.generator import select_angle
    match_result = {"PhygitalAssessment": {"Level": "Strong"}}
    assert select_angle("Product Engineer", match_result) == "Product Development Engineer"


def test_select_angle_strategy():
    from src.generator import select_angle
    match_result = {"PhygitalAssessment": {"Level": "Weak"}}
    assert select_angle("Product Manager", match_result) == "Strategic Product Lead"


def test_select_angle_hybrid():
    from src.generator import select_angle
    match_result = {"PhygitalAssessment": {"Level": "Moderate"}}
    assert select_angle("Innovation Lead", match_result) == "Hybrid"


def test_build_tailoring_prompt_contains_jd():
    from src.generator import build_tailoring_prompt
    prompt = build_tailoring_prompt(
        jd_text="We need a Python engineer with IoT experience",
        match_result={"WhyFit": {"StrongMatch": ["test"]}, "KeySkills": ["Python", "IoT"]},
        base_cv="# Ava Tse\nSenior Product Researcher",
        angle="Product Development Engineer",
    )
    assert "Python engineer with IoT" in prompt
    assert "Product Development Engineer" in prompt
    assert "Ava Tse" in prompt


def test_build_tailoring_prompt_contains_rules():
    from src.generator import build_tailoring_prompt
    prompt = build_tailoring_prompt(
        jd_text="test JD",
        match_result={},
        base_cv="# Ava Tse",
        angle="Hybrid",
    )
    assert "NEVER invent experience" in prompt
    assert "Mirror specific keywords" in prompt
