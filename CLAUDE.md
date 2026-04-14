# Jobsearcher

Automated job search tool for the Netherlands market. Scrapes, filters, and scores jobs daily; delivers a Phygital-weighted digest by email. CV tailoring was removed 2026-04-14 — the tool now focuses purely on surfacing good matches and learning from fast feedback.

## Architecture
- **Orchestration**: n8n (local Docker) triggers Python scripts; also supports manual CLI trigger. Target host for daily schedule: Raspberry Pi 5 (planned).
- **Scraping**: JobSpy (LinkedIn active; Indeed/Google/Glassdoor disabled — hang on Windows) + **trafilatura fallback** for missing descriptions.
- **Language Pre-Filter (Step 2)**: Hard-rejects Dutch-mandatory jobs. Rejects on: (a) explicit "Dutch required/mandatory/verplicht/vloeiend Nederlands", (b) JD primarily in Dutch (langid), (c) Dutch title keywords. "Dutch is a plus" passes through.
- **Enrichment + Soft Filters (Step 3)**: Rule-based filters via langid (junior role rejection, driving licence, agency detection, Phygital/SaaS classification, KM visa). Per-job filter log with reasons.
- **Pre-rank (Step 4.5)**: Rule-based scoring before LLM. Config in `config/prerank.yaml`. `--max-jobs N` picks top N by pre-rank.
- **Scoring**: Multi-LLM via 5 providers with auto-fallback: Gemini 2.5 Flash, Groq, NVIDIA NIM, Mistral, OpenRouter. Phygital-weighted decision card. 15s cooldown between calls.
- **KM Visa**: IND recognized sponsor register (cached locally, 30-day refresh, rapidfuzz match).
- **Storage**: Google Sheets (job tracking, dedup, feedback sync) + local JSON cache.
- **Feedback Loop (redesign in progress)**: Single source of truth = Google Sheets. Telegram bot is a thin client writing into the same Sheets tabs. Planned tabs: `Jobs`, `Inbox` (ad-hoc JD drop), `Rules` (dynamic weights + keyword overrides), `Feedback` (append-only verdict log). 1-sample weight nudge by default; autonomous pattern detection on top.
- **Delivery**: Email digest (daily cron + manual). Apply/Maybe cards + "Uncertain" section (LLM self-flags low confidence). Skip tier is aggregated, not per-row.

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
2. **Language Pre-Filter** (hard reject)
3. Enrich + soft filters (agency, phygital, salary, KM visa, seniority)
4. Travel time
5. LLM scoring (KeySkills, RoleSummary, WhyFit, Gaps, Confidence)
6. Sheets sync
7. Email digest (View button per card; Good match / Skip handled via Telegram deep links once Phase C lands)

Language filtering MUST happen before scoring — never after. Phygital=True requires physical/hardware context; "IoT"/"AI"/"digital twin" alone are not sufficient.

## CLI Flags
- `--manual` — Full pipeline (scrape + filter + prerank + LLM + sheets + email)
- `--no-score` — Scrape + filter + prerank + digest, skip LLM entirely (quota-free)
- `--rerank-only` — Re-apply prerank to cached enriched jobs (instant, no scrape, no LLM)
- `--scrape-only` — Scrape + filter only, no prerank, no LLM
- `--quick` — Primary keywords only (6 instead of 16)
- `--max-jobs N` — LLM-score top N by pre-rank (not first N scraped)

## Platform Status
- **LinkedIn**: Active (via JobSpy). Descriptions empty; trafilatura fetches from job URL.
- **Indeed**: Disabled (hangs on Windows, needs timeout fix)
- **Google Jobs**: Disabled (hangs on Windows, needs timeout fix)
- **Glassdoor**: Disabled (API errors)

## CI/CD
- Before pushing any CI fix, audit the ENTIRE pipeline for all potential failures.
- Use service account auth for CI environments, never OAuth/interactive.

## Historical Notes
Detailed incident history and root causes live in [`lessons.md`](lessons.md).
