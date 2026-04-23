"""Evidence sub-scorer (R3-03d) — PRD §15.7, weight 10.

Counts how many proof points from config/personal_fit/evidence.yaml
match this job's role_family and/or industry. Each match contributes
points_per_match × strength_multiplier, capped at max_score.

A proof point 'matches' when:
    role_family in proof.role_families_matched  OR
    industry    in proof.industries_matched

(V1 rule; skill-overlap matching deferred to V2 alongside R3-02b
synonym enrichment.)
"""

from pathlib import Path

import yaml

_DEFAULT_EVIDENCE_YAML = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "personal_fit"
    / "evidence.yaml"
)

_CACHE: dict | None = None
_CACHE_KEY: Path | None = None


def reset_cache() -> None:
    global _CACHE, _CACHE_KEY
    _CACHE = None
    _CACHE_KEY = None


def _load_evidence(path: Path) -> dict:
    global _CACHE, _CACHE_KEY
    if _CACHE is not None and _CACHE_KEY == path:
        return _CACHE
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    proofs = cfg.get("proof_points", []) or []
    defaults = cfg.get("scorer_defaults", {}) or {}
    compiled = {
        "proofs": [
            {
                "id": p.get("id", ""),
                "topic": p.get("topic", ""),
                "role_families_matched": list(p.get("role_families_matched") or []),
                "industries_matched": list(p.get("industries_matched") or []),
                "strength": p.get("strength", "Medium"),
                "claim": p.get("claim", ""),
            }
            for p in proofs
        ],
        "max_score": int(defaults.get("max_score", 10)),
        "points_per_match": float(defaults.get("points_per_match", 2.0)),
        "cap_per_job": int(defaults.get("cap_per_job", 10)),
        "strength_multiplier": defaults.get("strength_multiplier", {
            "High": 1.0, "Medium": 0.7, "Low": 0.4,
        }),
    }
    _CACHE = compiled
    _CACHE_KEY = path
    return compiled


def score_evidence(
    job,
    role_family: str | None = None,
    industry: str | None = None,
    role_classified: dict | None = None,
    industry_classified: dict | None = None,
    config_path: Path | None = None,
) -> dict:
    """Score the evidence support for a job.

    Args:
        job: dict-like (only used for reasoning display; core inputs are the
             classifier outputs).
        role_family: canonical role_family name. If omitted, read from role_classified.
        industry:   canonical industry name. If omitted, read from industry_classified.

    Returns:
        {score: int 0-10, signals, reasoning}.
    """
    compiled = _load_evidence(config_path or _DEFAULT_EVIDENCE_YAML)

    if role_family is None and role_classified is not None:
        role_family = role_classified.get("family")
    if industry is None and industry_classified is not None:
        industry = industry_classified.get("industry")

    role_family = role_family or ""
    industry = industry or ""

    matched: list[dict] = []
    total = 0.0
    mult_map = compiled["strength_multiplier"]
    for proof in compiled["proofs"]:
        matches_role = role_family and role_family in proof["role_families_matched"]
        matches_industry = industry and industry in proof["industries_matched"]
        if matches_role or matches_industry:
            mult = float(mult_map.get(proof["strength"], 0.7))
            pts = compiled["points_per_match"] * mult
            total += pts
            matched.append({
                "id": proof["id"],
                "topic": proof["topic"],
                "strength": proof["strength"],
                "matched_via": (
                    "role+industry"
                    if matches_role and matches_industry
                    else "role"
                    if matches_role
                    else "industry"
                ),
                "points": round(pts, 2),
            })

    score = min(compiled["cap_per_job"], int(round(total)))
    score = max(0, min(compiled["max_score"], score))

    if matched:
        via_counts = {"role": 0, "industry": 0, "role+industry": 0}
        for m in matched:
            via_counts[m["matched_via"]] = via_counts.get(m["matched_via"], 0) + 1
        reasoning = (
            f"{len(matched)} proof points matched (role={via_counts['role']}, "
            f"industry={via_counts['industry']}, both={via_counts['role+industry']}) "
            f"→ {score}/{compiled['max_score']}"
        )
    else:
        reasoning = f"No proof points matched role_family='{role_family}' or industry='{industry}' → 0/{compiled['max_score']}"

    return {
        "score": score,
        "signals": {
            "role_family": role_family,
            "industry": industry,
            "matched_proofs": matched,
            "matched_count": len(matched),
            "raw_points": round(total, 2),
        },
        "reasoning": reasoning,
    }
