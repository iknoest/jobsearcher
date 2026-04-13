"""Flask review UI for CV tailoring — edit, regenerate, export PDF."""

import json
import os
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
)

TAILORED_DIR = Path("output/tailored")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_draft(job_id):
    """Delegate to src.generator.load_draft."""
    from src.generator import load_draft
    return load_draft(job_id)


def _load_job_context(job_id):
    """Load job metadata + JD + match_result.

    Priority:
    1. metadata.json in draft dir (title/company/URL)
    2. enriched_jobs.pkl cache (full JD + match_result)
    3. Google Sheets (fallback)
    """
    meta_path = TAILORED_DIR / job_id / "metadata.json"
    if meta_path.exists():
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        metadata = {}

    job_url = metadata.get("job_url", "")
    job_data = None

    if job_url:
        try:
            from src.generator import load_job_from_cache
            job_data = load_job_from_cache(job_url)
        except Exception:
            pass

    if job_data is None and job_url:
        try:
            from src.generator import load_job_from_sheets
            job_data = load_job_from_sheets(job_url)
        except Exception:
            pass

    jd_text = ""
    match_result = {}

    if job_data:
        jd_text = str(job_data.get("description", ""))
        raw_match = job_data.get("match_result", "{}")
        if isinstance(raw_match, str):
            try:
                match_result = json.loads(raw_match)
            except json.JSONDecodeError:
                pass
        elif isinstance(raw_match, dict):
            match_result = raw_match

    return metadata, jd_text, match_result


def _find_job_url_in_cache(job_id, cache_path="output/enriched_jobs.pkl"):
    """Search enriched cache for a URL that contains the job_id substring."""
    import pickle
    cache = Path(cache_path)
    if not cache.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_pickle(cache)
        if "job_url" not in df.columns:
            return None
        for url in df["job_url"].dropna():
            if job_id in str(url):
                return str(url)
    except Exception:
        pass
    return None


def _navigate_nested(obj, keys):
    """Navigate a nested dict/list by a list of string keys (list indices as ints)."""
    for key in keys:
        if isinstance(obj, list):
            obj = obj[int(key)]
        else:
            obj = obj[key]
    return obj


def _set_nested(obj, keys, value):
    """Set a value in a nested dict/list by a list of string keys."""
    for key in keys[:-1]:
        if isinstance(obj, list):
            obj = obj[int(key)]
        else:
            obj = obj[key]
    last = keys[-1]
    if isinstance(obj, list):
        obj[int(last)] = value
    else:
        obj[last] = value


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/review/<job_id>")
def review(job_id):
    """Main review page — load draft + context, render review.html."""
    draft, meta = _load_draft(job_id)
    if draft is None:
        return f"No draft found for job_id={job_id}", 404

    metadata, jd_text, match_result = _load_job_context(job_id)
    title = metadata.get("job_title", meta.get("job_title", "Unknown"))
    company = metadata.get("company", meta.get("company", "Unknown"))

    return render_template(
        "review.html",
        job_id=job_id,
        draft=draft,
        metadata=metadata,
        jd_text=jd_text,
        match_result=match_result,
        title=title,
        company=company,
    )


@app.route("/tailor/<job_id>")
def tailor(job_id):
    """Trigger tailoring from email click — find job URL, call generator, redirect."""
    # Try to find the job URL from the draft metadata first
    meta_path = TAILORED_DIR / job_id / "metadata.json"
    job_url = None

    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        job_url = meta.get("job_url", "")

    # Fall back to searching enriched cache
    if not job_url:
        job_url = _find_job_url_in_cache(job_id)

    if not job_url:
        return f"Job URL not found for job_id={job_id}. Cannot tailor.", 404

    try:
        from src.generator import tailor_cv
        result_job_id, _ = tailor_cv(job_url)
    except Exception as exc:
        return f"Tailoring failed: {exc}", 500

    return redirect(url_for("review", job_id=result_job_id))


