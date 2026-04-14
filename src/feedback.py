"""Two-way feedback loop — sync user verdicts, detect patterns, adjust weights.

Adjustment tiers:
  Nudge (1+ verdicts)   — ±NUDGE_STEP pts (immediate, from first signal)
  Pattern (3+ verdicts) — ±PATTERN_STEP pts additional (auto-detected, TG notified)

Frozen patterns (from /wait command → Rules tab) are skipped by detect_patterns.
"""

import json
import re
from datetime import datetime
from pathlib import Path

FEEDBACK_PATH = Path("config/feedback_log.json")
NOTIFICATIONS_PATH = Path("config/pending_notifications.json")

_SAAS_KEYWORDS = re.compile(
    r"\b(saas|software only|pure software|cloud platform|no hardware|b2b software|enterprise software)\b",
    re.IGNORECASE,
)
_PHYGITAL_KEYWORDS = re.compile(
    r"\b(hardware|phygital|physical|iot|sensor|device|robot|automotive|mechatronic|embedded|product)\b",
    re.IGNORECASE,
)

PATTERN_THRESHOLD = 3   # verdicts before pattern bonus kicks in (was 5)
NUDGE_STEP = 3          # pts per verdict (1-sample nudge)
PATTERN_STEP = 8        # additional pts when threshold crossed


def load_feedback(feedback_path=None):
    """Load feedback log, upgrading schema if needed."""
    path = Path(feedback_path) if feedback_path else FEEDBACK_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    data.setdefault("negative_feedback", [])
    data.setdefault("positive_signals", [])
    data.setdefault("skipped_job_ids", [])
    data.setdefault("user_verdicts", [])
    data.setdefault("weight_adjustments", [])  # legacy, kept for schema compat
    data.setdefault("pattern_counts", {})
    data.setdefault("notified_milestones", [])  # {dim}:{count} already TG-notified
    return data


def save_feedback(data, feedback_path=None):
    path = Path(feedback_path) if feedback_path else FEEDBACK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_verdict(job_url, title, system_decision, system_score,
                   user_verdict, user_reason, feedback_path=None):
    """Record a user verdict. If same URL exists, supersede the old entry."""
    data = load_feedback(feedback_path)
    now = datetime.now().isoformat()

    supersedes_id = None
    for i, v in enumerate(data["user_verdicts"]):
        if v["job_url"] == job_url and v.get("superseded_by") is None:
            supersedes_id = i
            data["user_verdicts"][i]["superseded_by"] = len(data["user_verdicts"])

    data["user_verdicts"].append({
        "job_url": job_url,
        "title": title,
        "system_decision": system_decision,
        "system_score": system_score,
        "user_verdict": user_verdict,
        "user_reason": user_reason,
        "timestamp": now,
        "supersedes": supersedes_id,
    })

    save_feedback(data, feedback_path)
    return data


def get_active_verdicts(data):
    """Return only non-superseded verdicts."""
    return [v for v in data.get("user_verdicts", []) if v.get("superseded_by") is None]


def _is_skip_signal(verdict):
    v = verdict.lower()
    return "skip" in v or "disagree-skip" in v


def _is_good_signal(verdict):
    v = verdict.lower()
    return "good" in v or "apply" in v or "disagree-fit" in v


def detect_patterns(verdicts, frozen_patterns=None):
    """Compute weight adjustments from all active verdicts.

    Returns {dimension: cumulative_delta}.

    Nudge (±NUDGE_STEP) kicks in from the first signal.
    Pattern bonus (±PATTERN_STEP) added when count >= PATTERN_THRESHOLD.
    Frozen dimensions are skipped entirely.
    """
    frozen = set(p.lower() for p in (frozen_patterns or []))
    adjustments = {}

    saas_skip_count = sum(
        1 for v in verdicts
        if _is_skip_signal(v.get("user_verdict", ""))
        and _SAAS_KEYWORDS.search(v.get("user_reason", "") + " " + v.get("title", ""))
    )
    phygital_fit_count = sum(
        1 for v in verdicts
        if _is_good_signal(v.get("user_verdict", ""))
        and _PHYGITAL_KEYWORDS.search(v.get("user_reason", "") + " " + v.get("title", ""))
    )

    # SaaS: skip signals → increase penalty (negative delta)
    if saas_skip_count > 0 and "saas" not in frozen:
        delta = -NUDGE_STEP
        if saas_skip_count >= PATTERN_THRESHOLD:
            delta -= PATTERN_STEP
        adjustments["saas_penalty_boost"] = delta

    # Phygital: good signals → increase bonus (positive delta)
    if phygital_fit_count > 0 and "phygital" not in frozen:
        delta = NUDGE_STEP
        if phygital_fit_count >= PATTERN_THRESHOLD:
            delta += PATTERN_STEP
        adjustments["phygital_bonus_boost"] = delta

    return adjustments


