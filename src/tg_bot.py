"""Jobsearcher Telegram bot — feedback + ad-hoc scoring.

Run:
  python src/tg_bot.py

Env vars required:
  TG_JOBSEARCHER_BOT_TOKEN      — Token from @BotFather (separate from Claude Code plugin bot)
  TG_ALLOWED_USER_IDS           — Comma-separated Telegram user IDs allowed to use the bot
  GOOGLE_SHEETS_SPREADSHEET_ID  — Sheet ID (same as main pipeline)

Commands:
  /good <url_or_job_id> [note]   — Mark job as good match (writes Verdict to Sheet)
  /skip <url_or_job_id> <reason> — Mark job as skip (writes Verdict to Sheet)
  /uncertain <url_or_job_id>     — Flag for manual review (needs more info)
  /score <url_or_jd_text>        — Score an ad-hoc job and return the decision card
  /block <keyword>               — Add keyword_block rule to Rules tab
  /addkw <keyword>               — Add search keyword (keyword_add rule)
  /rmkw <keyword>                — Deactivate a keyword_add rule
  /wait <pattern>                — Freeze learning for a pattern
  /ok <pattern>                  — Unfreeze learning for a pattern
  /rules                         — List all active rules in the Rules tab
  /stats                         — Recent pipeline stats from Jobs tab
"""

import os
import sys
import json
import asyncio
import re as _re

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

from src.sheets import get_client, read_rules
from src.feedback import record_verdict, apply_weight_adjustments, drain_notifications

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
        return True  # open if not configured
    return update.effective_user.id in ALLOWED_USER_IDS


async def _deny(update: Update):
    await update.message.reply_text("Not authorized.")


# ---------------------------------------------------------------------------
# Sheets helpers
# ---------------------------------------------------------------------------

def _get_jobs_ws():
    client = get_client()
    return client.open_by_key(SPREADSHEET_ID).worksheet("Jobs")


def _get_rules_ws():
    client = get_client()
    return client.open_by_key(SPREADSHEET_ID).worksheet("Rules")


def _find_job_row(ws, url_or_id: str):
    """Return (row_index, record_dict) for the first Jobs row matching url or job_id.
    row_index is 1-based (gspread convention). Returns (None, None) if not found.
    """
    records = ws.get_all_records()
    needle = url_or_id.strip().lower()
    for i, rec in enumerate(records, start=2):  # row 1 = header
        job_url = str(rec.get("Job URL", "")).lower()
        if needle in job_url:
            return i, rec
        # Also match numeric job ID in URL
        m = _re.search(r"/(\d{7,})", job_url)
        if m and m.group(1) == needle:
            return i, rec
    return None, None


def _write_verdict_to_sheet(ws, row_index: int, verdict: str, reason: str):
    """Write My Verdict + My Reason into Jobs tab. Columns 31 and 32."""
    headers = ws.row_values(1)
    verdict_col = headers.index("My Verdict") + 1 if "My Verdict" in headers else 31
    reason_col = headers.index("My Reason") + 1 if "My Reason" in headers else 32
    ws.update_cell(row_index, verdict_col, verdict)
    ws.update_cell(row_index, reason_col, reason)


def _append_rule(rule_type: str, value: str, delta: str = "", note: str = ""):
    """Append one active rule row to the Rules tab."""
    ws = _get_rules_ws()
    ws.append_row([rule_type, value, delta, "TRUE", note], value_input_option="USER_ENTERED")


def _deactivate_rule(rule_type: str, value: str):
    """Set Active=FALSE for all Rules rows matching type+value."""
    ws = _get_rules_ws()
    records = ws.get_all_records()
    deactivated = 0
    for i, row in enumerate(records, start=2):
        if (str(row.get("Type", "")).strip().lower() == rule_type.lower()
                and str(row.get("Value", "")).strip().lower() == value.lower()
                and str(row.get("Active", "")).strip().upper() == "TRUE"):
            ws.update_cell(i, 4, "FALSE")  # Active column
            deactivated += 1
    return deactivated


