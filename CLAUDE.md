# Jobsearcher

Automated job search tool for the Netherlands market. Scrapes, filters, and scores jobs daily; delivers a Phygital-weighted digest by email. CV tailoring was removed 2026-04-14 — the tool now focuses purely on surfacing good matches and learning from fast feedback.

## Architecture
- **Orchestration**: n8n (local Docker) triggers Python scripts; also supports manual CLI trigger. Target host for daily schedule: Raspberry Pi 5 (planned).
- **Scraping**: JobSpy (LinkedIn + Indeed + Google active with 90s per-platform timeout; Glassdoor disabled — API errors) + **trafilatura fallback** for missing descriptions. JobSpy's LinkedIn scraper is **monkey-patched** (`src/jobspy_patches.py`) to inject LinkedIn's `geoId` (e.g. Netherlands=102890719) into the search URL, which raises NL hit rate from ~30% to ~100% at source. Post-scrape location filter (Step 2.5) is still kept as a safety net.
- **Language Pre-Filter (Step 2)**: Hard-rejects Dutch-mandatory jobs. Rejects on: (a) explicit "Dutch required/mandatory/verplicht/vloeiend Nederlands", (b) JD primarily in Dutch (langid), (c) Dutch title keywords. "Dutch is a plus" passes through.
- **Location Filter (Step 2.5)**: Hard-rejects jobs outside the Netherlands that are not remote. Keeps: location ending in ", NL" / "NL" / "Netherlands" / "Nederland", is_remote=True, or empty location. Rejects all others (e.g. "New York, NY", "Singapore"). Blocked ~200/464 jobs in first full run.
- **Enrichment + Soft Filters (Step 3)**: Rule-based filters via langid (junior role rejection, driving licence, agency detection, Phygital/SaaS classification, KM visa). Per-job filter log with reasons.
- **Pre-rank (Step 4.5)**: Rule-based scoring before LLM. Config in `config/prerank.yaml`. `--max-jobs N` picks top N by pre-rank.
- **Scoring**: Multi-LLM via 5 providers with auto-fallback: Gemini 2.5 Flash, Groq, NVIDIA NIM, Mistral, OpenRouter. Phygital-weighted decision card + 7-dim work-style block. 15s cooldown between calls.
- **Preferences (editable without Python)**: `config/preferences.yaml` holds seniority target/caution, preferred_loop, prefer/avoid/open_to lists, 7-dimension rubric, V2 feedback-loop weights. Injected into SCORING_PROMPT at scoring time.
- **KM Visa**: IND recognized sponsor register (cached locally, 30-day refresh, rapidfuzz match).
- **Storage**: Google Sheets (job tracking, dedup, feedback sync) + local JSON cache.
- **Feedback Loop (redesign in progress)**: Single source of truth = Google Sheets. Telegram bot is a thin client writing into the same Sheets tabs. Planned tabs: `Jobs`, `Inbox` (ad-hoc JD drop), `Rules` (dynamic weights + keyword overrides), `Feedback` (append-only verdict log). 1-sample weight nudge by default; autonomous pattern detection on top.
- **Delivery**: Email digest (daily cron + manual). Apply/Maybe cards + "Uncertain" section (LLM self-flags low confidence). Skip tier is aggregated, not per-row.

## Scoring Framework
- **Score (0-100)** — used for ranking. Phygital 0-40 · Seniority 0-20 · Domain 0-15 · Research 0-10 · Company 0-10 · Language -15 · Driver -10 · Pure SaaS -40.
- **7 Dimensions (0-5 each)** — used for decision quality, not ranking. Higher-better: ResearchFit, InnovationFit, HandsOnProximity, StrategyFit, TeamStyleFit. Lower-better (shown as "risk"): AlignmentBurden → "Alignment risk", ProcessHeaviness → "Process risk".
- **AntiPatternRisk** — Low/Medium/High summary of JD org signals (matrix, waterfall, sales-led, PPT-only strategy). Can downgrade an otherwise good role.
- **SuggestedAction (deterministic, post-LLM)** — 6 labels: `Strong Apply` (≥85 + Low risk), `Apply` (≥75 + Low/Med risk), `Review` (60-74 or mismatches), `Skip` (<60 or blocker), `Good role, risky org` (≥75 + High risk), `Seniority mismatch` (Head of / VP of / Director of caught via title regex).
- **DecisionHint (legacy tier)** — Apply (≥75), Maybe (60-74), Skip (<60). Kept for routing/stats.
- Blockers override score (Dutch mandatory, visa not supported, etc.).

