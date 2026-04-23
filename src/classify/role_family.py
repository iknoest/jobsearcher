"""Role-family classifier (R2-01).

Classifies (title, description) into a role family defined in
`config/personal_fit/roles.yaml`.

Design rules (locked per user 2026-04-22):
- Word-boundary regex on both title and JD signals; never substring match.
- Title patterns scan the TITLE field only; JD signals scan the DESCRIPTION only.
- Title alone never hard-rejects (classifier_defaults.review_on_conflict=true).
- Rescue logic: when an excluded-title role has any `jd_signals_rescuing`
  match, the classifier tries to PROMOTE the classification to the
  strongest desired / ambiguous family. If that beats the excluded score,
  it becomes the primary; otherwise the job stays tagged excluded but is
  routed to Review (would_hard_reject=False).
- Hard-reject only when: bucket=excluded AND score ≥ reject threshold AND
  no rescue signal present.
"""

import re
from pathlib import Path

import yaml

_DEFAULT_CONFIG = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "personal_fit"
    / "roles.yaml"
)

# Module-level cache. Tests can call `reset_cache()` or pass config_path to bypass.
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


def _pattern_re(patterns: list | None) -> re.Pattern | None:
    if not patterns:
        return None
    return re.compile(r"(?i)" + "|".join(patterns))


def _load_and_compile(config_path: Path) -> dict:
    global _CACHE, _CACHE_KEY
    if _CACHE is not None and _CACHE_KEY == config_path:
        return _CACHE

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    compiled = {
        "desired": {},
        "ambiguous": {},
        "excluded": {},
        "defaults": cfg.get("classifier_defaults", {}) or {},
    }

    for name, entry in (cfg.get("desired_families") or {}).items():
        compiled["desired"][name] = {
            "display_name": entry.get("display_name", name),
            "title_re": _pattern_re(entry.get("title_patterns")),
            "confirming_re": _word_boundary_re(entry.get("jd_signals_required")),
            "negative_re": _word_boundary_re(entry.get("jd_signals_negative")),
        }

    for name, entry in (cfg.get("ambiguous_families") or {}).items():
        split = entry.get("signal_split", {}) or {}
        compiled["ambiguous"][name] = {
            "display_name": entry.get("display_name", name),
            "title_re": _pattern_re(entry.get("title_patterns")),
            "favors_good_re": _word_boundary_re(split.get("favors_good_fit")),
            "favors_bad_re": _word_boundary_re(split.get("favors_bad_fit")),
        }

    for name, entry in (cfg.get("excluded_families") or {}).items():
        compiled["excluded"][name] = {
            "display_name": entry.get("display_name", name),
            "title_re": _pattern_re(entry.get("title_patterns")),
            "confirming_re": _word_boundary_re(entry.get("jd_signals_confirming")),
            "rescuing_re": _word_boundary_re(entry.get("jd_signals_rescuing")),
            "reject_threshold": float(entry.get("reject_confidence_threshold", 0.7)),
        }

    _CACHE = compiled
    _CACHE_KEY = config_path
    return compiled


def _find_all(regex: re.Pattern | None, text: str) -> list:
    if regex is None or not text:
        return []
    return [m.group(0) for m in regex.finditer(text)]


