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

## Phase C: Telegram bot (completed 2026-04-15)
- [x] Dedicated jobsearcher bot (separate token: `TG_JOBSEARCHER_BOT_TOKEN`; separate from Claude Code plugin)
- [x] Long-poll handler `src/tg_bot.py` — run with `python src/tg_bot.py`
- [x] Auth guard: `TG_ALLOWED_USER_IDS` (comma-separated) restricts access
- [x] Commands: `/good`, `/skip`, `/uncertain`, `/score`, `/block`, `/addkw`, `/rmkw`, `/wait`, `/ok`, `/rules`, `/stats`, `/start`, `/help`
- [x] Feedback commands write verdict directly to Jobs tab (My Verdict + My Reason) + local feedback_log
- [x] `/score <url|text>` — fetches JD via trafilatura if URL, runs pre-filters + LLM, returns decision card
- [x] Rules commands write to Rules tab (`/block`, `/addkw`, `/rmkw`, `/wait`, `/ok`)
- [x] `/rules` shows all active rules from Rules tab; `/stats` shows pipeline totals + your feedback history

## Phase D: Email redesign (completed 2026-04-15)
- [x] Three-tier digest: Apply · Maybe (only when Apply < 5) · **Uncertain** (LLM Confidence=Low, any score)
- [x] Skip tier: aggregated count only, no per-row cards
- [x] TG deep-link buttons per card: `View` · `Good match` · `Skip` (stateless, no server needed)
- [x] Deep links handled in tg_bot.py `/start good_<id>` / `/start skip_<id>` — records verdict immediately
- [x] Skip from email defaults to "Skipped from email digest"; user can refine with `/skip <id> <reason>`
- [x] `TG_BOT_USERNAME` env var enables TG buttons; falls back to View-only if not set
- [x] Uncertain card styled in grey, section-label header, shows missing info prominently
- [x] Interactive skip flow: deeplink shows job card + InlineKeyboard with 2-3 suggested reasons (from Gap Analysis + Main Risk) + "Type my own" free-text option
- [x] Overwrite support: re-tapping Good/Skip on any job supersedes prior verdict; confirmation shows "Overwrote: <prev>"
- [x] Weekly feedback summary shown after every verdict ("This week: N good · M not fit")
- [x] Verdict timestamp written to Sheet in "Verdict At" column (added dynamically if missing)

## Phase E: Learning tweaks (completed 2026-04-15)
- [x] 1-sample nudge: ±3 pts from first verdict in any direction (immediate, no waiting)
- [x] Pattern detection: threshold 3 (was 5), pattern bonus ±8 pts on top of nudge
- [x] TG notification when pattern first crosses threshold — drains pending_notifications.json on bot startup
- [x] `/wait <pattern>` freeze — writes to Rules tab, feedback.py respects it via frozen_patterns param
- [x] `/ok <pattern>` unfreeze — deactivates wait row in Rules tab
- [x] Adjustments computed dynamically from active verdicts (not accumulated in storage)
- [x] frozen_patterns plumbed: read_rules() → main.py → apply_weight_adjustments()
- [x] 7 feedback tests (replaced 3 old threshold tests with 6 covering nudge/bonus/freeze)

## Phase F: Ad-hoc scoring (completed — implemented in Phase B/C)
- [x] `Inbox` tab poller integrated into pipeline (Phase B: poll_inbox → main.py)
- [x] `/score <url|text>` — runs pre-filters first, reports which rule killed it, then LLM if passes (Phase C: tg_bot.py cmd_score)

## Phase 1g: Remaining
- [x] Run full keyword set (16 keywords across 3 tiers) — first full run 2026-04-16, 464 jobs scraped
- [x] Re-enable Indeed/Google platforms — per-platform 90s thread timeout via concurrent.futures; Glassdoor left disabled (API errors)
- [x] Fix Google Sheets feedback sync — switched get_all_records() → get_all_values() with manual header dedup; survives duplicate/empty header cells
- [x] Short-circuit on LLM exhaustion — AllProvidersExhausted exception bails score loop early with partial results
- [x] Populate industry_bonus / skill_bonus in config/prerank.yaml (automotive +15, IoT +12, robotics +12, user research +8, VOC +8, etc.)

