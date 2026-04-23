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

import pandas as pd

# Force UTF-8 stdout so Traditional Chinese from LLM doesn't crash on Windows cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper import scrape_all_keywords
from src.filters import enrich_and_filter, language_prefilter, location_filter
from src.matcher import score_all_jobs
from src.travel import enrich_with_travel_time
from src.sheets import append_jobs, setup_tabs, poll_inbox, update_inbox_status, read_rules, get_existing_urls
from src.notifier import send_email, build_prerank_digest
from src.feedback import sync_verdicts_from_sheet, apply_weight_adjustments
from src.prerank import apply_prerank, split_by_prerank
from src.classify.role_family import classify_dataframe as classify_role_families
from src.classify.industry import classify_dataframe as classify_industries
from src.classify.seniority import classify_dataframe as classify_seniorities
from src.score.pipeline import run_and_print as run_sub_scorers_and_print


def _apply_classify_stage(jobs):
    """Stage 3b: Role-family + industry + seniority classification.

    Step 1: role-family classify → attaches role_* columns.
    Step 2: drop jobs where role_hard_reject=True.
    Step 3: industry classify on survivors → attaches industry_* columns (never drops).
    Step 4: seniority classify → attaches seniority_* columns.
    Step 5: drop jobs where seniority_hard_reject=True (explicit early-career only).

    Returns (kept_df, rejected_records). Rejected records include BOTH
    role and seniority hard-rejects tagged by 'gate' field.
    """
    if jobs is None or len(jobs) == 0:
        return jobs, []

    classified = classify_role_families(jobs)
    role_rejected_mask = classified["role_hard_reject"] == True  # noqa: E712

    rejected_records = [
        {
            "gate": "role_family",
            "title": row.get("title", ""),
            "company": row.get("company", ""),
            "role_family": row.get("role_family", ""),
            "role_confidence": row.get("role_confidence", 0.0),
            "reason": row.get("role_reasoning", ""),
        }
        for _, row in classified[role_rejected_mask].iterrows()
    ]

    kept = classified[~role_rejected_mask].reset_index(drop=True)
    kept = classify_industries(kept)
    kept = classify_seniorities(kept)

    sen_rejected_mask = kept["seniority_hard_reject"] == True  # noqa: E712
    for _, row in kept[sen_rejected_mask].iterrows():
        rejected_records.append(
            {
                "gate": "seniority",
                "title": row.get("title", ""),
                "company": row.get("company", ""),
                "role_family": row.get("role_family", ""),
                "seniority_level": row.get("seniority_level", ""),
                "reason": row.get("seniority_reasoning", ""),
            }
        )
    kept = kept[~sen_rejected_mask].reset_index(drop=True)
    return kept, rejected_records

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


