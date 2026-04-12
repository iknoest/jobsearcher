# Phase 2: CV Tailoring & Cover Letter Generation — Design Spec

## Overview

Transform the email digest from a read-only report into an **action interface** with three buttons per job card: **View** (check JD), **Apply** (trigger CV + cover letter tailoring), **Skip** (feedback to train scoring model). When "Apply" is clicked, the system auto-tailors a full CV and cover letter using the LLM match analysis already computed during scoring, then presents an interactive review UI for editing before PDF export.

## Problem

Currently, clicking "View & Apply" in the email digest opens the LinkedIn job page — a dead end. The system has already analysed the job fit (WhyFit, Gaps, RoleSummary, PhygitalAssessment) but none of that intelligence flows into the application materials. The user manually re-reads the JD, edits their CV in a separate tool, and writes a cover letter from scratch each time.

## Goals

1. **One-click tailoring** — clicking "Apply" produces a draft CV + cover letter tailored to the specific JD
2. **Interactive review** — user can edit every section, regenerate individual blocks, before exporting
3. **Styled PDF output** — final export matches the user's current two-column CV design
4. **Skip feedback** — "Skip" captures a structured reason that feeds the existing scoring feedback loop
5. **KeySkills visibility** — email digest shows extracted JD skills as chips, so user can assess fit at a glance before deciding

## Non-Goals

