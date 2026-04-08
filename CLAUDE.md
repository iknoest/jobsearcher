# Jobsearcher

Automated job search tool for the Netherlands market. Uses n8n + JobSpy + multi-LLM to scrape, filter, score, and deliver Phygital-weighted job matches daily.

## Architecture
- **Orchestration**: n8n (local Docker) triggers Python scripts; also supports manual CLI trigger
- **Scraping**: JobSpy library (LinkedIn active; Indeed/Google/Glassdoor disabled — hang on Windows) + **trafilatura fallback** for missing descriptions
- **Pre-filtering**: Python rule-based filters using **langid** (Dutch JD detection, Dutch title patterns, junior roles, language requirements, driving licence, agency detection, Phygital/SaaS classification). Returns per-job filter log with reasons.
- **Scoring**: Multi-LLM via OpenRouter (free models with auto-rotation fallback) — Phygital-weighted, decision-first card output. 15s cooldown between calls to avoid rate limits.
- **KM Visa**: IND recognized sponsor register (downloaded + cached locally, 30-day refresh, fuzzy match via rapidfuzz)
- **Storage**: Google Sheets (job tracking, deduplication, feedback sync, 32-column schema) + local JSON cache
- **Feedback Loop**: Two-way — user fills My Verdict/My Reason in Sheet, system syncs + detects patterns + auto-adjusts weights after 5 similar signals
- **Delivery**: Email digest (daily cron + manual trigger) — Apply/Maybe cards only, keyword stats table, Skip summary line
- **Timing**: Each pipeline phase reports elapsed time; keyword-level job counts displayed

## Scoring Framework
- Phygital relevance: 0-40 (physical+digital = high; pure software = low)
- Seniority fit: 0-20
- Domain relevance: 0-15
- Research ownership: 0-10
- Company fit: 0-10
- Language penalty: 0 to -15
- Driver licence penalty: 0 to -10
- Pure SaaS penalty: 0 to -40 (from feedback log)
- Decision: Apply (>=75), Maybe (60-74), Skip (<60)
- Blockers override score (Dutch mandatory, visa not supported, etc.)

## Key Constraints
- User speaks English, Chinese, Cantonese — NO Dutch
- Based in Hoofddorp, NL — travel time via public transport matters
- No driving licence — flag but do NOT hard-filter
- 8+ years experience — not junior, possibly senior
- KM visa eligibility as salary proxy (even though no sponsorship needed)
- Domain preference: Phygital (physical product + software/digital integration)
- LLM output: reasoning in Traditional Chinese, raw data in English

## Documentation Rules
- **After ANY code change that completes a feature, fix, or refactor**: automatically update CLAUDE.md, README.md, and todo.md. Do NOT wait to be asked.
- **After ANY task/plan execution completes**: update todo.md to check off completed items and add new items if discovered.
- Produce bilingual documentation (English + Chinese) unless told otherwise
- CLAUDE.md = project rules + architecture + constraints (for AI context)
- README.md = user-facing docs with bilingual headers (for humans)
- todo.md = progress tracking with phases and checkboxes

## Lessons Learned
- **LinkedIn via JobSpy always returns empty descriptions** — trafilatura fallback is mandatory, not optional
- **OpenRouter free tier rate limits aggressively** — 15s cooldown between calls, 7-model rotation needed, expect ~3 min per job scored. For 80 jobs = ~40 min.
- **Indeed/Google/Glassdoor scraping hangs on Windows** — needs per-request timeouts before re-enabling
- **Python stdout buffering on Windows** — always run with `PYTHONUNBUFFERED=1` and `-u` flag for background tasks
- **langdetect is non-deterministic** — replaced with langid for reliable Dutch detection
- **Glassdoor API is unstable** — keep disabled until they fix their API responses
- **IND sponsor register is an HTML table** — use trafilatura to parse, not a downloadable CSV
- **Company names in IND register include B.V./N.V.** — rapidfuzz fuzzy matching with suffix stripping needed
- **Feedback threshold = 5 similar disagreements** — before auto-adjusting weights by +/-5 points
- **Salary metadata from JobSpy can be garbage** — validate min > 100, swap if inverted, reject non-numeric

## CLI Flags
- `--manual` — Run full pipeline (scrape + filter + score + sheets + email)
- `--scrape-only` — Scrape and filter only, no LLM scoring
- `--quick` — Primary keywords only (5 instead of 15)
- `--max-jobs N` — Cap LLM scoring to N jobs (useful for testing)

## Platform Status
- **LinkedIn**: Active (via JobSpy). Descriptions always empty; trafilatura fetches from job URL.
- **Indeed**: Disabled (hangs on Windows, needs timeout fix)
- **Google Jobs**: Disabled (hangs on Windows, needs timeout fix)
- **Glassdoor**: Disabled (API errors)

## CI/CD
- Before pushing any CI fix, audit the ENTIRE pipeline for all potential failures
- Use service account auth for CI environments, never OAuth/interactive
