"""Google Sheets integration — store jobs, track applications, sync feedback.

Tabs:
  Jobs   — main scored job store (32 columns)
  Inbox  — drop a URL or raw JD; pipeline picks up Pending rows on next run
  Rules  — dynamic weight/keyword overrides; human-editable + Telegram-writable
"""

import os
import json
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Date", "Score", "Decision", "Title", "Company", "Industry",
    "Location", "Work Mode", "Role Cluster", "Phygital Level",
    "Phygital Reason", "SaaS", "Seniority Fit",
    "Language Risk", "Driver Licence",
    "Salary", "KM Visa (JD)", "KM Visa (IND)", "Agency",
    "Travel Min", "Commute",
    "Why Fit", "Why Not Fit", "Gap Analysis",
    "Top Label", "Main Risk", "Confidence",
    "Job URL", "Travel Link", "Search Keyword",
    "My Verdict", "My Reason",
    # V1 of 4-block redesign (appended at end for backward compat):
    "Suggested Action", "Anti-Pattern Risk",
    "Dim Research", "Dim Innovation", "Dim Hands-on",
    "Dim Strategy", "Dim Alignment Risk", "Dim Process Risk", "Dim Team Fit",
]


def _extract_from_match_result(row):
    """Extract structured fields from the LLM match_result JSON.

    Supports both schemas:
    - New (V1 4-block): Dimensions + GreenFlags + RedFlags + AntiPatternRisk + SuggestedAction
    - Old (pre-redesign): WhyFit.StrongMatch/PartialMatch + Gaps + Risks
    """
    try:
        r = json.loads(row.get("match_result", "{}"))
    except (json.JSONDecodeError, TypeError):
        r = {}

    card = r.get("CardSummary", {})
    sig = r.get("DecisionSignals", {})
    phy = r.get("PhygitalAssessment", {})
    co = r.get("CompanySnapshot", {})
    trust = r.get("TrustNotes", {})
    dims = r.get("Dimensions", {}) or {}
    risk = r.get("AntiPatternRisk", {}) or {}

    # Green/Red flags (new) fall back to WhyFit + Gaps/Risks (old) for legacy rows
    green = r.get("GreenFlags", [])
    red = r.get("RedFlags", [])
    if not green and not red:
        fit = r.get("WhyFit", {}) or {}
        green = fit.get("StrongMatch", []) + fit.get("PartialMatch", [])
        red = (r.get("Gaps", []) or []) + (r.get("Risks", []) or [])

    def _dim(name):
        return dims.get(name, {}).get("score", "")

    return {
        "industry": co.get("Industry", ""),
        "company_size": co.get("Size", ""),
        "why_fit": "; ".join(green),
        "why_not_fit": "; ".join(red),
        "gap_analysis": "; ".join(r.get("Gaps", []) or []),
        "phygital_level": phy.get("Level", ""),
        "phygital_reason": phy.get("Reason", ""),
        "top_label": card.get("TopLabel", ""),
        "main_risk": card.get("MainRisk", ""),
        "confidence": trust.get("Confidence", ""),
        "work_mode_llm": sig.get("WorkMode", ""),
        "salary_llm": sig.get("SalaryText", ""),
        "commute_llm": sig.get("CommuteText", ""),
        "language_llm": sig.get("LanguageText", ""),
        "suggested_action": card.get("SuggestedAction", ""),
        "anti_pattern_risk": risk.get("Level", ""),
        "dim_research": _dim("ResearchFit"),
        "dim_innovation": _dim("InnovationFit"),
        "dim_handson": _dim("HandsOnProximity"),
        "dim_strategy": _dim("StrategyFit"),
        "dim_alignment_burden": _dim("AlignmentBurden"),
        "dim_process_heavy": _dim("ProcessHeaviness"),
        "dim_team_style": _dim("TeamStyleFit"),
    }


def get_client():
    creds_file = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "config/google_credentials.json")
    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheet(spreadsheet_id, sheet_name="Jobs"):
    client = get_client()
    spreadsheet = client.open_by_key(spreadsheet_id)

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=35)
        worksheet.update("A1", [HEADERS])

    return worksheet


