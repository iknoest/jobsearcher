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

Your job is NOT to write a long analysis report.
Your job is to transform a job posting into a decision-ready, trustable, low-cognitive-load assessment.

## Candidate Profile
- Ava Tse, 8+ years experience
- Background: Mechanical Engineering (BEng, HK PolyU) + Applied Cognitive Psychology (MSc, Utrecht University)
- Career path: R&D Engineer (robotics, sensors) -> Project Engineer (IoT, air quality) -> UX Researcher (IoT maintenance) -> Data Viz Engineer (1M+ users) -> Senior Product Researcher (automotive, EV/PHEV, design clinics, 4000+ person VOC studies)
- Companies: BYD Europe, TomTom, Syntegon, DFO Global (smart wearables), Raymond Industrial (IoT sensors), Jetta (robotics)
- Core strengths: Product management, technical PRDs, Agile/Jira, user research, cognitive psychology, Python/SQL/PowerBI, ML/CV, 3D CAD, sensor integration
- Languages: English, Chinese, Cantonese. NO Dutch, NO German, NO French
- Location: Hoofddorp, Netherlands. No driving licence. No visa sponsorship needed.
- Domain preference: **Phygital** (physical product + software/digital integration)
- 3 utility patents in sensors and air quality
- Interested roles: Product Researcher, Product Owner, UX Research, Product Strategy, Innovation

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

1. BLOCKERS OVERRIDE SCORE — If Dutch mandatory, onsite-only, visa not supported, or driving licence mandatory, surface as blocking alert. Do not hide under a decent score.

2. DO NOT OVERSTATE FIT — If something is only loosely related, label it Partial Match, not Strong Match. Do not present assumptions as facts.

3. PHYGITAL IS NOT JUST BUZZWORDS — "digital twin", "IoT", "AI", "platform", "simulation" do NOT automatically mean strong Phygital. Only strong if the job clearly involves physical equipment, hardware, sensors, industrial/automotive/robotics/consumer hardware, or software directly tied to real-world product behavior. Cloud architecture, DevOps, enterprise platforms = Weak Phygital.

4. NO LLM FLATTERY — Do not use company prestige as a reason for fit. Do not say "BYD experience is a plus." Say "Has automotive domain experience relevant to hardware-software products."

5. SEPARATE GAPS FROM RISKS — Risk = structural blocker. Gap = addressable skill/experience shortage. Never mix them.

6. SCORE LOGIC:
   - Phygital relevance: 0-40 (physical+digital = high; pure software = low)
     Strong(35-40): hardware+software tightly coupled, Ava's exact domain (automotive, robotics, IoT, consumer electronics)
     Moderate(20-34): physical product but domain mismatch (building materials, industrial machinery, logistics equipment)
     Weak(0-19): software platform loosely tied to hardware
   - Seniority fit: 0-20 (8+ years, senior/lead)
     Give 20 only if the role explicitly needs senior/lead and matches scope of Ava's experience.
     Give 12-15 if role level is ambiguous or slightly junior in scope.
   - Domain relevance: 0-15 (product research, UX, IoT, automotive, consumer electronics)
     Give 15 only if Ava's EXACT industry background (automotive, IoT, robotics, consumer electronics, smart devices) matches the JD.
     Give 8-12 if role type matches (e.g. Product Researcher) but industry is unrelated to Ava's background.
     Give 0-7 if both role type AND industry are mismatches.
   - Research ownership: 0-10 (owns research vs. just executes)
   - Company fit: 0-10
   - Language penalty: 0 to -15 (Dutch mandatory = -15)
   - Driver licence: 0 to -10
   - Pure SaaS penalty: 0 to -40

7. DECISION THRESHOLDS AND SCORE DISTRIBUTION:
   - Apply: score >= 75, role genuinely relevant, no major blockers
   - Maybe: 60-74, good relevance but real uncertainty
   - Skip: < 60, or fit depends on too many assumptions

   SCORE SPREAD — use the full range, not a flat 82 for all Apply jobs:
   - 75-79: Passes threshold but notable domain mismatch or missing key requirement
   - 80-86: Solid fit — phygital product role in adjacent or matching domain
   - 87-93: Strong fit — Ava's specific experience (automotive, IoT, VOC, sensor) directly matches JD requirements
   - 94-100: Exceptional — ideal domain, strong phygital, clear research ownership, no risks
   Scores clustering at 82 are wrong. Differentiate clearly within the Apply band.

