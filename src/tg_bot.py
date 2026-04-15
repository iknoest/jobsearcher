"""Jobsearcher Telegram bot — feedback + ad-hoc scoring.

Run:
  python src/tg_bot.py

Env vars required:
  TG_JOBSEARCHER_BOT_TOKEN      — Token from @BotFather (separate from Claude Code plugin bot)
  TG_ALLOWED_USER_IDS           — Comma-separated Telegram user IDs allowed to use the bot
  GOOGLE_SHEETS_SPREADSHEET_ID  — Sheet ID (same as main pipeline)

Deep-link flows (from email buttons):
  /start good_<job_id>  → show job card, record Good Match, weekly summary
  /start skip_<job_id>  → show job card + pre-filled reason buttons from LLM analysis,
                          user taps or types own reason, then weekly summary

All verdicts overwrite previous ones (latest always wins). Timestamp written to Sheet.
"""

import html as _html
import os
import sys
import json
import asyncio
import re as _re
from datetime import datetime, timedelta


def _h(text) -> str:
    """Escape text for Telegram HTML mode. Always use this on dynamic content."""
    return _html.escape(str(text))

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode

from src.sheets import get_client, read_rules
from src.feedback import (
    record_verdict, apply_weight_adjustments, drain_notifications,
    load_feedback, get_active_verdicts, _is_skip_signal, _is_good_signal,
)

SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
BOT_TOKEN = os.getenv("TG_JOBSEARCHER_BOT_TOKEN", "")

_ALLOWED_IDS_RAW = os.getenv("TG_ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = set(
    int(x.strip()) for x in _ALLOWED_IDS_RAW.split(",") if x.strip().isdigit()
)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def _is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return update.effective_user.id in ALLOWED_USER_IDS


async def _deny(update: Update):
    msg = update.message or (update.callback_query and update.callback_query.message)
    if msg:
        await msg.reply_text("Not authorized.")


# ---------------------------------------------------------------------------
# Sheets helpers
# ---------------------------------------------------------------------------

def _get_jobs_ws():
    client = get_client()
    return client.open_by_key(SPREADSHEET_ID).worksheet("Jobs")


def _get_rules_ws():
    client = get_client()
    return client.open_by_key(SPREADSHEET_ID).worksheet("Rules")


def _parse_ws_headers(ws):
    """Return deduplicated header list (handles duplicate/empty cells)."""
    raw = ws.row_values(1)
    seen, headers = {}, []
    for i, h in enumerate(raw):
        key = h.strip() if h.strip() else f"_col{i}"
        if key in seen:
            key = f"{key}_{i}"
        seen[key] = True
        headers.append(key)
    return headers


def _find_job_row(ws, url_or_id: str):
    """Return (row_index, record_dict) or (None, None). Uses get_all_values for header safety.

    Search strategy:
    1. needle substring in "Job URL" cell (fast, exact column match)
    2. regex digit-run in any cell that looks like a URL (handles column name mismatches)
    3. needle appears anywhere in any cell of the row (last resort)
    """
    all_values = ws.get_all_values()
    if not all_values:
        return None, None
    headers = list(all_values[0])  # use headers from same response, avoid extra API call
    # Deduplicate headers in-place
    seen, clean_headers = {}, []
    for idx, h in enumerate(headers):
        key = h.strip() if h.strip() else f"_col{idx}"
        if key in seen:
            key = f"{key}_{idx}"
        seen[key] = True
        clean_headers.append(key)

    needle = url_or_id.strip().lower()
    # Which column index (0-based) is "Job URL"?
    url_col_idx = next(
        (i for i, h in enumerate(clean_headers) if h.strip().lower() in ("job url", "joburl", "url")),
        None,
    )

    for row_num, row_values in enumerate(all_values[1:], start=2):
        padded = row_values + [""] * max(0, len(clean_headers) - len(row_values))
        rec = dict(zip(clean_headers, padded))

        # Strategy 1: known URL column
        if url_col_idx is not None and url_col_idx < len(row_values):
            cell = row_values[url_col_idx].lower()
            if needle in cell:
                return row_num, rec

        # Strategy 2: any cell that looks like a URL and contains the digit run
        for cell in row_values:
            cell_l = cell.lower()
            if ("http" in cell_l or "linkedin" in cell_l) and needle in cell_l:
                return row_num, rec

        # Strategy 3: digit-run regex match against any URL-like cell
        for cell in row_values:
            if "http" in cell.lower():
                m = _re.search(r"/(\d{7,})", cell)
                if m and m.group(1) == needle:
                    return row_num, rec

    return None, None


