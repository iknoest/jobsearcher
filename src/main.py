"""Jobsearcher -- main entry point.

Usage:
    python src/main.py --manual         # Full pipeline: scrape + filter + prerank + LLM + email
    python src/main.py --scrape-only    # Scrape + filter only, no prerank, no LLM
    python src/main.py --no-score       # Scrape + filter + prerank + digest, skip LLM (saves quota)
    python src/main.py --rerank-only    # Re-apply prerank to cached enriched jobs, skip scrape + LLM
"""

import argparse
import os
import sys
import time

# Force UTF-8 stdout so Traditional Chinese from LLM doesn't crash on Windows cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper import scrape_all_keywords
from src.filters import enrich_and_filter, language_prefilter
from src.matcher import score_all_jobs
from src.travel import enrich_with_travel_time
from src.sheets import append_jobs, setup_tabs, poll_inbox, update_inbox_status, read_rules
from src.notifier import send_email, build_prerank_digest
from src.feedback import sync_verdicts_from_sheet, apply_weight_adjustments
from src.prerank import apply_prerank, split_by_prerank

ENRICHED_CACHE_PATH = "output/enriched_jobs.pkl"


def _save_enriched_cache(df):
    """Persist enriched DataFrame so --rerank-only can replay without scraping."""
    try:
        os.makedirs("output", exist_ok=True)
        df.to_pickle(ENRICHED_CACHE_PATH)
        print(f"  Cached enriched jobs -> {ENRICHED_CACHE_PATH}")
    except Exception as e:
        print(f"  [warn] Could not write enriched cache: {e}")


def _load_enriched_cache():
    import pandas as pd
    if not os.path.exists(ENRICHED_CACHE_PATH):
        return None
    try:
        return pd.read_pickle(ENRICHED_CACHE_PATH)
    except Exception as e:
        print(f"  [warn] Could not read enriched cache: {e}")
        return None


def run_rerank_only():
    """Re-apply pre-rank to the cached enriched DataFrame. No scrape, no LLM."""
    print("=" * 60)
    print("JOBSEARCHER [RERANK-ONLY] — reading cached enriched jobs")
    print("=" * 60)

    df = _load_enriched_cache()
    if df is None or df.empty:
        print(f"No cache at {ENRICHED_CACHE_PATH}. Run --no-score or --manual first.")
        return

    print(f"Loaded {len(df)} cached jobs.")
    ranked = apply_prerank(df)
    _print_prerank_summary(ranked)

    output_path = build_prerank_digest(ranked)
    print(f"Digest: {output_path}")


def _print_prerank_summary(ranked, top_n=None):
    print("\n--- Pre-rank results ---")
    limit = top_n if top_n else len(ranked)
    for i, row in ranked.head(limit).iterrows():
        score = int(row.get("prerank_score", 0))
        title = str(row.get("title", "?"))[:50]
        company = str(row.get("company", "?"))[:25]
        reasons = str(row.get("prerank_reasons", ""))[:80]
        print(f"  {score:+4d}  {title:<50}  @ {company:<25}  [{reasons}]")