## Phase 1h: Location + Feedback fixes (completed 2026-04-16)
- [x] Location filter (Step 2.5) — hard-reject non-NL, non-remote jobs; keeps NL (", NL" suffix), remote (is_remote=True), empty location; rejects all others; ~198 non-NL jobs blocked in first full run
- [x] Fix verdict URL="Unknown" collision — all verdicts were keyed on literal "Unknown" causing every new verdict to supersede all previous; added _canonical_url() helper that falls back to https://www.linkedin.com/jobs/view/{job_id} when Sheet URL is bad
- [x] Add company to record_verdict() — stored in feedback_log so /goodmatches can display it
- [x] /goodmatches clickable links — now renders <a href="...">View job →</a> with company name
- [x] Bot startup conflict-clear — _clear_stale_connection() calls getUpdates with timeout=1 before polling to evict stale Telegram long-poll sessions on restart
- [x] --resend mode — load enriched cache, apply location filter, rescore top N with LLM, send email; does NOT write to Sheet (safe to run after broken runs)
- [x] score_all_jobs force=True — bypass seen_jobs.json dedup for resend/re-score use cases
- [x] Sheet header repair — _repair_jobs_headers() in setup_tabs() detects and fixes stale column headers (Industry column was missing, shifting Job URL to wrong column → all URLs stored as "Unknown")
- [ ] Verify Sheet header repair works on next --manual run (Job URL should no longer be "Unknown")
- [x] Fix TG deeplink Skip/Good for Indeed/Google jobs — `_job_id_from_url` now emits short Telegram-safe IDs (`in_<jk>`, `hx_<md5>`); `_find_job_row` strips prefix before searching Sheet

## Phase H: LinkedIn geoId patch (completed 2026-04-18)
- [x] `src/jobspy_patches.py` — monkey-patch `LinkedIn.__init__` to wrap `session.get`; inject `geoId` param when URL hits LinkedIn's search endpoint
- [x] `LINKEDIN_GEOID_BY_COUNTRY` map — NL=102890719, DE/BE/FR/UK included for future reuse
- [x] Imported at top of `src/scraper.py` so every scrape_jobs call benefits
- [x] Live verified: `scrape_jobs(site_name=['linkedin'], location='Netherlands', results_wanted=50)` → 50/50 NL jobs (was ~30% NL before)
- [x] Added 3 keywords to `config/search.yaml` exploratory tier: Consumer Insights Manager, Innovation Manager, Human Factors Engineer
- [ ] (Future) Add NL-native boards: Nationale Vacaturebank, Werkzoeken.nl, Stepstone.nl — 300K+ extra NL listings
- [ ] (Future) Raise `results_per_keyword` beyond 50 now that scrape yields mostly NL

## Phase G: 4-block work-style redesign (completed 2026-04-17)
- [x] `config/preferences.yaml` — Seniority target/caution, preferred_loop, avoid/prefer/open_to lists, 7-dim rubric, V2 dimension weights
- [x] `src/matcher.py` — New SCORING_PROMPT (Dimensions/GreenFlags/RedFlags/AntiPatternRisk/SuggestedAction); preferences injection; deterministic `_compute_suggested_action()` (6 labels: Strong Apply / Apply / Review / Skip / Good role, risky org / Seniority mismatch); Head-of title regex → Seniority mismatch
- [x] `src/sheets.py` — 9 new columns (SuggestedAction, AntiPatternRisk, 7×Dim); `_extract_from_match_result` handles both new (GreenFlags) and legacy (WhyFit) schemas
- [x] `src/notifier.py` — New card UI: 6-color SuggestedAction pill, Risk Low/Med/High badge, compact dimensions row ("Research 5/5 · Innovation 5/5 · Alignment risk 1/5 ..."), Green/Red Flags sections; legacy rows still render WhyFit/Gaps
- [x] `config/prerank.yaml` — Green-flag keyword bonuses (rapid prototyping +8, player-coach +10, 0 to 1 +8, small team +5, ...) + `description_penalty` section for anti-patterns (stakeholder alignment -8, waterfall -10, management consulting -8, ...)
- [x] `src/prerank.py` — Loop for `description_penalty` anti-pattern keywords
- [x] Tests — 56/56 passing; updated `test_scoring_prompt_formats_without_error` with new `preferences_block` key
- [x] Smoke test — `--rerank-only` runs clean; anti-pattern penalties fire on real data (e.g. "management consulting-8" on Oliver Wyman, "stakeholder alignment-8" on ISO role)
- [x] Backward compat — **only new jobs** get new schema; existing ~80 Sheet rows continue rendering via legacy WhyFit/Gaps path
- [ ] V2: feedback-loop integration (skip-reason → dimension penalty mapping; score↔decision mismatch tracking)
- [ ] Calibration rescore — verify Polaroid PM (user verdict=Good) → Apply/Strong Apply; Heliox Sr Systems Engineer (user=Skip) → Review/Skip with ProcessHeaviness high; Optics11 Engineering Manager (user=Skip) → Seniority mismatch (quota-cost; run after next `--manual` cycle)

