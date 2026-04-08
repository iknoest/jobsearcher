"""Google Sheets integration — store jobs, track applications, sync feedback."""

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
]


def _extract_from_match_result(row):
    """Extract structured fields from the LLM match_result JSON."""
    try:
        r = json.loads(row.get("match_result", "{}"))
    except (json.JSONDecodeError, TypeError):
        r = {}

    card = r.get("CardSummary", {})
    sig = r.get("DecisionSignals", {})
    fit = r.get("WhyFit", {})
    phy = r.get("PhygitalAssessment", {})
    co = r.get("CompanySnapshot", {})
    trust = r.get("TrustNotes", {})

    return {
        "industry": co.get("Industry", ""),
        "company_size": co.get("Size", ""),
        "why_fit": "; ".join(fit.get("StrongMatch", []) + fit.get("PartialMatch", [])),
        "why_not_fit": "; ".join(r.get("Gaps", []) + r.get("Risks", [])),
        "gap_analysis": "; ".join(r.get("Gaps", [])),
        "phygital_level": phy.get("Level", ""),
        "phygital_reason": phy.get("Reason", ""),
        "top_label": card.get("TopLabel", ""),
        "main_risk": card.get("MainRisk", ""),
        "confidence": trust.get("Confidence", ""),
        "work_mode_llm": sig.get("WorkMode", ""),
        "salary_llm": sig.get("SalaryText", ""),
        "commute_llm": sig.get("CommuteText", ""),
        "language_llm": sig.get("LanguageText", ""),
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
        ])

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"Added {len(new_rows)} new jobs to Google Sheets ({len(df) - len(new_rows)} duplicates skipped)")
    else:
        print("No new jobs to add (all duplicates)")

    return len(new_rows)


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