def run_pipeline(scrape_only=False, min_score=0, quick=False, max_jobs=0, no_score=False):
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

    # Step 0: Sync feedback from Sheet + apply learned adjustments + setup tabs
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    sheet_rules = {"weight_adjustments": {}, "keyword_blocks": [], "keyword_adds": [],
                   "company_boosts": {}, "company_demotes": {}}
    if spreadsheet_id:
        print("\n[0/7] Syncing feedback + reading rules...")
        setup_tabs(spreadsheet_id)
        sync_verdicts_from_sheet(spreadsheet_id)
        sheet_rules = read_rules(spreadsheet_id)
    frozen_patterns = sheet_rules.get("frozen_patterns", [])
    weight_adjustments = apply_weight_adjustments(frozen_patterns=frozen_patterns)
    # Merge sheet-based weight adjustments on top of learned ones
    weight_adjustments.update(sheet_rules["weight_adjustments"])
    if weight_adjustments:
        print(f"  Active weight adjustments: {weight_adjustments}")

    # Step 1: Scrape
    print("\n[1/7] Scraping jobs...")
    t0 = time.time()
    tiers = ["primary"] if quick else None
    jobs, keyword_counts = scrape_all_keywords(tiers=tiers)
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("No jobs found. Exiting.")
        return
    if keyword_counts:
        print(f"\n  Jobs per keyword (before dedup):")
        for kw, count in sorted(keyword_counts.items(), key=lambda x: -x[1]):
            print(f"    {kw}: {count}")

    # Merge Inbox jobs into the pipeline
    if spreadsheet_id and not scrape_only:
        import pandas as pd
        inbox_jobs = poll_inbox(spreadsheet_id)
        if inbox_jobs:
            inbox_df = pd.DataFrame(inbox_jobs)
            jobs = pd.concat([jobs, inbox_df], ignore_index=True)

    total_scraped = len(jobs)

    # Step 2: Language pre-filter (hard reject Dutch-mandatory jobs)
    print("\n[2/7] Language pre-filter...")
    t0 = time.time()
    jobs, lang_rejections = language_prefilter(jobs)
    if lang_rejections:
        print(f"  Rejected at Language Pre-filter:")
        for entry in lang_rejections:
            print(f"    [{entry['SkipReason']}] {entry['title']} @ {entry['company']}")
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("All jobs rejected by language pre-filter. Exiting.")
        return

    # Step 3: Enrich + remaining filters
    print("\n[3/7] Enriching & filtering...")
    t0 = time.time()
    jobs, filter_log = enrich_and_filter(jobs)
    filter_log = lang_rejections + filter_log
    filtered_count = len(filter_log)
    if filter_log:
        print(f"\n  Pre-filtered jobs:")
        for entry in filter_log:
            print(f"    [{entry['reason']}] {entry['title']} @ {entry['company']}")
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("All jobs filtered out. Exiting.")
        return

    # Step 4: Travel time
    print("\n[4/7] Estimating travel times...")
    t0 = time.time()
    origin = os.getenv("HOME_STATION", "Hoofddorp")
    arrival = os.getenv("ARRIVAL_TIME", "09:00")
    jobs = enrich_with_travel_time(jobs, origin, arrival)
    print(f"  -> {time.time() - t0:.0f}s")

    # Cache enriched jobs so --rerank-only can replay without scraping
    _save_enriched_cache(jobs)

    if scrape_only:
        print(f"\n[Scrape-only] {len(jobs)} jobs after filtering:")
        cols = ["title", "company", "work_mode", "phygital_detected", "driver_license_flagged", "dutch_mandatory"]
        existing = [c for c in cols if c in jobs.columns]
        print(jobs[existing].to_string().encode("ascii", errors="replace").decode("ascii"))
        os.makedirs("output", exist_ok=True)
        jobs.to_csv("output/scrape_results.csv", index=False)
        print("Saved to output/scrape_results.csv")
        return

    # Step 4.5: Pre-rank — cheap, rule-based scoring before the LLM
    print("\n[4.5/7] Applying pre-rank (rule-based, no LLM)...")
    t0 = time.time()
    jobs = apply_prerank(jobs)
    _print_prerank_summary(jobs, top_n=min(20, len(jobs)))
    print(f"  -> {time.time() - t0:.0f}s")

    if no_score:
        print(f"\n[no-score] Skipping LLM. Building pre-rank digest...")
        output_path = build_prerank_digest(jobs)
        print(f"Digest: {output_path}")
        print(f"Total pipeline time: {time.time() - pipeline_start:.0f}s")
        return

    # Step 5: LLM scoring — selects top N by pre-rank, not first N
    t0 = time.time()
    if max_jobs and len(jobs) > max_jobs:
        kept, dropped = split_by_prerank(jobs, max_jobs)
        jobs = kept
        print(f"\n[5/7] Scoring top {len(jobs)} by pre-rank with LLM ({len(dropped)} below threshold)...")
        if len(dropped):
            print(f"  Below pre-rank threshold (not LLM-scored):")
            for _, row in dropped.head(15).iterrows():
                print(f"    {int(row.get('prerank_score', 0)):+4d}  {str(row.get('title', '?'))[:50]} @ {str(row.get('company', '?'))[:25]}")
            if len(dropped) > 15:
                print(f"    ... and {len(dropped) - 15} more")
    else:
        print("\n[5/7] Scoring with LLM (Phygital-weighted framework)...")
    jobs = score_all_jobs(jobs, min_score=min_score)
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("No jobs scored. Exiting.")
        return

    # Mark Inbox rows as Scored
    if spreadsheet_id and "_inbox_row" in jobs.columns:
        for _, row in jobs[jobs["_inbox_row"].notna()].iterrows():
            update_inbox_status(
                spreadsheet_id,
                int(row["_inbox_row"]),
                "Scored",
                score=row.get("match_score", ""),
                decision=row.get("decision_hint", ""),
            )

    # Print quick summary
    print("\n--- Results ---")
    for _, row in jobs.iterrows():
        hint = row.get("decision_hint", "?")
        marker = {"Apply": "+", "Maybe": "~", "Skip": "-"}.get(hint, "?")
        print(f"  [{marker}] {row.get('match_score', 0):3d}% {row.get('title', '?')} @ {row.get('company', '?')}")

    # Step 6: Store in Google Sheets
    print("\n[6/7] Saving to Google Sheets...")
    t0 = time.time()
    if spreadsheet_id:
        append_jobs(jobs, spreadsheet_id)
    else:
        print("Google Sheets not configured. Saving CSV.")
        os.makedirs("output", exist_ok=True)
        jobs.to_csv(f"output/matches_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}.csv", index=False)

    print(f"  -> {time.time() - t0:.0f}s")

    # Step 7: Send email digest
    print("\n[7/7] Building digest...")
    t0 = time.time()

    # Build keyword stats for digest
    keyword_stats = []
    if "search_keyword" in jobs.columns:
        for kw in jobs["search_keyword"].unique():
            kw_jobs = jobs[jobs["search_keyword"] == kw]
            keyword_stats.append({
                "keyword": kw,
                "found": len(kw_jobs),
                "apply": len(kw_jobs[kw_jobs["decision_hint"] == "Apply"]),
                "maybe": len(kw_jobs[kw_jobs["decision_hint"] == "Maybe"]),
            })
        keyword_stats.sort(key=lambda x: x["apply"] + x["maybe"], reverse=True)

    output_path = send_email(jobs, filtered_count=filtered_count, keyword_stats=keyword_stats)
    print(f"  -> {time.time() - t0:.0f}s")

    print("\n" + "=" * 60)
    apply_count = len(jobs[jobs["decision_hint"] == "Apply"])
    print(f"DONE -- {len(jobs)} jobs scored, {apply_count} recommended to Apply")
    print(f"Digest: {output_path}")
    print(f"Total pipeline time: {time.time() - pipeline_start:.0f}s")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jobsearcher")
    parser.add_argument("--manual", action="store_true", help="Run full pipeline (scrape + filter + prerank + LLM + email)")
    parser.add_argument("--scrape-only", action="store_true", help="Scrape + filter only, no prerank, no LLM")
    parser.add_argument("--no-score", action="store_true", help="Scrape + filter + prerank + digest, skip LLM entirely")
    parser.add_argument("--rerank-only", action="store_true", help="Re-apply prerank to cached enriched jobs (no scrape, no LLM)")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum match score to include")
    parser.add_argument("--quick", action="store_true", help="Quick test: primary keywords only")
    parser.add_argument("--max-jobs", type=int, default=0, help="Max jobs to LLM-score (top N by pre-rank)")
    args = parser.parse_args()

    if args.rerank_only:
        run_rerank_only()
    elif args.manual or args.scrape_only or args.no_score:
        run_pipeline(
            scrape_only=args.scrape_only,
            min_score=args.min_score,
            quick=args.quick,
            max_jobs=args.max_jobs,
            no_score=args.no_score,
        )
    else:
        print("Use --manual, --no-score, --rerank-only, or --scrape-only.")