def get_existing_urls(spreadsheet_id, sheet_name="Jobs"):
    """Get all job URLs already in the sheet for deduplication."""
    try:
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        # Find Job URL column dynamically
        headers = worksheet.row_values(1)
        url_col = headers.index("Job URL") + 1 if "Job URL" in headers else 28
        urls = worksheet.col_values(url_col)
        return set(urls[1:])
    except Exception:
        return set()


def append_jobs(df, spreadsheet_id, sheet_name="Jobs"):
    """Append new jobs to Google Sheet, skipping duplicates."""
    existing_urls = get_existing_urls(spreadsheet_id, sheet_name)
    worksheet = get_or_create_worksheet(spreadsheet_id, sheet_name)

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    new_rows = []
    for _, row in df.iterrows():
        url = str(row.get("job_url", ""))
        if url in existing_urls:
            continue

        llm = _extract_from_match_result(row)

        lang_risk = "Dutch Mandatory" if row.get("dutch_mandatory") else (
            "Dutch Preferred" if row.get("dutch_nice_to_have") else "English OK")

        new_rows.append([
            today,                                              # Date
            str(row.get("match_score", "")),                    # Score
            str(row.get("decision_hint", "")),                  # Decision
            str(row.get("title", "")),                          # Title
            str(row.get("company", "")),                        # Company
            llm["industry"],                                    # Industry
            str(row.get("location", "")),                       # Location
            llm["work_mode_llm"] or str(row.get("work_mode", "")),  # Work Mode
            str(row.get("role_cluster", "")),                   # Role Cluster
            llm["phygital_level"],                              # Phygital Level
            llm["phygital_reason"],                             # Phygital Reason
            str(row.get("pure_saas_detected", "")),             # SaaS
            str(row.get("seniority_fit", "")),                  # Seniority Fit
            lang_risk,                                          # Language Risk
            str(row.get("driver_license_flagged", "")),         # Driver Licence
            llm["salary_llm"] or str(row.get("salary_info", "")),  # Salary
            str(row.get("km_visa_mentioned", "")),              # KM Visa (JD)
            str(row.get("km_visa_sponsor", "")),                # KM Visa (IND)
            str(row.get("is_agency", "")),                      # Agency
            str(row.get("travel_minutes", "")),                 # Travel Min
            llm["commute_llm"],                                 # Commute
            llm["why_fit"],                                     # Why Fit
            llm["why_not_fit"],                                 # Why Not Fit
            llm["gap_analysis"],                                # Gap Analysis
            llm["top_label"],                                   # Top Label
            llm["main_risk"],                                   # Main Risk
            llm["confidence"],                                  # Confidence
            url,                                                # Job URL
            str(row.get("travel_link", "")),                    # Travel Link
            str(row.get("search_keyword", "")),                 # Search Keyword
            "",                                                 # My Verdict (user fills)
            "",                                                 # My Reason (user fills)
            llm["suggested_action"],                            # Suggested Action
            llm["anti_pattern_risk"],                           # Anti-Pattern Risk
            str(llm["dim_research"]),                           # Dim Research
            str(llm["dim_innovation"]),                         # Dim Innovation
            str(llm["dim_handson"]),                            # Dim Hands-on
            str(llm["dim_strategy"]),                           # Dim Strategy
            str(llm["dim_alignment_burden"]),                   # Dim Alignment Risk
            str(llm["dim_process_heavy"]),                      # Dim Process Risk
            str(llm["dim_team_style"]),                         # Dim Team Fit
        ])

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"Added {len(new_rows)} new jobs to Google Sheets ({len(df) - len(new_rows)} duplicates skipped)")
    else:
        print("No new jobs to add (all duplicates)")

    return len(new_rows)


INBOX_HEADERS = [
    "URL", "Raw JD", "Title", "Company", "Status",
    "Date Added", "Score", "Decision",
]

RULES_HEADERS = [
    "Type", "Value", "Delta", "Active", "Note",
]


