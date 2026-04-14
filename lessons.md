# Lessons Learned

Append-only log of non-obvious decisions and the incidents that caused them.
New entries go at the top of the relevant section. Format: one-line rule → short reason.

## Scraping
- **LinkedIn via JobSpy always returns empty descriptions** — trafilatura fallback is mandatory, not optional.
- **Indeed / Google Jobs / Glassdoor hang on Windows** — need per-request timeouts before re-enabling.
- **Glassdoor API is unstable** — keep disabled until upstream is fixed.

## Language detection
- **langdetect is non-deterministic** — replaced with langid for reliable Dutch detection.
- **langid log-prob is NOT a confidence score** — it scales with text length (longer text → more negative). Never threshold on it; just check `top language == "nl"`. A prior `confidence > -100` threshold silently disabled Dutch JD detection for full-length descriptions.

## LLM
- **Gemini 2.5 Flash thinking tokens consume max_output_tokens** — must set `thinking_budget=0` for JSON generation, otherwise output is truncated at ~200 chars.
- **5 providers configured with auto-fallback** — Gemini + Groq (priority 1), NVIDIA NIM + Mistral (priority 2), OpenRouter (priority 3). Config in `config/llm_config.yaml`. Provider type `openai_compat` works for any OpenAI-compatible API via custom `base_url`.
- **OpenRouter free tier rate-limits aggressively** — 15s cooldown between calls, multi-model rotation required, ~3 min per job at worst.

## Data quality
- **Salary metadata from JobSpy can be garbage** — validate min > 100, swap if inverted, reject non-numeric.
- **IND sponsor register is an HTML table** — parse with trafilatura, not as CSV.
- **Company names in IND register include B.V./N.V.** — rapidfuzz fuzzy match with suffix stripping needed.

## Runtime / infra
- **Python stdout buffering on Windows** — run with `PYTHONUNBUFFERED=1` and `-u` for background tasks.
- **Windows cp1252 crashes on Unicode arrows / CJK output** — reconfigure stdout/stderr to UTF-8 at the top of any entry point.
- **OpenBLAS thread contention can OOM pandas** — set `OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_NUM_THREADS=1` before importing numpy/pandas in constrained environments.

## Deprecated decisions (kept for context)
- **Phase 2 CV tailoring was removed 2026-04-14** — generator, Flask review UI, PDF templates all deleted. LLM-generated CVs could not hit the quality bar (fabricated skills, wrong experience order, no photo, poor layout) and maintenance cost outweighed value. Focus is now daily scoring + fast feedback loop only.
- **5-sample feedback threshold (old rule)** — replaced with 1-sample nudge + pattern detection in the new design.
