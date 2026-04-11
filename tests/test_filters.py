import pandas as pd

from src.filters import _validate_salary, language_prefilter, detect_phygital


def test_valid_salary():
    assert _validate_salary("5000", "8000", "EUR", "yearly") == "EUR 5000-8000"


def test_swapped_salary():
    assert _validate_salary("8000", "5000", "EUR", "yearly") == "EUR 5000-8000"


def test_garbage_salary():
    assert _validate_salary("4", "3", "EUR", "") == ""
    assert _validate_salary("abc", "def", "EUR", "") == ""
    assert _validate_salary("", "", "EUR", "") == ""


def test_single_value_salary():
    assert _validate_salary("5000", "", "EUR", "monthly") == "EUR 5000+"
    assert _validate_salary("", "8000", "EUR", "yearly") == "EUR 0-8000"


# --- language_prefilter tests ---


def _row(title="Senior Product Researcher", company="Acme", description=""):
    return {"title": title, "company": company, "description": description, "job_url": f"https://x/{title}"}


def test_prefilter_rejects_dutch_mandatory_phrase():
    df = pd.DataFrame([_row(description="We are hiring. Dutch mandatory. " + "english words " * 20)])
    kept, rejected = language_prefilter(df)
    assert len(kept) == 0
    assert rejected[0]["SkipReason"] == "Dutch Mandatory"
    assert rejected[0]["Stage"] == "Language Pre-filter"


def test_prefilter_rejects_nederlands_verplicht():
    df = pd.DataFrame([_row(description="Nederlands verplicht. " + "english words " * 20)])
    kept, rejected = language_prefilter(df)
    assert len(kept) == 0
    assert rejected[0]["SkipReason"] == "Dutch Mandatory"


def test_prefilter_rejects_vloeiend_nederlands():
    df = pd.DataFrame([_row(description="Vloeiend Nederlands required. " + "english words " * 20)])
    kept, rejected = language_prefilter(df)
    assert len(kept) == 0


def test_prefilter_allows_dutch_nice_to_have():
    df = pd.DataFrame([_row(description="English is primary. Dutch is a plus. " + "english words " * 20)])
    kept, rejected = language_prefilter(df)
    assert len(kept) == 1
    assert len(rejected) == 0


def test_prefilter_rejects_dutch_title():
    df = pd.DataFrame([_row(title="Financieel Medewerker", description="English description " * 20)])
    kept, rejected = language_prefilter(df)
    assert len(kept) == 0
    assert rejected[0]["SkipReason"] == "Dutch Title"


def test_prefilter_keeps_english_job():
    df = pd.DataFrame([_row(description="Senior Product Researcher role. " + "english context " * 20)])
    kept, rejected = language_prefilter(df)
    assert len(kept) == 1
    assert len(rejected) == 0


# --- detect_phygital tests ---


def test_phygital_true_on_hardware():
    assert detect_phygital("We build hardware and sensors for automotive applications")


def test_phygital_true_on_robotics():
    assert detect_phygital("Robotics engineer for industrial automation")


def test_phygital_false_on_iot_alone():
    assert not detect_phygital("We build an IoT platform in the cloud")


def test_phygital_false_on_ai_alone():
    assert not detect_phygital("Work with AI and digital twin simulation")


def test_phygital_false_on_pure_saas():
    assert not detect_phygital("Build a SaaS platform with cloud infrastructure")
