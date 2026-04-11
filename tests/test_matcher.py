import json

from src.matcher import _extract_json


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