def _repair_jobs_headers(spreadsheet):
    """If the Jobs tab exists but has stale/misaligned headers, update row 1 to match HEADERS.

    This repairs the 'Industry column missing' bug where adding Industry to HEADERS after
    the sheet was created caused every subsequent column (including Job URL) to be written
    one position to the right of its header label.
    """
    try:
        ws = spreadsheet.worksheet("Jobs")
        current = ws.row_values(1)
        # Pad to the same length for comparison
        current_trimmed = [h.strip() for h in current if h.strip()]
        expected_trimmed = [h.strip() for h in HEADERS]
        if current_trimmed != expected_trimmed:
            ws.update("A1", [HEADERS])
            print(f"  Repaired Jobs header row ({len(current_trimmed)} → {len(expected_trimmed)} columns)")
    except gspread.WorksheetNotFound:
        pass  # will be created below
    except Exception as e:
        print(f"  [warn] Could not repair Jobs headers: {e}")


def setup_tabs(spreadsheet_id):
    """Create Inbox and Rules tabs if they don't exist. Safe to call on every run."""
    try:
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)

        # Repair Jobs header row if schema has changed since the sheet was created
        _repair_jobs_headers(spreadsheet)

        existing = {ws.title for ws in spreadsheet.worksheets()}

        if "Inbox" not in existing:
            ws = spreadsheet.add_worksheet(title="Inbox", rows=200, cols=len(INBOX_HEADERS))
            ws.update("A1", [INBOX_HEADERS])
            print("  Created Inbox tab in Google Sheets")

        if "Rules" not in existing:
            ws = spreadsheet.add_worksheet(title="Rules", rows=100, cols=len(RULES_HEADERS))
            ws.update("A1", [RULES_HEADERS])
            # Seed with example rows (commented-out format so user knows the schema)
            ws.update("A2", [
                ["weight_adjust", "phygital", "+10", "FALSE", "Example: boost Phygital score by 10"],
                ["keyword_block", "salesforce", "", "FALSE", "Example: hard-reject any JD mentioning this"],
                ["keyword_add", "Hardware Engineer", "", "FALSE", "Example: add search keyword next run"],
                ["company_boost", "ASML", "+20", "FALSE", "Example: boost ASML jobs by 20 pre-rank pts"],
                ["company_demote", "Randstad", "-30", "FALSE", "Example: demote agency by 30 pre-rank pts"],
            ])
            print("  Created Rules tab in Google Sheets (with example rows)")
    except Exception as e:
        print(f"  [warn] setup_tabs failed: {e}")


def poll_inbox(spreadsheet_id):
    """Read Pending rows from Inbox tab. Returns list of job dicts ready for scoring.

    Each dict has: url, raw_jd, title, company, description (resolved), _inbox_row (row index).
    Caller is responsible for marking rows Scored/Error via update_inbox_status().
    """
    try:
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        try:
            ws = spreadsheet.worksheet("Inbox")
        except gspread.WorksheetNotFound:
            return []

        records = ws.get_all_records()
        pending = []
        for i, row in enumerate(records, start=2):  # row 1 = header
            status = str(row.get("Status", "")).strip()
            if status.lower() != "pending":
                continue
            url = str(row.get("URL", "")).strip()
            raw_jd = str(row.get("Raw JD", "")).strip()
            title = str(row.get("Title", "")).strip() or "Unknown"
            company = str(row.get("Company", "")).strip() or "Unknown"

            description = raw_jd
            if not description and url:
                try:
                    import trafilatura
                    downloaded = trafilatura.fetch_url(url)
                    if downloaded:
                        description = trafilatura.extract(downloaded) or ""
                except Exception:
                    pass

            if not description:
                print(f"  [Inbox row {i}] No description available — skipping")
                continue

            pending.append({
                "job_url": url or f"inbox://row{i}",
                "description": description,
                "title": title,
                "company": company,
                "location": "Unknown",
                "work_mode": "Unknown",
                "salary_info": "",
                "job_type": "",
                "is_remote": None,
                "search_keyword": "inbox",
                "_inbox_row": i,
            })
        if pending:
            print(f"  Inbox: {len(pending)} Pending job(s) to score")
        return pending
    except Exception as e:
        print(f"  [warn] poll_inbox failed: {e}")
        return []