def _score_candidate(entry: dict, bucket: str, title: str, jd: str, defaults: dict) -> dict:
    """Compute score + evidence for a single family candidate."""
    title_weight = float(defaults.get("min_title_match_weight", 0.4))
    per_signal = float(defaults.get("per_signal_weight", 0.1))

    title_hits = _find_all(entry.get("title_re"), title)
    title_score = title_weight if title_hits else 0.0

    if bucket == "ambiguous":
        # Ambiguous families exist to disambiguate a specific title pattern.
        # Without a title hit, JD signals alone are not enough to classify.
        if not title_hits:
            return {
                "score": 0.0,
                "title_match": [],
                "confirming_jd": [],
                "rescuing_jd": [],
                "negative_jd": [],
                "has_rescue": False,
            }
        good_hits = _find_all(entry.get("favors_good_re"), jd)
        bad_hits = _find_all(entry.get("favors_bad_re"), jd)
        score = title_score + len(good_hits) * per_signal - len(bad_hits) * per_signal
        return {
            "score": max(0.0, min(1.0, score)),
            "title_match": title_hits,
            "confirming_jd": good_hits,
            "rescuing_jd": [],
            "negative_jd": bad_hits,
            "has_rescue": False,
        }

    confirming_hits = _find_all(entry.get("confirming_re"), jd)
    rescuing_hits = _find_all(entry.get("rescuing_re"), jd) if bucket == "excluded" else []
    negative_hits = _find_all(entry.get("negative_re"), jd) if bucket == "desired" else []

    score = title_score + len(confirming_hits) * per_signal - len(negative_hits) * per_signal
    return {
        "score": max(0.0, min(1.0, score)),
        "title_match": title_hits,
        "confirming_jd": confirming_hits,
        "rescuing_jd": rescuing_hits,
        "negative_jd": negative_hits,
        "has_rescue": len(rescuing_hits) > 0,
    }


def _empty_result(reasoning: str) -> dict:
    return {
        "family": "unknown",
        "bucket": "unknown",
        "confidence": 0.0,
        "display_name": "Unknown",
        "evidence_spans": {
            "title_match": [],
            "confirming_jd": [],
            "rescuing_jd": [],
            "negative_jd": [],
        },
        "would_hard_reject": False,
        "secondary_candidates": [],
        "reasoning": reasoning,
    }


def _build_result(primary: dict, secondary: list, would_hard_reject: bool, reasoning: str) -> dict:
    return {
        "family": primary["family"],
        "bucket": primary["bucket"],
        "confidence": round(primary["score"], 3),
        "display_name": primary["display_name"],
        "evidence_spans": {
            "title_match": primary["title_match"],
            "confirming_jd": primary["confirming_jd"],
            "rescuing_jd": primary["rescuing_jd"],
            "negative_jd": primary["negative_jd"],
        },
        "would_hard_reject": would_hard_reject,
        "secondary_candidates": [
            {
                "family": s["family"],
                "bucket": s["bucket"],
                "confidence": round(s["score"], 3),
                "display_name": s["display_name"],
            }
            for s in secondary
        ],
        "reasoning": reasoning,
    }