## Phase R: PRD-aligned rebuild (started 2026-04-19)
Canonical task tracker: `TASKS.json` (single source of truth; has full acceptance criteria).
High-level phases:
- [x] R0 — Alignment with user on PRD interpretation, scoring weights, taxonomy depth, role scope (2026-04-19)
- [x] R1 — Personal Fit Layer (roles.yaml / skills.yaml / industries.yaml / constraints.yaml / evidence.yaml) — COMPLETE 2026-04-22
  - [x] R1-01 roles.yaml (2026-04-20) — 6 desired / 6 ambiguous / 8 excluded families; `output/role_classifier_dryrun.json`
  - [x] R1-02 skills.yaml (2026-04-22) — 25 verified / 19 adjacent / 13 forbidden / 10 negative_signal (post-user-edit); `output/skills_dryrun.json`
  - [x] R1-03 industries.yaml (2026-04-20) — 31 industries, single-column proximity, unknown=4/10; `output/industries_dryrun.json`
  - [x] R1-04 constraints.yaml (2026-04-20) — 13 constraints (5 hard / 8 soft after seniority split); `output/constraints_dryrun.json`
  - [x] R1-05 evidence.yaml (2026-04-22) — 22 proof points; 25/25 verified skill coverage; `output/evidence_dryrun.json`
- [x] R2 — Classification stage (role family / industry / seniority classifiers) — DONE 2026-04-22
  - [x] R2-01 role-family classifier (2026-04-22) — `src/classify/role_family.py` + Stage 3b wired into `main.py`; 18 unit tests; dry-run: 94.6% coverage, 17 hard-rejects, 115 rescued
  - [x] R2-02 industry classifier (2026-04-22) — `src/classify/industry.py`; 17 unit tests; dry-run on 664 role-survivors: 12.2% anchor / 15.2% far / 61.3% unknown (→ R3 fallback 4/10); 56 company+JD disagreements captured as secondary_candidates
  - [x] R2-03 seniority classifier (2026-04-22) — `src/classify/seniority.py`; 28 unit tests; dry-run on 681 cached: 19 hard-rejects (interns/trainees/graduate-programs only, no false positives), 59 mismatch-flags (junior/head-of/mid-level routed to Review, never dropped); 119/119 total tests passing
