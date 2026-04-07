"""JobSpy wrapper — scrapes jobs from multiple platforms."""

import time

import pandas as pd
import trafilatura
from jobspy import scrape_jobs
import yaml


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


def scrape_all_keywords(config=None, tiers=None):
    """Scrape jobs for all configured keywords across all platforms.
    Returns a deduplicated DataFrame.

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

    all_jobs = []
    for keyword, tier in all_keywords:
        try:
            jobs = scrape_jobs(
                site_name=config["platforms"],
                search_term=keyword,
                location=config["location"],
                country_indeed=config.get("country_indeed", "Netherlands"),
                distance=config.get("distance_km", 50),
                results_wanted=config.get("results_per_keyword", 25),
                hours_old=config.get("hours_old", 24),
                job_type=config.get("job_type"),
                description_format="markdown",
            )
            jobs["search_keyword"] = keyword
            jobs["search_tier"] = tier
            all_jobs.append(jobs)
            print(f"  [{tier}] '{keyword}': {len(jobs)} jobs found")
        except Exception as e:
            print(f"  [{tier}] '{keyword}': ERROR - {e}")

    if not all_jobs:
        return pd.DataFrame()

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

    return combined


if __name__ == "__main__":
    df = scrape_all_keywords()
    print(df[["title", "company", "location", "search_keyword"]].head(20))
