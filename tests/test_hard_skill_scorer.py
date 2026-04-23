"""Tests for src/score/hard_skill.py (R3-02).

All tests pass pre-extracted `jd_skills` so no LLM is called.
Caching is exercised via temp paths to keep tests isolated.
"""

import json
from pathlib import Path

import pytest

from src.score.hard_skill import (
    cache_key_for,
    cache_get,
    cache_put,
    reset_yaml_cache,
    score_hard_skills,
)
from src.score.skill_extract import _parse_skills_json


@pytest.fixture(autouse=True)
def _clear_yaml_cache():
    reset_yaml_cache()
    yield
    reset_yaml_cache()


def _jd(name: str, importance: str = "core") -> dict:
    return {"name": name, "importance": importance}


# ── BASE SCORING: verified / adjacent ────────────────────────────
def test_single_verified_high_strength_hit():
    """One verified-High match → 3 pts × 1.0 = 3 pts.
    'user research' matches 'User research (general)' canonical_name."""
    r = score_hard_skills(
        {"title": "PM", "description": ""},
        jd_skills=[_jd("user research", "core")],
        use_cache=False,
    )
    assert r["score"] == 3
    assert len(r["signals"]["verified_matches"]) == 1
    assert not r["signals"]["rejected_by_forbidden_core"]


def test_multiple_verified_matches_accumulate():
    """Three distinct verified High-strength canonicals → 3+3+3 = 9 pts."""
    r = score_hard_skills(
        {"title": "PM", "description": ""},
        jd_skills=[
            _jd("user research", "core"),        # verified High
            _jd("prototyping", "core"),          # verified High
            _jd("stakeholder management", "core"),  # verified High
        ],
        use_cache=False,
    )
    assert r["score"] == 9
    assert len(r["signals"]["verified_matches"]) == 3


def test_adjacent_match_scores_half():
    """Adjacent match → 1.5 pts × strength_mult."""
    r = score_hard_skills(
        {"title": "PM", "description": ""},
        jd_skills=[_jd("product vision", "optional")],
        use_cache=False,
    )
    # Adjacent entry 'Product vision' is Medium → 1.5 × 0.7 ≈ 1.05 → rounds to 1
    assert r["score"] in (1, 2)
    assert len(r["signals"]["adjacent_matches"]) == 1
    assert len(r["signals"]["verified_matches"]) == 0


def test_base_caps_at_30():
    """15 distinct verified High canonicals × 3 pts = 45 pts raw → base capped at 30."""
    # All these are confirmed verified High-strength canonicals or synonyms in skills.yaml
    big_list = [
        _jd(name, "core")
        for name in [
            "voc", "qualitative research", "quantitative research",
            "user research", "cognitive psychology", "usability testing",
            "product strategy input", "prd", "backlog prioritization",
            "mvp", "stakeholder management", "market intelligence",
            "python", "power bi", "data visualization",
        ]
    ]
    r = score_hard_skills(
        {"title": "PM", "description": ""},
        jd_skills=big_list,
        use_cache=False,
    )
    assert r["score"] == 30
    assert r["signals"]["base_score"] == pytest.approx(30.0, abs=0.01)


# ── FORBIDDEN CORE → SCORE 0 (REJECT) ────────────────────────────
def test_forbidden_core_zeroes_score():
    """One forbidden CORE skill → score = 0 and rejected_by_forbidden_core=True.
    'kubernetes' matches forbidden 'DevOps / infrastructure engineering' entry."""
    r = score_hard_skills(
        {"title": "Engineer", "description": ""},
        jd_skills=[
            _jd("user research", "core"),  # verified, would add points
            _jd("kubernetes", "core"),      # forbidden CORE → reject
        ],
        use_cache=False,
    )
    assert r["score"] == 0
    assert r["signals"]["rejected_by_forbidden_core"] is True
    assert len(r["signals"]["forbidden_core_hits"]) == 1


def test_forbidden_optional_does_not_reject():
    """Forbidden skill tagged OPTIONAL → penalty capped at -5, NOT a reject."""
    r = score_hard_skills(
        {"title": "Engineer", "description": ""},
        jd_skills=[
            _jd("user research", "core"),         # +3
            _jd("backend development", "optional"),  # forbidden optional → -2.5
        ],
        use_cache=False,
    )
    assert r["signals"]["rejected_by_forbidden_core"] is False
    assert len(r["signals"]["forbidden_core_hits"]) == 0
    assert len(r["signals"]["forbidden_optional_hits"]) == 1
    # 3 (verified High) - 2.5 (optional forbidden) = 0.5 → rounds to 1 or 0
    assert 0 <= r["score"] <= 1


def test_forbidden_optional_penalty_caps_at_5():
    """Many optional forbidden hits → penalty stops at 5."""
    r = score_hard_skills(
        {"title": "Engineer", "description": ""},
        jd_skills=[
            _jd("user research", "core"),          # +3
            _jd("user research", "core"),          # matches already-used entry (no double)
            _jd("backend development", "optional"),
            _jd("microservices architecture", "optional"),
            _jd("go services", "optional"),
            _jd("rust services", "optional"),
        ],
        use_cache=False,
    )
    # At most -5 penalty no matter how many optional hits
    assert r["signals"]["forbidden_optional_penalty"] <= 5.0


