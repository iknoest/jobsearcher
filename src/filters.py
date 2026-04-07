"""Pre-LLM filters — fast, rule-based filtering before expensive LLM calls.

Hard filters: Dutch-only JDs, junior/entry-level roles
Soft flags: driving licence, Dutch preferred, agency detection
Everything else is enrichment for the LLM scorer.
"""

import re
import pandas as pd
import langid
import yaml


def load_filter_config(config_path="config/search.yaml"):
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("filters", {})


def detect_language(text):
    """Detect primary language of text. Returns (lang_code, confidence)."""
    try:
        lang, confidence = langid.classify(text)
        return lang, confidence
    except Exception:
        return "unknown", 0.0


def is_dutch_jd(description):
    """Check if JD is primarily written in Dutch."""
    if not description or len(description.strip()) < 50:
        return False
    lang, confidence = detect_language(description)
    return lang == "nl" and confidence > -100  # langid confidence is log-prob, any 'nl' classification is reliable


# --- Pattern definitions ---

DRIVING_PATTERNS = re.compile(
    r"(rijbewijs|driving\s+licen[cs]e|driver.?s?\s+licen[cs]e|"
    r"driving\s+permit|valid\s+licen[cs]e\s+required|car\s+required)",
    re.IGNORECASE,
)

DUTCH_REQUIRED_PATTERNS = re.compile(
    r"(vloeiend\s+nederlands|dutch\s+(is\s+)?(required|mandatory|must|essential)|"
    r"fluent\s+(in\s+)?dutch|native\s+dutch|nederlands\s+vereist|"
    r"dutch\s+speaking\s+required|nederlands\s+verplicht)",
    re.IGNORECASE,
)

DUTCH_NICE_TO_HAVE_PATTERNS = re.compile(
    r"(dutch\s+(is\s+)?(a\s+)?(nice\s+to\s+have|preferred|plus|advantage|asset|bonus)|"
    r"preferably\s+dutch|basic\s+dutch)",
    re.IGNORECASE,
)

SENIORITY_REJECT_PATTERNS = re.compile(
    r"\b(junior|entry[\s\-]?level|graduate\s+(position|role|program)|"
    r"0[\-–]3\s+year|[<]5\s+year|1[\-–]2\s+year)\b",
    re.IGNORECASE,
)

SENIORITY_FIT_PATTERNS = re.compile(
    r"\b(senior|lead|principal|5\+?\s+year|8\+?\s+year|7\+?\s+year|"
    r"experienced|mid[\-\s]?senior)\b",
    re.IGNORECASE,
)

WORK_MODE_PATTERNS = {
    "Remote": re.compile(r"(fully?\s+remote|work\s+from\s+home|WFH|remote\s+first|anywhere)", re.IGNORECASE),
    "Hybrid": re.compile(r"(hybrid|flexible\s+work|2[\-–]3\s+days?\s+(in\s+)?(the\s+)?office)", re.IGNORECASE),
    "Onsite": re.compile(r"(on[\-\s]?site|office[\-\s]?based|in[\-\s]?office|at\s+the\s+office)", re.IGNORECASE),
}

KM_VISA_PATTERNS = re.compile(
    r"(kennismigrant|knowledge\s+migrant|highly\s+skilled\s+migrant|"
    r"30%?\s+ruling|sponsor\s+visa|visa\s+sponsor)",
    re.IGNORECASE,
)

SALARY_PATTERNS = re.compile(
    r"(EUR\s*[\d,.]+[\s\-–]*[\d,.]*|"
    r"[\d,.]+\s*[\-–]\s*[\d,.]+\s*(EUR|euro|per\s+(month|year|annum))|"
    r"\u20ac\s*[\d,.]+[\s\-–]*[\d,.]*|"  # € symbol
    r"salary[:\s]+[\d,.]+)",
    re.IGNORECASE,
)

SALARY_PERIOD_PATTERNS = {
    "Year": re.compile(r"(per\s+(year|annum)|annual|yearly|p\.a\.)", re.IGNORECASE),
    "Month": re.compile(r"(per\s+month|monthly|p\.m\.)", re.IGNORECASE),
}

