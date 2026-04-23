"""Deterministic per-card explanation generator (R4-02, refined 2026-04-23).

Reads a row dict containing R3-05 score + audit columns and returns
human-readable text for:
    one_liner     : "Product Manager role in Robotics, focused on
                     prototyping, user research"
    matched_skills: concrete verified canonical-skill names from hard_skill
    gap_skills    : forbidden_core + forbidden_optional hits
    adjacent_familiar: hard_skill.adjacent_matches canonical names
    evidence_anchors : evidence.signals.matched_proofs topics
    why_fits      : 2-3 bullets — why this role may fit (Apply + Review)
    watch_outs    : 2-3 bullets — risks / caveats (Apply)
    holding_back  : 2-3 bullets — why it landed in Review (Review)
    still_worth   : 2-3 bullets — why Review is still worth a look
    skip_reason   : plain-language 1-line reason (Skip)
    subscore_bars : ordered list for renderer

NO LLM is used anywhere in this module. All content is derived from
sub-score + aggregate signals so the user can audit any explanation
against raw scorer output without trusting a generated paragraph.

User rule 2026-04-23: digest copy must answer "what this job is / why
it may fit / what holds it back" within 3 seconds per card. Score
numbers alone are not enough — concrete skill / evidence / gap tags
are required on every card.
"""

from __future__ import annotations

from typing import Any


# Sub-score maxima (mirror src/score/aggregate.py).
_SUBSCORES = [
    ("role_fit", "score_role_fit", "Role fit", 25),
    ("hard_skill", "score_hard_skill", "Hard skills", 30),
    ("seniority_fit", "score_seniority_fit", "Seniority fit", 15),
    ("industry_proximity", "score_industry_proximity", "Industry proximity", 10),
    ("desirability", "score_desirability", "Desirability", 10),
    ("evidence", "score_evidence", "Evidence", 10),
]

_STRONG_RATIO = 0.70
_WEAK_RATIO = 0.40


# ── Sub-score helpers ────────────────────────────────────────────────

def _sub(row: dict, col: str, max_val: int) -> tuple[int, float]:
    raw = row.get(col, 0)
    try:
        score = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        score = 0
    ratio = (score / max_val) if max_val else 0.0
    return score, ratio


def _strong_subscores(row: dict) -> list[tuple[str, int, int]]:
    out = []
    for _, col, label, max_val in _SUBSCORES:
        score, ratio = _sub(row, col, max_val)
        if ratio >= _STRONG_RATIO:
            out.append((label, score, max_val))
    out.sort(key=lambda x: -(x[1] / x[2]))
    return out


def _weak_subscores(row: dict) -> list[tuple[str, int, int]]:
    out = []
    for _, col, label, max_val in _SUBSCORES:
        score, ratio = _sub(row, col, max_val)
        if ratio <= _WEAK_RATIO:
            out.append((label, score, max_val))
    out.sort(key=lambda x: x[1] / x[2])
    return out


def _split_semi(val: Any) -> list[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(p).strip() for p in val if str(p).strip()]
    return [p.strip() for p in str(val).split(";") if p.strip()]


def _signal(row: dict, key: str) -> dict:
    raw = row.get(key)
    return raw if isinstance(raw, dict) else {}


def _canonical_names(matches: list, cap: int = 3) -> list[str]:
    """Extract up to `cap` canonical_name strings from a list of match dicts."""
    names = []
    for m in matches or []:
        if isinstance(m, dict):
            name = m.get("canonical_name") or m.get("name") or ""
        else:
            name = str(m or "")
        name = name.strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= cap:
            break
    return names


# ── Concrete signal extractors (for the "tags" surface) ───────────────

def _matched_skills(row: dict, cap: int = 3) -> list[str]:
    """Verified canonical skills from hard_skill.signals.verified_matches."""
    sig = _signal(row, "_score_raw_hard_skill_signals")
    return _canonical_names(sig.get("verified_matches", []), cap=cap)


def _gap_skills(row: dict, cap: int = 3) -> list[str]:
    """Forbidden-core + forbidden-optional skills present in JD."""
    sig = _signal(row, "_score_raw_hard_skill_signals")
    core = _canonical_names(sig.get("forbidden_core_hits", []), cap=cap)
    optional = _canonical_names(sig.get("forbidden_optional_hits", []), cap=cap)
    # Core hits take priority in display
    combined = core + [o for o in optional if o not in core]
    return combined[:cap]


def _adjacent_familiar(row: dict, cap: int = 3) -> list[str]:
    """Adjacent (Low strength) skills — 'only adjacent familiarity with ...'."""
    sig = _signal(row, "_score_raw_hard_skill_signals")
    return _canonical_names(sig.get("adjacent_matches", []), cap=cap)


