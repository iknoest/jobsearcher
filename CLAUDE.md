# Jobsearcher

Automated job search tool for the Netherlands market. Uses n8n + JobSpy + multi-LLM to scrape, filter, score, and deliver Phygital-weighted job matches daily.

## Architecture
- **Orchestration**: n8n (local Docker) triggers Python scripts; also supports manual CLI trigger
- **Scraping**: JobSpy library (LinkedIn active; Indeed/Google/Glassdoor disabled — hang on Windows) + **trafilatura fallback** for missing descriptions
- **Pre-filtering**: Python rule-based filters using **langid** (Dutch JD detection, Dutch title patterns, junior roles, language requirements, driving licence, agency detection, Phygital/SaaS classification). Returns per-job filter log with reasons.
- **Scoring**: Multi-LLM via OpenRouter (free models with auto-rotation fallback) — Phygital-weighted, decision-first card output. 15s cooldown between calls to avoid rate limits.
- **Storage**: Google Sheets (job tracking, deduplication, feedback sync) + local JSON cache
- **Delivery**: Email digest (daily cron + manual trigger) with 3-layer card layout
- **Timing**: Each pipeline phase reports elapsed time

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

## Documentation
- When updating any doc file, ALWAYS check and update: CLAUDE.md, README.md, todo.md
- Produce bilingual documentation (English + Chinese) unless told otherwise

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