AGENCY_PATTERNS = re.compile(
    r"\b(randstad|yacht|michael\s+page|hays|undisclosed\s+client|"
    r"brunel|manpower|adecco|robert\s+half|reed|staffing|recruitment\s+agency|"
    r"on\s+behalf\s+of\s+(our|a)\s+client|confidential\s+client)\b",
    re.IGNORECASE,
)

PHYGITAL_PATTERNS = re.compile(
    r"\b(iot|internet\s+of\s+things|automotive|robotics|consumer\s+electronics|"
    r"smart\s+device|mobility|hardware|sensor|embedded|wearable|"
    r"manufacturing|physical\s+product|connected\s+device|medtech|"
    r"medical\s+device|ev\s|electric\s+vehicle|3d\s+print|"
    r"industrial\s+automation|mechatronics|firmware)\b",
    re.IGNORECASE,
)

PURE_SAAS_PATTERNS = re.compile(
    r"\b(saas|cloud\s+platform|web\s+application|fintech\s+platform|"
    r"digital\s+marketing|ad[\s\-]?tech|martech|crm\s+platform)\b",
    re.IGNORECASE,
)

VISA_NOT_SUPPORTED_PATTERNS = re.compile(
    r"(no\s+visa\s+sponsor|cannot\s+sponsor|eu\s+(citizen|passport)\s+(only|required)|"
    r"must\s+have\s+(eu|dutch)\s+(work\s+)?permit|"
    r"not\s+able\s+to\s+sponsor)",
    re.IGNORECASE,
)

# Common Dutch words in titles that signal a Dutch-language role
DUTCH_TITLE_PATTERNS = re.compile(
    r"\b(medewerker|beheerder|adviseur|analist|ontwikkelaar|teamleider|"
    r"hoofd|directeur|beleids|coördinator|inkoop|verkoop|facilitair|"
    r"financieel|juridisch|technisch\s+specialist|projectleider|"
    r"ondersteuner|kwaliteit|dienstverlening|stichting)\b",
    re.IGNORECASE,
)