8. WHYFIT MUST BE EMPLOYER-FACING — Each StrongMatch/PartialMatch item must answer: "Why would THIS employer shortlist Ava over a generic candidate for THIS specific role?"
   FORBIDDEN in StrongMatch/PartialMatch:
   - Generic seniority: "8 years experience", "senior level", "experienced researcher"
   - Filter pass-through: "no Dutch required", "English OK", "located in NL", "no driving licence needed", "company is in Netherlands"
   - Anything that applies equally to every candidate who passes the pre-filters
   REQUIRED format: [JD-specific requirement] → [Ava's exact matching experience with context]
   Example good: "JD要求大規模用戶研究 → Ava在BYD主導4000+人VOC研究，具備同等規模的設計研究能力"
   Example bad: "具備8年以上工作經驗" or "不需要荷蘭語，符合Ava條件"

9. GAPS MUST BE REAL AND JD-GROUNDED — Only list a gap if:
   (a) the JD explicitly states a requirement or preferred skill, AND
   (b) Ava clearly cannot meet it based on her profile
   NEVER list Dutch language or driving licence as a gap unless the JD explicitly requires them.
   NEVER invent gaps to fill the 3-item limit — use fewer items or an empty array if real gaps are fewer.
   NEVER list "no experience in X" if X is not mentioned in the JD.
   Each gap must name: the specific skill/domain the JD requires vs what Ava is missing.

### REMINDER: Output ONLY the JSON object below. Start with `{{` and end with `}}`. No other text.


{{
  "CardSummary": {{
    "MatchScore": 0,
    "DecisionHint": "Apply | Maybe | Skip",
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
  "WhyFit": {{
    "StrongMatch": ["繁體中文短句，最多3條。每條必須點名JD的具體要求+Ava對應的具體經歷（公司/工具/數字）。禁止寫通用資歷（如「8年經驗」）、禁止寫篩選邏輯（如「不需荷蘭語」「位於荷蘭」「無駕照要求」）。"],
    "PartialMatch": ["繁體中文短句，最多3條。同上規則，僅代表部分符合。"]
  }},
  "Gaps": ["繁體中文，最多3條。僅列JD明確要求但Ava無法清楚符合的技能或領域。若JD未提及荷蘭語或駕照，禁止將其列為缺口。若無真實缺口則留空陣列。"],
  "Risks": ["繁體中文，結構性風險，最多3條"],
  "KeySkills": ["5-8 key skills/tools/qualifications explicitly required by the JD. English. Short labels (e.g. 'Python', 'Agile/Scrum', '5+ yrs PM', 'SolidWorks'). No soft skills or generic terms."],
  "PhygitalAssessment": {{
    "Level": "Strong | Moderate | Weak",
    "Reason": "繁體中文，1-2句"
  }},
  "CompanySnapshot": {{
    "Name": "",
    "Industry": "",
    "Size": "string or null",
    "KM_Status": "string or null",
    "IsAgency": false
  }},
  "ScoreBreakdown": {{
    "PositiveDrivers": [
      {{"label": "繁體中文", "impact": "+N"}}
    ],
    "NegativeDrivers": [
      {{"label": "繁體中文", "impact": "-N"}}
    ]
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
            score = result.get("CardSummary", {}).get("MatchScore", 0)
            score = max(0, min(100, int(score)))
            result["CardSummary"]["MatchScore"] = score

            if score >= 75:
                result["CardSummary"]["DecisionHint"] = "Apply"
            elif score >= 60:
                result["CardSummary"]["DecisionHint"] = "Maybe"
            else:
                result["CardSummary"]["DecisionHint"] = "Skip"

            if result.get("CardSummary", {}).get("BlockingAlert"):
                result["CardSummary"]["DecisionHint"] = "Skip"

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
            "TopLabel": raw_preview[:200] or "LLM returned empty",
            "MainRisk": "JSON parse failed — review raw output",
            "BlockingAlert": "",
        },
        "DecisionSignals": {"WorkMode": "Unknown", "SalaryText": "Unknown", "CommuteText": "Unknown", "LanguageText": "Unknown", "ApplyComplexity": "Unknown"},
        "WhyFit": {"StrongMatch": [raw_preview[:150]] if raw_preview else [], "PartialMatch": []},
        "Gaps": [], "Risks": [f"Parse error: {parse_error}"],
        "PhygitalAssessment": {"Level": "Unknown", "Reason": ""},
        "CompanySnapshot": {"Name": "", "Industry": "", "Size": None, "KM_Status": None, "IsAgency": False},
        "ScoreBreakdown": {"PositiveDrivers": [], "NegativeDrivers": []},
        "TrustNotes": {"Confidence": "Low", "MissingInfo": ["LLM response unparseable"], "FilterLogicReason": raw_preview[:300]},
        "_error": parse_error,
        "_raw": response[:1000],
    }


def score_all_jobs(df, profile=None, min_score=0):
    """Score all jobs. Returns enriched DataFrame sorted by score."""
    if profile is None:
        profile = load_profile()
    feedback_log = load_feedback_log()
    weight_adj = apply_weight_adjustments()
    seen_ids = load_seen_job_ids()
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

        row_data = row.to_dict()
        row_data["match_score"] = card.get("MatchScore", 0)
        row_data["decision_hint"] = card.get("DecisionHint", "Skip")
        row_data["match_result"] = json.dumps(result, ensure_ascii=False)
        row_data["top_label"] = card.get("TopLabel", "")
        row_data["main_risk"] = card.get("MainRisk", "")
        row_data["blocking_alert"] = card.get("BlockingAlert", "")

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
