import json

from src.matcher import SCORING_PROMPT, _extract_json


def test_scoring_prompt_formats_without_error():
    """Regression: literal { / } in the prompt must be escaped as {{ / }}
    so str.format() doesn't raise KeyError."""
    formatted = SCORING_PROMPT.format(
        title="Test", company="Test", location="Test", description="Test JD",
        phygital_detected=False, pure_saas_detected=False,
        driver_license_flagged=False, dutch_mandatory=False,
        dutch_nice_to_have=False, work_mode="Remote", seniority_fit="Strong",
        is_agency=False, km_visa_mentioned=False, feedback_context="None.",
        preferences_block="(test block)",
    )
    assert len(formatted) > 1000
    assert "OUTPUT CONTRACT" in formatted


def test_plain_json():
    assert _extract_json('{"a": 1}') == '{"a": 1}'


def test_fenced_json():
    raw = '```json\n{"a": 1}\n```'
    assert json.loads(_extract_json(raw)) == {"a": 1}


def test_prose_before_and_after():
    raw = 'Here is the JSON: {"a": 1} hope this helps'
    assert json.loads(_extract_json(raw)) == {"a": 1}


def test_nested_braces():
    raw = '{"outer": {"inner": {"x": 2}}}'
    parsed = json.loads(_extract_json(raw))
    assert parsed["outer"]["inner"]["x"] == 2


def test_brace_inside_string():
    raw = '{"note": "has } inside"}'
    parsed = json.loads(_extract_json(raw))
    assert parsed["note"] == "has } inside"


def test_truncated_returns_none():
    assert _extract_json('{"a": 1') is None


def test_no_json_returns_none():
    assert _extract_json("just prose, no json here") is None


def test_empty_input():
    assert _extract_json("") is None
    assert _extract_json(None) is None