@app.route("/api/save", methods=["POST"])
def api_save():
    """Save an edited section back to cv_draft.json.

    Body: {job_id, section, content}
    section uses dot-notation: e.g. "experience.0.bullets" or "summary"
    """
    data = request.get_json(force=True)
    job_id = data.get("job_id", "")
    section = data.get("section", "")
    content = data.get("content")

    if not job_id or not section:
        return jsonify({"error": "job_id and section required"}), 400

    draft_path = TAILORED_DIR / job_id / "cv_draft.json"
    if not draft_path.exists():
        return jsonify({"error": f"Draft not found for job_id={job_id}"}), 404

    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        keys = section.split(".")
        _set_nested(draft, keys, content)
        draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
        return jsonify({"ok": True})
    except (KeyError, IndexError, ValueError) as exc:
        return jsonify({"error": f"Bad section path '{section}': {exc}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/regenerate", methods=["POST"])
def api_regenerate():
    """Regenerate one section via LLM.

    Body: {job_id, section, instruction}
    Returns: {content: <regenerated text or object>}
    """
    data = request.get_json(force=True)
    job_id = data.get("job_id", "")
    section = data.get("section", "")
    instruction = data.get("instruction", "")

    if not job_id or not section:
        return jsonify({"error": "job_id and section required"}), 400

    draft, meta = _load_draft(job_id)
    if draft is None:
        return jsonify({"error": f"Draft not found for job_id={job_id}"}), 404

    metadata, jd_text, match_result = _load_job_context(job_id)

    # Get current section content for context
    try:
        keys = section.split(".")
        current = _navigate_nested(draft, keys)
    except (KeyError, IndexError):
        current = None

    prompt = f"""### SECTION REGENERATION

You are rewriting one section of a tailored CV. Return ONLY the new content for this section,
as a JSON value (string, list of strings, or object — matching the current structure).
No explanation, no markdown fences.

Section to rewrite: {section}
Current content: {json.dumps(current)}
Instruction: {instruction if instruction else "Improve this section for the target role"}

Job Title: {metadata.get("job_title", "")}
Company: {metadata.get("company", "")}
Job Description (excerpt):
{jd_text[:2000]}

Key Skills Required: {json.dumps(match_result.get("KeySkills", []))}

Output: just the JSON value for '{section}', nothing else.
"""

    try:
        from src.llm.router import call_llm
        response = call_llm(prompt)

        # Try to parse as JSON, fall back to raw string
        response = response.strip()
        try:
            content = json.loads(response)
        except json.JSONDecodeError:
            # Strip markdown fences if present
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            try:
                content = json.loads(response.strip())
            except json.JSONDecodeError:
                content = response.strip()

        return jsonify({"content": content})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/skip/<job_id>")
def skip_page(job_id):
    """Skip feedback page — show job title + company, collect reason."""
    title = "Unknown"
    company = "Unknown"

    # Try draft metadata first
    meta_path = TAILORED_DIR / job_id / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        title = meta.get("job_title", title)
        company = meta.get("company", company)
    else:
        # Try enriched cache
        try:
            job_url = _find_job_url_in_cache(job_id)
            if job_url:
                from src.generator import load_job_from_cache
                job_data = load_job_from_cache(job_url)
                if job_data:
                    title = str(job_data.get("title", title))
                    company = str(job_data.get("company", company))
        except Exception:
            pass

    return render_template("skip.html", job_id=job_id, title=title, company=company)


@app.route("/api/skip", methods=["POST"])
def api_skip():
    """Submit skip feedback — write Disagree-Skip + reason to Google Sheets.

    Body: {job_id, reason, detail}
    """
    data = request.get_json(force=True)
    job_id = data.get("job_id", "")
    reason = data.get("reason", "")
    detail = data.get("detail", "")

    if not job_id or not reason:
        return jsonify({"error": "job_id and reason required"}), 400

    # Find the job URL
    meta_path = TAILORED_DIR / job_id / "metadata.json"
    job_url = None
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        job_url = meta.get("job_url", "")

    if not job_url:
        job_url = _find_job_url_in_cache(job_id)

    if not job_url:
        return jsonify({"error": f"Job URL not found for job_id={job_id}"}), 404

    my_reason = f"{reason}: {detail}" if detail else reason

    try:
        from src.sheets import get_client
        spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        if not spreadsheet_id:
            return jsonify({"error": "GOOGLE_SHEET_ID not set"}), 500

        client = get_client()
        ws = client.open_by_key(spreadsheet_id).worksheet("Jobs")
        headers = ws.row_values(1)

        url_col_idx = headers.index("Job URL") if "Job URL" in headers else -1
        verdict_col_idx = headers.index("My Verdict") if "My Verdict" in headers else -1
        reason_col_idx = headers.index("My Reason") if "My Reason" in headers else -1

        if url_col_idx < 0:
            return jsonify({"error": "Job URL column not found in sheet"}), 500

        all_urls = ws.col_values(url_col_idx + 1)
        row_num = None
        for i, url in enumerate(all_urls):
            if url == job_url:
                row_num = i + 1
                break

        if row_num is None:
            return jsonify({"error": f"Job URL not found in sheet: {job_url}"}), 404

        if verdict_col_idx >= 0:
            ws.update_cell(row_num, verdict_col_idx + 1, "Disagree-Skip")
        if reason_col_idx >= 0:
            ws.update_cell(row_num, reason_col_idx + 1, my_reason)

        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _render_pdf(html_string):
    """Render HTML to PDF bytes using xhtml2pdf (pure Python, no GTK needed)."""
    from io import BytesIO
    from xhtml2pdf import pisa

    result = BytesIO()
    pisa.CreatePDF(html_string, dest=result)
    return result.getvalue()


@app.route("/api/export/cv/<job_id>")
def export_cv(job_id):
    """Render CV PDF via xhtml2pdf, return as download."""
    draft, meta = _load_draft(job_id)
    if draft is None:
        return f"No draft found for job_id={job_id}", 404

    metadata, _, _ = _load_job_context(job_id)
    title = metadata.get("job_title", "Role")
    company = metadata.get("company", "Company")

    html = render_template("cv_template.html", draft=draft, metadata=metadata)
    pdf_bytes = _render_pdf(html)

    safe_company = "".join(c for c in company if c.isalnum() or c in " _-").strip().replace(" ", "_")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip().replace(" ", "_")
    filename = f"Ava_Tse_CV_{safe_company}_{safe_title}.pdf"

    import io
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/export/cl/<job_id>")
def export_cl(job_id):
    """Render cover letter PDF via xhtml2pdf, return as download."""
    draft, meta = _load_draft(job_id)
    if draft is None:
        return f"No draft found for job_id={job_id}", 404

    metadata, _, _ = _load_job_context(job_id)
    title = metadata.get("job_title", "Role")
    company = metadata.get("company", "Company")

    html = render_template("cl_template.html", draft=draft, metadata=metadata)
    pdf_bytes = _render_pdf(html)

    safe_company = "".join(c for c in company if c.isalnum() or c in " _-").strip().replace(" ", "_")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip().replace(" ", "_")
    filename = f"Ava_Tse_CoverLetter_{safe_company}_{safe_title}.pdf"

    import io
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=True)
