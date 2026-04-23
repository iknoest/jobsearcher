"""JD hard-skill extraction (R3-02 support module).

Single LLM call per JD that returns the hard skills explicitly required
or preferred, tagged core/optional. Uses the existing llm.router but
restricts the provider chain to a cheaper two-provider subset per user
rule 2026-04-22:

    primary  = Gemini 2.5 Flash  (google-genai, free tier)
    fallback = Groq Llama 3.3 70B (OpenAI-compat, free tier)

Both are fast and return structured JSON reliably with thinking disabled.
Only used when no cached extraction exists for a (job_url, JD-hash) pair.
"""

import json
import re

from src.llm.router import call_llm, load_llm_config

_EXTRACTION_PROVIDERS = ("gemini", "groq")

_EXTRACTION_PROMPT = """You are extracting hard skills from a JOB DESCRIPTION.

Return STRICT JSON, no prose, no markdown fences. Schema:
{{"skills": [{{"name": "<short phrase from JD>", "importance": "core" | "optional"}}, ...]}}

RULES:
- Include only concrete hard skills, tools, methodologies, or domain topics explicitly present in the JD.
- "core"     = listed under must-have / required / you must / X+ years of / proven experience in.
- "optional" = listed under nice-to-have / bonus / a plus / preferred / familiarity with / exposure to.
- Skip soft traits ("team player", "communication", "self-starter") and seniority labels.
- Keep phrases short (2-5 words) and in their JD-original form (noun/verb-phrase).
- Do NOT invent skills that are not in the JD. If unclear, omit.
- 5-20 entries is typical; fewer is fine.

JOB DESCRIPTION:
<<<
{jd}
>>>

Return JSON only."""


def _restricted_config(full_config: dict) -> dict:
    """Return a copy of full_config limited to the extraction-provider subset."""
    return {
        **full_config,
        "models": {
            k: v
            for k, v in full_config.get("models", {}).items()
            if k in _EXTRACTION_PROVIDERS
        },
    }


def _parse_skills_json(raw: str) -> list[dict]:
    """Tolerant JSON parse. Strips ``` fences and picks the first JSON object."""
    if not raw:
        return []
    txt = raw.strip()
    # Strip ```json ... ``` fences if present
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt)
    # Grab first balanced JSON object
    start = txt.find("{")
    end = txt.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        obj = json.loads(txt[start : end + 1])
    except json.JSONDecodeError:
        return []

    skills = obj.get("skills") or []
    cleaned: list[dict] = []
    for s in skills:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()
        if not name:
            continue
        importance = str(s.get("importance", "optional")).strip().lower()
        if importance not in ("core", "optional"):
            importance = "optional"
        cleaned.append({"name": name, "importance": importance})
    return cleaned


def extract_jd_skills(description: str, max_chars: int = 6000) -> list[dict]:
    """Extract hard skills from a JD description via LLM.

    Returns [{"name": str, "importance": "core" | "optional"}, ...].
    Raises AllProvidersExhausted if both providers fail.
    """
    jd = (description or "").strip()[:max_chars]
    if not jd:
        return []
    full = load_llm_config()
    restricted = _restricted_config(full)
    if not restricted["models"]:
        # No configured extraction provider at all — fall back to full chain.
        restricted = full
    prompt = _EXTRACTION_PROMPT.format(jd=jd)
    raw = call_llm(prompt, config=restricted, max_retries=1)
    return _parse_skills_json(raw)
