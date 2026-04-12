# Phase 2: CV Tailoring & Cover Letter Generation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the email digest into an action interface (View/Apply/Skip), auto-tailor CV + cover letter when "Apply" is clicked, and provide an interactive review UI for editing before PDF export.

**Architecture:** Email buttons trigger n8n webhooks → `generator.py` calls the LLM router to rewrite all CV sections for a specific JD → saves structured JSON draft → Flask review UI (`app.py`) serves a three-panel editor → `weasyprint` renders styled PDF on export. Skip button captures feedback into Google Sheets via the existing feedback loop.

**Tech Stack:** Python, Flask, Jinja2, weasyprint, existing LLM router (`src/llm/router.py`), existing Sheets integration (`src/sheets.py`), vanilla JS for inline editing.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/generator.py` (new) | CLI entry point for CV tailoring — gathers job context, selects angle, calls LLM, saves draft, sends notification email |
| `src/app.py` (new) | Flask server — review UI, regenerate API, save API, PDF export, skip feedback page |
| `templates/review.html` (new) | Three-panel review page (JD context / CV draft / cover letter) with edit + regenerate |
| `templates/skip.html` (new) | Minimal skip feedback page with quick-select reasons |
| `templates/cv_template.html` (new) | Two-column CV layout for weasyprint PDF rendering |
| `templates/cl_template.html` (new) | Single-column cover letter layout for weasyprint PDF rendering |
| `src/matcher.py` (modify) | Add `KeySkills` field to `SCORING_PROMPT` JSON schema |
| `src/notifier.py` (modify) | Replace "View & Apply" button with View/Apply/Skip; add KeySkills chip rendering; add draft-ready notification email function |

---

## Task 1: Add KeySkills to SCORING_PROMPT

**Files:**
- Modify: `src/matcher.py:196-198` (JSON schema section of SCORING_PROMPT)
- Modify: `src/notifier.py:276-344` (`_parse_card()` function)
- Modify: `src/notifier.py:183-188` (email template, after signals div)
- Modify: `src/notifier.py:90-123` (email CSS)
- Test: `tests/test_matcher.py` (existing — verify prompt still formats)

- [ ] **Step 1: Add KeySkills field to SCORING_PROMPT JSON schema**

In `src/matcher.py`, find the line (around line 199):
```python
  "Risks": ["繁體中文，結構性風險，最多3條"],
```

Add after it:
```python
  "KeySkills": ["5-8 key skills/tools/qualifications explicitly required by the JD. English. Short labels (e.g. 'Python', 'Agile/Scrum', '5+ yrs PM', 'SolidWorks'). No soft skills or generic terms."],
```

- [ ] **Step 2: Add key_skills extraction to _parse_card()**

In `src/notifier.py`, in the `_parse_card()` function, find the return dict (around line 301). Add after the `"missing_info"` line:
```python
        # Key skills extracted from JD (for skill chip rendering)
        "key_skills": r.get("KeySkills", []),
