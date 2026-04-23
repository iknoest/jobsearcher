"""Rule-based pre-ranking — cheap heuristic applied BEFORE LLM scoring.

Purpose: LLM quota is scarce. Pre-rank uses enrichment flags, title keywords,
and a preferred-companies list to score every candidate, then the top N get
full LLM analysis while the rest are logged as "below pre-rank threshold".

All weights live in config/prerank.yaml — edit there, not here.
"""

import yaml
import pandas as pd


def load_prerank_config(config_path="config/prerank.yaml"):
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _score_one(row, config):
    """Return (score, reasons) for a single job row."""
    score = 0
    reasons = []

    title = str(row.get("title", "")).lower()
    company = str(row.get("company", "")).lower()
    description = str(row.get("description", "")).lower()

    for flag, weight in (config.get("flag_weights") or {}).items():
        if bool(row.get(flag, False)):
            score += weight
            reasons.append(f"{flag}{weight:+d}")

    for kw, weight in (config.get("title_bonus") or {}).items():
        if kw.lower() in title:
            score += weight
            reasons.append(f"title:{kw}{weight:+d}")

    for kw, weight in (config.get("title_penalty") or {}).items():
        if kw.lower() in title:
            score += weight
            reasons.append(f"title:{kw}{weight:+d}")

    for name, weight in (config.get("preferred_companies") or {}).items():
        if name.lower() in company:
            score += weight
            reasons.append(f"company:{name}{weight:+d}")

    for name, weight in (config.get("demoted_companies") or {}).items():
        if name.lower() in company:
            score += weight
            reasons.append(f"company:{name}{weight:+d}")

    for kw, weight in (config.get("industry_bonus") or {}).items():
        if kw.lower() in description:
            score += weight
            reasons.append(f"industry:{kw}{weight:+d}")

    for kw, weight in (config.get("skill_bonus") or {}).items():
        if kw.lower() in description:
            score += weight
            reasons.append(f"skill:{kw}{weight:+d}")

    for kw, weight in (config.get("description_penalty") or {}).items():
        if kw.lower() in description:
            score += weight
            reasons.append(f"anti:{kw}{weight:+d}")

    return score, ", ".join(reasons) if reasons else "no signals"


def apply_prerank(df, config=None):
    """Score every job in df and return a sorted copy (descending).

    Adds columns: `prerank_score`, `prerank_reasons`.
    """
    if df is None or df.empty:
        return df

    if config is None:
        config = load_prerank_config()

    scores = []
    reasons = []
    for _, row in df.iterrows():
        s, r = _score_one(row, config)
        scores.append(s)
        reasons.append(r)

    out = df.copy()
    out["prerank_score"] = scores
    out["prerank_reasons"] = reasons
    out = out.sort_values("prerank_score", ascending=False).reset_index(drop=True)
    return out


def split_by_prerank(df, top_n):
    """Return (kept, dropped) split by the top-N pre-rank score.

    Jobs outside the top N are dropped but preserved in the second frame so
    main.py can log them as 'below pre-rank threshold' in the digest.
    """
    if df is None or df.empty or top_n <= 0 or len(df) <= top_n:
        return df, df.iloc[0:0] if df is not None else df
    return df.iloc[:top_n].reset_index(drop=True), df.iloc[top_n:].reset_index(drop=True)