def _evidence_anchors(row: dict, cap: int = 3) -> list[str]:
    """Proof-point topics from evidence.yaml that matched this job."""
    sig = _signal(row, "_score_raw_evidence_signals")
    topics = []
    for p in sig.get("matched_proofs", []) or []:
        if isinstance(p, dict):
            t = str(p.get("topic") or p.get("id") or "").strip()
            if t and t not in topics:
                topics.append(t)
        if len(topics) >= cap:
            break
    return topics


def _deliverables_in_jd(row: dict, cap: int = 3) -> list[str]:
    """Strong deliverable phrases the role_fit scorer matched in the JD."""
    sig = _signal(row, "_score_raw_role_fit_signals")
    strong = [str(x).strip() for x in (sig.get("deliverable_hits") or []) if str(x).strip()]
    return strong[:cap]


# ── One-liner (what this job IS) ──────────────────────────────────────

_ROLE_FAMILY_SHORT = {
    "product_manager": "Product",
    "product_researcher": "Product / UX research",
    "product_engineer_hardware": "Product engineering (hardware)",
    "innovation_engineer": "Innovation",
    "design_technologist": "Design / prototype",
    "human_factors_engineer": "Human factors",
    "technical_product_manager": "Technical product",
    "research_engineer": "Research engineering",
    "ai_product_manager": "AI product",
    "growth_engineer": "Growth",
    "strategy_consultant": "Strategy / consulting",
    "product_marketing_manager": "Product marketing",
    "software_engineer": "Software engineering",
    "system_engineer": "Systems engineering",
    "biomedical_engineer": "Biomedical device engineering",
    "devops_infra": "DevOps / infra",
    "pure_qa": "QA / test",
    "process_manufacturing_engineer": "Process / manufacturing",
    "electrical_engineer": "Electrical / electronics",
    "domain_locked_engineering": "Domain-locked engineering",
}


def _role_label(row: dict) -> str:
    """Short phrase describing the role — family-aware, falls back to title."""
    fam = str(row.get("role_family", "") or "").strip()
    short = _ROLE_FAMILY_SHORT.get(fam)
    if short:
        return f"{short} role"
    title = str(row.get("title", "") or "").strip()
    if title:
        # "Senior Product Manager" → "Product role"; grab the last 1-2 meaningful tokens
        toks = [t for t in title.split() if t.lower() not in {"senior", "lead", "staff", "principal", "junior"}]
        if toks:
            return f"{toks[-1]} role"
    return "Role"


def _industry_label(row: dict) -> str:
    display = str(row.get("industry_display", "") or "").strip()
    if display:
        return display
    raw = str(row.get("industry", "") or "").strip().replace("_", " ")
    if raw and raw.lower() != "unknown":
        return raw
    return "unspecified industry"


def _focus_label(row: dict) -> str:
    """What the role is ACTUALLY about — from concrete signals, not labels.

    Priority:
      1. 2 verified hard skills (most grounded)
      2. Otherwise strong deliverables matched in JD
      3. Otherwise "role-specific focus"
    """
    skills = _matched_skills(row, cap=2)
    if skills:
        return ", ".join(s.lower() for s in skills)
    delivers = _deliverables_in_jd(row, cap=2)
    if delivers:
        return ", ".join(d.lower() for d in delivers)
    return "role-specific focus"


def _one_liner(row: dict) -> str:
    """Return the "[Role] in [industry], focused on [main work]" string."""
    return f"{_role_label(row)} in {_industry_label(row)}, focused on {_focus_label(row)}"


# ── Bullet builders ───────────────────────────────────────────────────

def _why_fits(row: dict) -> list[str]:
    """Why this role may fit — concrete, not just score numbers.

    Order:
      1. matched skills line
      2. evidence anchors line
      3. aggregate top_reasons (if still under 3 bullets)
    """
    bullets = []
    skills = _matched_skills(row, cap=3)
    if skills:
        bullets.append("Strong fit: " + ", ".join(skills))
    anchors = _evidence_anchors(row, cap=3)
    if anchors:
        bullets.append("Evidence anchor: " + ", ".join(anchors))
    for r in _split_semi(row.get("top_reasons")):
        if len(bullets) >= 3:
            break
        if not any(_same_line(r, b) for b in bullets):
            bullets.append(r)
    if not bullets:
        # Last-resort fallback — always say SOMETHING concrete.
        for label, score, max_val in _strong_subscores(row)[:2]:
            bullets.append(f"{label} strong ({score}/{max_val})")
    return _dedupe(bullets)[:3]


