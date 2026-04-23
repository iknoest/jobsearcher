"""Industry classifier (R2-02).

Classifies (company, description) into an industry defined in
`config/personal_fit/industries.yaml`.

Design rules (locked per user 2026-04-22):
- Word-boundary regex on both company name and JD text.
- Company match takes precedence over JD match.
- If company and JD disagree, JD-based guess surfaces as a secondary_candidate.
- Unknown classification returns proximity_to_ava=None — the scorer (R3-03)
  applies unknown_industry_score fallback, not this classifier.
- Industry NEVER hard-rejects.
"""

import re
from pathlib import Path

import yaml

_DEFAULT_CONFIG = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "personal_fit"
    / "industries.yaml"
)

_CACHE: dict | None = None
_CACHE_KEY: Path | None = None


def reset_cache() -> None:
    global _CACHE, _CACHE_KEY
    _CACHE = None
    _CACHE_KEY = None


def _word_boundary_re(phrases: list | None) -> re.Pattern | None:
    if not phrases:
        return None
    parts = [re.escape(p) for p in phrases if p]
    if not parts:
        return None
    return re.compile(r"(?i)\b(" + "|".join(parts) + r")\b")


def _load_and_compile(config_path: Path) -> dict:
    global _CACHE, _CACHE_KEY
    if _CACHE is not None and _CACHE_KEY == config_path:
        return _CACHE

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    industries = []
    for entry in cfg.get("industries", []) or []:
        industries.append(
            {
                "canonical_name": entry["canonical_name"],
                "display_name": entry.get("display_name", entry["canonical_name"]),
                "nace_code": entry.get("nace_code"),
                "proximity_to_ava": float(entry.get("proximity_to_ava", 0.0)),
                "company_re": _word_boundary_re(entry.get("company_hints")),
                "jd_re": _word_boundary_re(entry.get("jd_hints")),
            }
        )

    compiled = {"industries": industries, "defaults": cfg.get("scorer_defaults", {}) or {}}
    _CACHE = compiled
    _CACHE_KEY = config_path
    return compiled


def _unknown_result() -> dict:
    return {
        "industry": "unknown",
        "display_name": "Unknown",
        "nace_code": None,
        "proximity_to_ava": None,
        "evidence": {"company_match": None, "jd_match": None},
        "source": "unknown",
        "secondary_candidates": [],
        "reasoning": "No company or JD hint matched any industry.",
    }


def classify_industry(company: str, description: str, config_path: Path | None = None) -> dict:
    """Classify a job's industry using company name first, JD text second.

    Returns:
        dict with keys: industry, display_name, nace_code, proximity_to_ava
        (None if unknown), evidence {company_match, jd_match}, source,
        secondary_candidates, reasoning.
    """
    compiled = _load_and_compile(config_path or _DEFAULT_CONFIG)
    industries = compiled["industries"]

    company = company or ""
    jd = (description or "")[:8000]

    # Company match — first hit wins (YAML order: anchors before farther industries).
    company_hit = None
    for ind in industries:
        if ind["company_re"]:
            m = ind["company_re"].search(company)
            if m:
                company_hit = (ind, m.group(0))
                break

    # JD matches — collect all unique industries that match in JD
    jd_hits = []
    seen_jd = set()
    for ind in industries:
        if ind["jd_re"]:
            m = ind["jd_re"].search(jd)
            if m and ind["canonical_name"] not in seen_jd:
                jd_hits.append((ind, m.group(0)))
                seen_jd.add(ind["canonical_name"])

    if not company_hit and not jd_hits:
        return _unknown_result()

    if company_hit:
        primary, co_phrase = company_hit
        # JD matches that disagree with the company primary go to secondary
        disagreeing = [
            (ind, phrase)
            for ind, phrase in jd_hits
            if ind["canonical_name"] != primary["canonical_name"]
        ]
        secondary = [
            {
                "industry": ind["canonical_name"],
                "display_name": ind["display_name"],
                "proximity_to_ava": ind["proximity_to_ava"],
                "source": "jd",
                "matched_hint": phrase,
            }
            for ind, phrase in disagreeing[:3]
        ]
        return {
            "industry": primary["canonical_name"],
            "display_name": primary["display_name"],
            "nace_code": primary["nace_code"],
            "proximity_to_ava": primary["proximity_to_ava"],
            "evidence": {"company_match": co_phrase, "jd_match": None},
            "source": "company",
            "secondary_candidates": secondary,
            "reasoning": (
                f"Company hint '{co_phrase}' → {primary['display_name']} "
                f"(proximity {primary['proximity_to_ava']})."
                + (
                    f" JD disagrees: {disagreeing[0][0]['display_name']} via '{disagreeing[0][1]}'."
                    if disagreeing
                    else ""
                )
            ),
        }

    # JD-only path
    primary, jd_phrase = jd_hits[0]
    secondary = [
        {
            "industry": ind["canonical_name"],
            "display_name": ind["display_name"],
            "proximity_to_ava": ind["proximity_to_ava"],
            "source": "jd",
            "matched_hint": phrase,
        }
        for ind, phrase in jd_hits[1:4]
    ]
    return {
        "industry": primary["canonical_name"],
        "display_name": primary["display_name"],
        "nace_code": primary["nace_code"],
        "proximity_to_ava": primary["proximity_to_ava"],
        "evidence": {"company_match": None, "jd_match": jd_phrase},
        "source": "jd",
        "secondary_candidates": secondary,
        "reasoning": (
            f"No company match; JD hint '{jd_phrase}' → {primary['display_name']} "
            f"(proximity {primary['proximity_to_ava']})."
        ),
    }


def classify_dataframe(df, company_col: str = "company", desc_col: str = "description"):
    """Vectorised wrapper. Adds columns:
    industry, industry_display, industry_nace, industry_proximity (nullable),
    industry_source, industry_secondary (list of canonical_names), industry_reasoning.
    Does NOT drop rows. Used by Stage 3b in main.py alongside role_family classifier.
    """
    if df is None or len(df) == 0:
        return df

    def _run(row):
        return classify_industry(
            str(row.get(company_col, "") or ""),
            str(row.get(desc_col, "") or ""),
        )

    results = df.apply(_run, axis=1)
    df = df.copy()
    df["industry"] = results.map(lambda r: r["industry"])
    df["industry_display"] = results.map(lambda r: r["display_name"])
    df["industry_nace"] = results.map(lambda r: r["nace_code"])
    df["industry_proximity"] = results.map(lambda r: r["proximity_to_ava"])
    df["industry_source"] = results.map(lambda r: r["source"])
    df["industry_secondary"] = results.map(
        lambda r: [c["industry"] for c in r["secondary_candidates"]]
    )
    df["industry_reasoning"] = results.map(lambda r: r["reasoning"])
    return df
