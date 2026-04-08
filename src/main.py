"""Jobsearcher -- main entry point.

Usage:
    python src/main.py --manual         # Run full pipeline once
    python src/main.py --scrape-only    # Just scrape and filter, no LLM
    python src/main.py --test-email     # Generate test digest from last scrape
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper import scrape_all_keywords
from src.filters import enrich_and_filter
from src.matcher import score_all_jobs
from src.travel import enrich_with_travel_time
from src.sheets import append_jobs
from src.notifier import send_email
from src.feedback import sync_verdicts_from_sheet, apply_weight_adjustments


def run_pipeline(scrape_only=False, min_score=0, quick=False, max_jobs=0):
    """Run the full job search pipeline."""
    print("=" * 60)
    label = "JOBSEARCHER PIPELINE"
    if quick:
        label += " [QUICK]"
    if max_jobs:
        label += f" [max {max_jobs} jobs]"
    print(label)
    print("=" * 60)
    pipeline_start = time.time()

    # Step 0: Sync feedback from Sheet + apply learned adjustments
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    if spreadsheet_id:
        print("\n[0/6] Syncing feedback...")
        sync_verdicts_from_sheet(spreadsheet_id)
    weight_adjustments = apply_weight_adjustments()
    if weight_adjustments:
        print(f"  Active weight adjustments: {weight_adjustments}")

    # Step 1: Scrape
    print("\n[1/6] Scraping jobs...")
    t0 = time.time()
    tiers = ["primary"] if quick else None
    jobs = scrape_all_keywords(tiers=tiers)
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("No jobs found. Exiting.")
        return
    total_scraped = len(jobs)

    # Step 2: Filter + Enrich
    print("\n[2/6] Filtering & enriching...")
    t0 = time.time()
    jobs, filter_log = enrich_and_filter(jobs)
    filtered_count = len(filter_log)
    if filter_log:
        print(f"\n  Pre-filtered jobs:")
        for entry in filter_log:
            print(f"    [{entry['reason']}] {entry['title']} @ {entry['company']}")
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("All jobs filtered out. Exiting.")
        return

    # Step 3: Travel time
    print("\n[3/6] Estimating travel times...")
    t0 = time.time()
    origin = os.getenv("HOME_STATION", "Hoofddorp")
    arrival = os.getenv("ARRIVAL_TIME", "09:00")
    jobs = enrich_with_travel_time(jobs, origin, arrival)
    print(f"  -> {time.time() - t0:.0f}s")

    if scrape_only:
        print(f"\n[Scrape-only] {len(jobs)} jobs after filtering:")
        cols = ["title", "company", "work_mode", "phygital_detected", "driver_license_flagged", "dutch_mandatory"]
        existing = [c for c in cols if c in jobs.columns]
        print(jobs[existing].to_string())
        os.makedirs("output", exist_ok=True)
        jobs.to_csv("output/scrape_results.csv", index=False)
        print("Saved to output/scrape_results.csv")
        return

    # Step 4: LLM scoring
    t0 = time.time()
    if max_jobs:
        jobs = jobs.head(max_jobs)
        print(f"\n[4/6] Scoring {len(jobs)} jobs with LLM (capped at {max_jobs})...")
    else:
        print("\n[4/6] Scoring with LLM (Phygital-weighted framework)...")
    jobs = score_all_jobs(jobs, min_score=min_score)
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("No jobs scored. Exiting.")
        return

    # Print quick summary
    print("\n--- Results ---")
    for _, row in jobs.iterrows():
        hint = row.get("decision_hint", "?")
        marker = {"Apply": "+", "Maybe": "~", "Skip": "-"}.get(hint, "?")
        print(f"  [{marker}] {row.get('match_score', 0):3d}% {row.get('title', '?')} @ {row.get('company', '?')}")

    # Step 5: Store in Google Sheets
    print("\n[5/6] Saving to Google Sheets...")
    t0 = time.time()
    if spreadsheet_id:
        append_jobs(jobs, spreadsheet_id)
    else:
        print("Google Sheets not configured. Saving CSV.")
        os.makedirs("output", exist_ok=True)
        jobs.to_csv(f"output/matches_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}.csv", index=False)

    print(f"  -> {time.time() - t0:.0f}s")

    # Step 6: Send email digest
    print("\n[6/6] Building digest...")
    t0 = time.time()
    output_path = send_email(jobs, filtered_count=filtered_count)
    print(f"  -> {time.time() - t0:.0f}s")

    print("\n" + "=" * 60)
    apply_count = len(jobs[jobs["decision_hint"] == "Apply"])
    print(f"DONE -- {len(jobs)} jobs scored, {apply_count} recommended to Apply")
    print(f"Digest: {output_path}")
    print(f"Total pipeline time: {time.time() - pipeline_start:.0f}s")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jobsearcher")
    parser.add_argument("--manual", action="store_true", help="Run full pipeline")
    parser.add_argument("--scrape-only", action="store_true", help="Scrape + filter only, no LLM")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum match score to include")
    parser.add_argument("--quick", action="store_true", help="Quick test: primary keywords only")
    parser.add_argument("--max-jobs", type=int, default=0, help="Max jobs to score (0 = all)")
    args = parser.parse_args()

    if args.manual or args.scrape_only:
        run_pipeline(scrape_only=args.scrape_only, min_score=args.min_score, quick=args.quick, max_jobs=args.max_jobs)
    else:
        print("Use --manual to run the pipeline or --scrape-only for scraping only.")