def run_resend(max_jobs=25):
    """Re-filter cached enriched jobs with the location filter, rescore top N, resend email.

    Does NOT write to Google Sheets — safe to run after a broken/unfiltered run.
    Useful when the location filter was missing on the previous run.
    """
    print("=" * 60)
    print("JOBSEARCHER [RESEND] — filtered rescore from cache")
    print("=" * 60)

    df = _load_enriched_cache()
    if df is None or df.empty:
        print(f"No cache at {ENRICHED_CACHE_PATH}. Run --manual first.")
        return

    print(f"Loaded {len(df)} cached jobs.")

    # Apply location filter
    print("\n[1] Location filter (NL + remote only)...")
    df, loc_rejected = location_filter(df)
    print(f"  {len(loc_rejected)} non-NL non-remote jobs removed.")
    if df.empty:
        print("All jobs filtered by location. Exiting.")
        return

    # Apply Stage 3b classification gate (role family + seniority hard-rejects)
    print("\n[1b] Classification gate (role family + seniority)...")
    before_rf = len(df)
    df, rf_rejected = _apply_classify_stage(df)
    if rf_rejected:
        role_drops = sum(1 for r in rf_rejected if r.get("gate") == "role_family")
        sen_drops = sum(1 for r in rf_rejected if r.get("gate") == "seniority")
        print(f"  Hard-rejected {len(rf_rejected)}/{before_rf} "
              f"(role={role_drops}, seniority={sen_drops}).")
    if df.empty:
        print("All jobs hard-rejected by classification gate. Exiting.")
        return

    # Apply prerank
    print("\n[2] Pre-rank...")
    df = apply_prerank(df)
    _print_prerank_summary(df, top_n=min(20, len(df)))

    # Score top N with LLM
    kept, dropped = split_by_prerank(df, max_jobs)
    print(f"\n[3] Scoring top {len(kept)} by pre-rank with LLM ({len(dropped)} below threshold)...")
    jobs = score_all_jobs(kept, min_score=0, force=True)
    if jobs.empty:
        print("No jobs scored. Exiting.")
        return

    # Print summary
    print("\n--- Results ---")
    for _, row in jobs.iterrows():
        hint = row.get("decision_hint", "?")
        marker = {"Apply": "+", "Maybe": "~", "Skip": "-"}.get(hint, "?")
        print(f"  [{marker}] {row.get('match_score', 0):3d}% {row.get('title', '?')} @ {row.get('company', '?')}")

    # Send email only — no Sheet write
    print("\n[4] Sending filtered email digest...")
    output_path = send_email(jobs, filtered_count=len(loc_rejected), keyword_stats=[])
    print(f"Digest: {output_path}")
    print("\nDone. Sheet was NOT updated (use --manual for a full run).")


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
        print("\n[0/8] Syncing feedback + reading rules...")
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
    print("\n[1/8] Scraping jobs...")
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
    print("\n[2/8] Language pre-filter...")
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

    # Step 2.5: Location filter (hard reject non-NL, non-remote jobs)
    print("\n[2.5/8] Location filter (NL + remote only)...")
    t0 = time.time()
    jobs, loc_rejections = location_filter(jobs)
    if loc_rejections:
        print(f"  Rejected at Location Filter:")
        for entry in loc_rejections[:10]:
            print(f"    [{entry['location']}] {entry['title']} @ {entry['company']}")
        if len(loc_rejections) > 10:
            print(f"    ... and {len(loc_rejections) - 10} more")
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("All jobs rejected by location filter. Exiting.")
        return

    # Step 3: Enrich + remaining filters
    print("\n[3/8] Enriching & filtering...")
    t0 = time.time()
    jobs, filter_log = enrich_and_filter(jobs)
    filter_log = lang_rejections + loc_rejections + filter_log
    filtered_count = len(filter_log)
    if filter_log:
        print(f"\n  Pre-filtered jobs:")
        for entry in filter_log:
            print(f"    [{entry['reason']}] {entry['title']} @ {entry['company']}")
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("All jobs filtered out. Exiting.")
        return

    # Step 3b: Role-family + industry classification (PRD §8 Stage 4 / Jobsearcher Stage 3b)
    print("\n[3b/8] Classification (role family + industry + seniority) + hard-reject gate...")
    t0 = time.time()
    before_rf = len(jobs)
    jobs, rf_rejections = _apply_classify_stage(jobs)
    if rf_rejections:
        role_drops = sum(1 for r in rf_rejections if r.get("gate") == "role_family")
        sen_drops = sum(1 for r in rf_rejections if r.get("gate") == "seniority")
        print(f"  Hard-rejected {len(rf_rejections)} total "
              f"(role_family={role_drops}, seniority={sen_drops}):")
        for entry in rf_rejections[:10]:
            if entry.get("gate") == "seniority":
                print(f"    [seniority:{entry.get('seniority_level','?')}] "
                      f"{entry['title']} @ {entry['company']} — {entry.get('reason','')}")
            else:
                print(f"    [role:{entry.get('role_family','?')} "
                      f"conf={entry.get('role_confidence', 0.0):.2f}] "
                      f"{entry['title']} @ {entry['company']}")
        if len(rf_rejections) > 10:
            print(f"    ... and {len(rf_rejections) - 10} more")
    if len(jobs) > 0:
        bucket_counts = jobs["role_bucket"].value_counts().to_dict()
        rescued_count = int(jobs["role_rescued"].sum())
        print(f"  Role buckets: {bucket_counts}; rescued: {rescued_count}")
        ind_src_counts = jobs["industry_source"].value_counts().to_dict()
        known_industries = int((jobs["industry"] != "unknown").sum())
        print(f"  Industry coverage: {known_industries}/{len(jobs)} known; sources: {ind_src_counts}")
        print(f"  Kept {len(jobs)}/{before_rf}")
    filter_log = filter_log + [
        {
            "title": r["title"],
            "company": r["company"],
            "reason": (
                f"role_family:{r.get('role_family', '?')}"
                if r.get("gate") == "role_family"
                else f"seniority:{r.get('seniority_level', 'entry_level')}"
            ),
        }
        for r in rf_rejections
    ]
    filtered_count = len(filter_log)
    print(f"  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("All jobs hard-rejected by role-family gate. Exiting.")
        return

    # Step 4: Travel time
    print("\n[4/8] Estimating travel times...")
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

    # Step 4.2: Dedup against Sheet + fuzzy title/company dedup (no LLM, saves quota)
    print("\n[4.2/8] Deduplicating against Sheet + title/company...")
    t0 = time.time()
    import pandas as pd
    before_dedup = len(jobs)

    # 4.2a: Remove URLs already in the Sheet (they've been scored before)
    if spreadsheet_id:
        existing_urls = get_existing_urls(spreadsheet_id)
        jobs = jobs[~jobs["job_url"].isin(existing_urls)].reset_index(drop=True)
        sheet_removed = before_dedup - len(jobs)
        if sheet_removed:
            print(f"  Removed {sheet_removed} already-in-Sheet jobs (by URL)")

    # 4.2b: Fuzzy dedup by normalised title + company (same role, different tracking URLs)
    def _norm(s):
        return str(s).lower().strip()

    jobs["_tc_key"] = jobs["title"].map(_norm) + "|" + jobs["company"].map(_norm)
    before_fuzzy = len(jobs)
    jobs = jobs.drop_duplicates(subset=["_tc_key"], keep="first").reset_index(drop=True)
    jobs = jobs.drop(columns=["_tc_key"])
    fuzzy_removed = before_fuzzy - len(jobs)
    if fuzzy_removed:
        print(f"  Removed {fuzzy_removed} duplicate title+company jobs")

    print(f"  {before_dedup} -> {len(jobs)} jobs remaining  -> {time.time() - t0:.0f}s")
    if jobs.empty:
        print("All jobs already scored or deduped. Exiting.")
        return

    # Capture post-dedup count + distinct role groups for digest header
    total_new = len(jobs)
    role_groups = int(jobs["role_family"].nunique()) if "role_family" in jobs.columns else 0

    # Step 4.5: Pre-rank — cheap, rule-based scoring before the LLM
    print("\n[4.5/8] Applying pre-rank (rule-based, no LLM)...")
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

    # Step 4.8 (R3-05): Sub-scorers + aggregate → recommendation per row.
    # This is the new trust surface: role_fit / hard_skill / seniority_fit /
    # industry_proximity / desirability / evidence + final_score + Apply/Review/Skip.
    # Only `hard_skill` uses an LLM (skill extraction, cheap 2-provider chain,
    # cached by job_url + JD-hash). Heavy matcher LLM in step 5 is GATED on
    # the recommendation so Skip rows never consume quota.
    print("\n[4.8/8] Sub-scorers (role / hard-skill / seniority / industry / desirability / evidence)...")
    t0 = time.time()
    jobs, sub_counters = run_sub_scorers_and_print(jobs)
    print(f"  -> {time.time() - t0:.0f}s")

    # Split for matcher gating
    skip_mask = jobs["recommendation"] == "Skip"
    skipped_by_score = jobs[skip_mask].copy()
    jobs_for_llm = jobs[~skip_mask].reset_index(drop=True)

    # Honour --max-jobs on the Apply/Review pool only
    dropped_by_cap = pd.DataFrame()
    if max_jobs and len(jobs_for_llm) > max_jobs:
        jobs_for_llm, dropped_by_cap = split_by_prerank(jobs_for_llm, max_jobs)

    # Step 5: Heavy LLM (narrative / KeySkills / RoleSummary / dimensions).
    # Only Apply + Review survivors reach this stage.
    t0 = time.time()
    print(
        f"\n[5/8] Heavy LLM scoring: {len(jobs_for_llm)} jobs "
        f"(skipped {int(skip_mask.sum())} auto-Skip, "
        f"{len(dropped_by_cap)} below --max-jobs={max_jobs})"
    )
    print(
        f"  LLM evaluated (planned): {len(jobs_for_llm)} | "
        f"skipped by hard gates / auto-Skip: {int(skip_mask.sum()) + len(dropped_by_cap)} | "
        f"skill-extract LLM calls: {sub_counters['skill_llm_calls']} | "
        f"skill-extract cache hits: {sub_counters['skill_cache_hits']} | "
        f"skill-extract failures: {sub_counters['skill_extraction_failures']}"
    )
    if len(jobs_for_llm) > 0:
        jobs_for_llm = score_all_jobs(jobs_for_llm, min_score=min_score)
    print(f"  -> {time.time() - t0:.0f}s")

    # Bring the bypassed rows back so Sheets + digest still see them.
    # Legacy match_score / decision_hint are mirrored from final_score /
    # recommendation (Review → Maybe) until the R4-02 digest rewires these.
    for df_part, reason in (
        (skipped_by_score, "recommendation=Skip"),
        (dropped_by_cap, f"below_max_jobs_cap({max_jobs})"),
    ):
        if not df_part.empty:
            df_part["llm_stage_skipped_reason"] = reason
            df_part["match_score"] = df_part["final_score"]
            df_part["decision_hint"] = df_part["recommendation"].map(
                {"Apply": "Apply", "Review": "Maybe", "Skip": "Skip"}
            )

    jobs = pd.concat(
        [df for df in (jobs_for_llm, skipped_by_score, dropped_by_cap) if not df.empty],
        ignore_index=True,
    )
    if "match_score" in jobs.columns:
        jobs = jobs.sort_values("match_score", ascending=False).reset_index(drop=True)

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
    print("\n[6/8] Saving to Google Sheets...")
    t0 = time.time()
    if spreadsheet_id:
        append_jobs(jobs, spreadsheet_id)
    else:
        print("Google Sheets not configured. Saving CSV.")
        os.makedirs("output", exist_ok=True)
        jobs.to_csv(f"output/matches_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}.csv", index=False)

    print(f"  -> {time.time() - t0:.0f}s")

    # Step 7: Send email digest
    print("\n[7/8] Building digest...")
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

    # Trust-first digest (R4-02) when sub-score data is present, legacy otherwise.
    if "recommendation" in jobs.columns:
        from src.digest import (
            compute_bottleneck,
            compute_funnel,
            compute_pending_feedback,
            load_last_digest_snapshot,
            render_trust_digest,
            save_last_digest_snapshot,
            summarize_recent_feedback,
        )
        from src.feedback import load_feedback

        # Build funnel from the counters we've been tracking
        apply_df = jobs[jobs["recommendation"] == "Apply"]
        review_df = jobs[jobs["recommendation"] == "Review"]
        skip_df = jobs[jobs["recommendation"] == "Skip"]

        scored_mask = jobs["drop_stage"].astype(str) == "scored" if "drop_stage" in jobs.columns else None
        scored_df = jobs[scored_mask] if scored_mask is not None else jobs
        avg_hard_skill = (
            float(scored_df["score_hard_skill"].mean())
            if "score_hard_skill" in scored_df.columns and len(scored_df) > 0
            else None
        )

        funnel = compute_funnel(
            total_scraped=total_scraped,
            pre_scoring_filtered=filtered_count,
            scored_total=int(sub_counters.get("scored_ok", 0) + sub_counters.get("missing_jd", 0)),
            scored_but_skip=len(skip_df),
            below_max_jobs_cap=len(dropped_by_cap) if isinstance(dropped_by_cap, pd.DataFrame) else 0,
            apply_count=len(apply_df),
            review_count=len(review_df),
        )
        bottleneck_headline = compute_bottleneck(funnel, avg_hard_skill=avg_hard_skill)

        # Pending + summary: diff against previous snapshot
        last_snap = load_last_digest_snapshot()
        feedback_data = load_feedback()
        pending = compute_pending_feedback(last_snap, feedback_data)
        summary = summarize_recent_feedback(
            feedback_data,
            since_timestamp=last_snap.get("generated_at") if last_snap else None,
        )

        html = render_trust_digest(
            jobs,
            funnel=funnel,
            bottleneck_headline=bottleneck_headline,
            pending_feedback=pending,
            feedback_summary=summary,
            keyword_stats=keyword_stats,
            total_new=total_new,
            role_groups=role_groups,
            hours_window=24,
            llm_evaluated=int((jobs["scored_by_llm"] == True).sum()) if "scored_by_llm" in jobs.columns else 0,  # noqa: E712
            llm_skipped_by_gates=int(skip_mask.sum()) + int(len(dropped_by_cap)),
            sub_counters=sub_counters,
        )
        output_path = send_email(
            df=jobs,
            filtered_count=filtered_count,
            keyword_stats=keyword_stats,
            html_body=html,
        )

        # Save snapshot so next digest's "pending feedback" block knows what we showed.
        surfaced = pd.concat([apply_df, review_df], ignore_index=True)
        borderline = skip_df[skip_df["final_score"].between(45, 49)] if "final_score" in skip_df.columns else skip_df.iloc[:0]
        snapshot_rows = pd.concat([surfaced, borderline], ignore_index=True).to_dict(orient="records")
        save_last_digest_snapshot(snapshot_rows)
    else:
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
    parser.add_argument("--resend", action="store_true", help="Re-filter cached jobs with location filter, rescore top N, resend email (no Sheet write)")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum match score to include")
    parser.add_argument("--quick", action="store_true", help="Quick test: primary keywords only")
    parser.add_argument("--max-jobs", type=int, default=25, help="Max jobs to LLM-score (top N by pre-rank)")
    args = parser.parse_args()

    if args.rerank_only:
        run_rerank_only()
    elif args.resend:
        run_resend(max_jobs=args.max_jobs)
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
