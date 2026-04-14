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

## Phase 1e: LLM Reliability & Pre-rank (completed 2026-04-12)
- [x] Fix LLM JSON parsing — Gemini 2.5 Flash thinking tokens consumed output budget; disabled thinking for JSON generation
- [x] Fix Dutch JD detection — langid log-prob threshold `> -100` was silently disabling detection for full-length JDs; removed threshold
- [x] Add UTF-8 stdout reconfigure for Windows (Traditional Chinese from LLM no longer crashes)
- [x] Remove dead OpenRouter model (stepfun/step-3.5-flash:free)
- [x] Wire up 5 LLM providers: Gemini 2.5 Flash, Groq, NVIDIA NIM, Mistral, OpenRouter (auto-fallback)
- [x] Pre-rank system (config/prerank.yaml) — rule-based scoring before LLM; --max-jobs N picks top N by pre-rank
- [x] --no-score mode (scrape + filter + prerank + digest, skip LLM entirely)
- [x] --rerank-only mode (re-apply prerank to cached enriched jobs, instant, no scrape/LLM)
- [x] Cache enriched DataFrame to output/enriched_jobs.pkl after step 3
- [x] Pre-rank digest (HTML table with scores/reasons, no LLM fields needed)
- [x] Add "Product Engineer" to primary search keywords (found Quooker 82% Apply)
- [x] 13 pre-rank tests, 53 total tests passing

## Phase 1f: Digest Quality & LLM Card Fixes (completed 2026-04-12)
- [x] Fix KM Visa chip — use `km_visa_sponsor OR km_visa_mentioned` (IND register, not just JD text)
- [x] Add RoleSummary field to SCORING_PROMPT — 1-2 English sentences of daily duties + specific tools
- [x] Add RULE 8 to SCORING_PROMPT — WhyFit must be employer-facing; forbidden: generic seniority, filter logic (no Dutch / NL location / no licence)
- [x] Add RULE 9 to SCORING_PROMPT — Gaps only for explicit JD requirements Ava can't meet; never fabricate; empty array if no real gaps
- [x] Add role_summary to notifier _parse_card() and EMAIL_TEMPLATE (italic muted block above WhyFit)
- [x] Tighten StrongMatch/PartialMatch/Gaps schema descriptions to enforce quality rules

## Phase 2: CV Tailoring (REMOVED 2026-04-14)
- Deleted: `src/generator.py`, `src/app.py`, all `templates/cv_*`, `cl_*`, `review.html`, `skip.html`, `cover_letter.md`, `profile/cv_base.md`, `profile/cover_letter_reference.md`, `MasterCV_Coverletter/`, `output/tailored/`, `tests/test_generator.py`, `scripts/export_tailored_pdfs.py`
- Reason: LLM-generated CVs could not hit the quality bar (fabricated skills, wrong experience order, poor layout, no photo). Maintenance cost > value. See `lessons.md`.
- Scoring-phase KeySkills extraction and chip display are **kept** — they're useful signal regardless.

## Phase A: Cleanup (in progress — this commit)
- [x] Delete CV tailoring files
- [x] `.gitignore` `MasterCV_Coverletter/` (local only)
- [x] Remove Apply/Skip buttons from email digest (View only until Phase D)
- [x] Strip Phase-2 sections from `CLAUDE.md`, `README.md`
- [x] Create `lessons.md` and migrate lessons out of `CLAUDE.md`
- [x] Add `lessons.md` to auto-update rule

## Phase B: Sheets schema (completed 2026-04-15)
- [x] Add `Inbox` tab (drop URLs or raw JD text; pipeline picks up Pending rows on next run)
- [x] Add `Rules` tab (dynamic weight/keyword/company overrides; human-editable + TG-writable)
- [x] Add `Confidence` column to `Jobs` (High/Med/Low from LLM)
- [x] Teach scoring prompt to set Confidence explicitly (TrustNotes.Confidence field)
- [x] `setup_tabs()` — creates tabs lazily on every run; seeds Rules with example rows
- [x] `poll_inbox()` — reads Pending rows, scrapes URLs via trafilatura if no Raw JD
- [x] `read_rules()` — merges sheet-based weight_adjustments with learned ones
- [x] Inbox jobs flow through full pipeline (filter → prerank → LLM → Sheets); rows marked Scored on completion

## Phase C: Telegram bot
- [ ] Dedicated jobsearcher bot (separate token from the existing `plugin:telegram` bot)
- [ ] Long-poll handler `src/tg_bot.py` — writes every command into Sheets tabs
- [ ] Commands: `/good`, `/skip`, `/uncertain`, `/score`, `/block`, `/addkw`, `/rmkw`, `/wait`, `/ok`, `/rules`, `/stats`
- [ ] Sheets-edit ingestion (same commands work as `Rules` tab rows)

## Phase D: Email redesign
- [ ] Three-tier digest: Apply · Maybe (conditional on Apply<5) · **Uncertain** (LLM low-confidence, any score)
- [ ] Skip tier aggregated, not per-row
- [ ] TG deep-link buttons: `View` · `Good match` · `Skip (reason)`

## Phase E: Learning tweaks
- [ ] 1-sample weight nudge by default (±3 pts)
- [ ] Auto-pattern detection across 3 similar verdicts (±8 pts + TG notify)
- [ ] `/wait <pattern>` freeze + `/ok <pattern>` unfreeze

## Phase F: Ad-hoc scoring
- [ ] `Inbox` tab poller integrated into pipeline
- [ ] `/score <url|text>` — returns score + which filter would reject (if any)

## Phase 1g: Remaining (still relevant)
- [ ] Run full keyword set (15 keywords across 3 tiers) — now feasible with pre-rank filtering
- [ ] Re-enable Indeed/Google/Glassdoor platforms (fix hanging on Windows, add timeouts)
- [ ] Fix Google Sheets feedback sync (header row has duplicate empty cells)
- [ ] Add short-circuit: if all LLM providers rate-limited once, bail out of scoring loop
- [ ] Populate industry_bonus / skill_bonus in `config/prerank.yaml` based on scoring patterns

## Phase 3: Enhancements
- [ ] Company enrichment (Glassdoor ratings, Google reviews, company size via web scraping)
- [ ] Startup sources (Wellfound, YC, TechLeap.nl)
- [ ] Notion integration for application tracking
- [ ] Career 覆盤 — AI career positioning based on cross-disciplinary background
- [x] ~~Google Sheets feedback sync~~ (replaced by two-way feedback loop in Phase 1c)
