"""Monkey-patches for the vendored JobSpy library.

JobSpy's LinkedIn scraper passes only `location` as a text string, which LinkedIn's
guest API interprets loosely — it returns global results. This module patches the
scraper to additionally inject LinkedIn's `geoId` query parameter so LinkedIn
filters at source, not post-scrape.

For Netherlands: geoId 102890719. See `LINKEDIN_GEOID_BY_COUNTRY` below to extend.

Import this module once at application startup (before any `scrape_jobs` call).
"""

from __future__ import annotations

import jobspy.linkedin as _linkedin_module
from jobspy.linkedin import LinkedIn as _LinkedIn


LINKEDIN_SEARCH_PATH = "/jobs-guest/jobs/api/seeMoreJobPostings/search"

# LinkedIn country-level geoIds. Verify new ones by searching jobs on
# linkedin.com and reading the `geoId=` param in the resulting URL.
LINKEDIN_GEOID_BY_COUNTRY = {
    "netherlands":  "102890719",
    "nederland":    "102890719",
    "nl":           "102890719",
    "germany":      "101282230",
    "belgium":      "100565514",
    "france":       "105015875",
    "united kingdom": "101165590",
}


def _geoid_for_location(location: str | None) -> str | None:
    if not location:
        return None
    key = location.strip().lower()
    return LINKEDIN_GEOID_BY_COUNTRY.get(key)


def _patch_linkedin_session_get():
    """Wrap LinkedIn.__init__ so that every LinkedIn scraper instance gets a
    session whose .get() transparently injects geoId into the jobs search URL.
    """
    original_init = _LinkedIn.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        original_get = self.session.get

        def get_with_geoid(url, *a, **kw):
            if LINKEDIN_SEARCH_PATH in str(url):
                params = kw.get("params") or {}
                if "geoId" not in params:
                    geo = _geoid_for_location(params.get("location"))
                    if geo:
                        # requests merges dicts; safe to copy+add
                        params = {**params, "geoId": geo}
                        kw["params"] = params
            return original_get(url, *a, **kw)

        self.session.get = get_with_geoid

    _LinkedIn.__init__ = patched_init


_patch_linkedin_session_get()