## Key Constraints
- User speaks English, Chinese, Cantonese — NO Dutch
- Based in Hoofddorp, NL — travel time via public transport matters
- No driving licence — flag but do NOT hard-filter
- 8+ years experience — not junior, possibly senior
- KM visa eligibility as salary proxy (even though no sponsorship needed)
- Domain preference: Phygital (physical product + software/digital integration)
- LLM output: reasoning in Traditional Chinese, raw data in English

## Autonomous Action Policy
- **If Claude can do it, Claude does it** — do not ask the user to run commands, trigger pipelines, or copy-paste if it can be done directly via tools. This includes: running the pipeline, resending emails, running tests, fixing bugs end-to-end, updating docs.
- **Only ask the user** when: (a) action requires their credentials/interactive login, (b) action is irreversible and outside project scope (e.g. deleting data), or (c) genuine ambiguity about intent exists.
- **Always confirm completion** — after taking action, report what was done and the result.

## Documentation Rules
- **After ANY code change that completes a feature, fix, or refactor**: automatically update `CLAUDE.md`, `README.md`, `todo.md`, and `lessons.md`. Do NOT wait to be asked.
- **After ANY task/plan execution completes**: update `todo.md` to check off completed items and add new items if discovered.
- **After ANY bug fix or non-obvious decision**: append a one-line entry to `lessons.md` in the relevant section.
- Produce bilingual documentation (English + Chinese) unless told otherwise.
- `CLAUDE.md` = project rules + architecture + constraints (for AI context)
- `README.md` = user-facing docs with bilingual headers (for humans)
- `todo.md` = progress tracking with phases and checkboxes
- `lessons.md` = append-only log of non-obvious decisions and incidents

## Pipeline Order (enforced)
1. Scrape
2. **Language Pre-Filter** (hard reject: Dutch JD, Dutch mandatory, Dutch title)
2.5. **Location Filter** (hard reject: non-NL and non-remote jobs)
3. Enrich + soft filters (agency, phygital, salary, KM visa, seniority)
3b. **Classification stage** (PRD §8 Stage 4):
   - role-family classifier → drops excluded-without-rescue jobs
   - industry classifier → attaches proximity signal (never drops)
   - seniority classifier → drops explicit early-career only (intern/trainee/graduate-program); junior/head-of/mid-level are flagged, not dropped
4. Travel time
4.2. Sheet + fuzzy dedup (remove already-scored jobs by URL, then title+company)
4.5. Pre-rank (rule-based scoring, no LLM)
5. LLM scoring (KeySkills, RoleSummary, WhyFit, Gaps, Confidence)
6. Sheets sync
7. Email digest (View button per card; Good match / Skip via Telegram deep links)

Language + location filtering MUST happen before scoring — never after. Phygital=True requires physical/hardware context; "IoT"/"AI"/"digital twin" alone are not sufficient.

## CLI Flags
- `--manual` — Full pipeline (scrape + filter + prerank + LLM + sheets + email)
- `--no-score` — Scrape + filter + prerank + digest, skip LLM entirely (quota-free)
- `--rerank-only` — Re-apply prerank to cached enriched jobs (instant, no scrape, no LLM)
- `--scrape-only` — Scrape + filter only, no prerank, no LLM
- `--quick` — Primary keywords only (6 instead of 16)
- `--max-jobs N` — LLM-score top N by pre-rank (not first N scraped)

## Platform Status
- **LinkedIn**: Active (via JobSpy). Descriptions empty; trafilatura fetches from job URL.
- **Indeed**: Active — 90s per-keyword timeout via ThreadPoolExecutor; skipped silently if hangs.
- **Google Jobs**: Active — same timeout protection as Indeed.
- **Glassdoor**: Disabled (API errors)

## CI/CD
- Before pushing any CI fix, audit the ENTIRE pipeline for all potential failures.
- Use service account auth for CI environments, never OAuth/interactive.

## Historical Notes
Detailed incident history and root causes live in [`lessons.md`](lessons.md).
