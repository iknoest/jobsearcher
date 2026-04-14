"""Email digest — 3-layer decision card layout.

Layer 1: Decision signals (score, hint, blockers, work mode, salary, language, risk)
Layer 2: Fast explanation (WhyFit Strong/Partial, Gaps, Risks)
Layer 3: Expandable detail (Phygital, Company, ScoreBreakdown, TrustNotes)

Apply + Maybe = full cards. Skip = one-line summary.
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from jinja2 import Template
from dotenv import load_dotenv

load_dotenv()

import re as _re
import urllib.parse as _urlparse

def _format_job_type(job_type):
    """Return a display label for non-fulltime contract types, empty string otherwise."""
    if not job_type:
        return ""
    jt = str(job_type).lower().strip()
    labels = {"contract": "Contract", "parttime": "Part-time", "temporary": "Temporary", "internship": "Internship"}
    return labels.get(jt, "")  # "fulltime" → empty, unknown → empty


def _job_id_from_url(url):
    """Extract a short job ID from a job URL.
    LinkedIn: https://www.linkedin.com/jobs/view/4400368636 -> '4400368636'
    Other: URL-safe hash.
    """
    m = _re.search(r"/jobs/view/(\d+)", str(url))
    if m:
        return m.group(1)
    return _urlparse.quote(str(url), safe="")

EMAIL_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f7; color: #1d1d1f; padding: 16px; line-height: 1.5; font-size: 13px; }
  .container { max-width: 680px; margin: 0 auto; }

  /* Header */
  .header { text-align: center; padding: 20px 0 14px; }
  .header h1 { font-size: 20px; font-weight: 700; }
  .stats { font-size: 12px; color: #86868b; margin-top: 4px; }
  .s-apply { color: #34c759; font-weight: 700; }
  .s-maybe { color: #ff9f0a; font-weight: 700; }
  .s-skip { color: #86868b; font-weight: 600; }

  /* ========== JOB CARD ========== */
  .card { background: #fff; border-radius: 14px; margin-bottom: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e5e5ea; }
  .card.apply { border-left: 5px solid #34c759; }
  .card.maybe { border-left: 5px solid #ff9f0a; }

  /* --- LAYER 1: Decision Strip --- */
  .L1 { padding: 14px 16px 10px; }
  .L1-top { display: flex; align-items: flex-start; gap: 12px; }
  .score-ring { width: 46px; height: 46px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 800; flex-shrink: 0; }
  .score-ring.high { background: #e8f8ec; color: #1b7a2d; }
  .score-ring.mid { background: #fff3e0; color: #b36b00; }
  .score-ring.low { background: #fce8e6; color: #c5221f; }
  .L1-info { flex: 1; min-width: 0; }
  .job-title { font-size: 14px; font-weight: 600; line-height: 1.3; }
  .company-line { font-size: 12px; color: #515154; margin-top: 1px; }
  .top-label { font-size: 12px; color: #1d1d1f; margin-top: 3px; font-weight: 500; }
  .pill { font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 3px 10px; border-radius: 10px; letter-spacing: 0.5px; white-space: nowrap; align-self: flex-start; }
  .pill.apply { background: #e8f8ec; color: #1b7a2d; }
  .pill.maybe { background: #fff3e0; color: #b36b00; }

  /* Blocking alert */
  .blocking { background: #fce8e6; color: #c5221f; font-size: 12px; font-weight: 600; padding: 6px 12px; border-radius: 8px; margin-top: 8px; }

  /* Signal chips — inline-block for Outlook compatibility (no flex) */
  .signals { margin-top: 8px; line-height: 1.9; }
  .chip { display: inline-block; margin: 2px 3px 2px 0; font-size: 11px; padding: 3px 9px; border-radius: 8px; white-space: nowrap; }
  .c-green { background: #e8f8ec; color: #1b7a2d; }
  .c-red { background: #fce8e6; color: #c5221f; }
  .c-amber { background: #fff3e0; color: #b36b00; }
  .c-blue { background: #e8f0fe; color: #1a73e8; }
  .c-gray { background: #f1f3f4; color: #5f6368; }
  .c-purple { background: #f3e8fd; color: #7b1fa2; }

  /* Main risk line */
  .risk-line { font-size: 12px; color: #c5221f; margin-top: 6px; font-weight: 500; }

  /* --- LAYER 2: Fast Explanation --- */
  .L2 { padding: 0 16px 10px; }
  .L2 .sec { margin-top: 8px; }
  .sec-title { font-size: 10px; font-weight: 700; color: #86868b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }
  .sec-body { font-size: 12px; color: #1d1d1f; }
  .sec-body ul { padding-left: 14px; margin: 0; }
  .sec-body li { margin-bottom: 1px; }
  .sec-body li.strong { font-weight: 500; }
  .sec-body li.partial { color: #515154; }
  .role-summary { font-size: 12px; color: #515154; font-style: italic; margin: 0 0 8px; padding: 6px 8px; background: #f5f5f7; border-radius: 6px; line-height: 1.5; }
  .sec-body li.gap { color: #b36b00; }
  .sec-body li.risk { color: #c5221f; }

  /* --- LAYER 3: Detail (collapsed feel) --- */
  .L3 { background: #fafafa; padding: 10px 16px 12px; border-top: 1px solid #f0f0f2; }
  .L3 .sec { margin-top: 6px; }
  .L3 .sec:first-child { margin-top: 0; }

  /* Phygital badge */
  .phygital-bar { display: flex; align-items: center; gap: 8px; font-size: 12px; }
  .phy-level { font-weight: 700; padding: 2px 8px; border-radius: 6px; font-size: 11px; }
  .phy-strong { background: #e8f0fe; color: #1a73e8; }
  .phy-moderate { background: #fff3e0; color: #b36b00; }
  .phy-weak { background: #f1f3f4; color: #5f6368; }

  /* Company box */
  .co-box { font-size: 11px; color: #515154; display: flex; gap: 16px; flex-wrap: wrap; }
  .co-item span { font-weight: 600; color: #86868b; }

  /* Score drivers */
  .drivers { line-height: 1.9; }
  .drv { display: inline-block; margin: 2px 3px 2px 0; font-size: 10px; padding: 2px 7px; border-radius: 5px; }
  .drv-pos { background: #e8f8ec; color: #1b7a2d; }
  .drv-neg { background: #fce8e6; color: #c5221f; }

  /* Trust & confidence */
  .trust { font-size: 11px; color: #86868b; margin-top: 6px; }
  .trust .conf { font-weight: 600; }
  .trust .missing { font-style: italic; }

  /* Skill chips — inline-block for Outlook compatibility */
  .skill-chips { margin: 6px 0 2px; line-height: 1.9; }
  .skill-chip { display: inline-block; margin: 2px 3px 2px 0; font-size: 10px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
  .skill-match { background: #e8f8ec; color: #1b7a2d; }
  .skill-gap { background: #fce8e6; color: #c5221f; }
  .skill-neutral { background: #f0f0f5; color: #515154; }

  /* Action buttons — table-based for Outlook; CSS classes kept for web preview only */
  .btn { display: inline-block; padding: 7px 18px; border-radius: 8px; text-decoration: none; font-size: 12px; font-weight: 600; text-align: center; }

  /* No matches */
  .empty { text-align: center; padding: 40px 20px; color: #86868b; }
  .empty .icon { font-size: 28px; margin-bottom: 6px; }

  .footer { text-align: center; color: #86868b; font-size: 10px; margin-top: 20px; padding-top: 14px; border-top: 1px solid #e5e5ea; }
</style>
</head>
<body>
<div class="container">
<div class="header">
  <h1>Jobsearcher Daily Digest</h1>
  <div class="stats">
    {{ date }}
    &middot; <span class="s-apply">{{ apply_count }} Apply</span>
    &middot; <span class="s-maybe">{{ maybe_count }} Maybe</span>
    &middot; <span class="s-skip">{{ skip_count }} Skip</span>
    {% if filtered_count > 0 %}&middot; {{ filtered_count }} pre-filtered{% endif %}
  </div>
  {% if keyword_stats %}
  <table style="margin: 10px auto; font-size: 11px; border-collapse: collapse;">
    <tr style="color: #86868b;"><th style="padding: 2px 10px; text-align: left;">Keyword</th><th style="padding: 2px 6px;">Found</th><th style="padding: 2px 6px;">Apply</th><th style="padding: 2px 6px;">Maybe</th></tr>
    {% for kw in keyword_stats %}
    <tr><td style="padding: 2px 10px;">{{ kw.keyword }}</td><td style="padding: 2px 6px; text-align: center;">{{ kw.found }}</td><td style="padding: 2px 6px; text-align: center; color: #34c759;">{{ kw.apply }}</td><td style="padding: 2px 6px; text-align: center; color: #ff9f0a;">{{ kw.maybe }}</td></tr>
    {% endfor %}
  </table>
  {% endif %}
</div>

{% if not actionable_jobs %}
<div class="empty">
  <div class="icon">--</div>
  <div>No actionable matches today.</div>
  <div style="font-size:12px; margin-top:4px;">{{ total_jobs }} scored, all below 60%. See skip list below.</div>
</div>
{% endif %}

{% for j in actionable_jobs %}
<div class="card {{ j.decision_class }}">

  <!-- LAYER 1: Decision Strip -->
  <div class="L1">
    <div class="L1-top">
      <div class="score-ring {{ j.score_class }}">{{ j.score }}</div>
      <div class="L1-info">
        <div class="job-title">{{ j.title }}</div>
        <div class="company-line">{{ j.company }}{% if j.industry %} · {{ j.industry }}{% endif %}</div>
        <div class="top-label">{{ j.top_label }}</div>
      </div>
      <span class="pill {{ j.decision_class }}">{{ j.hint }}</span>
    </div>

    {% if j.blocking %}
    <div class="blocking">{{ j.blocking }}</div>
    {% endif %}

    <div class="signals">
      {% if j.job_type %}<span class="chip c-purple">{{ j.job_type }}</span> {% endif %}
      <span class="chip c-gray">{{ j.work_mode }}</span>
      {% if j.salary != 'Unknown' %} <span class="chip c-green">{{ j.salary }}</span>{% endif %}
      {% if j.commute != 'Unknown' %} <span class="chip c-gray">{{ j.commute }}</span>{% endif %}
      <span class="chip {% if j.lang_class == 'ok' %}c-green{% elif j.lang_class == 'risk' %}c-red{% else %}c-amber{% endif %}">{{ j.language }}</span>
      {% if j.phygital_level == 'Strong' %} <span class="chip c-blue">Phygital</span>{% elif j.phygital_level == 'Moderate' %} <span class="chip c-amber">Phygital-adj</span>{% endif %}
      {% if j.km_visa %} <span class="chip c-green">KM Visa</span>{% endif %}
      {% if j.is_agency %} <span class="chip c-gray">Agency</span>{% endif %}
      {% if j.driver_flagged %} <span class="chip c-amber">Driving Licence</span>{% endif %}
    </div>

    {% if j.main_risk %}
    <div class="risk-line">{{ j.main_risk }}</div>
    {% endif %}

    {% if j.key_skills %}
    <div class="skill-chips">
      {% for sk in j.key_skills %}<span class="skill-chip skill-neutral">{{ sk }}</span> {% endfor %}
    </div>
    {% endif %}
  </div>

  <!-- LAYER 2: Fast Explanation -->
  <div class="L2">
    {% if j.role_summary %}
    <div class="role-summary">{{ j.role_summary }}</div>
    {% endif %}
    {% if j.strong_match or j.partial_match %}
    <div class="sec">
      <div class="sec-title">Why Fit</div>
      <div class="sec-body"><ul>
        {% for s in j.strong_match %}<li class="strong">{{ s }}</li>{% endfor %}
        {% for s in j.partial_match %}<li class="partial">~ {{ s }}</li>{% endfor %}
      </ul></div>
    </div>
    {% endif %}

    {% if j.gaps %}
    <div class="sec">
      <div class="sec-title">Gaps</div>
      <div class="sec-body"><ul>{% for g in j.gaps %}<li class="gap">{{ g }}</li>{% endfor %}</ul></div>
    </div>
    {% endif %}

    {% if j.risks %}
    <div class="sec">
      <div class="sec-title">Risks</div>
      <div class="sec-body"><ul>{% for r in j.risks %}<li class="risk">{{ r }}</li>{% endfor %}</ul></div>
    </div>
    {% endif %}
  </div>

  <!-- LAYER 3: Detail -->
  <div class="L3">
    {% if j.phygital_reason %}
    <div class="sec">
      <div class="phygital-bar">
        <span class="phy-level {{ j.phygital_class }}">{{ j.phygital_level }}</span>
        <span>{{ j.phygital_reason }}</span>
      </div>
    </div>
    {% endif %}

    {% if j.co_industry or j.co_size or j.co_km %}
    <div class="sec">
      <div class="co-box">
        {% if j.co_industry %}<div class="co-item"><span>Industry</span> {{ j.co_industry }}</div>{% endif %}
        {% if j.co_size %}<div class="co-item"><span>Size</span> {{ j.co_size }}</div>{% endif %}
        {% if j.co_km %}<div class="co-item"><span>KM</span> {{ j.co_km }}</div>{% endif %}
      </div>
    </div>
    {% endif %}

    {% if j.pos_drivers or j.neg_drivers %}
    <div class="sec">
      <div class="sec-title">Score Drivers</div>
      <div class="drivers">
        {% for d in j.pos_drivers %}<span class="drv drv-pos">{{ d.label }} {{ d.impact }}</span>{% endfor %}
        {% for d in j.neg_drivers %}<span class="drv drv-neg">{{ d.label }} {{ d.impact }}</span>{% endfor %}
      </div>
    </div>
    {% endif %}

    <div class="trust">
      <span class="conf">Confidence: {{ j.confidence }}</span>
      {% if j.missing_info %}<span class="missing"> · Missing: {{ j.missing_info | join(', ') }}</span>{% endif %}
    </div>

    <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-top: 10px;">
      <tr>
        <td style="border-radius: 8px; background-color: #f0f0f5;">
          <a href="{{ j.job_url }}" style="display: inline-block; padding: 7px 18px; color: #1d1d1f; text-decoration: none; font-size: 12px; font-weight: 600; border-radius: 8px; background-color: #f0f0f5;">View</a>
        </td>
      </tr>
    </table>
  </div>
</div>
{% endfor %}

{% if skip_count > 0 %}
<div style="text-align: center; color: #86868b; font-size: 12px; margin-top: 16px;">
  {{ skip_count }} jobs scored below 60% -- see Google Sheet for details
</div>
{% endif %}

<div class="footer">
  Jobsearcher · {{ keywords_used }} · Powered by JobSpy + OpenRouter
</div>
</div>
</body>
</html>
"""


