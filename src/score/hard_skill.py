"""Hard-skill sub-scorer (R3-02) — PRD §15.3, weight 30.

Matches LLM-extracted JD hard skills against `config/personal_fit/skills.yaml`
and produces an interpretable 0-30 sub-score.

Design rules (locked per user 2026-04-22):
- Only sub-scorer that uses an LLM (single extraction call per JD, cached).
- Cache key = job_url + sha256(normalized_description)[:16] so re-posted
  JDs with the same URL but edited body don't return stale skills.
- Extraction path uses the two-provider chain from skill_extract.py
  (Gemini primary → Groq fallback), not the full 5-provider scoring chain.
- `jd_skills` is an optional parameter so tests stay deterministic without
  making LLM calls.
- Forbidden CORE match → score = 0 with `rejected_by_forbidden_core=True`
  regardless of other matches. (Per user: must zero out or trigger reject.)
- Forbidden OPTIONAL and negative-signal penalties are CAPPED so they
  cannot dominate. (Per user: "do not dominate the whole sub-score.")
- Clear separation from role_fit / industry_proximity scorers: this scorer
  reads skills.yaml only — never roles.yaml or industries.yaml.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SKILLS_YAML = _REPO_ROOT / "config" / "personal_fit" / "skills.yaml"
_DEFAULT_CACHE_PATH = _REPO_ROOT / "output" / "jd_skills_cache.json"

_STRENGTH_MULTIPLIER = {"High": 1.0, "Medium": 0.7, "Low": 0.4}
_DEFAULT_STRENGTH = 0.7  # used when a forbidden/negative entry has no strength field

# Scoring weights — grounded so one strong verified hit ≈ 3 pts
_VERIFIED_POINTS = 3.0
_ADJACENT_POINTS = 1.5
_FORBIDDEN_OPTIONAL_PER_HIT = 2.5
_FORBIDDEN_OPTIONAL_CAP = 5.0
_NEGATIVE_PER_HIT = 1.5
_NEGATIVE_CAP = 3.0
_BASE_CAP = 30.0


_YAML_CACHE: dict | None = None
_YAML_CACHE_KEY: Path | None = None


def reset_yaml_cache() -> None:
    global _YAML_CACHE, _YAML_CACHE_KEY
    _YAML_CACHE = None
    _YAML_CACHE_KEY = None


def _word_boundary_re(phrases: list) -> re.Pattern | None:
    if not phrases:
        return None
    parts = [re.escape(p) for p in phrases if p]
    if not parts:
        return None
    return re.compile(r"(?i)\b(" + "|".join(parts) + r")\b")


def _compile_skills(path: Path) -> dict:
    """Parse skills.yaml and compile per-entry word-boundary regex."""
    global _YAML_CACHE, _YAML_CACHE_KEY
    if _YAML_CACHE is not None and _YAML_CACHE_KEY == path:
        return _YAML_CACHE

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    def _build_entry(e: dict) -> dict:
        phrases = [e["canonical_name"]] + list(e.get("synonyms") or [])
        return {
            "canonical_name": e["canonical_name"],
            "synonyms": list(e.get("synonyms") or []),
            "strength": e.get("evidence_strength"),
            "regex": _word_boundary_re(phrases),
        }

    compiled = {
        "verified": [_build_entry(e) for e in (cfg.get("verified") or [])],
        "adjacent": [_build_entry(e) for e in (cfg.get("adjacent") or [])],
        "forbidden": [_build_entry(e) for e in (cfg.get("forbidden") or [])],
        "negative": [_build_entry(e) for e in (cfg.get("negative_signal") or [])],
    }
    _YAML_CACHE = compiled
    _YAML_CACHE_KEY = path
    return compiled


# ══════════════════════════════════════════════════════════════
# Caching
# ══════════════════════════════════════════════════════════════
def _normalize_jd(description: str) -> str:
    """Whitespace-collapsed, lowercased, capped. Used for cache-key hashing."""
    txt = " ".join((description or "").split()).lower()
    return txt[:8000]


def cache_key_for(job_url: str | None, description: str) -> str:
    """Cache key = {url or 'no-url'}|{sha256(normalized_jd)[:16]}.

    Detects stale extractions when a JD body is edited under the same URL.
    """
    norm = _normalize_jd(description)
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return f"{(job_url or 'no-url').strip()}|{h}"


def _cache_load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _cache_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cache_get(key: str, path: Path | None = None) -> dict | None:
    data = _cache_load(path or _DEFAULT_CACHE_PATH)
    return data.get(key)


def cache_put(key: str, value: dict, path: Path | None = None) -> None:
    p = path or _DEFAULT_CACHE_PATH
    data = _cache_load(p)
    data[key] = value
    _cache_save(p, data)


# ══════════════════════════════════════════════════════════════
# Matching + scoring
# ══════════════════════════════════════════════════════════════
def _match_entries_against_skills(entries: list[dict], jd_skills: list[dict]) -> list[dict]:
    """Return entries that match at least one JD skill, with the matched info."""
    hits: list[dict] = []
    for entry in entries:
        regex = entry["regex"]
        if regex is None:
            continue
        # Search all JD phrases (name field); first hit wins per entry.
        for s in jd_skills:
            name = str(s.get("name", "") or "")
            if not name:
                continue
            m = regex.search(name)
            if m:
                hits.append(
                    {
                        "canonical_name": entry["canonical_name"],
                        "strength": entry.get("strength"),
                        "importance": str(s.get("importance", "optional") or "optional"),
                        "matched_phrase": name,
                        "matched_token": m.group(0),
                    }
                )
                break
    return hits


def _score_base(verified_hits: list[dict], adjacent_hits: list[dict]) -> float:
    base = 0.0
    for h in verified_hits:
        mult = _STRENGTH_MULTIPLIER.get(h.get("strength") or "", _DEFAULT_STRENGTH)
        base += _VERIFIED_POINTS * mult
    for h in adjacent_hits:
        mult = _STRENGTH_MULTIPLIER.get(h.get("strength") or "", _DEFAULT_STRENGTH)
        base += _ADJACENT_POINTS * mult
    return min(_BASE_CAP, base)


def score_hard_skills(
    job,
    jd_skills: list[dict] | None = None,
    skills_yaml_path: Path | None = None,
    cache_path: Path | None = None,
    use_cache: bool = True,
) -> dict:
    """Score a job's hard-skill fit against skills.yaml.

    Args:
        job: dict-like with 'title', 'description', optional 'url'/'job_url'.
        jd_skills: pre-extracted list of {name, importance}. If provided,
                   skips the LLM call (used by tests and rerank flows).
        skills_yaml_path: optional override for the YAML location.
        cache_path: optional override for the cache file.
        use_cache: if True and jd_skills is None, check/update disk cache.

    Returns:
        dict with {score: int 0-30, signals: {...}, reasoning: str}.
    """
    title = str(job.get("title", "") or "")
    description = str(job.get("description", "") or "")
    job_url = str(job.get("url", "") or job.get("job_url", "") or "")

    compiled = _compile_skills(skills_yaml_path or _DEFAULT_SKILLS_YAML)

    extraction_source = "provided"
    if jd_skills is None:
        # Try cache → fall back to LLM call.
        key = cache_key_for(job_url, description)
        cached = cache_get(key, cache_path) if use_cache else None
        if cached and isinstance(cached.get("skills"), list):
            jd_skills = cached["skills"]
            extraction_source = "cache"
        else:
            # Defer import so tests that pass jd_skills don't need to mock it.
            from src.score.skill_extract import extract_jd_skills

            jd_skills = extract_jd_skills(description)
            extraction_source = "llm"
            if use_cache:
                cache_put(
                    key,
                    {
                        "skills": jd_skills,
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                        "title": title,
                        "job_url": job_url,
                    },
                    cache_path,
                )

    jd_skills = list(jd_skills or [])

    verified_hits = _match_entries_against_skills(compiled["verified"], jd_skills)
    adjacent_hits = _match_entries_against_skills(compiled["adjacent"], jd_skills)
    forbidden_hits_all = _match_entries_against_skills(compiled["forbidden"], jd_skills)
    negative_hits = _match_entries_against_skills(compiled["negative"], jd_skills)

    forbidden_core = [h for h in forbidden_hits_all if h["importance"] == "core"]
    forbidden_optional = [h for h in forbidden_hits_all if h["importance"] != "core"]

    base = _score_base(verified_hits, adjacent_hits)

    forbidden_optional_penalty = min(
        _FORBIDDEN_OPTIONAL_CAP, len(forbidden_optional) * _FORBIDDEN_OPTIONAL_PER_HIT
    )
    negative_penalty = min(_NEGATIVE_CAP, len(negative_hits) * _NEGATIVE_PER_HIT)
    total_penalty = forbidden_optional_penalty + negative_penalty

    rejected_core = bool(forbidden_core)
    if rejected_core:
        final = 0
    else:
        final = max(0, min(30, int(round(base - total_penalty))))

    # Reasoning
    parts: list[str] = []
    if rejected_core:
        names = ", ".join(h["canonical_name"] for h in forbidden_core)
        parts.append(f"REJECT: forbidden core skill(s) required — {names}")
    else:
        parts.append(
            f"Verified {len(verified_hits)} + Adjacent {len(adjacent_hits)} → base {base:.1f}"
        )
        if forbidden_optional:
            parts.append(
                f"-{forbidden_optional_penalty:.1f} optional-forbidden ({len(forbidden_optional)} hit)"
            )
        if negative_hits:
            parts.append(f"-{negative_penalty:.1f} negative ({len(negative_hits)} hit)")
        parts.append(f"→ final {final}/30")

    return {
        "score": final,
        "signals": {
            "jd_skills_count": len(jd_skills),
            "jd_skills_extracted": jd_skills,
            "verified_matches": verified_hits,
            "adjacent_matches": adjacent_hits,
            "forbidden_core_hits": forbidden_core,
            "forbidden_optional_hits": forbidden_optional,
            "negative_hits": negative_hits,
            "base_score": round(base, 2),
            "forbidden_optional_penalty": round(forbidden_optional_penalty, 2),
            "negative_penalty": round(negative_penalty, 2),
            "rejected_by_forbidden_core": rejected_core,
            "extraction_source": extraction_source,
        },
        "reasoning": "; ".join(parts),
    }
