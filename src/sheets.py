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
    "Date", "Score", "Decision", "Title", "Company", "Location",
    "Work Mode", "Role Cluster", "Phygital", "SaaS",
    "Seniority Fit", "Language Risk", "Driver Licence",
    "Salary", "KM Visa", "Agency", "Travel Min",
    "Why Fit", "Why Not Fit", "Gap Analysis",
    "Job URL", "Travel Link", "Search Keyword",
    "Status", "Skip Reason", "Notes",
]


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
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=30)
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
        url_col = headers.index("Job URL") + 1 if "Job URL" in headers else 21
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

        new_rows.append([
            today,
            str(row.get("match_score", "")),
            str(row.get("decision_hint", "")),
            str(row.get("title", "")),
            str(row.get("company", "")),
            str(row.get("location", "")),
            str(row.get("work_mode", "")),
            str(row.get("role_cluster", "")),
            str(row.get("phygital_detected", "")),
            str(row.get("pure_saas_detected", "")),
            str(row.get("seniority_fit", "")),
            "Dutch Mandatory" if row.get("dutch_mandatory") else ("Dutch Preferred" if row.get("dutch_nice_to_have") else "English OK"),
            str(row.get("driver_license_flagged", "")),
            str(row.get("salary_info", "")),
            str(row.get("km_visa_mentioned", "")),
            str(row.get("is_agency", "")),
            str(row.get("travel_minutes", "")),
            str(row.get("why_fit", "")),
            str(row.get("why_not_fit", "")),
            str(row.get("gap_analysis", "")),
            url,
            str(row.get("travel_link", "")),
            str(row.get("search_keyword", "")),
            "New",
            "",  # Skip Reason (user fills in)
            "",  # Notes
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
