"""KM Visa sponsor checking — IND public register download + fuzzy match."""

import json
import re
import os
import time
from pathlib import Path

import trafilatura
from rapidfuzz import fuzz

REGISTER_URL = "https://ind.nl/en/public-register-recognised-sponsors/public-register-regular-labour-and-highly-skilled-migrants"
CACHE_PATH = Path("config/km_sponsors.json")
MAX_AGE_DAYS = 30

# Suffixes to strip for fuzzy matching
_STRIP_SUFFIXES = re.compile(
    r"\s*(b\.?v\.?|n\.?v\.?|holding|international|group|europe|nederland|netherlands)\s*",
    re.IGNORECASE,
)


def normalize_company_name(name):
    """Normalize company name for matching: lowercase, strip B.V./N.V./Holding etc."""
    name = name.lower().strip()
    name = _STRIP_SUFFIXES.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(".")
    return name


def download_ind_register():
    """Download IND recognized sponsor list and parse company names + KvK numbers.
    Returns dict of {normalized_name: kvk_number}.
    """
    print("  Downloading IND sponsor register...")
    downloaded = trafilatura.fetch_url(REGISTER_URL)
    if not downloaded:
        print("  WARNING: Could not fetch IND register")
        return {}

    text = trafilatura.extract(downloaded, output_format="txt", include_tables=True)
    if not text:
        print("  WARNING: Could not parse IND register")
        return {}

    sponsors = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("|---") or line.startswith("| Organisation"):
            continue
        # Handle markdown table format: | Company Name | 12345678 |
        if line.startswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                company = parts[0]
                kvk_candidate = parts[-1]
                if re.match(r"^\d{8}$", kvk_candidate):
                    normalized = normalize_company_name(company)
                    if len(normalized) >= 2:  # Skip garbage entries
                        sponsors[normalized] = kvk_candidate
                    continue
        # Fallback: plain text with KvK at end
        kvk_match = re.search(r"(\d{8})\s*$", line)
        if kvk_match:
            kvk = kvk_match.group(1)
            company = line[:kvk_match.start()].strip()
            if company:
                normalized = normalize_company_name(company)
                sponsors[normalized] = kvk

    print(f"  IND register: {len(sponsors)} sponsors loaded")
    return sponsors


def load_or_download_sponsors(force_refresh=False):
    """Load cached sponsor list or download fresh. Refresh if older than MAX_AGE_DAYS."""
    if not force_refresh and CACHE_PATH.exists():
        age_days = (time.time() - CACHE_PATH.stat().st_mtime) / 86400
        if age_days < MAX_AGE_DAYS:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  KM sponsors: loaded {len(data)} from cache ({age_days:.0f} days old)")
            return data

    sponsors = download_ind_register()
    if sponsors:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(sponsors, f, ensure_ascii=False, indent=2)
    return sponsors


def is_km_sponsor(company_name, sponsors=None):
    """Check if a company is a recognized KM visa sponsor using fuzzy matching."""
    if sponsors is None:
        sponsors = load_or_download_sponsors()

    if not sponsors or not company_name:
        return False

    normalized = normalize_company_name(company_name)

    # 1. Exact match on normalized name
    if normalized in sponsors:
        return True

    # 2. Substring match — only if shorter string is >50% of longer string length
    for sponsor_name in sponsors:
        shorter = min(len(sponsor_name), len(normalized))
        longer = max(len(sponsor_name), len(normalized))
        if shorter < 3 or shorter / longer < 0.5:
            continue
        if normalized in sponsor_name or sponsor_name in normalized:
            return True

    # 3. Fuzzy match — token_set_ratio handles word order and partial matches
    for sponsor_name in sponsors:
        score = fuzz.token_set_ratio(normalized, sponsor_name)
        if score >= 85:
            return True

    return False
