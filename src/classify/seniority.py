"""Seniority classifier (R2-03).

Classifies (title, description) into a seniority level and optionally
extracts years_required. Drives a Stage 3b hard-reject gate for
explicit early-career roles only — everything else is flagged for R3.

Design rules (locked per user 2026-04-22):
- HARD reject only for explicit early-career (intern / stagiair /
  trainee / graduate program). Blanket 'junior' does NOT hard-reject
  (per user rule 2026-04-20, migrated into constraints.yaml as
  seniority_entry_level_role + seniority_junior_ambiguous).
- FLAG (mismatch, no drop) for: junior / associate / mid-level /
  medior / head of / VP of / director of / chief X officer.
- Word-boundary regex everywhere; title_patterns scan TITLE ONLY;
  phrases_any_in_role_context scans the JD blob.
- Soft penalty accumulation is deferred to R3's eligibility/scoring
  — this module NEVER emits a penalty value.
- Reads the same constraints.yaml file that R1-04 established; no
  separate seniority.yaml in V1.
"""

import re
from pathlib import Path

import yaml

_DEFAULT_CONFIG = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "personal_fit"
    / "constraints.yaml"
)

_CACHE: dict | None = None
_CACHE_KEY: Path | None = None

# Seniority-inferring title patterns used when no constraints.yaml rule fires.
# These are not in constraints.yaml because they're classification-only
# (senior / lead / principal don't need a violation action).
_SENIOR_TITLE_RE = re.compile(
    r"(?i)\b(senior|sr\.?|lead|principal|staff)\b"
)
_HEAD_OF_TITLE_RE = re.compile(
    r"(?i)\b(head of|vp of|vice president of|director of|chief [a-z]+ officer)\b"
)
_MID_LEVEL_PHRASE_RE = re.compile(
    r"(?i)\b(mid[\-\s]?level|medior|3\s*-\s*5\s*years|2\s*-\s*4\s*years)\b"
)
# Matches '8+ years', 'minimum 10 years', '5 or more years', '3-5 years'
_YEARS_SINGLE_RE = re.compile(
    r"(?i)(?:minimum\s+of\s+|minimum\s+|at\s+least\s+|min\.\s*)?"
    r"(\d{1,2})\s*\+?\s*(?:or\s+more\s+)?years?\s+(?:of\s+)?"
    r"(?:experience|exp\b|relevant|work|professional)"
)
_YEARS_RANGE_RE = re.compile(
    r"(?i)(\d{1,2})\s*[-–]\s*(\d{1,2})\s*years?\s+(?:of\s+)?(?:experience|exp\b|relevant)"
)


def reset_cache() -> None:
    global _CACHE, _CACHE_KEY
    _CACHE = None
    _CACHE_KEY = None


def _title_pattern_re(patterns: list | None) -> re.Pattern | None:
    if not patterns:
        return None
    return re.compile(r"(?i)" + "|".join(patterns))


def _phrase_re(phrases: list | None) -> re.Pattern | None:
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

    by_id = {}
    for c in cfg.get("constraints", []) or []:
        if c.get("scope") == "seniority":
            trig = c.get("trigger", {}) or {}
            by_id[c["id"]] = {
                "id": c["id"],
                "type": c.get("type"),
                "action": c.get("action_on_violation"),
                "flag_name": c.get("flag_name"),
                "reason": c.get("reason_template", ""),
                "title_re": _title_pattern_re(trig.get("title_patterns")),
                "context_re": _phrase_re(trig.get("phrases_any_in_role_context")),
                "phrase_re": _phrase_re(trig.get("phrases_any")),
            }

    compiled = {"by_id": by_id}
    _CACHE = compiled
    _CACHE_KEY = config_path
    return compiled


def _extract_years_required(text: str) -> int | None:
    """Extract minimum years of experience required from JD text.

    Prefers explicit experience phrases; returns the smallest value
    when multiple candidates appear.
    """
    if not text:
        return None

    values: list[int] = []
    for m in _YEARS_RANGE_RE.finditer(text):
        lo = int(m.group(1))
        if 0 < lo <= 30:
            values.append(lo)
    for m in _YEARS_SINGLE_RE.finditer(text):
        v = int(m.group(1))
        if 0 < v <= 30:
            values.append(v)

    if not values:
        return None
    return min(values)