- [ ] R3 — Sub-score engine (6 interpretable sub-scorers per PRD §15)
  - [x] R3-01 role_fit (2026-04-22) — `src/score/role_fit.py` + `src/score/__init__.py`; 18 unit tests; dry-run on 645 Stage-3b survivors: mean 14.38 / median 16 / stdev 5.25; bucket bands 15-20 / 10-14 / 6-10 caps + deliverable bonus (max +5, strong +1 / weak +0.5) + anti-pattern penalty (max −3, roadmap-alignment combo rule); 137/137 total tests passing
  - [x] R3-02 hard_skill (2026-04-22) — `src/score/hard_skill.py` + `src/score/skill_extract.py`; 21 unit tests (deterministic via `jd_skills` param); Gemini→Groq extraction chain; cache keyed by URL + sha256(normalized_jd)[:16]; forbidden_core → score=0, optional-forbidden cap -5, negative cap -3; 10-job live dry-run confirmed end-to-end + surfaced synonym-coverage gap (→ R3-02b); 158/158 total tests passing
  - [x] R3-02b close skills.yaml synonym coverage gap (2026-04-22) — renamed Mechanical engineering fundamentals → Product development for physical products; added 2 new adjacent Low entries (Design validation tools familiarity, General spreadsheet tools); extended 5 verified entries with user-approved synonyms; tightened forbidden to depth-qualified only. Lift: avg verified/job 1.1 → 2.00 (+82%), 7/10 jobs now ≥ 2 verified hits (was 2/10). Monitor phrases (product mechanical design, engineering validation) had zero over-triggering in sample
  - [x] R3-03 seniority_fit + industry_proximity + desirability + evidence (2026-04-22) — 4 modules in `src/score/`; 47 unit tests (15+9+16+7); dry-run on 645 Stage-3b survivors: subtotal mean 27.84 / median 28 / stdev 6.95 out of 45, range 7-43; top samples ranked correctly (Senior PM/UX/Mech Eng all 42-43/45); 205/205 total tests passing
  - [x] R3-04 aggregate (2026-04-22) — `src/score/aggregate.py`; 19 unit tests covering all Apply/Review/Skip paths, demoters, guardrails; full-pipeline dry-run validates flow end-to-end (Trip.com Senior Product Designer = only Apply at 78/100 because it's the only job with real LLM extraction — proves the hard_skill ≤ 8 guardrail works as designed); 224/224 total tests passing
  - [x] R3-05 wire sub-scorers into main.py pipeline (2026-04-23) — `src/score/pipeline.py` orchestrator + Stage 4.8 in `main.py`; attaches score columns (score_role_fit / score_hard_skill / score_seniority_fit / score_industry_proximity / score_desirability / score_evidence / final_score / recommendation / recommendation_reason / top_reasons / top_risks / blockers / review_flags) + audit columns (drop_stage / drop_reason / skill_extract_source / scored_by_llm / used_skill_cache / llm_stage_skipped_reason); heavy matcher LLM now GATED on `recommendation != "Skip"` so Skip rows and below --max-jobs cap rows never consume quota; observability line prints skill_llm_calls / cache_hits / extraction_failures per run; legacy match_score / decision_hint mirrored from final_score / recommendation (Review → Maybe) so existing notifier + Sheets keep working until R4-02 rewires the digest; 232/232 total tests passing (8 new in `tests/test_score_pipeline.py`)
- [ ] R4 — Sheets + Email redesign (Apply/Review/Skip, trimmed schema)
  - [x] R4-02 trust-first email digest (2026-04-23) — `src/digest/` module (bottleneck / explain / state / render); per-card 6 sub-score bars + deterministic "✅ Why Apply / ⚠ Watch-outs" (Apply) or "What's holding this back / Still worth a look" (Review); Skip collapsed with top reasons + borderline audit band (45-49); 3-band funnel (FILTERED / SCORED BUT NOT SURFACED / SCORED & SURFACED); pending-feedback block from last-digest snapshot; theme-clustered feedback summary (Kept / Rejected / Notes + common themes); Telegram deep-links: Open JD / Keep / Reject / Note (Note = standalone free-text annotation); `tg_bot.py` `_SKIP_CATEGORIES` expanded to user's 17-option quick-reject taxonomy + `note_<id>` deep-link handler; `main.py` step 7 routes to trust digest when `recommendation` column present, legacy path otherwise; 47 new tests (bottleneck + explain + state + render smoke); 279/279 total tests passing; preview HTML generated at `output/r4_02_preview.html`
  - [x] R4-02 refine: concrete trust signals + scope header + preview flag (2026-04-23) — per user feedback: digest must answer "what this job is / why it may fit / what holds it back" within 3s per card. Added: (1) one-line role+industry+focus on every card (e.g. "Product role in Robotics, focused on prototyping, user research"), derived deterministically from `role_family` + `industry_display` + verified hard-skill matches. (2) concrete tag chips — Matched / Gap / Adjacent / Evidence — from `_score_raw_hard_skill_signals` + `_score_raw_evidence_signals` (now also persisted by `src/score/pipeline.py`). (3) header scope line: "8 Apply · 14 Review · 5 borderline worth audit out of 125 new positions found across 7 role groups in the past 24 hours". (4) plain-language wording — "below the Review threshold" instead of "below Review floor", "SCORED BUT NOT SHOWN" / "SHOWN IN TODAY'S DIGEST" instead of "NOT SURFACED" / "SURFACED", softened `forbidden_core_skill:` labels into "Required skill outside Ava's fit: X". (5) Pending-feedback block moved BELOW Review cards so today's surface comes first. (6) Borderline Skip cards (45–49 band) now carry full card actions — Open JD / Keep / Reject / Note — not just a title line. (7) LLM usage visibility block in funnel (evaluated / skipped-by-gates / skill-LLM-calls / cache-hits / extraction-failures). (8) preview-mode banner labels placeholder buttons when rendered via `scripts/render_digest_preview.py`. (9) feedback history link slot in summary block. 292/292 tests passing (13 new).
  - [ ] R4-01 Sheets schema + drop-stage audit trail (Jobs ~22 cols, Raw with drop_stage/drop_reason for every row, Feedback append-only, `scripts/audit_url.py` for manual lookup) — NEXT
- [ ] R5 — Code restructure to adapters/parse/filters/classify/score/recommend/storage

## Phase 3: Enhancements
- [ ] Company enrichment (Glassdoor ratings, Google reviews, company size via web scraping)
- [ ] Startup sources (Wellfound, YC, TechLeap.nl)
- [ ] Notion integration for application tracking
- [ ] Career 覆盤 — AI career positioning based on cross-disciplinary background
- [x] ~~Google Sheets feedback sync~~ (replaced by two-way feedback loop in Phase 1c)
