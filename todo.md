# Jobsearcher — Todo

## Phase 1: MVP — Core Pipeline
- [x] Set up Python environment & dependencies
- [x] Create base CV in markdown from existing PDF
- [x] Implement JobSpy scraper wrapper (LinkedIn + Indeed + Glassdoor + Google)
- [x] Build pre-LLM filters (Dutch JD, empty descriptions, junior roles, language, driving licence, agency, Phygital/SaaS detection)
- [x] Implement multi-LLM router (OpenRouter free models with auto-rotation + Claude/Gemini/OpenAI fallback)
- [x] Build Phygital-weighted scoring framework (decision-first, 3-layer card output)
- [x] Travel time estimation (Hoofddorp station lookup table + 9292.nl links)
- [x] Email digest — 3-layer card layout (Apply/Maybe full cards, Skip compact rows)
- [x] Feedback loop (feedback_log.json for learning signals)
- [x] Deduplication (local JSON cache + Google Sheet URL tracking)
- [x] n8n workflow: daily 8AM cron + manual webhook trigger
- [x] End-to-end pipeline test (scrape -> filter -> score -> digest)

## Phase 1b: MVP — Remaining Setup (completed 2026-04-08)
- [x] Google Sheets integration (service account, sheet connected, dedup working)
- [x] Email delivery (Gmail app password configured, digests sent to iknoest@gmail.com)
- [x] Trafilatura fallback for missing descriptions (LinkedIn returns empty JDs)
- [x] Switch langdetect to langid (faster, deterministic Dutch detection)
- [x] Pre-filtered jobs log with per-job reasons (visible in pipeline output)
- [x] Pipeline phase timing (each step reports elapsed time)
- [x] jobspy metadata extraction (is_remote, salary, job_level as filter fallbacks)
- [x] Language Risk populated in Google Sheets (English OK / Dutch Mandatory / Dutch Preferred)

## Phase 1c: Sheet Quality, Feedback Loop & KM Visa (completed 2026-04-09)
- [x] Fix travel NaN -> empty string for unknown cities
- [x] Salary validation (reject garbage values like "4-3 EUR", swap inverted min/max)
- [x] KM Visa sponsor checking via IND register (download, cache, fuzzy match with rapidfuzz)
- [x] Fix Google Sheet empty cells (parse LLM JSON into 32 proper columns)
- [x] Two-way feedback loop (My Verdict/My Reason in Sheet, pattern detection, auto weight adjustment)
- [x] Email digest cleanup (remove Skip noise, add keyword stats table with Apply/Maybe per keyword)
- [x] Track and display jobs found per keyword in pipeline output
- [x] Schema documentation (docs/schema.md with column definitions, scoring logic, feedback loop)

## Phase 1d: Pipeline Ordering & Filter Hardening (completed 2026-04-11)
- [x] Language Pre-Filter as Step 2 — hard reject Dutch-mandatory jobs before scoring (issue #1)
- [x] Strengthen Phygital detection — require physical/hardware context, not just "IoT"/"AI"/"digital twin"
- [x] Separate language_prefilter() from enrich_and_filter() (two distinct pipeline stages)
- [x] Add pytest coverage for language_prefilter and detect_phygital (15 new tests)

## Phase 1e: Remaining Issues
- [ ] Fix LLM JSON parsing failures (some jobs return 0% with parse error)
- [ ] Run full keyword set (15 keywords across 3 tiers) — needs longer rate limit patience
- [ ] Re-enable Indeed/Google/Glassdoor platforms (fix hanging on Windows, add timeouts)
- [ ] Reduce LLM rate limiting impact (batch scoring or longer cooldowns)

## Phase 2: Resume Tailoring
- [ ] Resume tailoring per job match (generator.py)
- [ ] Cover letter generation per job match
- [ ] Output to Google Sheets with download links

## Phase 3: Enhancements
- [ ] Company enrichment (Glassdoor ratings, Google reviews, company size via web scraping)
- [ ] Startup sources (Wellfound, YC, TechLeap.nl)
- [ ] Notion integration for application tracking
- [ ] Career 覆盤 — AI career positioning based on cross-disciplinary background
- [x] ~~Google Sheets feedback sync~~ (replaced by two-way feedback loop in Phase 1c)