def _write_verdict_to_sheet(ws, row_index: int, verdict: str, reason: str):
    """Write My Verdict, My Reason, Verdict At into Jobs tab. Adds Verdict At column if missing."""
    headers = _parse_ws_headers(ws)

    def _col(name, fallback):
        return (headers.index(name) + 1) if name in headers else fallback

    verdict_col = _col("My Verdict", 31)
    reason_col = _col("My Reason", 32)
    ws.update_cell(row_index, verdict_col, verdict)
    ws.update_cell(row_index, reason_col, reason)

    # Timestamp column — add header if not present
    if "Verdict At" in headers:
        ts_col = headers.index("Verdict At") + 1
    else:
        ts_col = len([h for h in headers if not h.startswith("_col")]) + 1
        ws.update_cell(1, ts_col, "Verdict At")
    ws.update_cell(row_index, ts_col, datetime.now().strftime("%Y-%m-%d %H:%M"))


def _append_rule(rule_type: str, value: str, delta: str = "", note: str = ""):
    ws = _get_rules_ws()
    ws.append_row([rule_type, value, delta, "TRUE", note], value_input_option="USER_ENTERED")


def _deactivate_rule(rule_type: str, value: str):
    ws = _get_rules_ws()
    records = ws.get_all_records()
    deactivated = 0
    for i, row in enumerate(records, start=2):
        if (str(row.get("Type", "")).strip().lower() == rule_type.lower()
                and str(row.get("Value", "")).strip().lower() == value.lower()
                and str(row.get("Active", "")).strip().upper() == "TRUE"):
            ws.update_cell(i, 4, "FALSE")
            deactivated += 1
    return deactivated


# ---------------------------------------------------------------------------
# Weekly stats helpers
# ---------------------------------------------------------------------------

def _weekly_stats():
    """Return (not_fit_count, good_count) for the past 7 days from feedback_log."""
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    data = load_feedback()
    active = get_active_verdicts(data)
    recent = [v for v in active if v.get("timestamp", "") >= cutoff]
    not_fit = sum(1 for v in recent if _is_skip_signal(v.get("user_verdict", "")))
    good = sum(1 for v in recent if _is_good_signal(v.get("user_verdict", "")))
    return not_fit, good


def _weekly_summary_line():
    not_fit, good = _weekly_stats()
    parts = []
    if good:
        parts.append(f"{good} liked")
    if not_fit:
        parts.append(f"{not_fit} skipped")
    if not parts:
        return "No feedback this week yet. Use /goodmatches or /myskips to review."
    return f"Your week: {' · '.join(parts)}. Use /goodmatches to review picks."


# ---------------------------------------------------------------------------
# Skip reason builder — multi-choice categories + LLM-specific gaps
# ---------------------------------------------------------------------------

_JUNK_VALUES = {"false", "true", "none", "nan", "", "-", "n/a", "no gaps", "no gap"}

# Fixed categories always available in the skip flow (in order)
_SKIP_CATEGORIES = [
    "Wrong industry / sector",
    "Missing hardware · phygital domain",
    "Role scope or seniority doesn't fit",
    "Company culture / size doesn't fit",
    "Location or commute concern",
]


def _is_meaningful(s: str) -> bool:
    return s.lower() not in _JUNK_VALUES and len(s) > 4


