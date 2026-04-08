# Jobsearcher / 求職助手

Automated Phygital-focused job search tool for the Netherlands market, with smart filtering and AI-powered decision-first scoring.

自動化荷蘭求職工具，專注 Phygital（實體+數位）領域，具備智能篩選和AI決策優先評分。

## Features / 功能

### MVP (Working)
- **Multi-platform scraping** — LinkedIn via JobSpy + **trafilatura fallback** for full job descriptions (Indeed/Google/Glassdoor planned)
- **Smart pre-filtering** — Dutch JD detection via **langid** (fast, deterministic), Dutch title patterns, junior roles, language requirements, agency detection, Phygital/SaaS classification. Pre-filtered jobs logged with reasons.
- **Phygital-weighted scoring** — Decision-first card output with 3-layer design (Decision -> Explanation -> Detail)
- **Multi-LLM rotation** — OpenRouter free models with auto-fallback across 7 models; also supports Claude/Gemini/OpenAI API
- **Travel time estimation** — Public transport from Hoofddorp to company (arriving 09:00 weekday)
- **3-layer email digest** — Apply/Maybe as full decision cards, keyword stats table, Skip summary only (details in Sheet)
- **KM Visa sponsor checking** — IND recognized sponsor register (fuzzy match, cached locally, 30-day refresh via rapidfuzz)
- **Google Sheets integration** — 32-column schema with LLM-extracted fields (Industry, Phygital Level, Why Fit, Gaps, Confidence)
- **Two-way feedback loop** — User fills My Verdict + My Reason in Sheet; system detects patterns and auto-adjusts scoring weights after 5 similar signals
- **Salary validation** — Rejects garbage values, validates min/max ranges, swaps if inverted
- **Deduplication** — Local JSON cache + Google Sheet URL tracking
- **Pipeline timing** — Each phase reports elapsed time for performance visibility
- **jobspy metadata** — is_remote, salary, job_level extracted and used as filter fallbacks

### Scoring Framework / 評分框架
| Dimension | Weight | Description |
|-----------|--------|-------------|
| Phygital relevance | 0-40 | Physical+digital integration strength |
| Seniority fit | 0-20 | 8+ years, senior/lead level |
| Domain relevance | 0-15 | Product research, UX, IoT, automotive |
| Research ownership | 0-10 | Owns research vs. just executes |
| Company fit | 0-10 | Innovation-driven, culture signals |
| Language penalty | 0 to -15 | Dutch mandatory = -15 |
| Driver licence | 0 to -10 | Required = -10 |
| Pure SaaS penalty | 0 to -40 | From user feedback log |

Decision: **Apply** (>=75) · **Maybe** (60-74) · **Skip** (<60). Blockers override score.

> For full column definitions, scoring logic, and feedback loop details, see [docs/schema.md](docs/schema.md).

### Future / 未來功能
- Resume & cover letter tailoring per job match
- Career 覆盤 (retrospective) — AI-guided career positioning
- Startup discovery — Wellfound, YC, TechLeap.nl
- Notion integration for application tracking
- Company enrichment (Glassdoor ratings, Google reviews)

## Tech Stack / 技術棧

| Component | Tool |
|-----------|------|
| Orchestration | n8n (local Docker) or manual CLI |
| Scraping | JobSpy (Python) + trafilatura (description fallback) |
| Language Detection | langid (Dutch JD pre-filter) |
| LLM | OpenRouter (free: Qwen 3.6+, Nemotron, Gemma, Llama) + Claude/Gemini/OpenAI fallback |
| KM Visa | IND register + rapidfuzz (fuzzy company name matching) |
| Storage | Google Sheets (32-col schema) + local JSON |
| Feedback | Two-way: Sheet verdicts -> pattern detection -> auto weight adjustment |
| Notifications | Email (Gmail) + local HTML digest |
| Travel | 9292.nl links + distance lookup table |

## Quick Start / 快速開始

```bash
# 1. Clone & setup
git clone <repo-url> && cd Jobsearcher
python -m venv venv && source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — at minimum set OPENROUTER_API_KEY (free at openrouter.ai)

# 3. Test scrape only (no LLM costs)
python src/main.py --scrape-only

# 4. Full pipeline (scrape + filter + LLM score + digest)
python src/main.py --manual

# 5. Quick test (primary keywords only, cap 5 jobs)
python src/main.py --manual --quick --max-jobs 5

# 6. (Optional) Start n8n for daily automation
cd n8n && docker-compose up -d
# Open http://localhost:5678 and import n8n/workflow.json
```

## Project Structure / 項目結構

```
Jobsearcher/
├── src/
│   ├── main.py              # Entry point (--manual, --scrape-only)
│   ├── scraper.py           # JobSpy wrapper (multi-keyword, multi-platform)
│   ├── filters.py           # Pre-LLM filtering & enrichment (Dutch, seniority, Phygital, agency, etc.)
│   ├── matcher.py           # LLM scoring — Phygital-weighted, 3-layer card output
│   ├── generator.py         # Resume & cover letter tailoring (Phase 2)
│   ├── travel.py            # Travel time estimation from Hoofddorp
│   ├── sheets.py            # Google Sheets storage, dedup, feedback sync
│   ├── notifier.py          # Email digest — 3-layer card layout
│   └── llm/
│       ├── __init__.py
│       └── router.py        # Multi-LLM router with OpenRouter free model rotation
├── config/
│   ├── profile.yaml         # Candidate background & preferences
│   ├── search.yaml          # Search keywords (3 tiers) & filter rules
│   ├── llm_config.yaml      # LLM priority, quotas, rotation
│   └── feedback_log.json    # Negative feedback & learning signals
├── n8n/
│   ├── docker-compose.yml   # n8n local Docker setup
│   └── workflow.json        # n8n workflow (daily 8AM + manual webhook)
├── profile/
│   └── cv_base.md           # Base CV in markdown
├── templates/
│   └── cover_letter.md      # Cover letter template (Phase 2)
├── output/                  # Generated digests, CSVs, seen_jobs cache
├── .env.example
├── requirements.txt
├── CLAUDE.md
├── README.md
└── todo.md
```

## Reference Projects / 參考項目
- [DailyJobMatch](https://github.com/chunxubioinfor/DailyJobMatch) — n8n + OpenAI + Apify LinkedIn scraper
- [n8n-workflow](https://github.com/wenjiaqi8255/n8n-workflow) — n8n + Gemini + Supabase + Notion
- [JobHunter](https://github.com/wei-liping/JobHunter) — Next.js local AI workbench with mock interviews
- [linkedin-job-match-extension](https://github.com/YuxiaoMa66/linkedin-job-match-extension) — Chrome extension with inline scoring badges