def _queue_notification(message):
    """Append a notification message to the pending queue for tg_bot.py to drain."""
    try:
        NOTIFICATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(NOTIFICATIONS_PATH, "r", encoding="utf-8") as f:
                queue = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            queue = []
        queue.append({"message": message, "timestamp": datetime.now().isoformat()})
        with open(NOTIFICATIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # notifications are best-effort


def apply_weight_adjustments(frozen_patterns=None, feedback_path=None):
    """Compute weight adjustments from active verdicts. Returns {dimension: delta}.

    Adjustments are computed fresh each call from all active verdicts — not
    accumulated from a stored array. Pattern threshold crossings queue a TG
    notification the first time they're detected.
    """
    data = load_feedback(feedback_path)
    active = get_active_verdicts(data)
    adjustments = detect_patterns(active, frozen_patterns)

    # Count signals to detect milestone crossings
    saas_skip_count = sum(
        1 for v in active
        if _is_skip_signal(v.get("user_verdict", ""))
        and _SAAS_KEYWORDS.search(v.get("user_reason", "") + " " + v.get("title", ""))
    )
    phygital_fit_count = sum(
        1 for v in active
        if _is_good_signal(v.get("user_verdict", ""))
        and _PHYGITAL_KEYWORDS.search(v.get("user_reason", "") + " " + v.get("title", ""))
    )

    counts = {
        "saas_penalty_boost": saas_skip_count,
        "phygital_bonus_boost": phygital_fit_count,
    }
    notified = set(data.get("notified_milestones", []))
    new_notifications = False

    for dim, count in counts.items():
        if count >= PATTERN_THRESHOLD:
            key = f"{dim}:{count}"
            if key not in notified:
                delta = adjustments.get(dim, 0)
                msg = (
                    f"Pattern detected: {count} consistent '{dim}' signals\n"
                    f"Weight adjustment: {delta:+d} pts\n"
                    f"Use /wait {dim.split('_')[0]} to freeze if wrong."
                )
                _queue_notification(msg)
                print(f"  [LEARN] {msg}")
                notified.add(key)
                new_notifications = True

    if new_notifications:
        data["notified_milestones"] = list(notified)
        # Update pattern_counts for /stats display
        data["pattern_counts"] = {
            "disagree_skip:saas": saas_skip_count,
            "disagree_fit:phygital": phygital_fit_count,
        }
        save_feedback(data, feedback_path)
    elif data.get("pattern_counts") != {
        "disagree_skip:saas": saas_skip_count,
        "disagree_fit:phygital": phygital_fit_count,
    }:
        data["pattern_counts"] = {
            "disagree_skip:saas": saas_skip_count,
            "disagree_fit:phygital": phygital_fit_count,
        }
        save_feedback(data, feedback_path)

    return adjustments


def drain_notifications():
    """Read and clear pending_notifications.json. Returns list of message strings."""
    try:
        if not NOTIFICATIONS_PATH.exists():
            return []
        with open(NOTIFICATIONS_PATH, "r", encoding="utf-8") as f:
            queue = json.load(f)
        NOTIFICATIONS_PATH.write_text("[]", encoding="utf-8")
        return [item["message"] for item in queue if "message" in item]
    except Exception:
        return []


def sync_verdicts_from_sheet(spreadsheet_id, feedback_path=None, sheet_name="Jobs"):
    """Sync My Verdict + My Reason from Google Sheet into feedback_log.json."""
    try:
        from src.sheets import get_client
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        records = worksheet.get_all_records()
    except Exception as e:
        print(f"  Feedback sync failed: {e}")
        return 0

    data = load_feedback(feedback_path)
    existing_urls = {v["job_url"] for v in data.get("user_verdicts", [])}
    new_count = 0

    for record in records:
        verdict = str(record.get("My Verdict", "")).strip()
        reason = str(record.get("My Reason", "")).strip()
        url = str(record.get("Job URL", "")).strip()

        if not verdict or not url or url in existing_urls:
            continue

        record_verdict(
            job_url=url,
            title=str(record.get("Title", "")),
            system_decision=str(record.get("Decision", "")),
            system_score=int(record.get("Score", 0) or 0),
            user_verdict=verdict,
            user_reason=reason,
            feedback_path=feedback_path,
        )
        new_count += 1

    if new_count:
        print(f"  Synced {new_count} new verdicts from Sheet")

    return new_count