def classify_role_family(title: str, description: str, config_path: Path | None = None) -> dict:
    """Classify a job into a role family.

    Args:
        title: job title string (may be empty).
        description: JD text (may be empty; capped at 8000 chars internally).
        config_path: optional override for testing.

    Returns:
        dict with keys: family, bucket, confidence, display_name,
        evidence_spans, would_hard_reject, secondary_candidates, reasoning.
    """
    compiled = _load_and_compile(config_path or _DEFAULT_CONFIG)
    defaults = compiled["defaults"]

    title = title or ""
    description = (description or "")[:8000]

    # Score every candidate across all three buckets.
    scored: list[dict] = []
    for bucket in ("desired", "ambiguous", "excluded"):
        for name, entry in compiled[bucket].items():
            s = _score_candidate(entry, bucket, title, description, defaults)
            if s["score"] <= 0 and not s["title_match"]:
                continue
            effective_threshold = float(defaults.get("min_confidence_to_hard_reject", 0.7))
            if bucket == "excluded":
                effective_threshold = max(effective_threshold, entry.get("reject_threshold", 0.7))
            eligible_hard = (
                bucket == "excluded"
                and s["score"] >= effective_threshold
                and not s["has_rescue"]
            )
            scored.append(
                {
                    "family": name,
                    "bucket": bucket,
                    "display_name": entry["display_name"],
                    "score": s["score"],
                    "title_match": s["title_match"],
                    "confirming_jd": s["confirming_jd"],
                    "rescuing_jd": s["rescuing_jd"],
                    "negative_jd": s["negative_jd"],
                    "has_rescue": s["has_rescue"],
                    "eligible_hard_reject": eligible_hard,
                }
            )

    if not scored:
        return _empty_result("No title or JD signal matched any family.")

    scored.sort(key=lambda x: (-x["score"], x["bucket"], x["family"]))

    low_band = float((defaults.get("ambiguous_band") or [0.4, 0.7])[0])

    # 1) Hard-reject eligible excluded candidate wins.
    hard_candidates = [s for s in scored if s["eligible_hard_reject"]]
    if hard_candidates:
        primary = hard_candidates[0]
        secondary = [
            s for s in scored
            if s["family"] != primary["family"] and s["score"] >= low_band
        ][:5]
        return _build_result(
            primary,
            secondary,
            would_hard_reject=True,
            reasoning=(
                f"Excluded '{primary['display_name']}' score "
                f"{primary['score']:.2f} ≥ threshold with no rescue signal — hard reject."
            ),
        )

    # 2) Rescue-promotion logic: if the top scorer is an excluded-rescued,
    #    look for a stronger desired/ambiguous alternative.
    top = scored[0]
    if top["bucket"] == "excluded" and top["has_rescue"]:
        alternatives = [
            s for s in scored
            if s["bucket"] in ("desired", "ambiguous") and s["score"] > top["score"]
        ]
        if alternatives:
            primary = alternatives[0]
            secondary = [
                s for s in scored
                if s["family"] != primary["family"] and s["score"] >= low_band
            ][:5]
            return _build_result(
                primary,
                secondary,
                would_hard_reject=False,
                reasoning=(
                    f"Excluded '{top['display_name']}' rescued; promoted to "
                    f"{primary['bucket']} '{primary['display_name']}' "
                    f"(score {primary['score']:.2f} > {top['score']:.2f})."
                ),
            )
        secondary = [s for s in scored[1:] if s["score"] >= low_band][:5]
        return _build_result(
            top,
            secondary,
            would_hard_reject=False,
            reasoning=(
                f"Excluded '{top['display_name']}' rescued (rescue signals present) "
                "but no stronger desired/ambiguous candidate → Review."
            ),
        )

    # 3) Normal case: top scorer wins.
    secondary = [s for s in scored[1:] if s["score"] >= low_band][:5]
    return _build_result(
        top,
        secondary,
        would_hard_reject=False,
        reasoning=(
            f"{top['bucket'].title()} '{top['display_name']}' top score "
            f"{top['score']:.2f}."
        ),
    )


def classify_dataframe(df, title_col: str = "title", desc_col: str = "description"):
    """Vectorised wrapper for pandas DataFrames — used by Stage 3b in main.py.

    Adds columns:
        role_family, role_bucket, role_confidence, role_hard_reject,
        role_rescued, role_secondary
    Returns the augmented DataFrame (does NOT drop rows).
    """
    import pandas as pd  # imported lazily so unit tests can skip pandas

    if df is None or len(df) == 0:
        return df

    def _run(row):
        return classify_role_family(
            str(row.get(title_col, "") or ""),
            str(row.get(desc_col, "") or ""),
        )

    results = df.apply(_run, axis=1)
    df = df.copy()
    df["role_family"] = results.map(lambda r: r["family"])
    df["role_bucket"] = results.map(lambda r: r["bucket"])
    df["role_confidence"] = results.map(lambda r: r["confidence"])
    df["role_hard_reject"] = results.map(lambda r: r["would_hard_reject"])
    df["role_rescued"] = results.map(
        lambda r: r["bucket"] == "excluded" and not r["would_hard_reject"]
    )
    df["role_secondary"] = results.map(
        lambda r: [c["family"] for c in r["secondary_candidates"]]
    )
    df["role_reasoning"] = results.map(lambda r: r["reasoning"])
    return df
