# Jobsearcher Data Schema / 資料架構

## Google Sheet Columns / 工作表欄位

| Column | Source | Description |
|--------|--------|-------------|
| Date | Pipeline | Date job was scored (YYYY-MM-DD) |
| Score | LLM | Match score 0-100 |
| Decision | LLM | Apply (>=75) / Maybe (60-74) / Skip (<60) |
| Title | JobSpy | Job title |
| Company | JobSpy | Company name |
| Industry | LLM | Company industry (from LLM CompanySnapshot) |
| Location | JobSpy | Job location |
| Work Mode | LLM > Filter | Remote/Hybrid/Onsite/Unknown (LLM overrides filter detection) |
| Role Cluster | Filter | Role category tag |
| Phygital Level | LLM | Strong / Moderate / Weak |
| Phygital Reason | LLM | Why this Phygital level (Traditional Chinese) |
| SaaS | Filter | True if pure SaaS keywords detected |
| Seniority Fit | Filter | Strong / Medium / Unknown |
| Language Risk | Filter | English OK / Dutch Mandatory / Dutch Preferred |
| Driver Licence | Filter | True if driving licence mentioned in JD |
| Salary | LLM > Filter | Salary text (LLM first, filter regex fallback) |
| KM Visa (JD) | Filter | True if JD mentions kennismigrant/visa sponsor keywords |
| KM Visa (IND) | IND Register | True if company is on IND recognized sponsor list |
| Agency | Filter | True if recruitment agency detected |
| Travel Min | Lookup | Estimated public transport minutes from Hoofddorp (empty if unknown) |
| Commute | LLM | Commute text from LLM assessment |
| Why Fit | LLM | Strong + Partial match reasons (Traditional Chinese) |
| Why Not Fit | LLM | Gaps + Risks combined (Traditional Chinese) |
| Gap Analysis | LLM | Addressable skill gaps (Traditional Chinese) |
| Top Label | LLM | One-line summary in Traditional Chinese |
| Main Risk | LLM | Primary risk in Traditional Chinese |
| Confidence | LLM | High / Medium / Low -- LLM self-assessed confidence |
| Job URL | JobSpy | Direct link to job posting |
| Travel Link | Pipeline | 9292.nl public transport link |
| Search Keyword | Pipeline | Which search keyword found this job |
| My Verdict | User | Agree / Disagree-Fit / Disagree-Skip (user fills) |
| My Reason | User | Free text feedback (user fills) |

## Scoring Framework / 評分框架

```
Total Score = Phygital + Seniority + Domain + Research + Company + Penalties + Learned Adjustments
```

| Dimension | Range | What it measures |
|-----------|-------|-----------------|
| Phygital relevance | 0 to 40 | Physical+digital integration. Hardware/sensors/IoT/automotive = high. Pure cloud/SaaS = low. |
| Seniority fit | 0 to 20 | 8+ years experience, senior/lead level match |
| Domain relevance | 0 to 15 | Product research, UX, IoT, automotive, electronics |
| Research ownership | 0 to 10 | Owns research end-to-end vs. just executes |
| Company fit | 0 to 10 | Innovation-driven culture, R&D investment signals |
| Language penalty | 0 to -15 | Dutch mandatory = -15, Dutch preferred = -5 |
| Driver licence penalty | 0 to -10 | Required = -10 |
| Pure SaaS penalty | 0 to -40 | From feedback log (increases as user marks more SaaS as not fit) |

### Decision Thresholds / 決策門檻

| Decision | Score | Meaning |
|----------|-------|---------|
| **Apply** | >= 75 | Genuinely relevant, no major blockers. Act on it. |
| **Maybe** | 60-74 | Good relevance but real uncertainty. Worth reviewing. |
| **Skip** | < 60 | Weak fit or too many assumptions needed. |

Blockers (Dutch mandatory, visa not supported, driving licence mandatory) override score and force Skip regardless of points.

### Learned Adjustments / 自動學習 (Auto-Learning)

The system tracks user disagreements via the `My Verdict` column:

- **Disagree-Fit**: "System said Skip but I think it fits" -- system learns what it undervalues
- **Disagree-Skip**: "System said Apply/Maybe but I'm not interested" -- system learns what it overvalues

After 5 similar disagreements, the system auto-adjusts scoring weights by +/-5 points per trigger. All adjustments are logged in `config/feedback_log.json` under `weight_adjustments` with timestamps and reasons.

## KM Visa Checking / KM簽證檢查

Two sources:
1. **JD keywords**: Scans job description for "kennismigrant", "highly skilled migrant", "30% ruling", "visa sponsor"
2. **IND Register**: Downloads the official IND recognized sponsor list (https://ind.nl/en/public-register-recognised-sponsors/), cached locally in `config/km_sponsors.json`, refreshed every 30 days. Fuzzy-matches company names using rapidfuzz (handles B.V./N.V., abbreviations, word order differences).

## Pre-LLM Filters / 前置過濾

Jobs are removed before LLM scoring if:
- JD is written primarily in Dutch (langid detection)
- Job title contains Dutch-language keywords (medewerker, beheerder, etc.)
- Role is explicitly junior/entry-level (and not also marked senior)

Filtered jobs are logged with reasons and visible in the pipeline console output. They are NOT sent to the email digest or Google Sheet.

## Email Digest Structure / 電郵摘要

The daily email shows:
1. **Keyword stats table** -- jobs found per keyword, with Apply/Maybe counts
2. **Apply cards** (score >= 75) -- full 3-layer decision cards
3. **Maybe cards** (score 60-74) -- full 3-layer decision cards
4. **Skip summary** -- one-line count, details in Google Sheet only
5. **Pre-filtered count** -- how many removed before scoring

## Pipeline Flow / 流程

```
[Scrape] -> [Filter+Enrich] -> [Travel Time] -> [LLM Score] -> [Google Sheet] -> [Email Digest]
    |              |                  |               |               |                |
  JobSpy       langid, regex     Lookup table    OpenRouter      All jobs        Apply+Maybe
  +trafilatura + IND register                    free models     +feedback         only
                 fuzzy match                                      columns
```

## Feedback Loop / 回饋迴路

```
User reviews Sheet -> Fills My Verdict + My Reason
                          |
Pipeline start -> Sync verdicts from Sheet
                          |
                  Detect patterns (5+ similar signals)
                          |
                  Auto-adjust weights (+/-5 per trigger)
                          |
                  Feed into LLM scoring prompt
```