# ---------------------------------------------------------------------------
# /good  /skip  /uncertain  — feedback commands
# ---------------------------------------------------------------------------

async def cmd_good(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /good <url_or_job_id> [note]")
        return

    url_or_id = args[0]
    note = " ".join(args[1:]) if len(args) > 1 else ""
    await _write_feedback(update, url_or_id, verdict="Good Match", reason=note or "User marked good")


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /skip <url_or_job_id> <reason>")
        return

    url_or_id = args[0]
    reason = " ".join(args[1:]) if len(args) > 1 else "User skipped"
    await _write_feedback(update, url_or_id, verdict="Disagree-Skip", reason=reason)


async def cmd_uncertain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /uncertain <url_or_job_id>")
        return

    url_or_id = args[0]
    await _write_feedback(update, url_or_id, verdict="Uncertain", reason="Needs manual review")


async def _write_feedback(update: Update, url_or_id: str, verdict: str, reason: str):
    if not SPREADSHEET_ID:
        await update.message.reply_text("GOOGLE_SHEETS_SPREADSHEET_ID not configured.")
        return
    try:
        ws = _get_jobs_ws()
        row_index, rec = _find_job_row(ws, url_or_id)
        if row_index is None:
            await update.message.reply_text(
                f"Job not found in sheet: `{url_or_id}`\n"
                "Tip: use the numeric job ID from the LinkedIn URL.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        _write_verdict_to_sheet(ws, row_index, verdict, reason)

        # Also persist locally so pattern detection runs without waiting for next sync
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
        await update.message.reply_text(
            f"Recorded *{verdict}*\n{title} @ {company} (score {score})\nReason: {reason or '—'}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"Error writing verdict: {e}")


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

    msg = await update.message.reply_text(
        "Scoring... this takes ~30s, hold tight."
    )

    try:
        description = ""
        url = ""
        title = "Ad-hoc job"
        company = "Unknown"

        if is_url:
            url = arg.strip()
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                description = trafilatura.extract(downloaded) or ""
            if not description:
                await msg.edit_text("Could not fetch description from URL. Try pasting the JD text directly.")
                return
        else:
            description = arg
            url = "adhoc://manual"

        # Build a minimal job dict
        job_dict = {
            "title": title,
            "company": company,
            "location": "Unknown",
            "description": description,
            "job_url": url,
            "work_mode": "Unknown",
            "phygital_detected": False,
            "pure_saas_detected": False,
            "driver_license_flagged": False,
            "dutch_mandatory": False,
            "dutch_nice_to_have": False,
            "seniority_fit": "Medium",
            "is_agency": False,
            "km_visa_mentioned": False,
        }

        # Run pre-filters first (to surface filter reasons)
        from src.filters import enrich_and_filter, language_prefilter
        import pandas as pd
        df = pd.DataFrame([job_dict])
        df, lang_rejections = language_prefilter(df)
        if df.empty:
            reason = lang_rejections[0].get("SkipReason", "Dutch mandatory") if lang_rejections else "Language filter"
            await msg.edit_text(f"Rejected at language pre-filter: *{reason}*", parse_mode=ParseMode.MARKDOWN)
            return
        df, filter_log = enrich_and_filter(df)
        if df.empty:
            reason = filter_log[0].get("reason", "Filter") if filter_log else "Pre-filter"
            await msg.edit_text(f"Rejected at pre-filter: *{reason}*", parse_mode=ParseMode.MARKDOWN)
            return

        job_row = df.iloc[0].to_dict()

        # LLM score
        from src.matcher import score_job
        result = await asyncio.get_event_loop().run_in_executor(None, score_job, job_row)

        card = result.get("CardSummary", {})
        fit = result.get("WhyFit", {})
        gaps = result.get("Gaps", [])
        trust = result.get("TrustNotes", {})

        score = card.get("MatchScore", 0)
        decision = card.get("DecisionHint", "?")
        top_label = card.get("TopLabel", "")
        main_risk = card.get("MainRisk", "")
        blocking = card.get("BlockingAlert", "")
        role_summary = card.get("RoleSummary", "")
        confidence = trust.get("Confidence", "?")

        strong = fit.get("StrongMatch", [])
        partial = fit.get("PartialMatch", [])

        lines = [
            f"*{decision}* — Score: {score}/100 (Confidence: {confidence})",
            f"_{top_label}_" if top_label else "",
        ]
        if role_summary:
            lines.append(f"\n_{role_summary}_")
        if blocking:
            lines.append(f"\n⚠ {blocking}")
        if main_risk:
            lines.append(f"Risk: {main_risk}")
        if strong:
            lines.append("\n*Strong Match:*")
            lines += [f"• {s}" for s in strong]
        if partial:
            lines.append("\n*Partial Match:*")
            lines += [f"• {p}" for p in partial]
        if gaps:
            lines.append("\n*Gaps:*")
            lines += [f"• {g}" for g in gaps]

        # Filter verdict
        filter_flags = []
        if job_row.get("phygital_detected"):
            filter_flags.append("Phygital ✓")
        if job_row.get("pure_saas_detected"):
            filter_flags.append("SaaS ⚠")
        if filter_flags:
            lines.append(f"\nFlags: {', '.join(filter_flags)}")

        text = "\n".join(l for l in lines if l)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

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
        _append_rule("keyword_block", keyword, note=f"Added via Telegram by {update.effective_user.username}")
        await update.message.reply_text(f"Blocked keyword: *{keyword}*\nJobs mentioning this will be hard-rejected.", parse_mode=ParseMode.MARKDOWN)
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
        _append_rule("keyword_add", keyword, note=f"Added via Telegram by {update.effective_user.username}")
        await update.message.reply_text(
            f"Added search keyword: *{keyword}*\nWill be included in the next pipeline run.",
            parse_mode=ParseMode.MARKDOWN,
        )
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
        if n:
            await update.message.reply_text(f"Deactivated keyword: *{keyword}*", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"No active keyword_add rule found for: {keyword}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /wait <pattern>  — freeze learning for this pattern")
        return
    pattern = " ".join(context.args)
    try:
        _append_rule("wait", pattern, note=f"Learning freeze via Telegram")
        await update.message.reply_text(
            f"Learning frozen for: *{pattern}*\nUse /ok {pattern} to unfreeze.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /ok <pattern>  — unfreeze learning")
        return
    pattern = " ".join(context.args)
    try:
        n = _deactivate_rule("wait", pattern)
        if n:
            await update.message.reply_text(f"Learning unfrozen for: *{pattern}*", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"No active wait rule found for: {pattern}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


# ---------------------------------------------------------------------------
# /rules  /stats
# ---------------------------------------------------------------------------

async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not SPREADSHEET_ID:
        await update.message.reply_text("GOOGLE_SHEETS_SPREADSHEET_ID not configured.")
        return
    try:
        rules = read_rules(SPREADSHEET_ID)
        lines = ["*Active Rules:*\n"]
        if rules["weight_adjustments"]:
            lines.append("Weight adjustments:")
            for dim, delta in rules["weight_adjustments"].items():
                lines.append(f"  {dim}: {delta:+d}")
        if rules["keyword_blocks"]:
            lines.append("\nKeyword blocks:")
            for kw in rules["keyword_blocks"]:
                lines.append(f"  ✗ {kw}")
        if rules["keyword_adds"]:
            lines.append("\nSearch keywords added:")
            for kw in rules["keyword_adds"]:
                lines.append(f"  + {kw}")
        if rules["company_boosts"]:
            lines.append("\nCompany boosts:")
            for co, delta in rules["company_boosts"].items():
                lines.append(f"  {co}: {delta:+d}")
        if rules["company_demotes"]:
            lines.append("\nCompany demotes:")
            for co, delta in rules["company_demotes"].items():
                lines.append(f"  {co}: {delta:+d}")
        if len(lines) == 1:
            lines.append("No active rules.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Error reading rules: {e}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    if not SPREADSHEET_ID:
        await update.message.reply_text("GOOGLE_SHEETS_SPREADSHEET_ID not configured.")
        return
    try:
        ws = _get_jobs_ws()
        records = ws.get_all_records()
        if not records:
            await update.message.reply_text("No jobs in sheet yet.")
            return

        from collections import Counter
        decisions = Counter(str(r.get("Decision", "")).strip() for r in records)
        verdicts = Counter(str(r.get("My Verdict", "")).strip() for r in records if r.get("My Verdict"))

        # Last 7 days
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent = [r for r in records if str(r.get("Date", "")) >= cutoff]
        recent_decisions = Counter(str(r.get("Decision", "")) for r in recent)

        lines = [
            f"*Stats — {len(records)} total jobs*\n",
            "All time:",
            f"  Apply: {decisions.get('Apply', 0)}  Maybe: {decisions.get('Maybe', 0)}  Skip: {decisions.get('Skip', 0)}",
            f"\nLast 7 days ({len(recent)} jobs):",
            f"  Apply: {recent_decisions.get('Apply', 0)}  Maybe: {recent_decisions.get('Maybe', 0)}  Skip: {recent_decisions.get('Skip', 0)}",
        ]
        if verdicts:
            lines.append(f"\nYour verdicts:")
            for v, n in verdicts.most_common():
                if v:
                    lines.append(f"  {v}: {n}")

        # Learned weight adjustments
        adj = apply_weight_adjustments()
        if adj:
            lines.append("\nLearned adjustments:")
            for dim, delta in adj.items():
                lines.append(f"  {dim}: {delta:+d}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Error fetching stats: {e}")


# ---------------------------------------------------------------------------
# /start  /help
# ---------------------------------------------------------------------------

HELP_TEXT = """\
*Jobsearcher Bot*

Feedback:
  /good <url\\_or\\_id> [note]   — Mark as good match
  /skip <url\\_or\\_id> <reason> — Mark as skip
  /uncertain <url\\_or\\_id>     — Flag for manual review

Ad-hoc:
  /score <url\\_or\\_jd>         — Score any job on the spot

Rules:
  /block <keyword>    — Hard-reject jobs mentioning keyword
  /addkw <keyword>    — Add search keyword for next run
  /rmkw <keyword>     — Remove a search keyword
  /wait <pattern>     — Freeze learning for a pattern
  /ok <pattern>       — Unfreeze learning

Info:
  /rules   — Show active rules
  /stats   — Pipeline stats + your feedback history
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)

    # Handle deep links from email buttons: /start good_<job_id>  or  /start skip_<job_id>
    if context.args:
        param = context.args[0]
        if param.startswith("good_"):
            job_id = param[5:]
            await _write_feedback(update, job_id, verdict="Good Match", reason="Tapped from email digest")
            return
        if param.startswith("skip_"):
            job_id = param[5:]
            # Ask for reason via follow-up (record default now, user can refine with /skip)
            await _write_feedback(update, job_id, verdict="Disagree-Skip", reason="Skipped from email digest")
            await update.message.reply_text(
                f"Recorded Skip for job `{job_id}`.\n"
                f"Add a reason with: `/skip {job_id} <reason>`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _send_pending_notifications(app):
    """Drain pending_notifications.json and send each message to allowed users."""
    messages = drain_notifications()
    if not messages or not ALLOWED_USER_IDS:
        return
    for uid in ALLOWED_USER_IDS:
        for msg in messages:
            try:
                await app.bot.send_message(chat_id=uid, text=f"Pattern detected:\n{msg}", parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                print(f"  [warn] Could not send TG notification to {uid}: {e}")


def main():
    if not BOT_TOKEN:
        print("ERROR: TG_JOBSEARCHER_BOT_TOKEN is not set in .env")
        sys.exit(1)
    if not SPREADSHEET_ID:
        print("WARNING: GOOGLE_SHEETS_SPREADSHEET_ID not set — Sheets commands won't work")

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

    print(f"Jobsearcher bot running (allowed users: {ALLOWED_USER_IDS or 'all'})")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