def _build_skip_options(rec) -> list[str]:
    """Return ordered list of skip reason options shown as toggle buttons.

    Order:
    1. LLM-specific gaps from Gap Analysis (max 2) — most concrete
    2. Main Risk from LLM (if meaningful)
    3. "Don't like <industry>" if industry is known
    4. Fixed generic categories (always present)
    """
    options = []

    gap_analysis = str(rec.get("Gap Analysis", "")).strip()
    for item in gap_analysis.split(";"):
        item = item.strip()
        if _is_meaningful(item) and len(options) < 2:
            options.append(item[:50])

    main_risk = str(rec.get("Main Risk", "")).strip()
    if _is_meaningful(main_risk) and main_risk not in options:
        options.append(main_risk[:50])

    industry = str(rec.get("Industry", "")).strip()
    if _is_meaningful(industry):
        options.append(f"Don't like {industry} industry")

    # Always append fixed categories (skip any that duplicate LLM content)
    for cat in _SKIP_CATEGORIES:
        options.append(cat)

    return options


def _build_skip_keyboard(options: list[str], selected: set) -> InlineKeyboardMarkup:
    """Build toggle keyboard. Selected items show ✅ prefix. Submit at bottom."""
    keyboard = []
    for i, opt in enumerate(options):
        label = ("✅ " if i in selected else "") + (opt if len(opt) <= 38 else opt[:35] + "…")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"sr:T:{i}")])
    # Bottom row
    bottom = [InlineKeyboardButton("✍️ Add note", callback_data="sr:note")]
    if selected:
        bottom.append(InlineKeyboardButton("Submit →", callback_data="sr:submit"))
    keyboard.append(bottom)
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Deep-link handlers  (called from cmd_start)
# ---------------------------------------------------------------------------

