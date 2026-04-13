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

from src.llm import router


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

    Decision table:
    - Strong phygital + engineering title  → Product Development Engineer
    - Strong phygital + strategy-only title → Product Development Engineer (phygital wins)
    - Strong phygital + no clear title     → Product Development Engineer
    - Weak/Moderate + engineering title    → Product Development Engineer
    - Weak/Moderate + strategy-only title  → Strategic Product Lead
    - Moderate + ambiguous/both signals    → Hybrid
    - Weak + no clear title               → Hybrid
    """
    phy_level = match_result.get("PhygitalAssessment", {}).get("Level", "Weak")
    title_lower = str(title).lower()

    eng_match = bool(ENGINEERING_KEYWORDS.search(title_lower))
    strat_match = bool(STRATEGY_KEYWORDS.search(title_lower))

    # Strong phygital always pushes toward engineering angle
    if phy_level == "Strong":
        if strat_match and not eng_match:
            # e.g. "Product Manager" with strong phygital → still engineer angle
            return "Product Development Engineer"
        return "Product Development Engineer"

    # Moderate phygital: title is the tiebreaker; ambiguous titles → Hybrid
    if phy_level == "Moderate":
        if eng_match and not strat_match:
            return "Product Development Engineer"
        if strat_match and not eng_match:
            return "Strategic Product Lead"
        # Both signals or neither (ambiguous) → Hybrid
        return "Hybrid"

    # Weak phygital: title drives angle; no clear title → Hybrid
    if eng_match and not strat_match:
        return "Product Development Engineer"
    if strat_match and not eng_match:
        return "Strategic Product Lead"
    if strat_match and eng_match:
        return "Hybrid"
    return "Hybrid"


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


def save_draft(job_id, draft_json, metadata, output_dir="output/tailored"):
    """Save tailoring draft to output/tailored/<job_id>/."""
    draft_dir = Path(output_dir) / job_id
    draft_dir.mkdir(parents=True, exist_ok=True)

    (draft_dir / "cv_draft.json").write_text(
        json.dumps(draft_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

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
    """Pull first balanced JSON object from LLM response."""
    from src.matcher import _extract_json as matcher_extract
    return matcher_extract(text)


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


def tailor_cv(job_url):
    """Main tailoring pipeline. Returns (job_id, draft_dir) or raises."""
    job_id = extract_job_id(job_url)
    print(f"\n{'='*60}")
    print(f"CV TAILORING — {job_url}")
    print(f"Job ID: {job_id}")
    print(f"{'='*60}")

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

    angle = select_angle(title, match_result)
    print(f"Step 2: Angle selected → {angle}")

    base_cv = load_base_cv()
    prompt = build_tailoring_prompt(jd_text, match_result, base_cv, angle)
    print(f"Step 3: Calling LLM ({len(prompt)} chars prompt)...")

    response = router.call_llm(prompt)
    json_blob = _extract_json(response)
    if not json_blob:
        raise RuntimeError(f"LLM returned no valid JSON. Raw: {response[:300]}")

    draft = json.loads(json_blob)
    print(f"  Got draft: headline='{draft.get('headline', '?')}', {len(draft.get('experience', []))} roles")

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

    print("Step 5: Sending notification email...")
    try:
        _send_draft_notification(job_id, title, company, draft)
    except Exception as e:
        print(f"  [WARN] Email notification failed: {e}")

    print(f"\nDone! Review at: http://localhost:5000/review/{job_id}")
    return job_id, draft_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tailor CV for a specific job")
    parser.add_argument("--job-url", required=True, help="Job URL to tailor CV for")
    args = parser.parse_args()
    tailor_cv(args.job_url)
