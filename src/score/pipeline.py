"""R3-05: Sub-scorer pipeline orchestrator.

Runs the 6 R3 sub-scorers + aggregate_score on a DataFrame and attaches
score + audit columns so downstream stages (Sheets, digest) can render
the full trust surface without re-running any classification work.

Columns attached (per row):

Score columns:
    score_role_fit            0-25
    score_hard_skill          0-30
    score_seniority_fit       0-15
    score_industry_proximity  0-10  (renamed to avoid clashing with the
                                     0-1 float from the industry classifier)
    score_desirability        0-10
    score_evidence            0-10
    final_score               0-100
    recommendation            "Apply" | "Review" | "Skip"
    recommendation_reason     human-readable rationale
    top_reasons               "label: X/Y; …"  (semicolon-joined)
    top_risks                 "…"
    blockers                  "…"   (hard gates that forced Skip)
    review_flags              "…"   (demoters that forced Review)

Audit columns (for future trust debugging / R4-01 drop-stage view):
    drop_stage                "scored" | "scored_missing_jd"
    drop_reason               short string (empty if surfaced)
    skill_extract_source      "cache" | "llm" | "provided" | "skipped_missing_jd"
    scored_by_llm             bool — true iff skill_extract called the LLM
    used_skill_cache          bool — true iff skill_extract read from cache
    llm_stage_skipped_reason  "" while sub-scoring; main.py fills the
                              reason when the heavy matcher LLM is gated.

The scored DataFrame keeps every input row (no drops), so a caller can
split by `recommendation` to decide which rows reach the heavy matcher
LLM and which bypass it.
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from src.score import (
    aggregate_score,
    score_desirability,
    score_evidence,
    score_hard_skills,
    score_industry_proximity,
    score_role_fit,
    score_seniority_fit,
)


def _row_to_job(row: pd.Series) -> dict:
    """Convert a DataFrame row into the dict shape sub-scorers expect."""
    d = row.to_dict()
    # Normalise common aliases the scorers look up.
    d.setdefault("url", d.get("job_url", ""))
    d.setdefault("job_url", d.get("url", ""))
    return d


def _role_classified_from_row(row: pd.Series) -> dict:
    return {
        "family": row.get("role_family", "unknown"),
        "display_name": row.get("role_family", "") or "",
        "bucket": row.get("role_bucket", "unknown"),
        "confidence": float(row.get("role_confidence", 0.0) or 0.0),
        "would_hard_reject": bool(row.get("role_hard_reject", False)),
    }


def _seniority_classified_from_row(row: pd.Series) -> dict:
    years = row.get("seniority_years_required")
    try:
        years = int(years) if years is not None and str(years) != "" else None
    except (TypeError, ValueError):
        years = None
    return {
        "seniority_level": row.get("seniority_level", "unknown") or "unknown",
        "seniority_years_required": years,
        "seniority_hard_reject": bool(row.get("seniority_hard_reject", False)),
        "seniority_mismatch_flag": bool(row.get("seniority_mismatch_flag", False)),
    }


def _industry_classified_from_row(row: pd.Series) -> dict:
    prox = row.get("industry_proximity")
    try:
        prox = float(prox) if prox is not None and str(prox) != "" else None
    except (TypeError, ValueError):
        prox = None
    return {
        "industry": row.get("industry", "unknown") or "unknown",
        "display_name": row.get("industry_display", "") or "",
        "proximity_to_ava": prox,
        "source": row.get("industry_source", "unknown") or "unknown",
    }


def _has_jd_body(description: Any, min_len: int = 40) -> bool:
    """True iff the JD text is long enough to be worth scoring.

    Most scrapers emit an empty string rather than NaN when the body
    couldn't be fetched; a 40-char floor rejects "See original listing"
    style stubs without rejecting legitimately terse roles.
    """
    if description is None:
        return False
    if isinstance(description, float) and pd.isna(description):
        return False
    return len(str(description).strip()) >= min_len


def _missing_jd_result(row: pd.Series) -> dict:
    """Skip record for a row we cannot score (no usable JD body)."""
    return {
        "score_role_fit": 0,
        "score_hard_skill": 0,
        "score_seniority_fit": 0,
        "score_industry_proximity": 0,
        "score_desirability": 0,
        "score_evidence": 0,
        "final_score": 0,
        "recommendation": "Skip",
        "recommendation_reason": "No JD body available — cannot score",
        "top_reasons": "",
        "top_risks": "missing_jd_body",
        "blockers": "missing_jd_body",
        "review_flags": "",
        "drop_stage": "scored_missing_jd",
        "drop_reason": "missing_jd_body",
        "skill_extract_source": "skipped_missing_jd",
        "scored_by_llm": False,
        "used_skill_cache": False,
        "llm_stage_skipped_reason": "missing_jd_body",
    }


def _score_one(row: pd.Series) -> dict:
    """Run the 6 sub-scorers + aggregate for a single row."""
    job = _row_to_job(row)
    role_classified = _role_classified_from_row(row)
    seniority_classified = _seniority_classified_from_row(row)
    industry_classified = _industry_classified_from_row(row)

    rf = score_role_fit(job, classified=role_classified)
    hs = score_hard_skills(job)  # uses disk cache; no classifier hand-off
    sn = score_seniority_fit(job, classified=seniority_classified)
    ip = score_industry_proximity(job, classified=industry_classified)
    de = score_desirability(job)
    ev = score_evidence(
        job,
        role_family=role_classified.get("family"),
        industry=industry_classified.get("industry"),
    )

    agg = aggregate_score(
        role_fit=rf,
        hard_skill=hs,
        seniority_fit=sn,
        industry_proximity=ip,
        desirability=de,
        evidence=ev,
        role_classified=role_classified,
        seniority_classified=seniority_classified,
        industry_classified=industry_classified,
    )

    extraction_source = (hs.get("signals") or {}).get("extraction_source", "provided")
    # Skip set: aggregate says Skip ⇒ heavy LLM should NOT fire downstream.
    # Empty string signals "not yet decided" so main.py can fill
    # max_jobs / below_cap reasons later.
    llm_skip_reason = "recommendation=Skip" if agg["recommendation"] == "Skip" else ""

    return {
        "score_role_fit": int(rf.get("score", 0)),
        "score_hard_skill": int(hs.get("score", 0)),
        "score_seniority_fit": int(sn.get("score", 0)),
        "score_industry_proximity": int(ip.get("score", 0)),
        "score_desirability": int(de.get("score", 0)),
        "score_evidence": int(ev.get("score", 0)),
        "final_score": int(agg.get("final_score", 0)),
        "recommendation": agg.get("recommendation", "Skip"),
        "recommendation_reason": agg.get("reason", ""),
        "top_reasons": "; ".join(agg.get("top_reasons", []) or []),
        "top_risks": "; ".join(agg.get("top_risks", []) or []),
        "blockers": "; ".join(agg.get("blockers", []) or []),
        "review_flags": "; ".join(agg.get("review_flags", []) or []),
        "drop_stage": "scored",
        "drop_reason": (
            "; ".join(agg.get("blockers", []) or [])
            if agg.get("recommendation") == "Skip"
            else ""
        ),
        "skill_extract_source": extraction_source,
        "scored_by_llm": extraction_source == "llm",
        "used_skill_cache": extraction_source == "cache",
        "llm_stage_skipped_reason": llm_skip_reason,
        # Raw signals kept for audit / digest / explain (dict, survives pickling).
        "_score_raw_hard_skill_signals": hs.get("signals", {}) or {},
        "_score_raw_role_fit_signals": rf.get("signals", {}) or {},
    }


def run_sub_scorers(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Attach score + audit columns for every row in `df`.

    Never drops rows — a row that cannot be scored (missing JD body,
    aggregate-Skip, …) still emerges with `recommendation="Skip"` and
    `drop_stage` / `drop_reason` set so the caller can present it in
    the funnel.

    Returns:
        (df_with_scores, counters) where counters has keys:
            total, scored_ok, missing_jd,
            apply, review, skip,
            skill_llm_calls, skill_cache_hits, skill_extraction_failures
    """
    if df is None or len(df) == 0:
        return df, {
            "total": 0, "scored_ok": 0, "missing_jd": 0,
            "apply": 0, "review": 0, "skip": 0,
            "skill_llm_calls": 0, "skill_cache_hits": 0,
            "skill_extraction_failures": 0,
        }

    counters = {
        "total": len(df),
        "scored_ok": 0,
        "missing_jd": 0,
        "apply": 0,
        "review": 0,
        "skip": 0,
        "skill_llm_calls": 0,
        "skill_cache_hits": 0,
        "skill_extraction_failures": 0,
    }

    rows_out: list[dict] = []
    for _, row in df.iterrows():
        description = row.get("description", "")
        if not _has_jd_body(description):
            result = _missing_jd_result(row)
            counters["missing_jd"] += 1
        else:
            result = _score_one(row)
            counters["scored_ok"] += 1

            src = result["skill_extract_source"]
            if src == "llm":
                counters["skill_llm_calls"] += 1
                # Count as failure if the LLM returned nothing despite a
                # non-empty JD body — means extract_jd_skills hit its
                # fallback-exhausted / parse-error path.
                raw_hs = result.get("_score_raw_hard_skill_signals", {}) or {}
                if int(raw_hs.get("jd_skills_count", 0)) == 0:
                    counters["skill_extraction_failures"] += 1
            elif src == "cache":
                counters["skill_cache_hits"] += 1

            rec = result["recommendation"]
            if rec == "Apply":
                counters["apply"] += 1
            elif rec == "Review":
                counters["review"] += 1
            else:
                counters["skip"] += 1

        merged = {**row.to_dict(), **result}
        rows_out.append(merged)

    scored_df = pd.DataFrame(rows_out)
    return scored_df, counters


def format_counters(counters: dict, elapsed_seconds: float | None = None) -> str:
    """Pretty-print the counters dict returned by run_sub_scorers."""
    lines = [
        f"Scored: {counters['scored_ok']}/{counters['total']} ("
        f"Apply={counters['apply']}, "
        f"Review={counters['review']}, "
        f"Skip={counters['skip']}, "
        f"missing_JD={counters['missing_jd']})",
        f"Skill LLM: calls={counters['skill_llm_calls']}, "
        f"cache_hits={counters['skill_cache_hits']}, "
        f"extraction_failures={counters['skill_extraction_failures']}",
    ]
    if elapsed_seconds is not None:
        lines.append(f"Elapsed: {elapsed_seconds:.1f}s")
    return "\n".join("  " + l for l in lines)


def run_and_print(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Convenience wrapper for main.py: run + print counters in one step."""
    t0 = time.time()
    scored_df, counters = run_sub_scorers(df)
    print(format_counters(counters, elapsed_seconds=time.time() - t0))
    return scored_df, counters
