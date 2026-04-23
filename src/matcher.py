"""LLM-based job matching — Phygital-weighted, decision-first scoring.

Output: Structured JSON with 3-layer card design.
Layer 1: Decision signals (score, hint, blockers, work mode, salary, language, risk)
Layer 2: Fast explanation (WhyFit split into Strong/Partial, Gaps, Risks)
Layer 3: Expandable detail (Phygital, Company, ScoreBreakdown, TrustNotes)
"""

import json
import re
import yaml
import pandas as pd
from pathlib import Path
from src.llm import router
from src.llm.router import AllProvidersExhausted
from src.feedback import apply_weight_adjustments


def _extract_json(text):
    """Pull the first balanced JSON object out of an LLM response.

    Handles: markdown fences, leading/trailing prose, nested braces,
    braces inside string literals.
    """
    if not text:
        return None
    s = text.strip()
    # Strip common markdown fences
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)

    start = s.find("{")
    if start < 0:
        return None

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def load_profile(config_path="config/profile.yaml"):
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_preferences(config_path="config/preferences.yaml"):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _render_preferences(prefs):
    """Compact English block for LLM prompt injection."""
    if not prefs:
        return "None configured."
    lines = []
    sen = prefs.get("seniority", {})
    if sen.get("target_levels"):
        lines.append(f"Seniority target: {', '.join(sen['target_levels'])}")
    if sen.get("caution_levels"):
        lines.append(f"Caution (treat as separate category): {', '.join(sen['caution_levels'])}")
    if prefs.get("preferred_loop"):
        lines.append(f"Preferred work loop: {prefs['preferred_loop']}")
    if prefs.get("prefer"):
        lines.append("Prefer:")
        lines.extend(f"  - {p}" for p in prefs["prefer"])
    if prefs.get("open_to"):
        lines.append("Open to:")
        lines.extend(f"  - {p}" for p in prefs["open_to"])
    if prefs.get("avoid"):
        lines.append("Avoid:")
        lines.extend(f"  - {p}" for p in prefs["avoid"])
    return "\n".join(lines)


_HEAD_OF_REGEX = re.compile(r"\b(head of|vp of|vice president of|director of)\b", re.I)


def _compute_suggested_action(score, risk_level, is_head_of, blocking_alert):
    """Deterministic mapping from score + anti-pattern risk + seniority flag to action."""
    if blocking_alert:
        return "Skip"
    if is_head_of:
        return "Seniority mismatch"
    risk_level = (risk_level or "Low").strip().capitalize()
    if score >= 85 and risk_level == "Low":
        return "Strong Apply"
    if score >= 75 and risk_level in ("Low", "Medium"):
        return "Apply"
    if score >= 75 and risk_level == "High":
        return "Good role, risky org"
    if score >= 60:
        return "Review"
    return "Skip"


def _suggested_to_hint(action):
    """Collapse 6-label SuggestedAction back to 3-tier DecisionHint for digest routing."""
    if action in ("Strong Apply", "Apply"):
        return "Apply"
    if action in ("Review", "Good role, risky org", "Seniority mismatch"):
        return "Maybe"
    return "Skip"


