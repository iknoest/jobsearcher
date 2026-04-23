"""JobSpy wrapper — scrapes jobs from multiple platforms."""

import concurrent.futures
import time

import pandas as pd
import trafilatura
from . import jobspy_patches  # noqa: F401 — monkey-patches LinkedIn geoId before scrape_jobs import
from jobspy import scrape_jobs
import yaml

# Per-platform timeout (seconds). LinkedIn is fast; Indeed/Google can hang on Windows.
_PLATFORM_TIMEOUT = 90


def load_search_config(config_path="config/search.yaml"):
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fetch_description_fallback(job_url):
    """Fetch full job description from URL when jobspy returns empty/short text."""
    try:
        downloaded = trafilatura.fetch_url(job_url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text and len(text) > 200:
                return text
    except Exception:
        pass
    return None


def _scrape_one_platform(platform, keyword, config):
    """Scrape a single platform for one keyword. Called in a thread."""
    return scrape_jobs(
        site_name=[platform],
        search_term=keyword,
        location=config["location"],
        country_indeed=config.get("country_indeed", "Netherlands"),
        distance=config.get("distance_km", 50),
        results_wanted=config.get("results_per_keyword", 25),
        hours_old=config.get("hours_old", 24),
        job_type=config.get("job_type"),
        description_format="markdown",
    )


def _scrape_with_timeout(platform, keyword, config, timeout=_PLATFORM_TIMEOUT):
    """Scrape one platform with a timeout. Returns empty DataFrame on hang/error."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_scrape_one_platform, platform, keyword, config)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print(f"    TIMEOUT ({timeout}s) — skipping {platform} for '{keyword}'")
            return pd.DataFrame()
        except Exception as e:
            print(f"    ERROR on {platform} for '{keyword}': {e}")
            return pd.DataFrame()


def scrape_all_keywords(config=None, tiers=None):
    """Scrape jobs for all configured keywords across all platforms.
    Returns a deduplicated DataFrame.

    Each platform is scraped independently with a per-call timeout so a
    hanging Indeed/Google call does not block LinkedIn results.

    Args:
        tiers: list of tiers to include e.g. ["primary"] for quick test runs.
               Defaults to all tiers.
    """
    start = time.time()

    if config is None:
        config = load_search_config()

    if tiers is None:
        tiers = ["primary", "secondary", "exploratory"]

    all_keywords = []
    for tier in tiers:
        all_keywords.extend(
            [(kw, tier) for kw in config["search_keywords"].get(tier, [])]
        )

    platforms = config["platforms"]

    all_jobs = []
    for keyword, tier in all_keywords:
        kw_frames = []
        for platform in platforms:
            df = _scrape_with_timeout(platform, keyword, config)
            if not df.empty:
                kw_frames.append(df)

        if not kw_frames:
            print(f"  [{tier}] '{keyword}': 0 jobs (all platforms failed/timed out)")
            continue

        jobs = pd.concat(kw_frames, ignore_index=True)
        # Dedup within keyword across platforms
        jobs = jobs.drop_duplicates(subset=["job_url"], keep="first")
        jobs["search_keyword"] = keyword
        jobs["search_tier"] = tier
        all_jobs.append(jobs)
        print(f"  [{tier}] '{keyword}': {len(jobs)} jobs found")

    if not all_jobs:
        return pd.DataFrame(), {}

    # Track per-keyword counts (before dedup)
    keyword_counts = {}
    for kw_df in all_jobs:
        if not kw_df.empty:
            kw = kw_df["search_keyword"].iloc[0]
            keyword_counts[kw] = keyword_counts.get(kw, 0) + len(kw_df)

    combined = pd.concat(all_jobs, ignore_index=True)
    # Deduplicate by job URL
    combined = combined.drop_duplicates(subset=["job_url"], keep="first")
    print(f"\nTotal unique jobs: {len(combined)}")

    # Fallback: fetch full descriptions for jobs with short/missing text
    fallback_count = 0
    for idx, row in combined.iterrows():
        desc = str(row.get("description", ""))
        if not desc or desc.strip() in ("", "None", "nan") or len(desc.strip()) < 200:
            if "see more" in desc.lower() or len(desc.strip()) < 200:
                full_text = _fetch_description_fallback(str(row.get("job_url", "")))
                if full_text:
                    combined.at[idx, "description"] = full_text
                    fallback_count += 1
    if fallback_count:
        print(f"  Trafilatura fallback: enriched {fallback_count} descriptions")

    meta_cols = ["company_industry", "company_num_employees", "is_remote", "job_level", "min_amount", "max_amount"]
    available = [c for c in meta_cols if c in combined.columns]
    non_null = {c: combined[c].notna().sum() for c in available}
    print(f"  Metadata available: {non_null}")

    elapsed = time.time() - start
    print(f"  Scraping took {elapsed:.0f}s")

    return combined, keyword_counts


if __name__ == "__main__":
    df, _ = scrape_all_keywords()
    print(df[["title", "company", "location", "search_keyword"]].head(20))