# ── NEGATIVE SIGNAL → -3 CAPPED ──────────────────────────────────
def test_negative_signal_applies_capped_penalty():
    """Negative signal capped at -3 regardless of count."""
    r = score_hard_skills(
        {"title": "PM", "description": ""},
        jd_skills=[
            _jd("user research", "core"),      # +3
            _jd("dutch preferred", "optional"),  # negative → -1.5
            _jd("german preferred", "optional"),  # negative → -1.5
            _jd("deutsch required", "optional"),  # negative → -1.5
        ],
        use_cache=False,
    )
    assert r["signals"]["negative_penalty"] <= 3.0
    assert len(r["signals"]["negative_hits"]) >= 2


def test_negative_signal_alone_cannot_go_below_zero():
    """Only negative signals, no verified → final clamped to 0."""
    r = score_hard_skills(
        {"title": "PM", "description": ""},
        jd_skills=[_jd("dutch preferred", "optional")],
        use_cache=False,
    )
    assert r["score"] == 0


# ── EMPTY / NEUTRAL ──────────────────────────────────────────────
def test_empty_jd_skills_scores_zero():
    r = score_hard_skills(
        {"title": "PM", "description": "Work on things."},
        jd_skills=[],
        use_cache=False,
    )
    assert r["score"] == 0
    assert r["signals"]["jd_skills_count"] == 0


def test_unrelated_skills_score_zero():
    """JD skills that don't match anything in skills.yaml → 0."""
    r = score_hard_skills(
        {"title": "PM", "description": ""},
        jd_skills=[
            _jd("blockchain consensus", "core"),
            _jd("kubernetes operators", "core"),
        ],
        use_cache=False,
    )
    assert r["score"] == 0
    assert r["signals"]["verified_matches"] == []
    assert r["signals"]["adjacent_matches"] == []


# ── CACHE KEY STABILITY (URL + JD HASH) ──────────────────────────
def test_cache_key_same_for_same_url_and_jd():
    k1 = cache_key_for("https://example.com/job/1", "Build prototypes daily.")
    k2 = cache_key_for("https://example.com/job/1", "Build prototypes daily.")
    assert k1 == k2


def test_cache_key_changes_when_jd_changes_under_same_url():
    """URL unchanged, but JD body edited → key MUST change (no stale reuse)."""
    k1 = cache_key_for("https://example.com/job/1", "Build prototypes daily.")
    k2 = cache_key_for("https://example.com/job/1", "Build prototypes daily. Now C++ required.")
    assert k1 != k2


def test_cache_key_whitespace_normalization():
    """Trivial whitespace differences do NOT invalidate the cache."""
    k1 = cache_key_for("u", "Build prototypes daily.")
    k2 = cache_key_for("u", "Build  prototypes\n daily.")
    assert k1 == k2


def test_cache_roundtrip(tmp_path: Path):
    """cache_put → cache_get returns the same payload; no global state leaked."""
    cache_path = tmp_path / "jd_skills.json"
    key = cache_key_for("u1", "some jd")
    cache_put(key, {"skills": [_jd("user research", "core")]}, cache_path)
    got = cache_get(key, cache_path)
    assert got == {"skills": [_jd("user research", "core")]}


def test_cache_get_missing_file_returns_none(tmp_path: Path):
    assert cache_get("anything", tmp_path / "nope.json") is None


# ── INTEGRATION: cache hit path is used over LLM ─────────────────
def test_cached_skills_used_without_llm_call(tmp_path: Path, monkeypatch):
    """When a cache entry exists, the LLM path must NOT fire."""
    import src.score.hard_skill as hs

    cache_path = tmp_path / "jd_skills_cache.json"
    job = {"title": "PM", "description": "Run user research and prototypes.",
           "url": "https://example.com/job/9"}
    key = cache_key_for(job["url"], job["description"])
    cache_put(key, {"skills": [_jd("user research", "core")]}, cache_path)

    # Poison the import so any accidental LLM call raises.
    def _boom(*args, **kwargs):
        raise AssertionError("LLM path must not be called when cache hits")

    monkeypatch.setattr("src.score.skill_extract.extract_jd_skills", _boom)

    r = score_hard_skills(job, cache_path=cache_path)
    assert r["signals"]["extraction_source"] == "cache"
    assert r["score"] >= 1  # verified hit


# ── skill_extract.py tolerant JSON parser ────────────────────────
def test_skill_extract_parses_plain_json():
    raw = '{"skills":[{"name":"user research","importance":"core"}]}'
    out = _parse_skills_json(raw)
    assert out == [{"name": "user research", "importance": "core"}]


def test_skill_extract_strips_markdown_fences():
    raw = "```json\n{\"skills\":[{\"name\":\"python\",\"importance\":\"optional\"}]}\n```"
    out = _parse_skills_json(raw)
    assert out == [{"name": "python", "importance": "optional"}]


def test_skill_extract_normalizes_bad_importance():
    raw = '{"skills":[{"name":"python","importance":"required"}]}'
    out = _parse_skills_json(raw)
    # "required" is not in {core, optional} → coerced to 'optional'
    assert out == [{"name": "python", "importance": "optional"}]


def test_skill_extract_returns_empty_on_garbage():
    assert _parse_skills_json("not json") == []
    assert _parse_skills_json("") == []