def load_feedback_log(path="config/feedback_log.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"negative_feedback": [], "positive_signals": [], "skipped_job_ids": []}


def load_seen_job_ids(path="output/seen_jobs.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_seen_job_ids(ids, path="output/seen_jobs.json"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(list(ids), f)


SCORING_PROMPT = """### OUTPUT CONTRACT — READ FIRST
Your entire response MUST be a single JSON object and NOTHING else.
- First character: `{{`
- Last character: `}}`
- No preamble. No "Let me think...". No markdown fences. No comments after.
- If you violate this, the response is discarded.

You are a UX-aware Job Decision Layer for a senior Product Researcher in the Netherlands.
Your job: transform a job posting into a decision-ready, multi-dimensional assessment driven by **work-style fit**, not just domain keywords.

## Candidate Profile
- Ava Tse, 8+ years experience
- Background: Mechanical Engineering (BEng, HK PolyU) + Applied Cognitive Psychology (MSc, Utrecht University)
- Career: R&D Engineer (robotics, sensors) -> Project Engineer (IoT) -> UX Researcher -> Data Viz Engineer (1M+ users) -> Senior Product Researcher (automotive, EV/PHEV, 4000+ person VOC studies)
- Companies: BYD Europe, TomTom, Syntegon, DFO Global, Raymond Industrial, Jetta
- Strengths: product management, technical PRDs, Agile/Jira, user research, cognitive psychology, Python/SQL/PowerBI, ML/CV, 3D CAD, sensor integration
- Languages: English, Chinese, Cantonese. NO Dutch, NO German, NO French
- Location: Hoofddorp, NL. No driving licence. No visa sponsorship needed.
- Domain preference: **Phygital** (physical product + software/digital integration)
- 3 utility patents in sensors and air quality

## Work-Style Preferences (drive the Dimensions scoring)
{preferences_block}

## Job to Evaluate
**Title:** {title}
**Company:** {company}
**Location:** {location}

{description}

## Pre-detected flags
- Phygital keywords found: {phygital_detected}
- Pure SaaS keywords found: {pure_saas_detected}
- Driver licence mentioned: {driver_license_flagged}
- Dutch mandatory: {dutch_mandatory}
- Dutch nice-to-have: {dutch_nice_to_have}
- Work mode detected: {work_mode}
- Seniority fit: {seniority_fit}
- Agency: {is_agency}
- KM visa mentioned: {km_visa_mentioned}

## User's negative feedback history
{feedback_context}

## CRITICAL RULES

1. BLOCKERS OVERRIDE SCORE — Dutch mandatory, onsite-only, visa not supported, or driving licence mandatory → surface as BlockingAlert. Do not bury under a decent score.

2. DO NOT OVERSTATE FIT — Only claim a Strong signal if the JD clearly demonstrates it. Assumptions go to Partial, not Strong.

3. PHYGITAL IS NOT JUST BUZZWORDS — "digital twin", "IoT", "AI", "platform" alone do NOT equal Strong Phygital. Strong requires physical equipment, hardware, sensors, industrial/automotive/robotics/consumer hardware, or software directly tied to real-world product behavior.

4. NO LLM FLATTERY — Do not name-drop companies ("BYD experience is a plus"). Say "Automotive domain experience relevant to hardware-software products."

5. SCORE LOGIC (MatchScore 0-100, still the primary ranking signal):
   - Phygital relevance: 0-40
   - Seniority fit: 0-20 (8+ years; target Senior/Lead/Principal)
   - Domain relevance: 0-15
   - Research ownership: 0-10
   - Company fit: 0-10
   - Language penalty: 0 to -15
   - Driver licence: 0 to -10
   - Pure SaaS penalty: 0 to -40

6. SCORE SPREAD — use the full range, not a flat 82 for all Apply jobs:
   75-79 passes threshold with notable mismatch. 80-86 solid fit. 87-93 strong (exact domain). 94-100 exceptional.

7. DIMENSIONS — score each 0-5 ONLY from JD evidence. If signal is absent, give 2-3 (neutral/unknown), not 0. Use reason field to name JD phrase that drove the score.
   - ResearchFit: 5 = clear research owner; 3 = partial; 0 = zero research signal
   - InnovationFit: 5 = explicit 0->1 / experimentation / prototyping mandate; 3 = one of several priorities; 0 = pure maintenance / BAU
   - HandsOnProximity: 5 = IC or player-coach; 3 = oversees but still gets hands dirty; 0 = 100% people management
   - StrategyFit: 5 = owns product vision / long-term strategy; 3 = contributes; 0 = pure execution
   - AlignmentBurden (LOWER = BETTER): 5 = heavy matrix / multi-stakeholder consensus / slow decisions; 3 = normal cross-functional; 0 = autonomous small team
   - ProcessHeaviness (LOWER = BETTER): 5 = waterfall / compliance / approval gates; 3 = standard agile; 0 = lean minimal process
   - TeamStyleFit: 5 = small lean team, IDEO-style collaboration; 3 = standard product team; 0 = siloed corporate

8. GREEN FLAGS — 2-3 items, 繁體中文, each MUST be [JD-specific requirement] → [Ava's exact matching experience with context]. Example good: "JD要求大規模用戶研究 → Ava在BYD主導4000+人VOC研究". FORBIDDEN: "8年經驗", "不需荷蘭語", "位於荷蘭", "無駕照要求", anything that applies to every candidate who passed the pre-filters.

9. RED FLAGS — 2-3 items, 繁體中文, each names a JD-grounded work-style anti-pattern or risk (e.g. "多部門對齊需求高，決策速度慢" / "純執行導向，無研究自主權" / "流程重、審批層級多"). Must be traceable to JD text. Do NOT list language/licence — those go to BlockingAlert.

10. ANTI-PATTERN RISK — derive Low/Medium/High from Dimensions:
    - High: AlignmentBurden ≥ 4 OR ProcessHeaviness ≥ 4 OR (HandsOnProximity ≤ 1 AND role is not pure people-mgmt that Ava wants)
    - Medium: AlignmentBurden = 3 OR ProcessHeaviness = 3 OR ambiguous work-style signals
    - Low: AlignmentBurden ≤ 2 AND ProcessHeaviness ≤ 2 AND at least one of (ResearchFit/InnovationFit/HandsOnProximity) ≥ 4
    Reason: 1-line 繁體中文 quoting the JD signal that drove the level.

11. SUGGESTED ACTION — emit one of: "Strong Apply" / "Apply" / "Review" / "Skip" / "Good role, risky org" / "Seniority mismatch". System may override based on MatchScore + AntiPatternRisk + title regex, but your choice is the baseline.
    Use "Seniority mismatch" when title is "Head of / VP of / Director of" — the role is too management-heavy for Ava's Senior→Lead target.
    Use "Good role, risky org" when the role content fits but AntiPatternRisk is High.

### REMINDER: Output ONLY the JSON object. Start with `{{`, end with `}}`. No other text.

{{
  "CardSummary": {{
    "MatchScore": 0,
    "DecisionHint": "Apply | Maybe | Skip",
    "SuggestedAction": "Strong Apply | Apply | Review | Skip | Good role, risky org | Seniority mismatch",
    "TopLabel": "一句話總結，繁體中文，不超過25字",
    "MainRisk": "繁體中文，不超過20字，若無風險則空字串",
    "BlockingAlert": "若有阻礙用⚠開頭，繁體中文，若無則空字串",
    "RoleSummary": "1-2 English sentences: what this role does daily and what specific skills/tools/methods are used. Based ONLY on what the JD states — do not invent."
  }},
  "DecisionSignals": {{
    "WorkMode": "Remote | Hybrid | Onsite | Unknown",
    "SalaryText": "e.g. EUR6000/month or Unknown",
    "CommuteText": "e.g. ~25min commute or Unknown",
    "LanguageText": "English OK | Dutch Mandatory | Dutch Preferred",
    "ApplyComplexity": "Easy | Medium | Hard"
  }},
  "Dimensions": {{
    "ResearchFit":       {{"score": 0, "reason": "繁體中文，1行，引用JD關鍵詞"}},
    "InnovationFit":     {{"score": 0, "reason": ""}},
    "HandsOnProximity":  {{"score": 0, "reason": ""}},
    "StrategyFit":       {{"score": 0, "reason": ""}},
    "AlignmentBurden":   {{"score": 0, "reason": ""}},
    "ProcessHeaviness":  {{"score": 0, "reason": ""}},
    "TeamStyleFit":      {{"score": 0, "reason": ""}}
  }},
  "GreenFlags": ["繁體中文，最多3條，employer-facing [JD要求]→[Ava對應經歷]"],
  "RedFlags": ["繁體中文，最多3條，JD-grounded work-style risk"],
  "AntiPatternRisk": {{"Level": "Low | Medium | High", "Reason": "繁體中文，1行"}},
  "KeySkills": ["5-8 key skills/tools explicitly required by JD. English. Short labels."],
  "PhygitalAssessment": {{"Level": "Strong | Moderate | Weak", "Reason": "繁體中文，1-2句"}},
  "CompanySnapshot": {{
    "Name": "",
    "Industry": "",
    "Size": "string or null",
    "KM_Status": "string or null",
    "IsAgency": false
  }},
  "ScoreBreakdown": {{
    "PositiveDrivers": [{{"label": "繁體中文", "impact": "+N"}}],
    "NegativeDrivers": [{{"label": "繁體中文", "impact": "-N"}}]
  }},
  "TrustNotes": {{
    "Confidence": "High | Medium | Low",
    "MissingInfo": ["Unknown salary", "Unknown company size"],
    "FilterLogicReason": "繁體中文，清楚說明為何系統這樣判斷"
  }}
}}
"""


def score_job(job_row, profile=None, feedback_log=None, weight_adjustments=None):
    """Score a single job. Returns structured card JSON."""
    if profile is None:
        profile = load_profile()
    if feedback_log is None:
        feedback_log = load_feedback_log()

    feedback_lines = []
    for fb in feedback_log.get("negative_feedback", []):
        feedback_lines.append(f"- PENALTY: {fb['reason']} ({fb['penalty']} points) -- {fb.get('note', '')}")
    if weight_adjustments:
        for dim, delta in weight_adjustments.items():
            feedback_lines.append(f"- LEARNED ADJUSTMENT: {dim} {delta:+d} points (from user feedback patterns)")
    feedback_context = "\n".join(feedback_lines) if feedback_lines else "None."

    preferences_block = _render_preferences(load_preferences())

    prompt = SCORING_PROMPT.format(
        title=job_row.get("title", "Unknown"),
        company=job_row.get("company", "Unknown"),
        location=job_row.get("location", "Unknown"),
        description=str(job_row.get("description", ""))[:4000],
        phygital_detected=job_row.get("phygital_detected", False),
        pure_saas_detected=job_row.get("pure_saas_detected", False),
        driver_license_flagged=job_row.get("driver_license_flagged", False),
        dutch_mandatory=job_row.get("dutch_mandatory", False),
        dutch_nice_to_have=job_row.get("dutch_nice_to_have", False),
        work_mode=job_row.get("work_mode", "Unspecified"),
        seniority_fit=job_row.get("seniority_fit", "Medium"),
        is_agency=job_row.get("is_agency", False),
        km_visa_mentioned=job_row.get("km_visa_mentioned", False),
        feedback_context=feedback_context,
        preferences_block=preferences_block,
    )

    response = router.call_llm(prompt)

    json_blob = _extract_json(response)
    parse_error = None
    result = None

    if json_blob:
        try:
            result = json.loads(json_blob)
        except json.JSONDecodeError as e:
            parse_error = f"json.loads: {str(e)[:80]}"
    else:
        parse_error = "no JSON object found in response"

    if result is not None:
        try:
            card = result.setdefault("CardSummary", {})
            score = max(0, min(100, int(card.get("MatchScore", 0))))
            card["MatchScore"] = score

            # Clamp all dimension scores to 0-5 ints
            dims = result.setdefault("Dimensions", {})
            for dname in ("ResearchFit", "InnovationFit", "HandsOnProximity", "StrategyFit",
                          "AlignmentBurden", "ProcessHeaviness", "TeamStyleFit"):
                d = dims.setdefault(dname, {"score": 0, "reason": ""})
                try:
                    d["score"] = max(0, min(5, int(d.get("score", 0))))
                except (ValueError, TypeError):
                    d["score"] = 0

            risk = result.setdefault("AntiPatternRisk", {"Level": "Low", "Reason": ""})
            risk["Level"] = (risk.get("Level") or "Low").strip().capitalize()
            if risk["Level"] not in ("Low", "Medium", "High"):
                risk["Level"] = "Low"

            title_text = str(job_row.get("title", ""))
            is_head_of = bool(_HEAD_OF_REGEX.search(title_text))
            blocking = card.get("BlockingAlert", "")

            suggested = _compute_suggested_action(score, risk["Level"], is_head_of, blocking)
            card["SuggestedAction"] = suggested
            card["DecisionHint"] = _suggested_to_hint(suggested)

            return result
        except (KeyError, ValueError, TypeError) as e:
            parse_error = f"structure: {str(e)[:80]}"

    # --- Fallback: surface raw LLM text so the user can still judge fit ---
    raw_preview = response.strip().replace("\n", " ")[:500]
    print(f"    [LLM parse failed] {parse_error} — raw: {raw_preview[:200]}")
    return {
        "CardSummary": {
            "MatchScore": 50,
            "DecisionHint": "Maybe",
            "SuggestedAction": "Review",
            "TopLabel": raw_preview[:200] or "LLM returned empty",
            "MainRisk": "JSON parse failed — review raw output",
            "BlockingAlert": "",
            "RoleSummary": "",
        },
        "DecisionSignals": {"WorkMode": "Unknown", "SalaryText": "Unknown", "CommuteText": "Unknown", "LanguageText": "Unknown", "ApplyComplexity": "Unknown"},
        "Dimensions": {d: {"score": 0, "reason": ""} for d in
                        ("ResearchFit", "InnovationFit", "HandsOnProximity", "StrategyFit",
                         "AlignmentBurden", "ProcessHeaviness", "TeamStyleFit")},
        "GreenFlags": [raw_preview[:150]] if raw_preview else [],
        "RedFlags": [f"Parse error: {parse_error}"],
        "AntiPatternRisk": {"Level": "Low", "Reason": "parse failed"},
        "PhygitalAssessment": {"Level": "Unknown", "Reason": ""},
        "CompanySnapshot": {"Name": "", "Industry": "", "Size": None, "KM_Status": None, "IsAgency": False},
        "ScoreBreakdown": {"PositiveDrivers": [], "NegativeDrivers": []},
        "TrustNotes": {"Confidence": "Low", "MissingInfo": ["LLM response unparseable"], "FilterLogicReason": raw_preview[:300]},
        "_error": parse_error,
        "_raw": response[:1000],
    }


def score_all_jobs(df, profile=None, min_score=0, force=False):
    """Score all jobs. Returns enriched DataFrame sorted by score.

    Args:
        force: if True, bypass seen_jobs.json dedup (for --resend mode).
    """
    if profile is None:
        profile = load_profile()
    feedback_log = load_feedback_log()
    weight_adj = apply_weight_adjustments()
    seen_ids = set() if force else load_seen_job_ids()
    new_seen = set(seen_ids)

    scores_data = []
    for idx, row in df.iterrows():
        job_url = str(row.get("job_url", ""))
        if job_url in seen_ids:
            print(f"  [SKIP] Duplicate: {row.get('title', '?')}")
            continue

        print(f"  Scoring [{idx+1}/{len(df)}]: {row.get('title', '?')} @ {row.get('company', '?')}")
        try:
            result = score_job(row, profile, feedback_log, weight_adj)
        except AllProvidersExhausted as e:
            print(f"\n  [BAIL] All LLM providers exhausted — stopping scoring loop early.")
            print(f"  Scored {len(scores_data)} of {len(df)} jobs before bail-out.")
            break
        except Exception as e:
            print(f"    [ERROR] LLM scoring failed, skipping: {str(e)[:80]}")
            new_seen.add(job_url)
            continue
        new_seen.add(job_url)

        # Cooldown between LLM calls to reduce rate limiting
        import time
        time.sleep(15)

        card = result.get("CardSummary", {})
        signals = result.get("DecisionSignals", {})
        dims = result.get("Dimensions", {}) or {}
        risk = result.get("AntiPatternRisk", {}) or {}

        row_data = row.to_dict()
        row_data["match_score"] = card.get("MatchScore", 0)
        row_data["decision_hint"] = card.get("DecisionHint", "Skip")
        row_data["suggested_action"] = card.get("SuggestedAction", "")
        row_data["anti_pattern_risk"] = risk.get("Level", "")
        row_data["match_result"] = json.dumps(result, ensure_ascii=False)
        row_data["top_label"] = card.get("TopLabel", "")
        row_data["main_risk"] = card.get("MainRisk", "")
        row_data["blocking_alert"] = card.get("BlockingAlert", "")

        for dname, colname in (
            ("ResearchFit", "dim_research"),
            ("InnovationFit", "dim_innovation"),
            ("HandsOnProximity", "dim_handson"),
            ("StrategyFit", "dim_strategy"),
            ("AlignmentBurden", "dim_alignment_burden"),
            ("ProcessHeaviness", "dim_process_heavy"),
            ("TeamStyleFit", "dim_team_style"),
        ):
            row_data[colname] = dims.get(dname, {}).get("score", 0)

        scores_data.append(row_data)

    save_seen_job_ids(new_seen)

    if not scores_data:
        print("No new jobs to score.")
        return pd.DataFrame()

    scored_df = pd.DataFrame(scores_data)
    scored_df = scored_df.sort_values("match_score", ascending=False).reset_index(drop=True)

    qualified = scored_df[scored_df["match_score"] >= min_score]
    apply_n = len(scored_df[scored_df["decision_hint"] == "Apply"])
    maybe_n = len(scored_df[scored_df["decision_hint"] == "Maybe"])
    skip_n = len(scored_df[scored_df["decision_hint"] == "Skip"])
    print(f"\nScored: {len(scored_df)} jobs | Apply: {apply_n} | Maybe: {maybe_n} | Skip: {skip_n}")
    return qualified
