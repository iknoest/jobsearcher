"""Resume & cover letter tailoring — generates customized versions per job."""

import json
from src.llm import router


RESUME_TAILOR_PROMPT = """You are a professional resume writer. Tailor this base CV for the specific job below.

## Base CV
{base_cv}

## Target Job
**Title:** {title}
**Company:** {company}
**Description:**
{description}

## Instructions
1. Rewrite the professional summary to align with this specific role
2. Reorder and emphasize relevant experience bullets
3. Highlight matching skills, de-emphasize less relevant ones
4. Keep factual — do NOT invent experience or skills
5. Output in clean Markdown format

Output the tailored CV in Markdown.
"""

COVER_LETTER_PROMPT = """You are a professional cover letter writer. Write a tailored cover letter.

## Candidate Profile
{base_cv}

## Target Job
**Title:** {title}
**Company:** {company}
**Location:** {location}
**Description:**
{description}

## Match Analysis
{match_summary}

## Instructions
1. Address specific requirements from the JD
2. Connect candidate's experience to the role's needs
3. Show genuine interest in the company/product
4. Keep it under 400 words, professional but warm
5. Do NOT mention visa/sponsorship (candidate doesn't need it)
6. Language: English

Output the cover letter as plain text (no markdown headers).
"""


def load_base_cv(path="profile/cv_base.md"):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def tailor_resume(job_row, base_cv=None):
    """Generate a tailored resume for a specific job."""
    if base_cv is None:
        base_cv = load_base_cv()

    prompt = RESUME_TAILOR_PROMPT.format(
        base_cv=base_cv,
        title=job_row.get("title", ""),
        company=job_row.get("company", ""),
        description=str(job_row.get("description", ""))[:3000],
    )
    return router.call_llm(prompt)


def generate_cover_letter(job_row, base_cv=None, match_summary=""):
    """Generate a tailored cover letter for a specific job."""
    if base_cv is None:
        base_cv = load_base_cv()

    prompt = COVER_LETTER_PROMPT.format(
        base_cv=base_cv,
        title=job_row.get("title", ""),
        company=job_row.get("company", ""),
        location=job_row.get("location", ""),
        description=str(job_row.get("description", ""))[:3000],
        match_summary=match_summary,
    )
    return router.call_llm(prompt)
