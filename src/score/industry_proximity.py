"""Industry-proximity sub-scorer (R3-03b) — PRD §15.5, weight 10.

Consumes R2-02 industry classifier output. Proximity is linearly scaled
to 0-10. Unknown industry → 4 (neutral-low fallback per user rule from
R1-03 scorer_defaults: "unknown_industry_score: 4/10"). Industry NEVER
hard-rejects (classifier rule + user directive 2026-04-20).
"""

from pathlib import Path

import yaml

from src.classify.industry import classify_industry

_DEFAULT_SKILLS_YAML = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "personal_fit"
    / "industries.yaml"
)

_MAX_SCORE = 10
_UNKNOWN_FALLBACK = 4  # per R1-03 scorer_defaults.unknown_industry_score

_DEFAULTS_CACHE: dict | None = None
_DEFAULTS_KEY: Path | None = None


def _load_defaults(path: Path) -> dict:
    """Read scorer_defaults block from industries.yaml (for max_score + fallback)."""
    global _DEFAULTS_CACHE, _DEFAULTS_KEY
    if _DEFAULTS_CACHE is not None and _DEFAULTS_KEY == path:
        return _DEFAULTS_CACHE
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    defaults = cfg.get("scorer_defaults", {}) or {}
    compiled = {
        "max_score": int(defaults.get("max_score", _MAX_SCORE)),
        "unknown_industry_score": int(
            defaults.get("unknown_industry_score", _UNKNOWN_FALLBACK)
        ),
    }
    _DEFAULTS_CACHE = compiled
    _DEFAULTS_KEY = path
    return compiled


def reset_cache() -> None:
    global _DEFAULTS_CACHE, _DEFAULTS_KEY
    _DEFAULTS_CACHE = None
    _DEFAULTS_KEY = None


def score_industry_proximity(
    job, classified: dict | None = None, config_path: Path | None = None
) -> dict:
    """Score a job's industry proximity.

    Args:
        job: dict-like with 'company' and 'description'.
        classified: optional pre-computed R2-02 classifier output.
        config_path: optional industries.yaml override.

    Returns:
        {score: int 0-10, signals, reasoning}.
    """
    defaults = _load_defaults(config_path or _DEFAULT_SKILLS_YAML)
    max_score = defaults["max_score"]
    unknown_fallback = defaults["unknown_industry_score"]

    if classified is None:
        classified = classify_industry(
            str(job.get("company", "") or ""),
            str(job.get("description", "") or ""),
        )

    industry = classified.get("industry", "unknown")
    prox = classified.get("proximity_to_ava")
    source = classified.get("source", "unknown")

    if prox is None:
        score = unknown_fallback
        reasoning = (
            f"Industry unknown → fallback {unknown_fallback}/{max_score}"
        )
    else:
        score = int(round(max(0.0, min(1.0, float(prox))) * max_score))
        reasoning = (
            f"Industry '{industry}' proximity {prox:.2f} × {max_score} → {score}/{max_score} "
            f"(source: {source})"
        )

    return {
        "score": score,
        "signals": {
            "industry": industry,
            "proximity_to_ava": prox,
            "source": source,
            "display_name": classified.get("display_name", industry),
        },
        "reasoning": reasoning,
    }
