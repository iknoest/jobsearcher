"""Two-way feedback loop — sync user verdicts, detect patterns, adjust weights."""

import json
import re
from datetime import datetime
from pathlib import Path

FEEDBACK_PATH = Path("config/feedback_log.json")

_SAAS_KEYWORDS = re.compile(r"\b(saas|software only|pure software|cloud platform|no hardware)\b", re.IGNORECASE)
_PHYGITAL_KEYWORDS = re.compile(r"\b(hardware|phygital|physical|iot|sensor|device|robot|automotive|mechatronic)\b", re.IGNORECASE)

PATTERN_THRESHOLD = 5
ADJUSTMENT_STEP = 5


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
    data.setdefault("weight_adjustments", [])
    data.setdefault("pattern_counts", {})
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


def detect_patterns(verdicts):
    """Detect recurring patterns in user disagreements.
    Returns dict of weight adjustments to apply.
    """
    adjustments = {}

    saas_disagree_skip = 0
    phygital_disagree_fit = 0

    for v in verdicts:
        reason = v.get("user_reason", "")
        verdict = v.get("user_verdict", "")

        if verdict == "Disagree-Skip" and _SAAS_KEYWORDS.search(reason):
            saas_disagree_skip += 1
        elif verdict == "Disagree-Fit" and _PHYGITAL_KEYWORDS.search(reason):
            phygital_disagree_fit += 1

    if saas_disagree_skip >= PATTERN_THRESHOLD:
        adjustments["saas_penalty_boost"] = -ADJUSTMENT_STEP
    if phygital_disagree_fit >= PATTERN_THRESHOLD:
        adjustments["phygital_bonus_boost"] = ADJUSTMENT_STEP

    return adjustments


def apply_weight_adjustments(feedback_path=None):
    """Detect patterns from active verdicts and record any new weight adjustments.
    Returns dict of current cumulative adjustments.
    """
    data = load_feedback(feedback_path)
    active = get_active_verdicts(data)
    new_adjustments = detect_patterns(active)

    existing_dims = {a["dimension"] for a in data.get("weight_adjustments", [])}
    now = datetime.now().isoformat()

    for dimension, delta in new_adjustments.items():
        if dimension not in existing_dims:
            data["weight_adjustments"].append({
                "dimension": dimension,
                "delta": delta,
                "trigger": f"{PATTERN_THRESHOLD}+ user disagreements detected",
                "timestamp": now,
            })
            print(f"  [LEARN] New weight adjustment: {dimension} = {delta:+d}")

    # Update pattern counts
    data["pattern_counts"] = {}
    for v in active:
        reason = v.get("user_reason", "")
        verdict = v.get("user_verdict", "")
        if verdict == "Disagree-Skip" and _SAAS_KEYWORDS.search(reason):
            data["pattern_counts"]["disagree_skip:saas"] = data["pattern_counts"].get("disagree_skip:saas", 0) + 1
        if verdict == "Disagree-Fit" and _PHYGITAL_KEYWORDS.search(reason):
            data["pattern_counts"]["disagree_fit:phygital"] = data["pattern_counts"].get("disagree_fit:phygital", 0) + 1

    save_feedback(data, feedback_path)

    cumulative = {}
    for adj in data["weight_adjustments"]:
        dim = adj["dimension"]
        cumulative[dim] = cumulative.get(dim, 0) + adj["delta"]
    return cumulative


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
