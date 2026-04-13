# Jobsearcher

Automated job search tool for the Netherlands market. Uses n8n + JobSpy + multi-LLM to scrape, filter, score, and deliver Phygital-weighted job matches daily.

## Architecture
- **Orchestration**: n8n (local Docker) triggers Python scripts; also supports manual CLI trigger
- **Scraping**: JobSpy library (LinkedIn active; Indeed/Google/Glassdoor disabled — hang on Windows) + **trafilatura fallback** for missing descriptions
- **Language Pre-Filter (Step 2)**: Hard-rejects Dutch-mandatory jobs BEFORE any other processing. Rejects on: (a) explicit "Dutch required/mandatory/verplicht/vloeiend Nederlands", (b) JD primarily in Dutch (langid), (c) Dutch title keywords. "Dutch is a plus" passes through. Never scored.
- **Enrichment + Soft Filters (Step 3)**: Python rule-based filters using **langid** (junior role rejection, driving licence, agency detection, Phygital/SaaS classification, KM visa). Returns per-job filter log with reasons.
- **Pre-rank (Step 4.5)**: Rule-based scoring before LLM — uses enrichment flags (phygital, SaaS, driver licence), title keywords, preferred companies. Config in `config/prerank.yaml`. `--max-jobs N` picks top N by pre-rank, not first N scraped.
- **Scoring**: Multi-LLM via 5 providers with auto-fallback: Gemini 2.5 Flash (thinking disabled), Groq, NVIDIA NIM, Mistral, OpenRouter. Phygital-weighted, decision-first card output. 15s cooldown between calls.
- **KM Visa**: IND recognized sponsor register (downloaded + cached locally, 30-day refresh, fuzzy match via rapidfuzz)
- **Storage**: Google Sheets (job tracking, deduplication, feedback sync, 32-column schema) + local JSON cache
- **Feedback Loop**: Two-way — user fills My Verdict/My Reason in Sheet, system syncs + detects patterns + auto-adjusts weights after 5 similar signals
- **Delivery**: Email digest (daily cron + manual trigger) — Apply/Maybe cards only, keyword stats table, Skip summary line. Three action buttons per card: View (JD link), Apply (trigger CV tailoring), Skip (feedback)
- **KeySkills**: LLM extracts 5-8 key skills from each JD during scoring; displayed as chips in email digest
- **CV Tailoring (Phase 2)**: `src/generator.py` — LLM rewrites all CV sections + cover letter for a specific job. Auto-selects angle (Product Development Engineer / Strategic Product Lead / Hybrid) based on PhygitalAssessment + title keywords. Saves structured draft to `output/tailored/<job_id>/`
- **Review UI**: `src/app.py` — Flask server at localhost:5000. Three-panel editor: JD context | CV draft (editable) | Cover letter (editable). Section-level regeneration via LLM. PDF export via xhtml2pdf.
- **Skip Feedback**: Skip button in email opens quick-select reason page, writes to Google Sheets My Verdict/My Reason columns, feeds existing feedback loop
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

## Pipeline Order (enforced)
1. Scrape
2. **Language Pre-Filter** (hard reject)
3. Enrich + soft filters (agency, phygital, salary, KM visa, seniority)
4. Travel time
5. LLM scoring (extracts KeySkills, RoleSummary, WhyFit, Gaps)
6. Sheets sync
7. Email digest (View/Apply/Skip buttons per card, KeySkills chips)
8. **CV Tailoring** (on-demand — triggered by Apply button in email)

Language filtering MUST happen before scoring — never after. Phygital=True requires physical/hardware context; "IoT"/"AI"/"digital twin" alone are not sufficient.

## Lessons Learned
- **LinkedIn via JobSpy always returns empty descriptions** — trafilatura fallback is mandatory, not optional
- **OpenRouter free tier rate limits aggressively** — 15s cooldown between calls, 7-model rotation needed, expect ~3 min per job scored. For 80 jobs = ~40 min.
- **Indeed/Google/Glassdoor scraping hangs on Windows** — needs per-request timeouts before re-enabling
- **Python stdout buffering on Windows** — always run with `PYTHONUNBUFFERED=1` and `-u` flag for background tasks
- **langdetect is non-deterministic** — replaced with langid for reliable Dutch detection
- **langid log-prob is NOT a confidence score** — it scales with text length (longer text = more negative). Never threshold on it; just check if top language == "nl". The old `confidence > -100` threshold silently disabled Dutch JD detection for all full-length descriptions.
- **Gemini 2.5 Flash thinking tokens consume max_output_tokens budget** — must set `thinking_budget=0` for JSON generation, otherwise output gets truncated at ~200 chars
- **5 LLM providers configured** — Gemini (priority 1), Groq (priority 1), NVIDIA NIM (priority 2), Mistral (priority 2), OpenRouter (priority 3). Config in `config/llm_config.yaml`. Provider type `openai_compat` works for any OpenAI-compatible API with custom base_url.
- **Glassdoor API is unstable** — keep disabled until they fix their API responses
- **IND sponsor register is an HTML table** — use trafilatura to parse, not a downloadable CSV
- **Company names in IND register include B.V./N.V.** — rapidfuzz fuzzy matching with suffix stripping needed
- **Feedback threshold = 5 similar disagreements** — before auto-adjusting weights by +/-5 points
- **Salary metadata from JobSpy can be garbage** — validate min > 100, swap if inverted, reject non-numeric
- **weasyprint requires GTK on Windows** — switched to xhtml2pdf (pure Python) for PDF rendering
- **CV tailoring uses 1 LLM call per Apply click** — user-initiated only, no runaway quota usage

## CLI Flags
- `--manual` — Full pipeline (scrape + filter + prerank + LLM + sheets + email)
- `--no-score` — Scrape + filter + prerank + digest, skip LLM entirely (quota-free)
- `--rerank-only` — Re-apply prerank to cached enriched jobs (instant, no scrape, no LLM)
- `--scrape-only` — Scrape and filter only, no prerank, no LLM
- `--quick` — Primary keywords only (6 instead of 16)
- `--max-jobs N` — LLM-score top N by pre-rank (not first N scraped)
- `python src/generator.py --job-url <url>` — Tailor CV + cover letter for a specific job
- `python src/app.py` — Start Flask review server on localhost:5000

## Platform Status
- **LinkedIn**: Active (via JobSpy). Descriptions always empty; trafilatura fetches from job URL.
- **Indeed**: Disabled (hangs on Windows, needs timeout fix)
- **Google Jobs**: Disabled (hangs on Windows, needs timeout fix)
- **Glassdoor**: Disabled (API errors)

## CI/CD
- Before pushing any CI fix, audit the ENTIRE pipeline for all potential failures
- Use service account auth for CI environments, never OAuth/interactive
