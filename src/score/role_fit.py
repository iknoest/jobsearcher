"""Role-fit sub-scorer (R3-01) — PRD §15.2, weight 25.

Rule-based scorer that combines R2-01 role-family classification with
JD deliverable / anti-pattern signals grounded in Ava's preferred
work loop (research → idea → prototype → validation).

Design rules (locked per user 2026-04-22):
- Bucket × confidence drives the base score inside hard caps:
    desired          15 → 20
    ambiguous        10 → 14
    excluded+rescued  6 → 10
    excluded+reject   0  (safety net; should be gated earlier)
    unknown           6
- Deliverable bonus (max +5): strong verbs +1, weak/vague +0.5
- Anti-pattern penalty (max −3): hard signals −1 each;
  `roadmap alignment` only fires when combined with a hard signal
  (not standalone — per user rule 2026-04-22)
- No LLM. Word-boundary regex on all phrase detection.
"""

import re

from src.classify.role_family import classify_role_family

# ── Deliverable vocabulary (grounded in config/preferences.yaml + user feedback) ──
# Strong: specific verbs matching Ava's actual work; +1 each
_STRONG_DELIVERABLES = [
    "research",
    "prototype",
    "prototyping",
    "validate",
    "validation",
    "user testing",
    "concept development",
    "mvp",
    "experiment",
    "discovery",
    "benchmark",
    "requirements",
    "feature definition",
    "solution evaluation",
    "insight synthesis",
]

# Weak: generic startup/coaching language; +0.5 each — kept as signal but
# deliberately noisier per user feedback 2026-04-22.
# Requires a dedicated regex because phrases contain non-word chars (→, -).
_WEAK_DELIVERABLES_DISPLAY = ["hands-on", "player-coach", "0→1"]
_WEAK_RE = re.compile(
    r"(?i)"
    r"(\bhands[-\s]?on\b"
    r"|\bplayer[-\s]?coach\b"
    r"|(?<!\w)0\s*(?:→|to)\s*1(?!\w))"
)

# Hard anti-patterns: −1 each, cap −3
_HARD_ANTI_PATTERNS = [
    "ppt",
    "slide deck",
    "waterfall",
    "steering committee",
]

# Conditional anti-pattern: fires only when a hard anti-pattern also hit
# (per user rule 2026-04-22: "roadmap alignment" is too noisy as a standalone
# heavy penalty trigger).
_ROADMAP_ALIGNMENT_TERM = "roadmap alignment"


def _word_boundary_re(phrases: list) -> re.Pattern | None:
    if not phrases:
        return None
    parts = [re.escape(p) for p in phrases if p]
    if not parts:
        return None
    return re.compile(r"(?i)\b(" + "|".join(parts) + r")\b")


_STRONG_RE = _word_boundary_re(_STRONG_DELIVERABLES)
_HARD_ANTI_RE = _word_boundary_re(_HARD_ANTI_PATTERNS)
_ROADMAP_ALIGN_RE = re.compile(r"(?i)\broadmap alignment\b")


def _find_unique(regex: re.Pattern | None, text: str) -> list[str]:
    if regex is None or not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in regex.finditer(text):
        token = m.group(0).lower()
        if token not in seen:
            seen.add(token)
            out.append(m.group(0))
    return out


def _base_from_bucket(bucket: str, confidence: float, would_hard_reject: bool) -> float:
    """Compute the pre-bonus base score from classifier bucket + confidence.

    Caps mirror the user-locked bands:
        desired:          15 → 20
        ambiguous:        10 → 14
        excluded+rescued:  6 → 10
        excluded+reject:   0
        unknown:           6
    """
    conf_delta = max(0.0, confidence - 0.4)

    if bucket == "desired":
        return min(20.0, 15.0 + 10.0 * conf_delta)
    if bucket == "ambiguous":
        return min(14.0, 10.0 + 8.0 * conf_delta)
    if bucket == "excluded":
        if would_hard_reject:
            return 0.0  # safety net: should have been dropped in Stage 3b
        return min(10.0, 6.0 + 6.0 * conf_delta)
    # unknown
    return 6.0