def _parse_card(row):
    """Parse match_result JSON into template-ready dict."""
    r = {}
    try:
        r = json.loads(row.get("match_result", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass

    card = r.get("CardSummary", {})
    sig = r.get("DecisionSignals", {})
    fit = r.get("WhyFit", {})
    phy = r.get("PhygitalAssessment", {})
    co = r.get("CompanySnapshot", {})
    sb = r.get("ScoreBreakdown", {})
    trust = r.get("TrustNotes", {})

    score = card.get("MatchScore", row.get("match_score", 0))
    hint = card.get("DecisionHint", row.get("decision_hint", "Skip"))

    lang_text = sig.get("LanguageText", "Unknown")
    lang_class = "ok" if "OK" in lang_text else ("risk" if "Mandatory" in lang_text else "pref")

    phy_level = phy.get("Level", "Weak")
    phy_class = {"Strong": "phy-strong", "Moderate": "phy-moderate"}.get(phy_level, "phy-weak")

    return {
        "title": row.get("title", ""),
        "company": row.get("company", ""),
        "industry": co.get("Industry", ""),
        "job_url": row.get("job_url", "#"),
        "job_id": _job_id_from_url(row.get("job_url", "")),
        "job_type": _format_job_type(row.get("job_type", "")),
        "score": score,
        "hint": hint,
        "decision_class": hint.lower(),
        "score_class": "high" if score >= 75 else ("mid" if score >= 60 else "low"),
        "top_label": card.get("TopLabel", ""),
        "main_risk": card.get("MainRisk", ""),
        "blocking": card.get("BlockingAlert", ""),
        # Signals
        "work_mode": sig.get("WorkMode", row.get("work_mode", "Unknown")),
        "salary": sig.get("SalaryText", row.get("salary_info", "") or "Unknown"),
        "commute": sig.get("CommuteText", f"~{row['travel_minutes']}min" if row.get("travel_minutes") else "Unknown"),
        "language": lang_text,
        "lang_class": lang_class,
        # Show KM Visa chip if company is on IND sponsor register OR if JD mentions it
        "km_visa": row.get("km_visa_sponsor", False) or row.get("km_visa_mentioned", False),
        "is_agency": row.get("is_agency", False),
        "driver_flagged": row.get("driver_license_flagged", False),
        # Phygital
        "phygital_level": phy_level,
        "phygital_class": phy_class,
        "phygital_reason": phy.get("Reason", ""),
        # Role summary (what this job actually does, in English)
        "role_summary": card.get("RoleSummary", ""),
        # WhyFit
        "strong_match": fit.get("StrongMatch", []),
        "partial_match": fit.get("PartialMatch", []),
        "gaps": r.get("Gaps", []),
        "risks": r.get("Risks", []),
        "key_skills": r.get("KeySkills", []),
        # Company
        "co_industry": co.get("Industry", ""),
        "co_size": co.get("Size"),
        "co_km": co.get("KM_Status"),
        # Score drivers
        "pos_drivers": sb.get("PositiveDrivers", []),
        "neg_drivers": sb.get("NegativeDrivers", []),
        # Trust
        "confidence": trust.get("Confidence", "Low"),
        "missing_info": trust.get("MissingInfo", []),
    }


def build_email_html(df, filtered_count=0, keyword_stats=None):
    """Render digest. Only Apply+Maybe as full cards. Skip as summary line."""
    all_cards = [_parse_card(row) for _, row in df.iterrows()]

    actionable = [c for c in all_cards if c["hint"] in ("Apply", "Maybe")]
    skip_count = sum(1 for c in all_cards if c["hint"] == "Skip")

    template = Template(EMAIL_TEMPLATE)
    keywords = ", ".join(df["search_keyword"].unique()) if "search_keyword" in df.columns else "all"

    return template.render(
        date=datetime.now().strftime("%Y-%m-%d"),
        total_jobs=len(all_cards),
        apply_count=sum(1 for c in all_cards if c["hint"] == "Apply"),
        maybe_count=sum(1 for c in all_cards if c["hint"] == "Maybe"),
        skip_count=skip_count,
        filtered_count=filtered_count,
        actionable_jobs=actionable,
        keyword_stats=keyword_stats or [],
        keywords_used=keywords,
    )


PRERANK_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Jobsearcher Pre-rank Digest</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f7; color: #1d1d1f; padding: 16px; font-size: 13px; }
  .wrap { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  .sub { color: #86868b; font-size: 12px; margin-bottom: 14px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e5e5ea; vertical-align: top; }
  th { background: #fafafa; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: #86868b; }
  tr:last-child td { border-bottom: none; }
  .score { font-weight: 700; font-size: 15px; width: 50px; text-align: center; }
  .score.pos { color: #1b7a2d; }
  .score.neu { color: #86868b; }
  .score.neg { color: #c5221f; }
  .title a { color: #0066cc; text-decoration: none; font-weight: 600; }
  .title a:hover { text-decoration: underline; }
  .company { color: #515154; font-size: 12px; }
  .reasons { color: #86868b; font-size: 11px; font-family: ui-monospace, Menlo, monospace; }
  .meta { color: #515154; font-size: 11px; }
  .footer { text-align: center; color: #86868b; font-size: 11px; margin-top: 16px; }
</style>
</head>
<body>
<div class="wrap">
<h1>Jobsearcher · Pre-rank Digest</h1>
<div class="sub">{{ total }} jobs ranked · no LLM scoring · {{ generated_at }}</div>
<table>
<thead><tr><th>Rank</th><th>Job</th><th>Work / Salary</th><th>Signals</th></tr></thead>
<tbody>
{% for j in jobs %}
<tr>
  <td class="score {{ j.cls }}">{{ j.score }}</td>
  <td>
    <div class="title"><a href="{{ j.url }}" target="_blank">{{ j.title }}</a></div>
    <div class="company">{{ j.company }} · {{ j.location }}</div>
    <div class="reasons">{{ j.reasons }}</div>
  </td>
  <td class="meta">{{ j.work_mode }}<br>{{ j.salary }}</td>
  <td class="meta">
    {% if j.phygital %}Phygital · {% endif %}
    {% if j.pure_saas %}Pure SaaS · {% endif %}
    {% if j.driver %}Driver licence · {% endif %}
    {% if j.dutch_nth %}Dutch preferred{% endif %}
  </td>
</tr>
{% endfor %}
</tbody>
</table>
<div class="footer">Edit weights in <code>config/prerank.yaml</code> · No LLM quota spent on this digest</div>
</div>
</body>
</html>
"""


def build_prerank_digest(df, output_dir="output"):
    """Render a pre-rank-only digest. No LLM fields, no match_result required."""
    jobs = []
    for _, row in df.iterrows():
        score = int(row.get("prerank_score", 0))
        cls = "pos" if score > 0 else ("neg" if score < 0 else "neu")
        jobs.append({
            "score": f"{score:+d}" if score else "0",
            "cls": cls,
            "title": str(row.get("title", "?"))[:90],
            "company": str(row.get("company", "?"))[:60],
            "location": str(row.get("location", ""))[:40],
            "url": str(row.get("job_url", "#")),
            "reasons": str(row.get("prerank_reasons", "")),
            "work_mode": str(row.get("work_mode", "Unknown")),
            "salary": str(row.get("salary_info", "")) or "Unknown",
            "phygital": bool(row.get("phygital_detected", False)),
            "pure_saas": bool(row.get("pure_saas_detected", False)),
            "driver": bool(row.get("driver_license_flagged", False)),
            "dutch_nth": bool(row.get("dutch_nice_to_have", False)),
        })

    template = Template(PRERANK_TEMPLATE)
    html = template.render(
        jobs=jobs,
        total=len(jobs),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    os.makedirs(output_dir, exist_ok=True)
    output_path = f"{output_dir}/prerank_digest_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved pre-rank digest: {output_path}")
    return output_path


def send_email(df=None, subject=None, filtered_count=0, keyword_stats=None, html_body=None):
    """Send HTML email. Accepts either a DataFrame (digest) or raw html_body."""
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_APP_PASSWORD")
    recipient = os.getenv("EMAIL_RECIPIENT")

    if html_body is not None:
        html = html_body
    else:
        html = build_email_html(df, filtered_count=filtered_count, keyword_stats=keyword_stats)

    output_path = f"output/digest_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    os.makedirs("output", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved digest: {output_path}")

    if not all([sender, password, recipient]):
        print("Email not configured. Digest saved locally only.")
        return output_path

    if subject is None:
        if df is not None:
            apply_n = len(df[df["decision_hint"] == "Apply"]) if "decision_hint" in df.columns else 0
            subject = f"Jobsearcher: {apply_n} Apply, {len(df)} total -- {datetime.now().strftime('%Y-%m-%d')}"
        else:
            subject = f"Jobsearcher -- {datetime.now().strftime('%Y-%m-%d')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

    print(f"Email sent to {recipient}")
    return output_path