def enrich_and_filter(df, filter_config=None):
    """Apply hard filters and add enrichment columns.

    Hard filters (remove job):
    - Dutch-only JDs (or empty JD with Dutch title)
    - Junior/entry-level roles
    - Empty/missing descriptions (unreviewable)

    Soft flags (keep job, add metadata):
    - Driving licence, Dutch required/preferred, work mode, salary,
      agency, phygital, KM visa, seniority, visa support
    """
    if filter_config is None:
        filter_config = load_filter_config()

    if df.empty:
        return df, []

    results = []
    filtered_log = []  # List of {title, company, reason} for filtered jobs

    for _, row in df.iterrows():
        desc = str(row.get("description", ""))
        title = str(row.get("title", ""))

        # --- HARD FILTERS ---

        # 0. Empty/missing description — flag but keep (LinkedIn often omits JD)
        has_description = bool(desc and desc.strip() not in ("", "None", "nan") and len(desc.strip()) >= 30)
        row["missing_description"] = not has_description

        # 1. Dutch-only JD (by description or by Dutch title keywords) — only check if we have text
        if filter_config.get("skip_dutch_jd", True):
            if (has_description and is_dutch_jd(desc)) or DUTCH_TITLE_PATTERNS.search(title):
                filtered_log.append({
                    "title": title[:60],
                    "company": str(row.get("company", ""))[:30],
                    "reason": "Dutch JD" if (has_description and is_dutch_jd(desc)) else "Dutch title",
                })
                continue

        # 2. Junior/entry-level (reject) — only if we have description to check
        if has_description and SENIORITY_REJECT_PATTERNS.search(desc) and not SENIORITY_FIT_PATTERNS.search(desc):
            filtered_log.append({
                "title": title[:60],
                "company": str(row.get("company", ""))[:30],
                "reason": "Junior/entry-level",
            })
            continue

        # --- SOFT FLAGS (do NOT reject, just tag) ---

        if not has_description:
            # No description — set defaults for all soft flags
            row["driver_license_flagged"] = False
            row["dutch_mandatory"] = False
            row["dutch_nice_to_have"] = False
            row["work_mode"] = "Remote" if row.get("is_remote") is True else "Unspecified"
            row["seniority_fit"] = "Unknown"
            row["km_visa_mentioned"] = False
            row["visa_not_supported"] = False
            row["salary_info"] = ""
            row["salary_period"] = ""
            row["is_agency"] = bool(AGENCY_PATTERNS.search(str(row.get("company", ""))))
            row["phygital_detected"] = False
            row["pure_saas_detected"] = False
            results.append(row)
            continue

        # Driving licence
        row["driver_license_flagged"] = bool(DRIVING_PATTERNS.search(desc))

        # Dutch language
        row["dutch_mandatory"] = bool(DUTCH_REQUIRED_PATTERNS.search(desc)) and not bool(DUTCH_NICE_TO_HAVE_PATTERNS.search(desc))
        row["dutch_nice_to_have"] = bool(DUTCH_NICE_TO_HAVE_PATTERNS.search(desc))

        # Work mode
        detected_modes = [mode for mode, pat in WORK_MODE_PATTERNS.items() if pat.search(desc)]
        row["work_mode"] = detected_modes[0] if len(detected_modes) == 1 else (", ".join(detected_modes) if detected_modes else "Unspecified")

        # Fallback: use jobspy is_remote if we couldn't detect from description
        if row["work_mode"] == "Unspecified" and row.get("is_remote") is True:
            row["work_mode"] = "Remote"

        # Seniority fit
        if SENIORITY_FIT_PATTERNS.search(desc):
            row["seniority_fit"] = "Strong"
        else:
            row["seniority_fit"] = "Medium"

        # KM visa
        row["km_visa_mentioned"] = bool(KM_VISA_PATTERNS.search(desc))

        # Visa not supported
        row["visa_not_supported"] = bool(VISA_NOT_SUPPORTED_PATTERNS.search(desc))

        # Salary extraction
        salary_match = SALARY_PATTERNS.search(desc)
        row["salary_info"] = salary_match.group(0).strip() if salary_match else ""
        period = "Unknown"
        for p, pat in SALARY_PERIOD_PATTERNS.items():
            if pat.search(desc):
                period = p
                break
        row["salary_period"] = period if row["salary_info"] else ""

        # Fallback: use jobspy salary fields if regex found nothing
        if not row["salary_info"] and row.get("min_amount") and str(row.get("min_amount")) != "nan":
            min_amt = row.get("min_amount", "")
            max_amt = row.get("max_amount", "")
            currency = row.get("currency", "EUR")
            interval = row.get("interval", "")
            if min_amt and max_amt:
                row["salary_info"] = f"{currency} {min_amt}-{max_amt}"
            elif min_amt:
                row["salary_info"] = f"{currency} {min_amt}+"
            if interval:
                row["salary_period"] = str(interval).capitalize()

        # Agency detection
        row["is_agency"] = bool(AGENCY_PATTERNS.search(desc)) or bool(AGENCY_PATTERNS.search(str(row.get("company", ""))))

        # Phygital detection
        row["phygital_detected"] = bool(PHYGITAL_PATTERNS.search(desc))
        row["pure_saas_detected"] = bool(PURE_SAAS_PATTERNS.search(desc)) and not row["phygital_detected"]

        results.append(row)

    filtered_df = pd.DataFrame(results)
    reason_counts = {}
    for entry in filtered_log:
        reason_counts[entry["reason"]] = reason_counts.get(entry["reason"], 0) + 1
    skip_details = ", ".join(f"{k}: {v}" for k, v in reason_counts.items())
    print(f"Filtered: {len(df)} -> {len(filtered_df)} jobs ({len(filtered_log)} removed: {skip_details})")
    return filtered_df, filtered_log