def score_role_fit(job, classified: dict | None = None) -> dict:
    """Score a job's role fit against Ava's preferred loop.

    Args:
        job: dict-like with 'title' and 'description' keys (pandas Series works).
        classified: optional pre-computed role_family classifier result.
                    If None, classifies internally.

    Returns:
        dict with:
            score: int 0-25 (final, clipped and rounded)
            signals: {
                bucket, confidence, rescued, title_match,
                deliverable_hits, weak_deliverable_hits,
                anti_pattern_hits, roadmap_alignment_combo
            }
            reasoning: short human-readable explanation
    """
    title = str(job.get("title", "") or "")
    description = str(job.get("description", "") or "")

    if classified is None:
        classified = classify_role_family(title, description)

    bucket = classified.get("bucket", "unknown")
    confidence = float(classified.get("confidence", 0.0) or 0.0)
    would_hard_reject = bool(classified.get("would_hard_reject", False))
    rescued = bucket == "excluded" and not would_hard_reject
    title_match = classified.get("evidence_spans", {}).get("title_match", [])

    base = _base_from_bucket(bucket, confidence, would_hard_reject)

    # Deliverable bonus
    jd = description[:8000]
    strong_hits = _find_unique(_STRONG_RE, jd)
    weak_hits = _find_unique(_WEAK_RE, jd)
    bonus_raw = len(strong_hits) * 1.0 + len(weak_hits) * 0.5
    bonus = min(5.0, bonus_raw)

    # Anti-pattern penalty
    hard_anti_hits = _find_unique(_HARD_ANTI_RE, jd)
    roadmap_present = bool(_ROADMAP_ALIGN_RE.search(jd))
    roadmap_combo = roadmap_present and bool(hard_anti_hits)
    penalty_raw = len(hard_anti_hits) * 1.0 + (1.0 if roadmap_combo else 0.0)
    penalty = min(3.0, penalty_raw)

    # Safety net: a hard-reject excluded role must stay at 0 regardless of
    # deliverable bonuses (it shouldn't have reached this scorer at all).
    if bucket == "excluded" and would_hard_reject:
        final = 0
    else:
        final = max(0, min(25, int(round(base + bonus - penalty))))

    # Reasoning
    parts: list[str] = []
    if bucket == "desired":
        parts.append(f"Desired role '{classified.get('display_name', '?')}' (conf {confidence:.2f}) → base {base:.1f}")
    elif bucket == "ambiguous":
        parts.append(f"Ambiguous role '{classified.get('display_name', '?')}' (conf {confidence:.2f}) → base {base:.1f}")
    elif bucket == "excluded":
        if rescued:
            parts.append(f"Excluded-but-rescued '{classified.get('display_name', '?')}' (conf {confidence:.2f}) → base {base:.1f} (capped at 10)")
        else:
            parts.append(f"Excluded '{classified.get('display_name', '?')}' — would hard-reject → base 0 (safety net)")
    else:
        parts.append("No role classification → neutral base 6")

    if strong_hits or weak_hits:
        shown = ", ".join(strong_hits + [f"{h} (weak)" for h in weak_hits][:4])
        parts.append(f"+{bonus:.1f} deliverables ({shown})")

    if hard_anti_hits:
        parts.append(f"−{len(hard_anti_hits)} anti-patterns ({', '.join(hard_anti_hits)})")
    if roadmap_combo:
        parts.append("−1 roadmap-alignment combo (paired with hard anti-pattern)")

    reasoning = "; ".join(parts) + f" → final {final}/25"

    return {
        "score": final,
        "signals": {
            "bucket": bucket,
            "confidence": round(confidence, 3),
            "rescued": rescued,
            "title_match": title_match,
            "deliverable_hits": strong_hits,
            "weak_deliverable_hits": weak_hits,
            "anti_pattern_hits": hard_anti_hits,
            "roadmap_alignment_combo": roadmap_combo,
        },
        "reasoning": reasoning,
    }