def _watch_outs(row: dict) -> list[str]:
    """Risks / caveats for an Apply-tier card.

    Order:
      1. concrete gap skills (forbidden core/optional)
      2. adjacent-only familiarity note (Low-strength hits)
      3. aggregate top_risks / review_flags
      4. weak sub-score fallback
    """
    bullets = []
    gaps = _gap_skills(row, cap=3)
    if gaps:
        bullets.append("Gap: " + ", ".join(gaps))
    adj = _adjacent_familiar(row, cap=2)
    if adj:
        bullets.append("Only adjacent familiarity with " + ", ".join(adj))
    for r in _split_semi(row.get("top_risks")) + _split_semi(row.get("review_flags")):
        if len(bullets) >= 3:
            break
        if not any(_same_line(r, b) for b in bullets):
            bullets.append(r)
    if len(bullets) < 2:
        for label, score, max_val in _weak_subscores(row)[:2]:
            bullets.append(f"Weaker on {label.lower()} ({score}/{max_val})")
    return _dedupe(bullets)[:3]


def _holding_back(row: dict) -> list[str]:
    """Why this Review-tier role didn't make Apply.

    Same shape as watch_outs but the review_flags come first because
    they are the actual demoters in the aggregate.
    """
    bullets = _split_semi(row.get("review_flags"))
    gaps = _gap_skills(row, cap=2)
    if gaps:
        bullets.append("Gap: " + ", ".join(gaps))
    if not bullets:
        bullets = _split_semi(row.get("top_risks"))
    for label, score, max_val in _weak_subscores(row)[:2]:
        note = f"Weaker on {label.lower()} ({score}/{max_val})"
        if not any(_same_line(note, b) for b in bullets):
            bullets.append(note)
    return _dedupe(bullets)[:3]


def _still_worth(row: dict) -> list[str]:
    """Why a Review-tier card is still worth a look."""
    bullets = []
    skills = _matched_skills(row, cap=3)
    if skills:
        bullets.append("Matched skills: " + ", ".join(skills))
    anchors = _evidence_anchors(row, cap=2)
    if anchors:
        bullets.append("Evidence anchor: " + ", ".join(anchors))
    for r in _split_semi(row.get("top_reasons")):
        if len(bullets) >= 3:
            break
        if not any(_same_line(r, b) for b in bullets):
            bullets.append(r)
    return _dedupe(bullets)[:3]


def _skip_reason(row: dict) -> str:
    """Plain-language one-line reason for Skip rows.

    User rule 2026-04-23: avoid internal phrasing like "below Review floor".
    """
    blockers = _split_semi(row.get("blockers"))
    if blockers:
        b = blockers[0]
        # soften forbidden_core_skill label
        if b.startswith("forbidden_core_skill:"):
            return "Required skill outside Ava's fit: " + b.split(":", 1)[1].strip()
        if b.startswith("role_hard_reject:"):
            return "Role family is outside Ava's fit: " + b.split(":", 1)[1].strip()
        if b.startswith("seniority_hard_reject:"):
            return "Early-career listing: " + b.split(":", 1)[1].strip()
        return b
    drop = str(row.get("drop_reason", "") or "").strip()
    if drop:
        if drop == "missing_jd_body":
            return "Couldn't read a usable job description"
        return drop
    final = int(row.get("final_score", 0) or 0)
    if final > 0:
        return f"Score {final}/100 — just below the Review threshold"
    return "Skipped"


# ── Sub-score bars ────────────────────────────────────────────────────

def _subscore_bars(row: dict) -> list[dict]:
    out = []
    for _, col, label, max_val in _SUBSCORES:
        score, ratio = _sub(row, col, max_val)
        if ratio >= _STRONG_RATIO:
            band = "strong"
        elif ratio <= _WEAK_RATIO:
            band = "weak"
        else:
            band = "ok"
        out.append({
            "key": col,
            "label": label,
            "score": score,
            "max": max_val,
            "ratio": round(ratio, 2),
            "band": band,
        })
    return out


# ── Public API ────────────────────────────────────────────────────────

def explain_row(row: dict) -> dict:
    """Return all explanation fields for this row.

    The renderer picks what it needs per tier:
        Apply  → one_liner + why_fits + watch_outs + tag surfaces
        Review → one_liner + holding_back + still_worth + tag surfaces
        Skip   → one_liner (optional) + skip_reason
    """
    rec = str(row.get("recommendation", "") or "").strip() or "Skip"
    return {
        "recommendation": rec,
        "one_liner": _one_liner(row),
        "matched_skills": _matched_skills(row),
        "gap_skills": _gap_skills(row),
        "adjacent_familiar": _adjacent_familiar(row),
        "evidence_anchors": _evidence_anchors(row),
        "deliverables_in_jd": _deliverables_in_jd(row),
        "why_fits": _why_fits(row),
        "watch_outs": _watch_outs(row),
        "holding_back": _holding_back(row),
        "still_worth": _still_worth(row),
        "skip_reason": _skip_reason(row),
        "subscore_bars": _subscore_bars(row),
    }


# ── Helpers ───────────────────────────────────────────────────────────

def _same_line(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for i in items:
        k = i.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(i.strip())
    return out