def update_inbox_status(spreadsheet_id, row_index, status, score="", decision=""):
    """Update Status (and optionally Score/Decision) for an Inbox row."""
    try:
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        ws = spreadsheet.worksheet("Inbox")
        # Columns: URL=1, Raw JD=2, Title=3, Company=4, Status=5, Date Added=6, Score=7, Decision=8
        ws.update(f"E{row_index}", [[status]])
        if score or decision:
            ws.update(f"G{row_index}", [[str(score), str(decision)]])
    except Exception as e:
        print(f"  [warn] update_inbox_status failed: {e}")


def read_rules(spreadsheet_id):
    """Read active rows from Rules tab. Returns structured override dict.

    Keys:
      weight_adjustments  — {dimension: delta_int}
      keyword_blocks      — [str]
      keyword_adds        — [str]
      company_boosts      — {name: delta_int}
      company_demotes     — {name: delta_int}
      frozen_patterns     — [str]  (from 'wait' type rows — freeze learning)
    """
    result = {
        "weight_adjustments": {},
        "keyword_blocks": [],
        "keyword_adds": [],
        "company_boosts": {},
        "company_demotes": {},
        "frozen_patterns": [],
    }
    try:
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        try:
            ws = spreadsheet.worksheet("Rules")
        except gspread.WorksheetNotFound:
            return result

        for row in ws.get_all_records():
            active = str(row.get("Active", "")).strip().upper()
            if active not in ("TRUE", "1", "YES"):
                continue
            rule_type = str(row.get("Type", "")).strip().lower()
            value = str(row.get("Value", "")).strip()
            delta_raw = str(row.get("Delta", "")).strip()

            try:
                delta = int(delta_raw) if delta_raw else 0
            except ValueError:
                delta = 0

            if rule_type == "weight_adjust" and value:
                result["weight_adjustments"][value] = delta
            elif rule_type == "keyword_block" and value:
                result["keyword_blocks"].append(value.lower())
            elif rule_type == "keyword_add" and value:
                result["keyword_adds"].append(value)
            elif rule_type == "company_boost" and value:
                result["company_boosts"][value.lower()] = delta
            elif rule_type == "company_demote" and value:
                result["company_demotes"][value.lower()] = delta
            elif rule_type == "wait" and value:
                result["frozen_patterns"].append(value.lower())

        active_count = sum(len(v) if isinstance(v, (list, dict)) else 1
                           for v in result.values() if v)
        if active_count:
            print(f"  Rules tab: {active_count} active rule(s) loaded")
    except Exception as e:
        print(f"  [warn] read_rules failed: {e}")
    return result


def sync_feedback_from_sheet(spreadsheet_id, feedback_path="config/feedback_log.json", sheet_name="Jobs"):
    """Sync skip reasons from Google Sheet back to local feedback log.
    When user fills 'Skip Reason' column in the Sheet, it gets pulled into feedback_log.
    """
    try:
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        records = worksheet.get_all_records()

        with open(feedback_path, "r", encoding="utf-8") as f:
            feedback = json.load(f)

        skipped_ids = set(feedback.get("skipped_job_ids", []))
        for record in records:
            skip_reason = record.get("Skip Reason", "").strip()
            url = record.get("Job URL", "")
            if skip_reason and url and url not in skipped_ids:
                skipped_ids.add(url)
                # Learn from skip pattern
                if "sas" in skip_reason.lower() or "software" in skip_reason.lower():
                    # Reinforce Pure SaaS penalty
                    pass  # Already in negative_feedback

        feedback["skipped_job_ids"] = list(skipped_ids)
        with open(feedback_path, "w", encoding="utf-8") as f:
            json.dump(feedback, f, ensure_ascii=False, indent=2)

        print(f"Synced feedback: {len(skipped_ids)} skipped jobs")
    except Exception as e:
        print(f"Feedback sync failed: {e}")