def classify_seniority(
    title: str, description: str, config_path: Path | None = None
) -> dict:
    """Classify a job's seniority and compute Stage 3b gate outputs.

    Returns dict with keys:
        seniority_level: one of {entry_level, junior, mid, senior, lead,
                                  head_of, unknown}
        seniority_confidence: 0.0-1.0
        seniority_years_required: int or None
        seniority_hard_reject: bool — True ONLY for explicit early-career
        seniority_mismatch_flag: bool — True for junior/head_of/mid signals
        seniority_reasoning: short human-readable explanation
        matched_rules: list[str] of constraint IDs that fired
    """
    compiled = _load_and_compile(config_path or _DEFAULT_CONFIG)
    by_id = compiled["by_id"]

    title = title or ""
    jd = (description or "")[:8000]

    matched_rules: list[str] = []
    evidence: list[str] = []

    # ── 1. HARD: explicit early-career (intern / stagiair / graduate program / trainee)
    entry = by_id.get("seniority_entry_level_role")
    hard_reject = False
    hard_reason = ""
    if entry:
        title_hit = entry["title_re"].search(title) if entry["title_re"] else None
        ctx_hit = entry["context_re"].search(jd) if entry["context_re"] else None
        if title_hit:
            hard_reject = True
            matched_rules.append(entry["id"])
            evidence.append(f"title:'{title_hit.group(0)}'")
            hard_reason = f"Entry-level title '{title_hit.group(0)}'"
        elif ctx_hit:
            hard_reject = True
            matched_rules.append(entry["id"])
            evidence.append(f"jd:'{ctx_hit.group(0)}'")
            hard_reason = f"Entry-level JD phrase '{ctx_hit.group(0)}'"

    # ── 2. Extract years_required from JD (used for level inference)
    years_required = _extract_years_required(jd)

    # ── 3. FLAG: junior/jr (ambiguous — NOT hard reject)
    junior_ambig = by_id.get("seniority_junior_ambiguous")
    junior_hit = None
    if junior_ambig and junior_ambig["title_re"]:
        junior_hit = junior_ambig["title_re"].search(title)
        if junior_hit:
            matched_rules.append(junior_ambig["id"])
            evidence.append(f"title:'{junior_hit.group(0)}'")

    # ── 4. FLAG: head of / VP / director / CXO
    head_of = by_id.get("seniority_caution_head_of")
    head_hit = None
    if head_of and head_of["title_re"]:
        head_hit = head_of["title_re"].search(title)
        if head_hit:
            matched_rules.append(head_of["id"])
            evidence.append(f"title:'{head_hit.group(0)}'")

    # Also catch head-of via generic regex in case constraints.yaml misses it
    if not head_hit:
        m = _HEAD_OF_TITLE_RE.search(title)
        if m:
            head_hit = m
            evidence.append(f"title:'{m.group(0)}'")

    # ── 5. FLAG: mid-level / medior
    mid_level = by_id.get("seniority_target_mid_level")
    mid_hit = None
    if mid_level and mid_level["phrase_re"]:
        mid_hit = mid_level["phrase_re"].search(title + " " + jd)
        if mid_hit:
            matched_rules.append(mid_level["id"])
            evidence.append(f"jd:'{mid_hit.group(0)}'")

    # Also catch mid-level via generic regex
    if not mid_hit:
        m = _MID_LEVEL_PHRASE_RE.search(title + " " + jd)
        if m:
            mid_hit = m
            evidence.append(f"jd:'{m.group(0)}'")

    # ── 6. Senior title inference (no constraint; classification only)
    senior_hit = _SENIOR_TITLE_RE.search(title)

    # ── 7. Resolve level (priority: hard > head_of > senior > mid > junior > years > unknown)
    if hard_reject:
        level = "entry_level"
        confidence = 0.95
        reasoning = hard_reason + " — hard reject"
    elif head_hit:
        level = "head_of"
        confidence = 0.9
        reasoning = f"Head-of / executive title '{head_hit.group(0)}' — flagged as mismatch"
    elif senior_hit and not junior_hit:
        # Senior title wins over mid-level JD noise when the title itself is senior/lead/principal
        token = senior_hit.group(0).lower()
        level = "lead" if token in ("lead", "principal", "staff") else "senior"
        confidence = 0.85
        reasoning = (
            f"Senior/lead title '{senior_hit.group(0)}'"
            + (f" + {years_required}+ yrs" if years_required else "")
        )
    elif mid_hit and not junior_hit:
        level = "mid"
        confidence = 0.75
        reasoning = f"Mid-level cue '{mid_hit.group(0)}' — flagged as mismatch"
    elif junior_hit:
        level = "junior"
        confidence = 0.8
        reasoning = f"Junior title '{junior_hit.group(0)}' — ambiguous, flagged"
    elif years_required is not None:
        # Fall back to years_required inference
        if years_required <= 2:
            level = "junior"
            confidence = 0.6
            reasoning = f"~{years_required}+ yrs required → junior band"
        elif years_required <= 4:
            level = "mid"
            confidence = 0.6
            reasoning = f"~{years_required}+ yrs required → mid band"
        elif years_required <= 9:
            level = "senior"
            confidence = 0.65
            reasoning = f"{years_required}+ yrs required → senior band"
        else:
            level = "lead"
            confidence = 0.65
            reasoning = f"{years_required}+ yrs required → lead/principal band"
    else:
        level = "unknown"
        confidence = 0.0
        reasoning = "No seniority signal in title or JD"

    mismatch_flag = (not hard_reject) and (
        head_hit is not None or junior_hit is not None or (mid_hit is not None and level == "mid")
    )

    return {
        "seniority_level": level,
        "seniority_confidence": round(confidence, 3),
        "seniority_years_required": years_required,
        "seniority_hard_reject": hard_reject,
        "seniority_mismatch_flag": mismatch_flag,
        "seniority_reasoning": reasoning,
        "matched_rules": matched_rules,
    }


def classify_dataframe(df, title_col: str = "title", desc_col: str = "description"):
    """Vectorised wrapper. Adds columns:
        seniority_level, seniority_confidence, seniority_years_required,
        seniority_hard_reject, seniority_mismatch_flag, seniority_reasoning,
        seniority_rules (list).
    Does NOT drop rows. The Stage 3b driver in main.py is responsible for
    filtering on seniority_hard_reject.
    """
    if df is None or len(df) == 0:
        return df

    def _run(row):
        return classify_seniority(
            str(row.get(title_col, "") or ""),
            str(row.get(desc_col, "") or ""),
        )

    results = df.apply(_run, axis=1)
    df = df.copy()
    df["seniority_level"] = results.map(lambda r: r["seniority_level"])
    df["seniority_confidence"] = results.map(lambda r: r["seniority_confidence"])
    df["seniority_years_required"] = results.map(lambda r: r["seniority_years_required"])
    df["seniority_hard_reject"] = results.map(lambda r: r["seniority_hard_reject"])
    df["seniority_mismatch_flag"] = results.map(lambda r: r["seniority_mismatch_flag"])
    df["seniority_reasoning"] = results.map(lambda r: r["seniority_reasoning"])
    df["seniority_rules"] = results.map(lambda r: r["matched_rules"])
    return df
