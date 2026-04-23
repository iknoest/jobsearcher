"""Previous-digest state + feedback summary (R4-02).

Maintains `output/last_digest.json` so the next digest can:
  - Remind the user about Apply / Review / borderline rows that have
    no verdict yet (pending feedback block).
  - Summarise verdicts recorded between the two digests with a simple
    theme-cluster view (Kept X / Rejected Y / Notes Z + top themes).

No LLM. Theme clustering is keyword-based and bounded to a small fixed
taxonomy so the same phrasing reliably collapses into the same bucket.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_DEFAULT_PATH = Path("output") / "last_digest.json"
_BORDERLINE_MIN = 45
_BORDERLINE_MAX = 49


# ── Snapshot IO ───────────────────────────────────────────────────────

def save_last_digest_snapshot(
    rows: Iterable[dict],
    timestamp: str | None = None,
    path: Path | None = None,
) -> None:
    """Persist rows that were surfaced (Apply + Review + borderline Skip).

    Only the fields needed for the pending-feedback block are stored —
    the digest never needs to rehydrate the full DataFrame from disk.
    """
    snapshot = {
        "generated_at": timestamp or datetime.now().isoformat(timespec="seconds"),
        "rows": [_trim_row(r) for r in rows],
    }
    out_path = Path(path) if path else _DEFAULT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def load_last_digest_snapshot(path: Path | None = None) -> dict | None:
    """Read the last snapshot; return None if missing or corrupt."""
    in_path = Path(path) if path else _DEFAULT_PATH
    if not in_path.exists():
        return None
    try:
        with open(in_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "rows" not in data:
        return None
    return data


def _trim_row(row: dict) -> dict:
    """Keep only what the pending-feedback block needs. Dict-safe on DataFrame rows."""
    return {
        "job_url": str(row.get("job_url", "") or ""),
        "title": str(row.get("title", "") or ""),
        "company": str(row.get("company", "") or ""),
        "recommendation": str(row.get("recommendation", "") or ""),
        "final_score": int(row.get("final_score", 0) or 0),
    }


# ── Pending feedback (from previous digest) ───────────────────────────

def compute_pending_feedback(
    last_snapshot: dict | None,
    feedback_data: dict | None,
) -> list[dict]:
    """Rows surfaced previously that still have NO user verdict.

    A verdict counts as "present" when `user_verdicts[*].job_url` matches
    (exact) and the entry is the active (non-superseded) one.
    """
    if not last_snapshot or not last_snapshot.get("rows"):
        return []
    responded = _verdict_urls(feedback_data)
    return [
        row for row in last_snapshot["rows"]
        if row.get("job_url") and row["job_url"] not in responded
    ]


def _verdict_urls(feedback_data: dict | None) -> set[str]:
    """URLs with an active (non-superseded) verdict."""
    if not feedback_data:
        return set()
    urls: set[str] = set()
    for v in feedback_data.get("user_verdicts", []) or []:
        if v.get("superseded_by") is not None:
            continue
        url = v.get("job_url")
        if url:
            urls.add(str(url))
    return urls


# ── Recent-feedback summary + theme clustering ────────────────────────

# Keyword → theme label. Each regex is tested against `user_reason` +
# `title` (lowercased). First-match-wins; small, fixed taxonomy so two
# slightly-different rejects cluster the same way on repeated runs.
_REJECT_THEMES: list[tuple[str, re.Pattern]] = [
    ("Too technical / engineering-heavy", re.compile(r"\b(too technical|engineering-?heavy|deep technical|systems engineer|embedded|firmware|fea|cfd|simulation)\b", re.I)),
    ("Wrong industry / sector", re.compile(r"\b(wrong industry|not.{0,10}(industry|sector|domain)|building material|construction|finance|banking|insurance|defense|defence)\b", re.I)),
    ("Too software / SaaS-focused", re.compile(r"\b(software only|pure software|saas|cloud platform|b2b software|no hardware|missing hardware)\b", re.I)),
    ("Too coordination / alignment-heavy", re.compile(r"\b(coordination|alignment|cross-functional alignment|stakeholder alignment|matrix|steering)\b", re.I)),
    ("Too senior / head-of mismatch", re.compile(r"\b(head of|vp of|director of|too senior|vp,|director,)\b", re.I)),
    ("Too junior / entry-level", re.compile(r"\b(too junior|junior|entry-?level|intern|trainee|graduate)\b", re.I)),
    ("Scope / role focus mismatch", re.compile(r"\b(wrong role|scope|unclear ownership|title misleading|not the role)\b", re.I)),
    ("Commute / work mode", re.compile(r"\b(commute|travel time|too far|on-?site|work mode|remote)\b", re.I)),
    ("Salary / language / licence", re.compile(r"\b(salary|compensation|dutch|nederlands|driver.?licence|driving licence)\b", re.I)),
]


def summarize_recent_feedback(
    feedback_data: dict | None,
    since_timestamp: str | None = None,
) -> dict:
    """Summarise verdicts newer than `since_timestamp`.

    Returns:
        {
          "kept": int, "rejected": int, "notes": int,
          "themes": [{"label": str, "count": int}, ...],   # top 3 reject themes
          "since": str | None,
        }
    """
    kept = 0
    rejected = 0
    notes = 0
    reject_reasons: list[str] = []

    for v in _active_since(feedback_data, since_timestamp):
        verdict = str(v.get("user_verdict", "") or "").lower()
        if _is_keep(verdict):
            kept += 1
        elif _is_reject(verdict):
            rejected += 1
            combined = " ".join([
                str(v.get("user_reason", "")),
                str(v.get("title", "")),
            ])
            reject_reasons.append(combined)
        elif verdict == "note":
            notes += 1

    themes = _cluster_reject_themes(reject_reasons)
    return {
        "kept": kept,
        "rejected": rejected,
        "notes": notes,
        "themes": themes[:3],
        "since": since_timestamp,
    }


def _active_since(
    feedback_data: dict | None,
    since_timestamp: str | None,
) -> list[dict]:
    if not feedback_data:
        return []
    since_dt = _parse_ts(since_timestamp) if since_timestamp else None
    out = []
    for v in feedback_data.get("user_verdicts", []) or []:
        if v.get("superseded_by") is not None:
            continue
        if since_dt is None:
            out.append(v)
            continue
        ts = _parse_ts(str(v.get("timestamp", "") or ""))
        if ts is None or ts >= since_dt:
            out.append(v)
    return out


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _is_keep(verdict: str) -> bool:
    return "good" in verdict or "keep" in verdict or "disagree-fit" in verdict


def _is_reject(verdict: str) -> bool:
    return "skip" in verdict or "reject" in verdict or "disagree-skip" in verdict


def _cluster_reject_themes(reject_reasons: list[str]) -> list[dict]:
    """Return sorted [{label, count}, ...] for Reject verdicts.

    Each reason contributes to at most one theme (first regex match wins).
    Unmatched reasons fall into an "Other reason" bucket so the block
    still adds up to the total reject count.
    """
    counts: dict[str, int] = {}
    for r in reject_reasons:
        label = _classify_reject(r)
        counts[label] = counts.get(label, 0) + 1
    return [
        {"label": lbl, "count": n}
        for lbl, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def _classify_reject(text: str) -> str:
    text_norm = (text or "").lower()
    for label, pat in _REJECT_THEMES:
        if pat.search(text_norm):
            return label
    return "Other reason"