```

- [ ] **Step 3: Add skill chip CSS to EMAIL_TEMPLATE**

In `src/notifier.py`, find the CSS line (around line 117):
```css
  /* Apply button */
  .apply-btn { display: inline-block; margin-top: 10px; padding: 7px 22px; background: #1d1d1f; color: #fff; border-radius: 8px; text-decoration: none; font-size: 12px; font-weight: 600; }
```

Add before it:
```css
  /* Skill chips */
  .skill-chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0 2px; }
  .skill-chip { font-size: 10px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
  .skill-match { background: #e8f8ec; color: #1b7a2d; }
  .skill-gap { background: #fce8e6; color: #c5221f; }
  .skill-neutral { background: #f0f0f5; color: #515154; }
```

- [ ] **Step 4: Add skill chips to email template HTML**

In `src/notifier.py`, find the template section after the signals div closing (around line 188, after `</div>` that closes signals):
```html
    {% if j.main_risk %}
    <div class="risk-line">{{ j.main_risk }}</div>
    {% endif %}
  </div>
```

Add before `</div>` (the one that closes L1):
```html
    {% if j.key_skills %}
    <div class="skill-chips">
      {% for sk in j.key_skills %}<span class="skill-chip skill-neutral">{{ sk }}</span>{% endfor %}
    </div>
    {% endif %}
```

Note: For MVP, all chips are neutral-colored. Profile matching (green/red) will be added in a follow-up once we confirm the LLM outputs KeySkills correctly.

- [ ] **Step 5: Run tests to verify prompt still formats**

Run: `python -m pytest tests/ -q`
Expected: All 53 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/matcher.py src/notifier.py
git commit -m "feat: add KeySkills to scoring prompt + skill chips in email digest"
```

---

## Task 2: Replace "View & Apply" with View / Apply / Skip Buttons

**Files:**
- Modify: `src/notifier.py:116-117` (CSS for buttons)
- Modify: `src/notifier.py:256` (template HTML for buttons)
- Modify: `src/notifier.py:276-344` (`_parse_card()` — add job_id)

- [ ] **Step 1: Add helper to extract job_id from URL**

In `src/notifier.py`, add after the imports (around line 17, after `load_dotenv()`):

```python
import re as _re
import urllib.parse as _urlparse

def _job_id_from_url(url):
    """Extract a short job ID from a job URL.
    LinkedIn: https://www.linkedin.com/jobs/view/4400368636 -> '4400368636'
    Other: URL-safe hash.
    """
    m = _re.search(r"/jobs/view/(\d+)", str(url))
    if m:
        return m.group(1)
    return _urlparse.quote(str(url), safe="")
```

- [ ] **Step 2: Add job_id to _parse_card()**

In the return dict of `_parse_card()`, add after `"job_url"`:
```python
        "job_id": _job_id_from_url(row.get("job_url", "")),
```

- [ ] **Step 3: Replace button CSS**

In `src/notifier.py`, replace the existing apply-btn CSS:
```css
  /* Apply button */
  .apply-btn { display: inline-block; margin-top: 10px; padding: 7px 22px; background: #1d1d1f; color: #fff; border-radius: 8px; text-decoration: none; font-size: 12px; font-weight: 600; }
```

With:
```css
  /* Action buttons */
  .action-btns { display: flex; gap: 8px; margin-top: 10px; }
  .btn { display: inline-block; padding: 7px 18px; border-radius: 8px; text-decoration: none; font-size: 12px; font-weight: 600; text-align: center; }
  .btn-view { background: #f0f0f5; color: #1d1d1f; }
  .btn-apply { background: #34c759; color: #fff; }
  .btn-skip { background: #f0f0f5; color: #86868b; }
```

- [ ] **Step 4: Replace button HTML in template**

In `src/notifier.py`, replace (around line 256):
```html
    <a href="{{ j.job_url }}" class="apply-btn">View &amp; Apply</a>
```

With:
```html
    <div class="action-btns">
      <a href="{{ j.job_url }}" class="btn btn-view">View</a>
      <a href="http://localhost:5000/tailor/{{ j.job_id }}" class="btn btn-apply">Apply</a>
      <a href="http://localhost:5000/skip/{{ j.job_id }}" class="btn btn-skip">Skip</a>
    </div>
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/notifier.py
git commit -m "feat: replace View & Apply with separate View/Apply/Skip buttons in email digest"
```

---

## Task 3: Build the Tailoring Engine — `src/generator.py`

**Files:**
- Create: `src/generator.py`
- Test: `tests/test_generator.py`

- [ ] **Step 1: Write tests for job_id extraction and angle selection**

Create `tests/test_generator.py`:

```python
"""Tests for src/generator.py — CV tailoring engine."""
import pytest


def test_extract_job_id_linkedin():
    from src.generator import extract_job_id
    assert extract_job_id("https://www.linkedin.com/jobs/view/4400368636") == "4400368636"


def test_extract_job_id_other_url():
    from src.generator import extract_job_id
    result = extract_job_id("https://example.com/jobs/123")
    assert isinstance(result, str)
    assert len(result) > 0


def test_select_angle_engineering():
    from src.generator import select_angle
    match_result = {"PhygitalAssessment": {"Level": "Strong"}}
    assert select_angle("Product Engineer", match_result) == "Product Development Engineer"


def test_select_angle_strategy():
    from src.generator import select_angle
    match_result = {"PhygitalAssessment": {"Level": "Weak"}}
    assert select_angle("Product Manager", match_result) == "Strategic Product Lead"


def test_select_angle_hybrid():
    from src.generator import select_angle
    match_result = {"PhygitalAssessment": {"Level": "Moderate"}}
    assert select_angle("Innovation Lead", match_result) == "Hybrid"


def test_build_tailoring_prompt_contains_jd():
    from src.generator import build_tailoring_prompt
    prompt = build_tailoring_prompt(
        jd_text="We need a Python engineer with IoT experience",
        match_result={"WhyFit": {"StrongMatch": ["test"]}, "KeySkills": ["Python", "IoT"]},
        base_cv="# Ava Tse\nSenior Product Researcher",
        angle="Product Development Engineer",
    )
    assert "Python engineer with IoT" in prompt
    assert "Product Development Engineer" in prompt
    assert "Ava Tse" in prompt


def test_build_tailoring_prompt_contains_rules():
    from src.generator import build_tailoring_prompt
    prompt = build_tailoring_prompt(
        jd_text="test JD",
        match_result={},
        base_cv="# Ava Tse",
        angle="Hybrid",
    )
    assert "NEVER invent experience" in prompt
    assert "Mirror specific keywords" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_generator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.generator'`

- [ ] **Step 3: Create `src/generator.py` with core functions**

Create `src/generator.py`:

```python
"""CV Tailoring Engine — generates tailored CV + cover letter for a specific job.

Usage: python src/generator.py --job-url <url>

Gathers the job's match_result + JD text, selects a CV angle, calls the LLM
to rewrite all CV sections, and saves structured output to output/tailored/<job_id>/.
"""

import argparse
import json
import re
import hashlib
from pathlib import Path

import yaml

from src.llm import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_job_id(url):
    """Extract a short job ID from a job URL.
    LinkedIn: .../jobs/view/4400368636 -> '4400368636'
    Other: first 12 chars of URL hash.
    """
    m = re.search(r"/jobs/view/(\d+)", str(url))
    if m:
        return m.group(1)
    return hashlib.md5(str(url).encode()).hexdigest()[:12]


ENGINEERING_KEYWORDS = re.compile(
    r"\b(engineer|innovation|r&d|prototype|prototyping|hardware|sensor|cad)\b", re.I
)
STRATEGY_KEYWORDS = re.compile(
    r"\b(product\s+owner|product\s+manager|strategy|strategist|lead|director)\b", re.I
)


def select_angle(title, match_result):
    """Pick CV angle based on job title + phygital assessment.

    Returns one of: 'Product Development Engineer', 'Strategic Product Lead', 'Hybrid'.
    """
    phy_level = match_result.get("PhygitalAssessment", {}).get("Level", "Weak")
    title_lower = str(title).lower()

    eng_match = bool(ENGINEERING_KEYWORDS.search(title_lower))
    strat_match = bool(STRATEGY_KEYWORDS.search(title_lower))

    if phy_level == "Strong" and (eng_match or not strat_match):
        return "Product Development Engineer"
    if strat_match and not eng_match:
        return "Strategic Product Lead"
    if phy_level == "Strong":
        return "Product Development Engineer"
    if strat_match:
        return "Strategic Product Lead"
    return "Hybrid"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _load_cover_letter_reference():
    """Load the Philips cover letter as style reference."""
    ref_path = Path("profile/cover_letter_reference.md")
    if ref_path.exists():
        return ref_path.read_text(encoding="utf-8")
    return ""


def build_tailoring_prompt(jd_text, match_result, base_cv, angle):
    """Build the LLM prompt for CV + cover letter tailoring."""
    key_skills = match_result.get("KeySkills", [])
    why_fit = match_result.get("WhyFit", {})
    gaps = match_result.get("Gaps", [])
    role_summary = match_result.get("CardSummary", {}).get("RoleSummary", "")
    cl_ref = _load_cover_letter_reference()

    cl_section = ""
    if cl_ref:
        cl_section = f"""
## Cover Letter Style Reference
Use this as a style and structure guide — adapt the content to the new role.
{cl_ref}
"""

    return f"""### OUTPUT CONTRACT — READ FIRST
Your entire response MUST be a single JSON object and NOTHING else.
- First character: `{{`
- Last character: `}}`
- No preamble. No markdown fences. No comments after.

You are a CV Tailoring Engine. Given a job description, match analysis, and a base CV,
rewrite every section of the CV to maximize relevance for this specific role.
Also generate a tailored cover letter.

## CV Angle: {angle}
{"Lead with hardware, prototyping, sensors, CAD, manufacturing experience." if angle == "Product Development Engineer" else ""}
{"Lead with roadmap, VOC, pricing, stakeholder management, data strategy." if angle == "Strategic Product Lead" else ""}
{"Balance both engineering and strategy based on what the JD emphasizes most." if angle == "Hybrid" else ""}

## Job Description
{jd_text[:5000]}

## Match Analysis (already computed)
Role Summary: {role_summary}
Key Skills Required: {json.dumps(key_skills)}
Strong Match: {json.dumps(why_fit.get("StrongMatch", []))}
Partial Match: {json.dumps(why_fit.get("PartialMatch", []))}
Gaps: {json.dumps(gaps)}

## Base CV (rewrite from this — all facts must come from here)
{base_cv}
{cl_section}
## CRITICAL RULES
1. NEVER invent experience Ava doesn't have — only reframe, emphasize, and reorder existing bullets
2. Mirror specific keywords/tools/methods from the JD in bullets and skills labels
3. Every role from base CV must appear — expand relevant roles to 3-4 bullets, compress less relevant to 1-2
4. Skills categories can be renamed and reordered to lead with JD-relevant skills
5. Cover letter opening must hook on the company's specific domain/product, not generic enthusiasm
6. Cover letter body maps 2-3 StrongMatch items into concrete narrative paragraphs
7. All output in English
8. Keep CV to 2 pages maximum (6 roles, 3 skill categories, concise bullets)
9. Headline should reflect the target role title or a close variant
10. Summary: 3-4 sentences, mirrors JD language, positions Ava for THIS specific role

### Output the following JSON:

{{
  "angle_used": "{angle}",
  "headline": "Job-specific headline, e.g. Product Innovation Engineer",
  "summary": "3-4 sentences positioning Ava for this role",
  "experience": [
    {{
      "company": "BYD",
      "title": "Adjusted title if needed",
      "period": "Sep 2023 - Present",
      "location": "Amsterdam, Netherlands",
      "company_desc": "Leading electric car manufacturer",
      "bullets": ["Rewritten bullet 1", "Rewritten bullet 2"]
    }}
  ],
  "skills": {{
    "Category 1 Name": ["skill1", "skill2", "skill3"],
    "Category 2 Name": ["skill1", "skill2", "skill3"],
    "Category 3 Name": ["skill1", "skill2", "skill3"]
  }},
  "cover_letter": {{
    "opening": "Domain-specific hook paragraph (3-4 sentences)",
    "experience_paragraph": "Maps strongest match items into narrative (4-5 sentences)",
    "skills_paragraph": "Technical depth + cross-functional value (3-4 sentences)",
    "closing": "Company-vision alignment + call to action (2-3 sentences)"
  }}
}}
"""


# ---------------------------------------------------------------------------
# Draft I/O
# ---------------------------------------------------------------------------

def save_draft(job_id, draft_json, metadata, output_dir="output/tailored"):
    """Save tailoring draft to output/tailored/<job_id>/."""
    draft_dir = Path(output_dir) / job_id
    draft_dir.mkdir(parents=True, exist_ok=True)

    # Structured JSON (for review UI)
    (draft_dir / "cv_draft.json").write_text(
        json.dumps(draft_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Readable markdown CV
    md_lines = [f"# {draft_json.get('headline', 'Ava Tse')}\n"]
    md_lines.append(f"{draft_json.get('summary', '')}\n")
    for exp in draft_json.get("experience", []):
        md_lines.append(f"\n## {exp.get('title', '')} — {exp.get('company', '')}")
        md_lines.append(f"*{exp.get('company_desc', '')}*")
        md_lines.append(f"{exp.get('period', '')} | {exp.get('location', '')}\n")
        for b in exp.get("bullets", []):
            md_lines.append(f"- {b}")
    md_lines.append("\n## Core Skills\n")
    for cat, skills in draft_json.get("skills", {}).items():
        md_lines.append(f"**{cat}:** {', '.join(skills)}")
    (draft_dir / "cv_draft.md").write_text("\n".join(md_lines), encoding="utf-8")

    # Cover letter markdown
    cl = draft_json.get("cover_letter", {})
    cl_text = f"""Dear Hiring Manager,

{cl.get('opening', '')}

{cl.get('experience_paragraph', '')}

{cl.get('skills_paragraph', '')}

{cl.get('closing', '')}

Yours sincerely,
Ava Tse
{draft_json.get('headline', '')}
"""
    (draft_dir / "cover_letter.md").write_text(cl_text, encoding="utf-8")

    # Metadata
    (draft_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return draft_dir


def load_draft(job_id, output_dir="output/tailored"):
    """Load a saved draft. Returns (draft_json, metadata) or (None, None)."""
    draft_dir = Path(output_dir) / job_id
    cv_path = draft_dir / "cv_draft.json"
    meta_path = draft_dir / "metadata.json"
    if not cv_path.exists():
        return None, None
    draft = json.loads(cv_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return draft, meta


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_base_cv(path="profile/cv_base.md"):
    return Path(path).read_text(encoding="utf-8")


def load_job_from_cache(job_url, cache_path="output/enriched_jobs.pkl"):
    """Load a job row from the enriched cache by URL."""
    import pandas as pd
    cache = Path(cache_path)
    if not cache.exists():
        return None
    df = pd.read_pickle(cache)
    matches = df[df["job_url"] == job_url]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def load_job_from_sheets(job_url):
    """Load a job row from Google Sheets by URL. Returns dict or None."""
    import os
    from src.sheets import get_client

    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    if not spreadsheet_id:
        return None

    try:
        client = get_client()
        ws = client.open_by_key(spreadsheet_id).worksheet("Jobs")
        headers = ws.row_values(1)
        url_col_idx = headers.index("Job URL") if "Job URL" in headers else -1
        if url_col_idx < 0:
            return None

        all_urls = ws.col_values(url_col_idx + 1)
        for row_num, url in enumerate(all_urls):
            if url == job_url:
                row_values = ws.row_values(row_num + 1)
                return dict(zip(headers, row_values))
        return None
    except Exception as e:
        print(f"  [WARN] Could not load from Sheets: {e}")
        return None


def _extract_json(text):
    """Pull first balanced JSON object from LLM response. Reuses matcher logic."""
    from src.matcher import _extract_json as matcher_extract
    return matcher_extract(text)


def tailor_cv(job_url):
    """Main tailoring pipeline. Returns (job_id, draft_dir) or raises."""
    job_id = extract_job_id(job_url)
    print(f"\n{'='*60}")
    print(f"CV TAILORING — {job_url}")
    print(f"Job ID: {job_id}")
    print(f"{'='*60}")

    # Step 1: gather context
    print("Step 1: Loading job context...")
    job_data = load_job_from_cache(job_url)
    if job_data is None:
        job_data = load_job_from_sheets(job_url)
    if job_data is None:
        raise ValueError(f"Job not found in cache or Sheets: {job_url}")

    match_result = {}
    raw_match = job_data.get("match_result", "{}")
    if isinstance(raw_match, str):
        try:
            match_result = json.loads(raw_match)
        except json.JSONDecodeError:
            pass
    elif isinstance(raw_match, dict):
        match_result = raw_match

    jd_text = str(job_data.get("description", ""))
    title = str(job_data.get("title", "Unknown"))
    company = str(job_data.get("company", "Unknown"))

    print(f"  Title: {title}")
    print(f"  Company: {company}")
    print(f"  JD length: {len(jd_text)} chars")

    # Step 2: select angle
    angle = select_angle(title, match_result)
    print(f"Step 2: Angle selected → {angle}")

    # Step 3: build prompt and call LLM
    base_cv = load_base_cv()
    prompt = build_tailoring_prompt(jd_text, match_result, base_cv, angle)
    print(f"Step 3: Calling LLM ({len(prompt)} chars prompt)...")

    response = router.call_llm(prompt)
    json_blob = _extract_json(response)
    if not json_blob:
        raise RuntimeError(f"LLM returned no valid JSON. Raw: {response[:300]}")

    draft = json.loads(json_blob)
    print(f"  Got draft: headline='{draft.get('headline', '?')}', {len(draft.get('experience', []))} roles")

    # Step 4: save
    metadata = {
        "job_id": job_id,
        "job_url": job_url,
        "job_title": title,
        "company": company,
        "angle_used": angle,
        "key_skills": match_result.get("KeySkills", []),
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }
    draft_dir = save_draft(job_id, draft, metadata)
    print(f"Step 4: Draft saved to {draft_dir}/")

    # Step 5: notify
    print("Step 5: Sending notification email...")
    try:
        _send_draft_notification(job_id, title, company, draft)
    except Exception as e:
        print(f"  [WARN] Email notification failed: {e}")

    print(f"\nDone! Review at: http://localhost:5000/review/{job_id}")
    return job_id, draft_dir


def _send_draft_notification(job_id, title, company, draft):
    """Send a short email that the draft is ready."""
    from src.notifier import send_email

    headline = draft.get("headline", "")
    summary = draft.get("summary", "")

    body = f"""<html><body style="font-family: -apple-system, sans-serif; padding: 20px;">
<h2>CV Ready: {title} @ {company}</h2>
<p><b>Headline:</b> {headline}</p>
<p><b>Summary:</b> {summary}</p>
<p><a href="http://localhost:5000/review/{job_id}"
   style="display:inline-block; padding:10px 24px; background:#34c759; color:#fff;
          border-radius:8px; text-decoration:none; font-weight:600;">
   Review &amp; Export PDF</a></p>
</body></html>"""

    send_email(
        subject=f"CV Ready: {title} @ {company}",
        html_body=body,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tailor CV for a specific job")
    parser.add_argument("--job-url", required=True, help="Job URL to tailor CV for")
    args = parser.parse_args()
    tailor_cv(args.job_url)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_generator.py -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/generator.py tests/test_generator.py
git commit -m "feat: add CV tailoring engine (generator.py) with angle selection + LLM prompt"
```

---

## Task 4: Add `send_email` support for arbitrary subject/body

**Files:**
- Modify: `src/notifier.py` — check if `send_email` already accepts `subject` + `html_body` params

- [ ] **Step 1: Read current send_email signature**

Check `src/notifier.py` for the `send_email` function signature and update if needed.

- [ ] **Step 2: Ensure send_email accepts subject and html_body params**

The current `send_email` may be hardcoded for digest emails. If so, refactor to accept:

```python
def send_email(subject=None, html_body=None, df=None, filtered_count=0, keyword_stats=None):
```

If `html_body` is provided, use it directly. Otherwise fall back to existing `build_email_html(df, ...)` logic. This keeps backward compatibility.

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -q`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/notifier.py
git commit -m "refactor: make send_email accept arbitrary subject + html_body"
```

---

## Task 5: Build the Flask Review UI — `src/app.py`

**Files:**
- Create: `src/app.py`
- Create: `templates/review.html`
- Create: `templates/skip.html`

- [ ] **Step 1: Create `src/app.py` with all routes**

Create `src/app.py`:

```python
"""Flask review UI for CV tailoring — edit, regenerate, export PDF."""

import json
import os
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv

load_dotenv()

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
)

TAILORED_DIR = Path("output/tailored")


def _load_draft(job_id):
    """Load draft JSON + metadata for a job."""
    from src.generator import load_draft
    return load_draft(job_id)


def _load_job_context(job_id):
    """Load JD and match_result for the left panel."""
    meta_path = TAILORED_DIR / job_id / "metadata.json"
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    job_url = meta.get("job_url", "")

    from src.generator import load_job_from_cache, load_job_from_sheets
    job_data = load_job_from_cache(job_url) or load_job_from_sheets(job_url) or {}

    match_result = {}
    raw = job_data.get("match_result", "{}")
    if isinstance(raw, str):
        try:
            match_result = json.loads(raw)
        except json.JSONDecodeError:
            pass
    elif isinstance(raw, dict):
        match_result = raw

    return {
        "description": str(job_data.get("description", ""))[:5000],
        "match_result": match_result,
        "title": meta.get("job_title", ""),
        "company": meta.get("company", ""),
        "key_skills": meta.get("key_skills", []),
        "job_url": job_url,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/review/<job_id>")
def review(job_id):
    draft, meta = _load_draft(job_id)
    if draft is None:
        return f"<h1>Draft not found for job {job_id}</h1>", 404
    context = _load_job_context(job_id)
    return render_template("review.html", draft=draft, meta=meta, context=context, job_id=job_id)


@app.route("/tailor/<job_id>")
def trigger_tailor(job_id):
    """Trigger tailoring from email button click. Looks up job URL from cache."""
    import pandas as pd
    cache_path = Path("output/enriched_jobs.pkl")
    if not cache_path.exists():
        return "<h1>No enriched job cache found. Run the pipeline first.</h1>", 404

    df = pd.read_pickle(cache_path)
    # Find the job URL that contains this job_id
    matches = df[df["job_url"].str.contains(job_id, na=False)]
    if matches.empty:
        return f"<h1>Job {job_id} not found in cache</h1>", 404

    job_url = str(matches.iloc[0]["job_url"])

    from src.generator import tailor_cv
    try:
        tailor_cv(job_url)
        return f"""<html><body style="font-family: sans-serif; text-align: center; padding: 60px;">
        <h1>CV draft generated!</h1>
        <p><a href="/review/{job_id}"
              style="display:inline-block; padding:12px 30px; background:#34c759; color:#fff;
                     border-radius:8px; text-decoration:none; font-weight:600; font-size:16px;">
           Open Review</a></p>
        </body></html>"""
    except Exception as e:
        return f"<h1>Tailoring failed</h1><p>{str(e)[:500]}</p>", 500


@app.route("/api/save", methods=["POST"])
def save_edit():
    """Save an edited section back to the draft JSON."""
    data = request.json
    job_id = data.get("job_id")
    section = data.get("section")  # e.g. "headline", "summary", "experience.0.bullets", "cover_letter.opening"
    content = data.get("content")

    draft_path = TAILORED_DIR / job_id / "cv_draft.json"
    if not draft_path.exists():
        return jsonify({"error": "Draft not found"}), 404

    draft = json.loads(draft_path.read_text(encoding="utf-8"))

    # Navigate to the right section and update
    keys = section.split(".")
    target = draft
    for k in keys[:-1]:
        if k.isdigit():
            target = target[int(k)]
        else:
            target = target[k]
    final_key = keys[-1]
    if final_key.isdigit():
        target[int(final_key)] = content
    else:
        target[final_key] = content

    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/regenerate", methods=["POST"])
def regenerate_section():
    """Regenerate one section of the CV/cover letter via LLM."""
    data = request.json
    job_id = data.get("job_id")
    section = data.get("section")
    instruction = data.get("instruction", "")

    draft, meta = _load_draft(job_id)
    if draft is None:
        return jsonify({"error": "Draft not found"}), 404

    context = _load_job_context(job_id)

    from src.llm import router
    prompt = f"""Regenerate ONLY this section of a tailored CV. Return ONLY the new content as a JSON value (string or array of strings).

Section to regenerate: {section}
{"User instruction: " + instruction if instruction else ""}

Job description (excerpt):
{context.get('description', '')[:2000]}

Key skills required: {json.dumps(context.get('key_skills', []))}

Current full draft for context:
{json.dumps(draft, indent=2, ensure_ascii=False)[:3000]}

RULES:
- NEVER invent experience — only reframe existing content
- Mirror JD keywords
- Return ONLY the JSON value for this section, nothing else
"""

    response = router.call_llm(prompt)
    # Try to parse as JSON value
    try:
        result = json.loads(response.strip())
    except json.JSONDecodeError:
        # If it's a plain string, use as-is
        result = response.strip().strip('"')

    return jsonify({"content": result})


@app.route("/skip/<job_id>")
def skip_page(job_id):
    meta_path = TAILORED_DIR / job_id / "metadata.json"
    title = company = ""
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        title = meta.get("job_title", "")
        company = meta.get("company", "")
    else:
        # Try to look up from enriched cache
        import pandas as pd
        cache = Path("output/enriched_jobs.pkl")
        if cache.exists():
            df = pd.read_pickle(cache)
            matches = df[df["job_url"].str.contains(job_id, na=False)]
            if not matches.empty:
                title = str(matches.iloc[0].get("title", ""))
                company = str(matches.iloc[0].get("company", ""))

    return render_template("skip.html", job_id=job_id, title=title, company=company)


@app.route("/api/skip", methods=["POST"])
def submit_skip():
    """Write skip feedback to Google Sheets."""
    data = request.json
    job_id = data.get("job_id")
    reason = data.get("reason", "")
    detail = data.get("detail", "")

    full_reason = f"{reason}: {detail}" if detail else reason

    # Find job URL from cache
    import pandas as pd
    cache = Path("output/enriched_jobs.pkl")
    job_url = ""
    if cache.exists():
        df = pd.read_pickle(cache)
        matches = df[df["job_url"].str.contains(job_id, na=False)]
        if not matches.empty:
            job_url = str(matches.iloc[0]["job_url"])

    # Write to Sheets
    try:
        spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        if spreadsheet_id and job_url:
            from src.sheets import get_client
            client = get_client()
            ws = client.open_by_key(spreadsheet_id).worksheet("Jobs")
            headers = ws.row_values(1)
            url_col = headers.index("Job URL") + 1 if "Job URL" in headers else -1
            verdict_col = headers.index("My Verdict") + 1 if "My Verdict" in headers else -1
            reason_col = headers.index("My Reason") + 1 if "My Reason" in headers else -1

            if url_col > 0:
                all_urls = ws.col_values(url_col)
                for row_num, u in enumerate(all_urls):
                    if u == job_url:
                        if verdict_col > 0:
                            ws.update_cell(row_num + 1, verdict_col, "Disagree-Skip")
                        if reason_col > 0:
                            ws.update_cell(row_num + 1, reason_col, full_reason)
                        break
    except Exception as e:
        print(f"[WARN] Could not write skip feedback to Sheets: {e}")

    return jsonify({"ok": True})


@app.route("/api/export/cv/<job_id>")
def export_cv_pdf(job_id):
    """Render CV as styled PDF and return for download."""
    draft, meta = _load_draft(job_id)
    if draft is None:
        return "Draft not found", 404

    from weasyprint import HTML
    html = render_template("cv_template.html", draft=draft, meta=meta)
    pdf_dir = TAILORED_DIR / job_id
    company = meta.get("company", "Company").replace(" ", "_")
    title = meta.get("job_title", "Role").replace(" ", "_")[:30]
    pdf_path = pdf_dir / f"Ava_Tse_CV_{company}_{title}.pdf"
    HTML(string=html).write_pdf(str(pdf_path))
    return send_file(str(pdf_path.resolve()), as_attachment=True)


@app.route("/api/export/cl/<job_id>")
def export_cl_pdf(job_id):
    """Render cover letter as styled PDF and return for download."""
    draft, meta = _load_draft(job_id)
    if draft is None:
        return "Draft not found", 404

    from weasyprint import HTML
    html = render_template("cl_template.html", draft=draft, meta=meta)
    pdf_dir = TAILORED_DIR / job_id
    company = meta.get("company", "Company").replace(" ", "_")
    title = meta.get("job_title", "Role").replace(" ", "_")[:30]
    pdf_path = pdf_dir / f"Ava_Tse_CoverLetter_{company}_{title}.pdf"
    HTML(string=html).write_pdf(str(pdf_path))
    return send_file(str(pdf_path.resolve()), as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
```

- [ ] **Step 2: Create `templates/skip.html`**

Create `templates/skip.html`:

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Skip — {{ title }}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f7; color: #1d1d1f; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  .card { background: #fff; border-radius: 16px; padding: 32px; max-width: 420px; width: 100%; box-shadow: 0 2px 12px rgba(0,0,0,0.08); text-align: center; }
  h2 { font-size: 18px; margin-bottom: 4px; }
  .sub { color: #86868b; font-size: 13px; margin-bottom: 20px; }
  .reasons { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-bottom: 16px; }
  .reason-btn { padding: 8px 16px; border: 1px solid #e5e5ea; border-radius: 20px; background: #fff; cursor: pointer; font-size: 13px; transition: all 0.15s; }
  .reason-btn:hover { background: #f0f0f5; }
  .reason-btn.selected { background: #1d1d1f; color: #fff; border-color: #1d1d1f; }
  input[type=text] { width: 100%; padding: 10px 14px; border: 1px solid #e5e5ea; border-radius: 10px; font-size: 13px; margin-bottom: 16px; }
  .submit-btn { padding: 10px 32px; background: #1d1d1f; color: #fff; border: none; border-radius: 10px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .submit-btn:disabled { opacity: 0.4; }
  .done { display: none; }
  .done h2 { color: #34c759; }
</style>
</head>
<body>
<div class="card" id="form-card">
  <h2>{{ title }}</h2>
  <div class="sub">{{ company }}</div>

  <div class="reasons">
    <button class="reason-btn" data-reason="Wrong domain">Wrong domain</button>
    <button class="reason-btn" data-reason="Too junior">Too junior</button>
    <button class="reason-btn" data-reason="Too senior">Too senior</button>
    <button class="reason-btn" data-reason="Pure SaaS">Pure SaaS</button>
    <button class="reason-btn" data-reason="Bad company">Bad company</button>
    <button class="reason-btn" data-reason="Location">Location</button>
    <button class="reason-btn" data-reason="Other">Other</button>
  </div>

  <input type="text" id="detail" placeholder="Optional: add a note...">
  <br>
  <button class="submit-btn" id="submit" disabled onclick="submitSkip()">Submit</button>
</div>

<div class="card done" id="done-card">
  <h2>Noted!</h2>
  <div class="sub">Feedback saved. You can close this tab.</div>
</div>

<script>
let selectedReason = '';
document.querySelectorAll('.reason-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.reason-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    selectedReason = btn.dataset.reason;
    document.getElementById('submit').disabled = false;
  });
});

function submitSkip() {
  const detail = document.getElementById('detail').value;
  fetch('/api/skip', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({job_id: '{{ job_id }}', reason: selectedReason, detail: detail})
  }).then(() => {
    document.getElementById('form-card').style.display = 'none';
    document.getElementById('done-card').style.display = 'block';
  });
}
</script>
</body>
</html>
```

- [ ] **Step 3: Run the Flask app to verify routes load**

Run: `python -c "from src.app import app; print('Routes:', [r.rule for r in app.url_map.iter_rules()])"`
Expected: List of routes including `/review/<job_id>`, `/tailor/<job_id>`, `/skip/<job_id>`, `/api/save`, `/api/regenerate`, `/api/export/cv/<job_id>`, `/api/export/cl/<job_id>`, `/api/skip`.

- [ ] **Step 4: Commit**

```bash
git add src/app.py templates/skip.html
git commit -m "feat: add Flask review server with tailor/skip/regenerate/export routes"
```

---

## Task 6: Build the Review Page UI — `templates/review.html`

**Files:**
- Create: `templates/review.html`

This task uses the **frontend-design skill** for a polished, production-grade interface.

- [ ] **Step 1: Create `templates/review.html` with three-panel layout**

Use the frontend-design skill to create a polished three-panel review page. The page must:

- **Left panel (25%):** Read-only JD context — job title, company, role summary, key skills as colored chips (match=green, gap=red, neutral=grey), WhyFit strong/partial matches, Gaps, Score.
- **Center panel (45%):** CV draft — headline, summary, each experience block (company/title/period/bullets), skills categories. Each section has [Edit] and [Regenerate] buttons.
- **Right panel (30%):** Cover letter — opening, experience paragraph, skills paragraph, closing. Each section has [Edit] and [Regenerate] buttons.
- **Bottom bar:** Fixed — "Export CV as PDF" + "Export Cover Letter as PDF" buttons.
- **Edit mode:** Clicking [Edit] makes the text `contenteditable`. On blur, POST to `/api/save` with `{job_id, section, content}`.
- **Regenerate:** Clicking [Regenerate] shows a small text input for optional instruction, then POST to `/api/regenerate` with `{job_id, section, instruction}`. Replace section content with response.

Template data available:
- `{{ draft }}` — the full cv_draft.json object
- `{{ meta }}` — metadata (job_title, company, angle_used, key_skills, etc.)
- `{{ context }}` — JD context (description, match_result, title, company, key_skills, job_url)
- `{{ job_id }}` — the job ID string

- [ ] **Step 2: Verify the page renders**

Run: `python src/app.py &` then open `http://localhost:5000/review/test` in browser (will 404 but confirms Flask serves templates).

- [ ] **Step 3: Commit**

```bash
git add templates/review.html
git commit -m "feat: add three-panel review UI for CV tailoring"
```

---

## Task 7: Build PDF Templates — `templates/cv_template.html` + `templates/cl_template.html`

**Files:**
- Create: `templates/cv_template.html`
- Create: `templates/cl_template.html`

This task uses the **frontend-design skill** to match Ava's existing CV design.

- [ ] **Step 1: Create `templates/cv_template.html`**

Two-column CV layout for weasyprint. Must match Ava's current CV design:
- **Left column (~65%):** Professional Experience (chronological)
  - Each role: title / company name + description (italic) / period + location / bullet points
- **Right column (~35%):** 
  - Name (large) + headline
  - Summary paragraph
  - Contact info (email, phone, location, LinkedIn, "No visa sponsorship required")
  - Core Skills (3 categories, each with skill list)
  - Education (2 entries)
  - Certifications (4 entries)
  - Patents (3 entries)
  - Languages
- Font: clean sans-serif (Helvetica/Arial)
- Print-ready: A4 page size, proper margins

Template data: `{{ draft }}` (the cv_draft.json), `{{ meta }}`.

Static data for right column (not in draft JSON — hardcoded from profile):
- Email: iknoest@gmail.com
- Phone: +31 6272 45632
- Location: Utrecht, the Netherlands
- LinkedIn: linkedin.com/in/avatse/
- Education: MSc Applied Cognitive Psychology (Utrecht), BEng Mechanical Engineering (HK PolyU)
- Certs: Power BI, Google UX, CUHK Data Mining, HKUST Advanced Mechanical Behavior
- Patents: CN210803346U, CN210784790U, CN209945420U
- Languages: English, Chinese

- [ ] **Step 2: Create `templates/cl_template.html`**

Single-column cover letter layout:
- Contact header block (right-aligned): name, email, phone, location, LinkedIn
- Date (auto-filled)
- "Dear Hiring Manager,"
- 4 paragraphs from `{{ draft.cover_letter }}`
- "Yours sincerely," + "AVA TSE" + headline
- A4, same font family as CV

- [ ] **Step 3: Install weasyprint**

Run: `pip install weasyprint`

Note: weasyprint on Windows requires GTK libraries. If installation fails, document the dependency and test on WSL or add to requirements.txt with a note.

- [ ] **Step 4: Test PDF generation with sample data**

```python
python -c "
from weasyprint import HTML
html = '<html><body><h1>Test</h1></body></html>'
HTML(string=html).write_pdf('output/test_weasyprint.pdf')
print('PDF generated successfully')
"
```

- [ ] **Step 5: Commit**

```bash
git add templates/cv_template.html templates/cl_template.html
git commit -m "feat: add CV + cover letter PDF templates (weasyprint, two-column layout)"
```

---

## Task 8: Create Cover Letter Reference File

**Files:**
- Create: `profile/cover_letter_reference.md`

- [ ] **Step 1: Extract Philips cover letter text to markdown reference**

Create `profile/cover_letter_reference.md` from the Philips cover letter PDF content (already extracted earlier):

```markdown
# Cover Letter Style Reference

This is the style and structure guide for generating tailored cover letters.
Adapt the content to each new role — keep the structure and tone.

---

Dear Hiring Manager,

Every successful product begins where human need meets engineering possibility. I have spent the past
eight years turning that intersection into working prototypes and data-driven solutions, from baby air
purifiers and connected air-quality devices to user-validated consumer innovations. I want to bring this
experience to [Company]'s [Team/Department] to help create the next generation of [product domain].

My foundation is built on hands-on mechanical engineering and user-centered innovation. At Raymond
Industrial, I led the development of a baby air purifier from concept through CAD design, prototyping,
DFMEA, certification, and pilot production. Working with sensors, Arduino systems, and testing protocols
strengthened my skills in rapid prototyping and design for manufacturability. As a Research Fellow, I
developed a low-energy wireless indoor-air monitoring device integrating multiple sensors for real-time
analysis, deepening my expertise in 3D CAD, electronics integration, and data-driven design.

At BYD Europe, I focused on understanding user needs and market behavior. I ran a multi-country design
clinic to validate a new vehicle concept and managed an owner research study with over 4,000 participants.
Working with R&D teams in Europe and China, I translated insights into functional requirements, feasible
solutions, and PRDs that guided connected system development. This experience honed my ability to align
consumer insights, engineering focus, and business objectives in agile, cross-functional teams.

I bring eight years of end-to-end product development experience across consumer and health-related
technologies, combining user research, market analysis, and engineering execution. My strengths include
mechanical design, rapid prototyping, Python-based data analysis, and supplier collaboration across Asia
and Europe. I drive innovation through continuous refinement and cross-functional teamwork.

[Company]'s vision to [company-specific mission/value] reflects exactly how I build products: meaningful,
validated, and successful in the market. I am eager to bring this mindset and experience to your
[specific team/product] roadmap.

Yours sincerely,
Ava Tse
```

- [ ] **Step 2: Commit**

```bash
git add profile/cover_letter_reference.md
git commit -m "docs: add cover letter style reference from Philips example"
```

---

## Task 9: End-to-End Integration Test

**Files:**
- No new files — integration verification across all components

- [ ] **Step 1: Run all unit tests**

Run: `python -m pytest tests/ -q`
Expected: All tests pass (53 existing + 7 new generator tests = 60+).

- [ ] **Step 2: Start Flask server**

Run: `python src/app.py` (in background)
Expected: `* Running on http://127.0.0.1:5000`

- [ ] **Step 3: Test the tailor flow with a real job**

Pick a job from the enriched cache (e.g. Quooker Product Engineer):
```bash
python src/generator.py --job-url "https://www.linkedin.com/jobs/view/4400368636"
```

Expected:
- Angle selected (should be "Product Development Engineer" for engineering role)
- Draft saved to `output/tailored/4400368636/`
- Notification email sent
- `cv_draft.json`, `cv_draft.md`, `cover_letter.md`, `metadata.json` all created

- [ ] **Step 4: Test the review UI**

Open `http://localhost:5000/review/4400368636` in browser.
Verify:
- Left panel shows JD context + KeySkills chips
- Center panel shows tailored CV sections with Edit/Regenerate buttons
- Right panel shows cover letter sections
- Edit: click edit on headline, change text, click away — verify saved to `cv_draft.json`
- Export: click "Export CV as PDF" — verify PDF downloads

- [ ] **Step 5: Test skip feedback**

Open `http://localhost:5000/skip/4400368636`.
Verify:
- Page shows job title + company
- Click "Wrong domain" → submit → see "Noted!" confirmation
- Check Google Sheet — verify My Verdict and My Reason columns updated

- [ ] **Step 6: Commit any fixes from integration testing**

```bash
git add -A
git commit -m "fix: integration test fixes for CV tailoring flow"
```

---

## Task 10: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `todo.md`

- [ ] **Step 1: Update CLAUDE.md**

Add to Architecture section:
- Phase 2: CV Tailoring — `src/generator.py` (LLM tailoring engine), `src/app.py` (Flask review UI), `templates/` (PDF templates)
- Email digest now has View/Apply/Skip buttons per card
- KeySkills extracted from JD during scoring, shown as chips in email
- Apply triggers LLM-based CV + cover letter tailoring
- Review UI at localhost:5000 for section-by-section editing + PDF export
- Skip captures feedback into Google Sheets for scoring model training

Add to CLI Flags:
- `python src/generator.py --job-url <url>` — tailor CV for a specific job
- `python src/app.py` — start Flask review server on localhost:5000

Add to Pipeline Order:
- Step 8: CV Tailoring (on-demand, triggered by Apply button)

- [ ] **Step 2: Update README.md**

Add bilingual Phase 2 section:
- How to use the Apply/Skip buttons
- How to start the review server
- How to export PDF
- Screenshots placeholder

- [ ] **Step 3: Update todo.md**

Check off Phase 2 items:
```
## Phase 2: Resume Tailoring (completed 2026-04-13)
- [x] Resume tailoring per job match (generator.py)
- [x] Cover letter generation per job match
- [x] KeySkills extraction + email chip display
- [x] View/Apply/Skip email buttons
- [x] Interactive review UI (Flask, three-panel, edit + regenerate)
- [x] PDF export (weasyprint, two-column CV + cover letter)
- [x] Skip feedback into Google Sheets
```

Update remaining items:
```
- [ ] Output to Google Sheets with download links
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md todo.md
git commit -m "docs: update CLAUDE.md, README.md, todo.md for Phase 2 CV tailoring"
```

---

## Task Summary

| Task | What | Depends On |
|------|------|-----------|
| 1 | KeySkills in scoring prompt + email chips | — |
| 2 | View/Apply/Skip buttons in email | — |
| 3 | Generator engine (`src/generator.py`) | — |
| 4 | Refactor `send_email` for arbitrary content | — |
| 5 | Flask review server (`src/app.py`) + skip page | Task 3 |
| 6 | Review page UI (`templates/review.html`) | Task 5 |
| 7 | PDF templates (CV + cover letter) | — |
| 8 | Cover letter reference file | — |
| 9 | End-to-end integration test | Tasks 1-8 |
| 10 | Documentation updates | Task 9 |

Tasks 1, 2, 3, 4, 7, 8 are independent and can be parallelized.
Tasks 5-6 depend on Task 3.
Task 9 depends on all previous tasks.
Task 10 is final.