- Auto-submitting applications (user always submits manually)
- Managing multiple candidate profiles (single user: Ava)
- Tracking application status (that's Phase 3 Notion integration)

---

## Architecture

### Data Flow

```
Daily pipeline (existing)
    |
    v
Email digest with [View] [Apply] [Skip] per card
    |                    |              |
    v                    v              v
Opens JD link    n8n webhook       localhost/skip/<job_id>
                    |                   |
                    v                   v
              generator.py         Google Sheet feedback
              (LLM tailoring)      (existing feedback loop)
                    |
                    v
              Saves draft to
              output/tailored/<job_id>/
                    |
                    v
              Sends "draft ready"
              email with review link
                    |
                    v
              localhost:5000/review/<job_id>
              (edit, regenerate sections, export)
                    |
                    v
              Export styled PDF
              (weasyprint)
```

### Components

| Component | File | New/Modify | Purpose |
|-----------|------|------------|---------|
| Tailoring engine | `src/generator.py` | New | LLM-based CV + cover letter generation |
| Review web UI | `src/app.py` | New | Flask app for interactive review + export |
| CV PDF template | `templates/cv_template.html` | New | Two-column styled CV layout for weasyprint |
| Cover letter PDF template | `templates/cl_template.html` | New | Single-column letter layout |
| Email buttons | `src/notifier.py` | Modify | Replace single "View & Apply" with View/Apply/Skip |
| KeySkills in scoring | `src/matcher.py` | Modify | Add KeySkills field to SCORING_PROMPT |
| KeySkills in email | `src/notifier.py` | Modify | Render skill chips with match/gap highlighting |
| Skip feedback page | `src/app.py` | New | Quick-select reason + text input |
| n8n webhook nodes | `n8n/` | Modify | Add /webhook/tailor and /webhook/skip endpoints |

### Dependencies (new)

| Package | Purpose |
|---------|---------|
| `flask` | Review web UI |
| `weasyprint` | HTML-to-PDF rendering |

---

## Section 1: Email Buttons — The Trigger

### Current State
Single button: `[View & Apply]` → opens job URL.

### New State
Three buttons per job card:

```
[View]  [Apply]  [Skip]
```

- **View** — opens job URL (same as today, renamed). Neutral style.
- **Apply** — `GET <n8n_webhook_base>/webhook/tailor?job_url=<encoded_url>`. Green, prominent. n8n receives the webhook and runs `python src/generator.py --job-url <url>`.
- **Skip** — `GET http://localhost:5000/skip/<job_id>`. Or fallback `mailto:iknoest@gmail.com?subject=[SKIP] {title} @ {company}` if server isn't running. Muted style.

### n8n Webhook Nodes

Two new n8n nodes:
- **Tailor webhook** (`/webhook/tailor`): receives `job_url` param, executes `python src/generator.py --job-url <job_url>`, returns 200.
- **Skip webhook** (`/webhook/skip`): receives `job_url` + `reason` params, writes to Google Sheet "My Verdict" + "My Reason" columns.

---

## Section 2: KeySkills in Email Digest (Pre-requisite)

### SCORING_PROMPT Change

Add to the JSON output schema in `src/matcher.py`:

```json
"KeySkills": ["5-8 key skills, tools, or qualifications explicitly required by the JD. English. Short labels only (e.g. 'Python', 'Agile/Scrum', '5+ years PM', 'SolidWorks'). Do NOT list soft skills or generic terms."]
```

### Email Rendering

In `src/notifier.py`, extract `KeySkills` from `match_result` in `_parse_card()`:

```python
"key_skills": r.get("KeySkills", []),
```

Render as chips between Layer 1 (decision strip) and Layer 2 (RoleSummary/WhyFit):

```html
<div class="skill-chips">
  {% for skill in j.key_skills %}
    <span class="skill {{ 'match' if skill_matches_profile(skill) else 'neutral' }}">{{ skill }}</span>
  {% endfor %}
</div>
```

Skill-to-profile matching: compare each KeySkill against `config/profile.yaml` core_skills using case-insensitive substring matching. Green = match, grey = neutral/unknown, red-muted = identified in Gaps.

### Downstream Use

`generator.py` reads these KeySkills to know exactly which JD keywords to mirror in tailored CV bullets and skills section.

---

## Section 3: The Tailoring Engine — `src/generator.py`

### CLI Interface

```bash
python src/generator.py --job-url <url>
```

### Process

**Step 1 — Gather context:**
- Load job row from Google Sheets by `job_url` match (has `match_result`, `description`, all enrichment fields)
- Load base CV from `profile/cv_base.md`
- Load profile from `config/profile.yaml`

**Step 2 — Auto-select CV angle:**

Based on the job's signals from the match analysis:

| Signal | Angle | Emphasis |
|--------|-------|----------|
| PhygitalAssessment=Strong, title contains engineer/innovation/R&D | **Product Development Engineer** | Hardware, prototyping, sensors, CAD, manufacturing |
| Title contains product owner/manager/strategy/lead | **Strategic Product Lead** | Roadmap, VOC, pricing, stakeholder management, data |
| Mixed/unclear | **Hybrid** | LLM decides emphasis based on JD language |

This is an instruction to the LLM, not a template file selection.

**Step 3 — LLM tailoring call:**

Single structured prompt. Input:
- Full JD text
- `match_result` JSON (WhyFit, Gaps, RoleSummary, KeySkills, PhygitalAssessment)
- Base CV content (all roles, all bullets)
- Selected angle
- Cover letter reference style (from Philips letter)

Output JSON:
```json
{
  "angle_used": "Product Development Engineer",
  "headline": "Product Innovation Engineer",
  "summary": "3-4 sentences, mirrors JD language, positions Ava for this specific role",
  "experience": [
    {
      "company": "BYD",
      "title": "Senior Product Research Engineer",
      "period": "Sep 2023 - Present",
      "location": "Amsterdam, Netherlands",
      "company_desc": "Leading electric car manufacturer",
      "bullets": [
        "Rewritten bullet mirroring JD keyword X...",
        "Rewritten bullet mirroring JD keyword Y..."
      ]
    }
  ],
  "skills": {
    "Category Name 1": ["skill", "skill", "skill"],
    "Category Name 2": ["skill", "skill", "skill"],
    "Category Name 3": ["skill", "skill", "skill"]
  },
  "cover_letter": {
    "opening": "Domain-specific hook paragraph",
    "experience_paragraph": "Maps StrongMatch items into narrative",
    "skills_paragraph": "Technical depth + cross-functional value",
    "closing": "Company-vision alignment + call to action"
  }
}
```

**LLM Tailoring Rules (baked into prompt):**
1. NEVER invent experience Ava doesn't have — only reframe, emphasize, and reorder existing bullets
2. Mirror specific keywords/tools/methods from the JD in bullets and skills labels
3. Every role from base CV must appear — but bullet count can vary (expand relevant roles to 3-4 bullets, compress less relevant to 1-2)
4. Skills categories can be renamed and reordered to lead with JD-relevant skills
5. Cover letter opening must hook on the company's specific domain/product, not generic enthusiasm
6. Cover letter body maps 2-3 StrongMatch items into concrete narrative paragraphs
7. All output in English
8. Keep CV to 2 pages maximum

**Step 4 — Save draft:**

Output directory: `output/tailored/<job_id>/`

`job_id` is derived from the job URL: for LinkedIn `https://www.linkedin.com/jobs/view/4400368636`, the `job_id` = `4400368636`. For non-LinkedIn URLs, use a URL-safe hash. This ID is used in all file paths and URL routes.

- `cv_draft.json` — structured JSON (for the review UI to render and edit)
- `cv_draft.md` — readable markdown version
- `cover_letter.md` — markdown
- `metadata.json` — `{ job_id, job_title, company, job_url, timestamp, angle_used, key_skills }`

**Step 5 — Notify:**

Send email via existing `send_email()` in `notifier.py`:
- Subject: `CV Ready: {title} @ {company}`
- Body: preview of headline + summary + link to `localhost:5000/review/<job_id>`

### Quota Impact

1 LLM call per "Apply" click. User-initiated only — no runaway usage.

---

## Section 4: The Review UI — `src/app.py`

### Tech Stack
- **Flask** — lightweight, already in Python ecosystem
- **Jinja2** — already used for email templates
- **Vanilla JS** — inline editing + API calls for regeneration (no React/Vue needed)
- **frontend-design skill** — for polished, production-grade UI design

### Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/review/<job_id>` | GET | Main review page |
| `/api/regenerate` | POST | Regenerate one CV/CL section via LLM |
| `/api/save` | POST | Save edited content back to draft JSON |
| `/api/export/cv/<job_id>` | GET | Render CV PDF via weasyprint, return download |
| `/api/export/cl/<job_id>` | GET | Render cover letter PDF, return download |
| `/skip/<job_id>` | GET | Skip feedback page |
| `/api/skip` | POST | Submit skip reason to Google Sheets |

### Review Page Layout

Three-panel layout:

```
+----------------------+---------------------+--------------------+
|   JD + Match Info    |  Tailored CV Draft  |  Cover Letter      |
|                      |                     |                    |
| RoleSummary          | Headline   [edit][regen] | Opening  [edit][regen]
| KeySkills chips      | Summary    [edit][regen] | Body 1   [edit][regen]
|  (green=match)       | BYD bullets[edit][regen] | Body 2   [edit][regen]
|  (grey=neutral)      | TomTom     [edit][regen] | Closing  [edit][regen]
|  (red=gap)           | Syntegon   [edit][regen] |                    |
| WhyFit               | DFO        [edit][regen] |                    |
| Gaps                 | Raymond    [edit][regen] |                    |
| Score breakdown      | Jetta      [edit][regen] |                    |
|                      | Skills     [edit][regen] |                    |
| (read-only)          |                     |                    |
|                      | [Export CV as PDF]   | [Export CL as PDF] |
+----------------------+---------------------+--------------------+
```

### Interactions

- **[Edit]** — clicking makes the section content-editable. Changes auto-save via `/api/save` on blur.
- **[Regenerate]** — sends that section's context to `/api/regenerate`. LLM produces a new version of just that section. Replaces content in-place. One small LLM call per click.
- **[Export PDF]** — calls `/api/export/cv/<job_id>` or `/api/export/cl/<job_id>`. Browser downloads the file.

### Regenerate API

```json
POST /api/regenerate
{
  "job_id": "...",
  "section": "experience.byd",    // or "headline", "summary", "skills", "cover_letter.opening"
  "instruction": "optional user hint"  // e.g. "emphasize more hardware"
}
```

Returns the regenerated section content. The LLM receives the JD + match context + the current state of the full draft + specific instruction for that section.

### Skip Feedback Page

Minimal single-purpose page at `/skip/<job_id>`:

- Shows: job title + company (context)
- Quick-select reason buttons: `Wrong domain` | `Too junior` | `Too senior` | `Pure SaaS` | `Bad company` | `Location` | `Other`
- Optional one-line text input
- Submit → writes to Google Sheet (My Verdict = "Disagree-Skip", My Reason = selected + text) → page shows "Noted, thanks" → auto-closes

### Server Lifecycle

- Starts manually: `python src/app.py` (or auto-started by n8n after generator.py completes)
- Runs on `localhost:5000`
- Lightweight — single user, no auth needed

---

## Section 5: PDF Rendering

### CV Template (`templates/cv_template.html`)

Two-column layout matching Ava's existing CV design:

- **Left column (~65%):** Professional Experience (chronological, each role with company/title/period/bullets)
- **Right column (~35%):** Name + headline + summary (top), contact info, Core Skills (categorized), Education, Certifications, Patents, Languages, Hobbies

Styling: clean sans-serif font, subtle color accents, consistent with current PDF look.

### Cover Letter Template (`templates/cl_template.html`)

Single-column layout:
- Contact header (right-aligned, matching CV style)
- "Dear Hiring Manager,"
- 4 paragraphs (opening, experience, skills, closing)
- "Yours sincerely, Ava Tse" + role title

### Rendering

`weasyprint` converts the Jinja2-rendered HTML to PDF. Called by the `/api/export/` routes.

PDF files saved to `output/tailored/<job_id>/`:
- `Ava_Tse_CV_{company}_{title}.pdf`
- `Ava_Tse_CoverLetter_{company}_{title}.pdf`

---

## Section 6: Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `src/generator.py` | LLM tailoring engine (gather context, select angle, call LLM, save draft) |
| `src/app.py` | Flask review UI + skip feedback + export routes |
| `templates/cv_template.html` | Two-column CV layout for weasyprint PDF rendering |
| `templates/cl_template.html` | Cover letter layout for weasyprint PDF rendering |
| `templates/review.html` | Three-panel review page (Jinja2 + vanilla JS) |
| `templates/skip.html` | Skip feedback quick-select page |

### Modified Files

| File | Change |
|------|--------|
| `src/matcher.py` | Add `KeySkills` field to SCORING_PROMPT JSON schema |
| `src/notifier.py` | Replace "View & Apply" with View/Apply/Skip buttons; add KeySkills chip rendering; add "draft ready" notification email |
| `src/notifier.py` | Add `_parse_card()` extraction for `key_skills` |
| `n8n/` | Add tailor + skip webhook nodes |
| `requirements.txt` | Add `flask`, `weasyprint` |

### Documentation Updates (after implementation)

| File | Update |
|------|--------|
| `CLAUDE.md` | Add Phase 2 architecture, generator.py, app.py, PDF rendering, new CLI flags |
| `README.md` | Add bilingual Phase 2 usage section, review UI instructions |
| `todo.md` | Check off Phase 2 items, add any discovered sub-tasks |

---

## Verification Plan

1. **KeySkills in digest** — run scoring on 1 job, confirm KeySkills array present in match_result and chips render in email
2. **Email buttons** — send test digest, confirm View/Apply/Skip buttons appear with correct URLs
3. **Generator** — run `python src/generator.py --job-url <quooker_url>`, confirm:
   - Angle auto-selected correctly (should be "Product Development Engineer" for Quooker)
   - All 6 roles present in output, bullets rewritten to mirror JD language
   - Cover letter opening hooks on Quooker's specific product domain
   - No invented experience
4. **Review UI** — open `localhost:5000/review/<job_id>`, confirm:
   - Three panels render correctly
   - Edit works (content-editable, saves on blur)
   - Regenerate replaces section content
   - Left panel shows JD + KeySkills + WhyFit correctly
5. **PDF export** — export CV and cover letter, confirm:
   - Two-column layout matches current CV design
   - All content from edited draft appears correctly
   - Cover letter formatting matches Philips letter style
6. **Skip feedback** — click Skip, select reason, confirm it appears in Google Sheet row
7. **All existing tests pass** — `python -m pytest tests/ -q`