async def _handle_good_deeplink(update: Update, job_id: str):
    if not SPREADSHEET_ID:
        await update.message.reply_text("Sheets not configured.")
        return
    try:
        ws = _get_jobs_ws()
        row_index, rec = _find_job_row(ws, job_id)
        if row_index is None:
            rec = {}
            ws = None  # skip sheet write

        prev_verdict = str(rec.get("My Verdict", "")).strip()
        title = str(rec.get("Title", f"Job {job_id}"))
        company = str(rec.get("Company", ""))
        score = str(rec.get("Score", "?"))

        if ws is not None and row_index is not None:
            _write_verdict_to_sheet(ws, row_index, "Good Match", "Tapped Good match in email")
        record_verdict(
            job_url=str(rec.get("Job URL", job_id)),
            title=title,
            system_decision=str(rec.get("Decision", "")),
            system_score=int(rec.get("Score", 0) or 0),
            user_verdict="Good Match",
            user_reason="Tapped Good match in email",
        )

        overwrite = f"\n<i>Overwrote: {_h(prev_verdict)}</i>" if prev_verdict and prev_verdict != "Good Match" else ""
        await update.message.reply_text(
            f"✅ <b>Good Match recorded</b>\n"
            f"{_h(title)}\n"
            f"{_h(company)}  ·  Score {_h(score)}"
            f"{overwrite}\n\n"
            f"{_weekly_summary_line()}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _handle_skip_deeplink(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str):
    if not SPREADSHEET_ID:
        await update.message.reply_text("Sheets not configured.")
        return
    try:
        ws = _get_jobs_ws()
        row_index, rec = _find_job_row(ws, job_id)
        if row_index is None:
            # Job not in sheet (may be from an older digest or dedup-skipped).
            # Still let user record feedback in feedback_log.
            rec = {}
            ws = None

        title = str(rec.get("Title", f"Job {job_id}"))
        company = str(rec.get("Company", "Unknown company"))
        score = str(rec.get("Score", "?"))
        decision = str(rec.get("Decision", "?"))
        prev_verdict = str(rec.get("My Verdict", "")).strip()

        options = _build_skip_options(rec)

        # Store state in user_data
        context.user_data["pending_skip"] = {
            "job_id": job_id,
            "row_index": row_index,  # may be None if not found in sheet
            "rec": rec,
            "options": options,
            "selected": set(),   # indices of toggled options
            "extra_note": "",    # typed note appended to reasons
        }
        context.user_data["awaiting_skip_reason"] = False

        prev_note = f"\n<i>Previous: {_h(prev_verdict)}</i>" if prev_verdict else ""
        await update.message.reply_text(
            f"<b>Skip — {_h(title)}</b>\n"
            f"{_h(company)}  ·  Score {_h(score)}  ({_h(decision)})"
            f"{prev_note}\n\n"
            f"Select all reasons that apply, then tap Submit ↓",
            parse_mode=ParseMode.HTML,
            reply_markup=_build_skip_keyboard(options, set()),
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def handle_skip_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle tap on a suggested reason button or 'Type my own'."""
    query = update.callback_query
    await query.answer()

    if not _is_allowed(update):
        await query.edit_message_text("Not authorized.")
        return

    pending = context.user_data.get("pending_skip")
    if not pending:
        await query.edit_message_text(
            "Session expired — please tap the Skip button in the email again."
        )
        return

    data = query.data  # "sr:T:<idx>", "sr:note", "sr:submit"

    options = pending.get("options", [])
    selected: set = pending.get("selected", set())

    if data.startswith("sr:T:"):
        # Toggle a reason on/off
        try:
            idx = int(data.split(":")[2])
        except (ValueError, IndexError):
            idx = -1
        if idx >= 0:
            if idx in selected:
                selected.discard(idx)
            else:
                selected.add(idx)
            pending["selected"] = selected
        await query.edit_message_reply_markup(
            reply_markup=_build_skip_keyboard(options, selected)
        )
        return

    if data == "sr:note":
        context.user_data["awaiting_skip_reason"] = True
        await query.answer()  # dismiss spinner
        await query.message.reply_text(
            "✍️ Type your note and send it — I'll add it to your skip reasons:"
        )
        return

    if data == "sr:submit":
        real_selected = {i for i in selected if i >= 0}
        if not real_selected and not pending.get("extra_note"):
            await query.answer("Select at least one reason first.", show_alert=True)
            return
        parts = [options[i] for i in sorted(real_selected) if i < len(options)]
        if pending.get("extra_note"):
            parts.append(pending["extra_note"])
        reason = "; ".join(parts) if parts else "Skipped from email"
        await _commit_skip(query=query, context=context, pending=pending, reason=reason)
        return

    # Fallback — unknown action
    await query.answer()


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle typed note during skip flow. Adds note to selection, re-shows keyboard."""
    if not _is_allowed(update):
        return
    if not context.user_data.get("awaiting_skip_reason"):
        return  # not in a skip flow

    pending = context.user_data.get("pending_skip")
    if not pending:
        return

    context.user_data["awaiting_skip_reason"] = False
    pending["extra_note"] = update.message.text.strip()
    pending["selected"].add(-1)  # sentinel so Submit becomes visible

    options = pending.get("options", [])
    selected = pending.get("selected", set())
    rec = pending["rec"]
    title = str(rec.get("Title", pending["job_id"]))

    await update.message.reply_text(
        f"Note added ✓  Tap <b>Submit →</b> to record your skip for <b>{_h(title)}</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=_build_skip_keyboard(options, selected),
    )


async def _commit_skip(context, pending, reason, query=None, message=None):
    """Write skip verdict to Sheet + feedback_log, reply with confirmation."""
    rec = pending["rec"]
    row_index = pending.get("row_index")  # None if job wasn't found in sheet
    job_id = pending["job_id"]
    title = str(rec.get("Title", f"Job {job_id}"))
    company = str(rec.get("Company", ""))

    try:
        if row_index is not None:
            ws = _get_jobs_ws()
            _write_verdict_to_sheet(ws, row_index, "Disagree-Skip", reason)
        record_verdict(
            job_url=str(rec.get("Job URL", job_id)),
            title=title,
            system_decision=str(rec.get("Decision", "")),
            system_score=int(rec.get("Score", 0) or 0),
            user_verdict="Disagree-Skip",
            user_reason=reason,
        )
    except Exception as e:
        text = f"Error saving verdict: {e}"
        if query:
            await query.edit_message_text(text)
        elif message:
            await message.reply_text(text)
        return

    context.user_data.pop("pending_skip", None)

    text = (
        f"⏭ <b>Skipped</b> — {_h(title)}\n"
        f"{_h(company)}\n"
        f"<i>Reason: {_h(reason)}</i>\n\n"
        f"{_weekly_summary_line()}"
    )
    if query:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
    elif message:
        await message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /good  /skip  /uncertain  — manual feedback commands (text-based)
# ---------------------------------------------------------------------------

async def _write_feedback(update: Update, url_or_id: str, verdict: str, reason: str):
    if not SPREADSHEET_ID:
        await update.message.reply_text("GOOGLE_SHEETS_SPREADSHEET_ID not configured.")
        return
    try:
        ws = _get_jobs_ws()
        row_index, rec = _find_job_row(ws, url_or_id)
        if row_index is None:
            await update.message.reply_text(
                f"Job not found: {_h(url_or_id)}\nUse the numeric job ID from the LinkedIn URL."
            )
            return

        prev_verdict = str(rec.get("My Verdict", "")).strip()
        _write_verdict_to_sheet(ws, row_index, verdict, reason)
        record_verdict(
            job_url=str(rec.get("Job URL", url_or_id)),
            title=str(rec.get("Title", "?")),
            system_decision=str(rec.get("Decision", "")),
            system_score=int(rec.get("Score", 0) or 0),
            user_verdict=verdict,
            user_reason=reason,
        )

        title = rec.get("Title", "?")
        company = rec.get("Company", "?")
        score = rec.get("Score", "?")
        overwrite = f"\n<i>Overwrote: {_h(prev_verdict)}</i>" if prev_verdict and prev_verdict != verdict else ""
        await update.message.reply_text(
            f"<b>{_h(verdict)}</b> recorded\n"
            f"{_h(title)}  ·  {_h(company)}  (score {_h(score)})"
            f"{overwrite}\n"
            f"Reason: {_h(reason) if reason else '—'}\n\n"
            f"{_weekly_summary_line()}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_good(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /good <url_or_job_id> [note]")
        return
    url_or_id = context.args[0]
    note = " ".join(context.args[1:]) if len(context.args) > 1 else "Marked good via /good"
    await _write_feedback(update, url_or_id, verdict="Good Match", reason=note)


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /skip <url_or_job_id> <reason>")
        return
    url_or_id = context.args[0]
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "User skipped"
    await _write_feedback(update, url_or_id, verdict="Disagree-Skip", reason=reason)


async def cmd_uncertain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /uncertain <url_or_job_id>")
        return
    await _write_feedback(update, context.args[0], verdict="Uncertain", reason="Needs manual review")


# ---------------------------------------------------------------------------
# /score — ad-hoc job scoring
# ---------------------------------------------------------------------------

async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /score <url_or_jd_text>")
        return

    arg = " ".join(context.args)
    is_url = arg.startswith("http://") or arg.startswith("https://")
    msg = await update.message.reply_text("Scoring... this takes ~30s, hold tight.")

    try:
        if is_url:
            import trafilatura
            downloaded = trafilatura.fetch_url(arg.strip())
            description = trafilatura.extract(downloaded) or "" if downloaded else ""
            if not description:
                await msg.edit_text("Could not fetch description from URL. Try pasting the JD text directly.")
                return
            url = arg.strip()
        else:
            description = arg
            url = "adhoc://manual"

        job_dict = {
            "title": "Ad-hoc job", "company": "Unknown", "location": "Unknown",
            "description": description, "job_url": url, "work_mode": "Unknown",
            "phygital_detected": False, "pure_saas_detected": False,
            "driver_license_flagged": False, "dutch_mandatory": False,
            "dutch_nice_to_have": False, "seniority_fit": "Medium",
            "is_agency": False, "km_visa_mentioned": False,
        }

        from src.filters import enrich_and_filter, language_prefilter
        import pandas as pd
        df = pd.DataFrame([job_dict])
        df, lang_rejections = language_prefilter(df)
        if df.empty:
            reason = lang_rejections[0].get("SkipReason", "Dutch mandatory") if lang_rejections else "Language filter"
            await msg.edit_text(f"Rejected at language pre-filter: {_h(reason)}")
            return
        df, filter_log = enrich_and_filter(df)
        if df.empty:
            reason = filter_log[0].get("reason", "Filter") if filter_log else "Pre-filter"
            await msg.edit_text(f"Rejected at pre-filter: {_h(reason)}")
            return

        from src.matcher import score_job
        result = await asyncio.get_event_loop().run_in_executor(None, score_job, df.iloc[0].to_dict())

        card = result.get("CardSummary", {})
        fit = result.get("WhyFit", {})
        trust = result.get("TrustNotes", {})
        score = card.get("MatchScore", 0)
        decision = card.get("DecisionHint", "?")
        lines = [
            f"<b>{_h(decision)}</b>  {score}/100  (Confidence: {_h(trust.get('Confidence', '?'))})",
        ]
        if card.get("TopLabel"):
            lines.append(f"<i>{_h(card['TopLabel'])}</i>")
        if card.get("RoleSummary"):
            lines.append(f"\n<i>{_h(card['RoleSummary'])}</i>")
        if card.get("BlockingAlert"):
            lines.append(f"\n⚠ {_h(card['BlockingAlert'])}")
        if card.get("MainRisk"):
            lines.append(f"Risk: {_h(card['MainRisk'])}")
        strong = fit.get("StrongMatch", [])
        if strong:
            lines.append("\n<b>Strong Match</b>")
            lines += [f"• {_h(s)}" for s in strong]
        gaps = result.get("Gaps", [])
        if gaps:
            lines.append("\n<b>Gaps</b>")
            lines += [f"• {_h(g)}" for g in gaps]
        await msg.edit_text("\n".join(l for l in lines if l), parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"Scoring failed: {e}")


# ---------------------------------------------------------------------------
# Rules commands
# ---------------------------------------------------------------------------

async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /block <keyword>")
        return
    keyword = " ".join(context.args)
    try:
        _append_rule("keyword_block", keyword, note=f"Added via TG by {update.effective_user.username}")
        await update.message.reply_text(f"Blocked: {_h(keyword)}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_addkw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /addkw <keyword>")
        return
    keyword = " ".join(context.args)
    try:
        _append_rule("keyword_add", keyword, note=f"Added via TG by {update.effective_user.username}")
        await update.message.reply_text(f"Added keyword: {_h(keyword)}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_rmkw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /rmkw <keyword>")
        return
    keyword = " ".join(context.args)
    try:
        n = _deactivate_rule("keyword_add", keyword)
        msg = f"Deactivated: {_h(keyword)}" if n else f"No active keyword found: {_h(keyword)}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /wait <pattern>")
        return
    pattern = " ".join(context.args)
    try:
        _append_rule("wait", pattern, note="Learning freeze via TG")
        await update.message.reply_text(
            f"Learning frozen: {_h(pattern)}\nUse /ok {_h(pattern)} to unfreeze."
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /ok <pattern>")
        return
    pattern = " ".join(context.args)
    try:
        n = _deactivate_rule("wait", pattern)
        msg = f"Unfrozen: {_h(pattern)}" if n else f"No active wait found: {_h(pattern)}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


# ---------------------------------------------------------------------------
# /rules  /stats
# ---------------------------------------------------------------------------

async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not SPREADSHEET_ID:
        await update.message.reply_text("Sheets not configured.")
        return
    try:
        rules = read_rules(SPREADSHEET_ID)
        lines = ["<b>Active Rules</b>\n"]
        if rules["weight_adjustments"]:
            lines.append("Weight adjustments:")
            for dim, delta in rules["weight_adjustments"].items():
                lines.append(f"  {_h(dim)}: {delta:+d}")
        if rules["keyword_blocks"]:
            lines.append("\nKeyword blocks:")
            lines += [f"  ✗ {_h(kw)}" for kw in rules["keyword_blocks"]]
        if rules["keyword_adds"]:
            lines.append("\nSearch keywords:")
            lines += [f"  + {_h(kw)}" for kw in rules["keyword_adds"]]
        if rules["company_boosts"]:
            lines.append("\nCompany boosts:")
            lines += [f"  {_h(co)}: {d:+d}" for co, d in rules["company_boosts"].items()]
        if rules["company_demotes"]:
            lines.append("\nCompany demotes:")
            lines += [f"  {_h(co)}: {d:+d}" for co, d in rules["company_demotes"].items()]
        if rules["frozen_patterns"]:
            lines.append("\nFrozen (learning paused):")
            lines += [f"  ⏸ {_h(p)}" for p in rules["frozen_patterns"]]
        if len(lines) == 1:
            lines.append("No active rules.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    try:
        # --- Sheet stats ---
        sheet_lines = []
        if SPREADSHEET_ID:
            ws = _get_jobs_ws()
            all_values = ws.get_all_values()
            if len(all_values) >= 2:
                headers = list(all_values[0])
                seen_h, clean_h = {}, []
                for idx, h in enumerate(headers):
                    key = h.strip() if h.strip() else f"_col{idx}"
                    if key in seen_h:
                        key = f"{key}_{idx}"
                    seen_h[key] = True
                    clean_h.append(key)
                records = [dict(zip(clean_h, r + [""] * max(0, len(clean_h) - len(r)))) for r in all_values[1:]]

                from collections import Counter
                decisions = Counter(str(r.get("Decision", "")).strip() for r in records)
                cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                recent = [r for r in records if str(r.get("Date", "")) >= cutoff]
                recent_dec = Counter(str(r.get("Decision", "")).strip() for r in recent)

                total = len(records)
                r_apply = recent_dec.get("Apply", 0)
                r_maybe = recent_dec.get("Maybe", 0)
                r_skip  = recent_dec.get("Skip", 0)
                r_total = len(recent)

                sheet_lines = [
                    f"<b>Pipeline — last 7 days</b>  ({r_total} scanned)",
                    f"  Apply {r_apply}  ·  Maybe {r_maybe}  ·  Skip {r_skip}",
                    f"All time: {total} jobs  (Apply {decisions.get('Apply',0)} / Maybe {decisions.get('Maybe',0)} / Skip {decisions.get('Skip',0)})",
                ]

        # --- Feedback stats ---
        not_fit, good = _weekly_stats()
        data = load_feedback()
        active = get_active_verdicts(data)
        all_good  = sum(1 for v in active if _is_good_signal(v.get("user_verdict", "")))
        all_skips = sum(1 for v in active if _is_skip_signal(v.get("user_verdict", "")))

        fb_lines = [
            f"\n<b>Your feedback — this week</b>",
            f"  Liked: {good}  ·  Skipped: {not_fit}",
            f"All time: {all_good} liked  ·  {all_skips} skipped",
            f"\n/goodmatches — review jobs you liked",
            f"/myskips — review jobs you skipped",
        ]

        adj = apply_weight_adjustments()
        adj_lines = []
        if adj:
            adj_lines = ["\n<b>Learned adjustments</b>"]
            for dim, delta in adj.items():
                adj_lines.append(f"  {_h(dim)}: {delta:+d}")

        await update.message.reply_text(
            "\n".join(sheet_lines + fb_lines + adj_lines),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_goodmatches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List jobs the user marked as Good Match — stored for later application."""
    if not _is_allowed(update):
        return await _deny(update)
    data = load_feedback()
    active = get_active_verdicts(data)
    good = [v for v in active if _is_good_signal(v.get("user_verdict", ""))]
    if not good:
        await update.message.reply_text("No Good Match verdicts recorded yet.")
        return

    lines = [f"<b>Good Matches — {len(good)} jobs to apply</b>\n"]
    for v in reversed(good):  # most recent first
        ts = v.get("timestamp", "")[:10]
        title = _h(v.get("title", "?"))
        score = v.get("system_score", "?")
        url = v.get("job_url", "")
        lines.append(f"• <b>{title}</b>  (Score {score})  {ts}\n  {url}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True)


async def cmd_myskips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List jobs the user skipped — with reasons."""
    if not _is_allowed(update):
        return await _deny(update)
    data = load_feedback()
    active = get_active_verdicts(data)
    skipped = [v for v in active if _is_skip_signal(v.get("user_verdict", ""))]
    if not skipped:
        await update.message.reply_text("No skipped jobs recorded yet.")
        return

    lines = [f"<b>Skipped Jobs — {len(skipped)} total</b>\n"]
    for v in reversed(skipped[-15:]):  # last 15, most recent first
        ts = v.get("timestamp", "")[:10]
        title = _h(v.get("title", "?"))
        reason = _h(v.get("user_reason", "—"))
        lines.append(f"• <b>{title}</b>  {ts}\n  <i>{reason}</i>")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /start  /help
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "<b>Jobsearcher Bot</b>\n\n"
    "<b>From email</b>\n"
    "Tap Good match or Skip → guided flow with suggested reasons\n\n"
    "<b>Review your picks</b>\n"
    "/goodmatches — jobs you liked (apply later)\n"
    "/myskips — jobs you skipped + reasons\n"
    "/stats — pipeline stats + learned adjustments\n\n"
    "<b>Manual feedback</b>\n"
    "/good [id] [note] — mark good match\n"
    "/skip [id] [reason] — mark skip\n"
    "/uncertain [id] — flag for review\n\n"
    "<b>Ad-hoc scoring</b>\n"
    "/score [url or job description] — score any job now\n\n"
    "<b>Rules</b>\n"
    "/block [kw] — hard-reject jobs with this keyword\n"
    "/addkw [kw] — add search keyword\n"
    "/rmkw [kw] — remove search keyword\n"
    "/wait [pattern] — freeze learning for pattern\n"
    "/ok [pattern] — unfreeze\n"
    "/rules — show active rules"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)

    if context.args:
        param = context.args[0]
        if param.startswith("good_"):
            await _handle_good_deeplink(update, param[5:])
            return
        if param.startswith("skip_"):
            await _handle_skip_deeplink(update, context, param[5:])
            return

    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _send_pending_notifications(app):
    messages = drain_notifications()
    if not messages or not ALLOWED_USER_IDS:
        return
    for uid in ALLOWED_USER_IDS:
        for msg in messages:
            try:
                await app.bot.send_message(chat_id=uid, text=f"Pattern detected:\n{_h(msg)}", parse_mode=ParseMode.HTML)
            except Exception as e:
                print(f"  [warn] Could not notify {uid}: {e}")


def main():
    if not BOT_TOKEN:
        print("ERROR: TG_JOBSEARCHER_BOT_TOKEN is not set in .env")
        sys.exit(1)
    if not SPREADSHEET_ID:
        print("WARNING: GOOGLE_SHEETS_SPREADSHEET_ID not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.post_init = _send_pending_notifications

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("good", cmd_good))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("uncertain", cmd_uncertain))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CommandHandler("block", cmd_block))
    app.add_handler(CommandHandler("addkw", cmd_addkw))
    app.add_handler(CommandHandler("rmkw", cmd_rmkw))
    app.add_handler(CommandHandler("wait", cmd_wait))
    app.add_handler(CommandHandler("ok", cmd_ok))
    app.add_handler(CommandHandler("rules", cmd_rules))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("goodmatches", cmd_goodmatches))
    app.add_handler(CommandHandler("myskips", cmd_myskips))

    # Inline keyboard handler for skip reason buttons
    app.add_handler(CallbackQueryHandler(handle_skip_reason_callback, pattern=r"^sr:"))

    # Text message handler — only active when awaiting a typed skip reason
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    print(f"Jobsearcher bot running (allowed users: {ALLOWED_USER_IDS or 'all'})")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
