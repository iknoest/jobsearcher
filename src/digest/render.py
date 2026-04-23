"""Trust-first digest HTML renderer (R4-02).

Builds the full email body from:
    - scored DataFrame (rows already carry R3-05 score + audit columns)
    - funnel + bottleneck dicts from bottleneck.py
    - pending_feedback list + feedback_summary dict from state.py

All explanation strings come from explain.explain_row(); this module
is pure layout + CSS. No LLM, no Python-side computation of scores.

Telegram deep-link card actions are produced here from the row URL:
    Open JD  → row.job_url
    Keep     → tg://start good_<id>
    Reject   → tg://start skip_<id>
    Note     → tg://start note_<id>
"""

from __future__ import annotations

import hashlib as _hashlib
import os
import re as _re
from datetime import datetime
from typing import Any

from jinja2 import Template

from src.digest.bottleneck import compute_trust_status_line
from src.digest.explain import explain_row


# ── Telegram-safe job ID (mirrors src/notifier.py) ────────────────────

def _job_id_from_url(url: str) -> str:
    url = str(url or "")
    m = _re.search(r"/jobs/view/(\d+)", url)
    if m:
        return m.group(1)
    m = _re.search(r"[?&]jk=([A-Za-z0-9]+)", url)
    if m:
        return "in_" + m.group(1)
    return "hx_" + _hashlib.md5(url.encode()).hexdigest()[:12]


# ── Card dict (renderer-facing view of a row) ─────────────────────────

def _card(row: dict, tg_username: str) -> dict:
    expl = explain_row(row)
    url = str(row.get("job_url", "") or "")
    jid = _job_id_from_url(url)
    tg = (tg_username or "").lstrip("@")

    def _tg(prefix: str) -> str:
        if not tg:
            return ""
        return f"https://t.me/{tg}?start={prefix}_{jid}"

    title = str(row.get("title", "") or "").strip() or "(no title)"
    company = str(row.get("company", "") or "").strip() or "Unknown company"
    location = str(row.get("location", "") or "").strip()
    work_mode = str(row.get("work_mode", "") or "").strip()
    travel_min = row.get("travel_minutes") or row.get("travel_min") or ""

    return {
        "job_id": jid,
        "title": title,
        "company": company,
        "location": location,
        "work_mode": work_mode,
        "travel_min": travel_min,
        "url_open": url,
        "url_keep": _tg("good"),
        "url_reject": _tg("skip"),
        "url_note": _tg("note"),
        "final_score": int(row.get("final_score", 0) or 0),
        "recommendation": expl["recommendation"],
        "subscore_bars": expl["subscore_bars"],
        "why_apply": expl["why_apply"],
        "watch_outs": expl["watch_outs"],
        "holding_back": expl["holding_back"],
        "still_worth": expl["still_worth"],
        "skip_reason": expl["skip_reason"],
        "blockers": str(row.get("blockers", "") or ""),
    }


# ── Skip aggregation ──────────────────────────────────────────────────

def _aggregate_skip_reasons(skip_cards: list[dict]) -> list[dict]:
    """Count Skip cards by their 1-line skip_reason, top 5 most frequent."""
    counts: dict[str, int] = {}
    for c in skip_cards:
        key = (c.get("skip_reason") or "Skipped").strip()
        counts[key] = counts.get(key, 0) + 1
    # If "Other pre-filter" or a catch-all bucket grows >10, we surface
    # the drop_reason distribution in a sub-line so the user sees it split.
    return [
        {"label": lbl, "count": n}
        for lbl, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ][:5]


def _borderline_skips(skip_cards: list[dict], lo: int = 45, hi: int = 49) -> list[dict]:
    """Skip cards in the audit-worthy score band, sorted by final_score desc."""
    out = [c for c in skip_cards if lo <= c["final_score"] <= hi]
    out.sort(key=lambda c: -c["final_score"])
    return out[:5]


# ── Main entry point ──────────────────────────────────────────────────

def render_trust_digest(
    df,
    funnel: dict,
    bottleneck_headline: str | None = None,
    pending_feedback: list[dict] | None = None,
    feedback_summary: dict | None = None,
    keyword_stats: list[dict] | None = None,
    tg_bot_username: str | None = None,
) -> str:
    """Return the full HTML digest string.

    `df` must carry the R3-05 columns (recommendation, final_score,
    score_role_fit, …). Rows without those fields render as Skip with
    0-score bars — no crash, just empty.
    """
    tg = tg_bot_username if tg_bot_username is not None else os.getenv("TG_BOT_USERNAME", "")

    cards = [_card(row.to_dict(), tg) for _, row in df.iterrows()]
    apply_cards = [c for c in cards if c["recommendation"] == "Apply"]
    review_cards = [c for c in cards if c["recommendation"] == "Review"]
    skip_cards = [c for c in cards if c["recommendation"] == "Skip"]

    # Sort within tiers by final_score desc for stable ordering
    apply_cards.sort(key=lambda c: -c["final_score"])
    review_cards.sort(key=lambda c: -c["final_score"])

    borderline = _borderline_skips(skip_cards)
    skip_top_reasons = _aggregate_skip_reasons(skip_cards)

    trust_line = compute_trust_status_line(
        apply_count=len(apply_cards),
        review_count=len(review_cards),
        borderline_skip_count=len(borderline),
    )

    template = Template(_TEMPLATE)
    return template.render(
        date=datetime.now().strftime("%Y-%m-%d"),
        trust_line=trust_line,
        bottleneck_headline=bottleneck_headline,
        apply_cards=apply_cards,
        review_cards=review_cards,
        skip_cards=skip_cards,
        skip_count=len(skip_cards),
        skip_top_reasons=skip_top_reasons,
        borderline=borderline,
        funnel=funnel,
        pending_feedback=pending_feedback or [],
        feedback_summary=feedback_summary or {"kept": 0, "rejected": 0, "notes": 0, "themes": []},
        keyword_stats=keyword_stats or [],
        tg_enabled=bool(tg),
    )


# ── Template ──────────────────────────────────────────────────────────

_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Jobsearcher · Trust digest</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f7; color: #1d1d1f; padding: 16px; font-size: 14px; margin: 0; }
  .wrap { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px 0; }
  h2 { font-size: 15px; margin: 22px 0 8px 0; color: #1d1d1f; letter-spacing: 0.2px; }
  .trust-line { font-size: 14px; color: #1d1d1f; font-weight: 600; margin-bottom: 6px; }
  .sub { color: #86868b; font-size: 12px; margin-bottom: 10px; }
  .bottleneck { background: #fff7ea; border: 1px solid #f0c56a; color: #7a4e00; padding: 8px 12px; border-radius: 8px; margin: 10px 0 16px 0; font-size: 13px; }

  .card { background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); padding: 14px 16px; margin-bottom: 12px; }
  .card .head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .card .title { font-weight: 600; font-size: 15px; margin-bottom: 2px; }
  .card .title a { color: #0066cc; text-decoration: none; }
  .card .meta { color: #515154; font-size: 12px; margin-bottom: 8px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; letter-spacing: 0.2px; }
  .pill.apply { background: #d4edda; color: #1b5e20; }
  .pill.review { background: #fff3cd; color: #7c5e00; }
  .pill.skip { background: #f1f1f1; color: #555; }
  .score { font-weight: 700; font-size: 18px; color: #1d1d1f; white-space: nowrap; }

  .bars { margin: 8px 0 10px 0; }
  .bar-row { display: flex; align-items: center; margin-bottom: 3px; font-size: 12px; }
  .bar-label { width: 150px; color: #515154; }
  .bar-track { flex: 1; background: #eee; height: 8px; border-radius: 4px; overflow: hidden; margin-right: 8px; }
  .bar-fill { height: 100%; }
  .bar-fill.strong { background: #38a169; }
  .bar-fill.ok { background: #d69e2e; }
  .bar-fill.weak { background: #c53030; }
  .bar-num { width: 48px; text-align: right; color: #515154; font-variant-numeric: tabular-nums; }

  .reasons { margin: 6px 0 4px 0; }
  .reasons .label { font-weight: 600; font-size: 12px; color: #1d1d1f; margin-right: 6px; }
  .reasons ul { margin: 2px 0 6px 20px; padding: 0; font-size: 13px; color: #333; }
  .reasons li { margin-bottom: 2px; }
  .reasons.why .label { color: #1b5e20; }
  .reasons.watch .label { color: #7c5e00; }
  .reasons.holding .label { color: #7c5e00; }
  .reasons.worth .label { color: #1b5e20; }

  .actions { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
  .btn { display: inline-block; padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; text-decoration: none; border: 1px solid transparent; }
  .btn.open { background: #f1f3f5; color: #1d1d1f; border-color: #d0d3d6; }
  .btn.keep { background: #d4edda; color: #155724; border-color: #b6ddbe; }
  .btn.reject { background: #f8d7da; color: #721c24; border-color: #efb4b8; }
  .btn.note { background: #fff3cd; color: #7c5e00; border-color: #ecd591; }

  .skip-summary { background: #fff; border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; }
  .skip-summary ul { margin: 6px 0 4px 18px; padding: 0; color: #333; font-size: 13px; }
  .borderline { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #e0e0e0; }
  .borderline .b-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 13px; }
  .borderline .b-title { color: #1d1d1f; }
  .borderline .b-title a { color: #0066cc; text-decoration: none; }
  .borderline .b-score { font-weight: 700; color: #7c5e00; margin-left: 12px; }

  .funnel { background: #fff; border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; }
  .funnel table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .funnel td { padding: 4px 6px; }
  .funnel .band { font-weight: 600; }
  .funnel .band.filtered { color: #7c5e00; }
  .funnel .band.notsurf { color: #555; }
  .funnel .band.surf { color: #1b5e20; }
  .funnel .num { text-align: right; font-variant-numeric: tabular-nums; width: 80px; }

  .pending, .feedback-summary { background: #fff; border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; }
  .pending .row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 13px; border-bottom: 1px dashed #eee; }
  .pending .row:last-child { border-bottom: none; }
  .pending .row-meta { color: #86868b; font-size: 12px; }

  .footer { text-align: center; color: #86868b; font-size: 12px; margin: 22px 0 10px 0; }
  .footer a { color: #0066cc; text-decoration: none; }
</style>
</head>
<body>
<div class="wrap">

  <h1>Jobsearcher · Trust digest</h1>
  <div class="trust-line">Trust status today: {{ trust_line }}</div>
  <div class="sub">{{ date }}</div>
  {% if bottleneck_headline %}
  <div class="bottleneck"><b>Main bottleneck today:</b> {{ bottleneck_headline }}</div>
  {% endif %}

  {# ── Pending feedback block ─────────────────────────────── #}
  {% if pending_feedback %}
  <h2>Feedback still pending ({{ pending_feedback|length }})</h2>
  <div class="pending">
    <div class="sub">From the previous digest, no Keep / Reject / Note yet:</div>
    {% for p in pending_feedback %}
    <div class="row">
      <div>
        <b>{{ p.title }}</b> — {{ p.company }}
        <span class="row-meta"> · <span class="pill {% if p.recommendation == 'Apply' %}apply{% elif p.recommendation == 'Review' %}review{% else %}skip{% endif %}">{{ p.recommendation }}</span> · {{ p.final_score }}/100</span>
      </div>
      <div>
        {% if p.job_url %}<a class="btn open" href="{{ p.job_url }}">Open JD</a>{% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {# ── Apply cards ─────────────────────────────────────────── #}
  {% if apply_cards %}
  <h2>Apply ({{ apply_cards|length }})</h2>
  {% for c in apply_cards %}
  <div class="card">
    <div class="head">
      <div>
        <div class="title"><a href="{{ c.url_open }}">{{ c.title }}</a></div>
        <div class="meta">{{ c.company }}{% if c.location %} · {{ c.location }}{% endif %}{% if c.work_mode %} · {{ c.work_mode }}{% endif %}{% if c.travel_min %} · {{ c.travel_min }}min{% endif %}</div>
        <span class="pill apply">Apply</span>
      </div>
      <div class="score">{{ c.final_score }}/100</div>
    </div>
    <div class="bars">
      {% for b in c.subscore_bars %}
      <div class="bar-row">
        <div class="bar-label">{{ b.label }}</div>
        <div class="bar-track"><div class="bar-fill {{ b.band }}" style="width: {{ (b.ratio * 100)|round(0, 'floor')|int }}%;"></div></div>
        <div class="bar-num">{{ b.score }}/{{ b.max }}</div>
      </div>
      {% endfor %}
    </div>
    {% if c.why_apply %}
    <div class="reasons why">
      <div><span class="label">✅ Why Apply</span></div>
      <ul>{% for r in c.why_apply %}<li>{{ r }}</li>{% endfor %}</ul>
    </div>
    {% endif %}
    {% if c.watch_outs %}
    <div class="reasons watch">
      <div><span class="label">⚠ Watch-outs</span></div>
      <ul>{% for r in c.watch_outs %}<li>{{ r }}</li>{% endfor %}</ul>
    </div>
    {% endif %}
    <div class="actions">
      <a class="btn open" href="{{ c.url_open }}">Open JD</a>
      {% if tg_enabled %}
        <a class="btn keep" href="{{ c.url_keep }}">Keep</a>
        <a class="btn reject" href="{{ c.url_reject }}">Reject</a>
        <a class="btn note" href="{{ c.url_note }}">Note</a>
      {% endif %}
    </div>
  </div>
  {% endfor %}
  {% endif %}

  {# ── Review cards ────────────────────────────────────────── #}
  {% if review_cards %}
  <h2>Review ({{ review_cards|length }})</h2>
  {% for c in review_cards %}
  <div class="card">
    <div class="head">
      <div>
        <div class="title"><a href="{{ c.url_open }}">{{ c.title }}</a></div>
        <div class="meta">{{ c.company }}{% if c.location %} · {{ c.location }}{% endif %}{% if c.work_mode %} · {{ c.work_mode }}{% endif %}{% if c.travel_min %} · {{ c.travel_min }}min{% endif %}</div>
        <span class="pill review">Review</span>
      </div>
      <div class="score">{{ c.final_score }}/100</div>
    </div>
    <div class="bars">
      {% for b in c.subscore_bars %}
      <div class="bar-row">
        <div class="bar-label">{{ b.label }}</div>
        <div class="bar-track"><div class="bar-fill {{ b.band }}" style="width: {{ (b.ratio * 100)|round(0, 'floor')|int }}%;"></div></div>
        <div class="bar-num">{{ b.score }}/{{ b.max }}</div>
      </div>
      {% endfor %}
    </div>
    {% if c.holding_back %}
    <div class="reasons holding">
      <div><span class="label">What's holding this back?</span></div>
      <ul>{% for r in c.holding_back %}<li>{{ r }}</li>{% endfor %}</ul>
    </div>
    {% endif %}
    {% if c.still_worth %}
    <div class="reasons worth">
      <div><span class="label">Still worth a look because…</span></div>
      <ul>{% for r in c.still_worth %}<li>{{ r }}</li>{% endfor %}</ul>
    </div>
    {% endif %}
    <div class="actions">
      <a class="btn open" href="{{ c.url_open }}">Open JD</a>
      {% if tg_enabled %}
        <a class="btn keep" href="{{ c.url_keep }}">Keep</a>
        <a class="btn reject" href="{{ c.url_reject }}">Reject</a>
        <a class="btn note" href="{{ c.url_note }}">Note</a>
      {% endif %}
    </div>
  </div>
  {% endfor %}
  {% endif %}

  {# ── Skip summary (collapsed) + borderline audit ─────────── #}
  {% if skip_cards %}
  <h2>Skip ({{ skip_count }}) — collapsed</h2>
  <div class="skip-summary">
    {% if skip_top_reasons %}
    <div><b>Top skip reasons:</b></div>
    <ul>
      {% for r in skip_top_reasons %}
      <li>{{ r.label }} — {{ r.count }}</li>
      {% endfor %}
    </ul>
    {% endif %}
    {% if borderline %}
    <div class="borderline">
      <div><b>Borderline skips worth audit (score 45–49):</b></div>
      {% for b in borderline %}
      <div class="b-row">
        <div class="b-title"><a href="{{ b.url_open }}">{{ b.title }}</a> — {{ b.company }}</div>
        <div class="b-score">{{ b.final_score }}/100</div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
  {% endif %}

  {# ── 3-band funnel ───────────────────────────────────────── #}
  <h2>Funnel today</h2>
  <div class="funnel">
    <table>
      <tr><td class="band filtered">FILTERED (before scoring)</td><td class="num">{{ funnel.pre_scoring_filtered }}</td></tr>
      <tr><td class="band notsurf">SCORED BUT NOT SURFACED</td><td class="num">{{ funnel.not_surfaced_scored }}</td></tr>
      <tr><td class="band surf">SCORED & SURFACED (Apply + Review)</td><td class="num">{{ funnel.surfaced }}</td></tr>
      <tr><td class="sub" colspan="2">Total scraped: {{ funnel.total_scraped }} · Scored total: {{ funnel.scored_total }} · Filtered: {{ funnel.filtered_pct }}% · Surfaced: {{ funnel.surfaced_pct }}%</td></tr>
    </table>
  </div>

  {# ── Feedback summary block ──────────────────────────────── #}
  {% if feedback_summary.kept or feedback_summary.rejected or feedback_summary.notes %}
  <h2>Your feedback since last digest</h2>
  <div class="feedback-summary">
    <div>Kept <b>{{ feedback_summary.kept }}</b> · Rejected <b>{{ feedback_summary.rejected }}</b> · Notes <b>{{ feedback_summary.notes }}</b></div>
    {% if feedback_summary.themes %}
    <div class="sub" style="margin-top:6px;">Common reject themes:</div>
    <ul>
      {% for t in feedback_summary.themes %}
      <li>{{ t.label }} — {{ t.count }}</li>
      {% endfor %}
    </ul>
    {% endif %}
  </div>
  {% endif %}

  {# ── Footer ──────────────────────────────────────────────── #}
  <div class="footer">
    Trust-first digest · deterministic scoring · no LLM narrative on explanations<br>
    <a href="#">Review filter settings</a> · <a href="#">Inspect filter logic</a>
  </div>

</div>
</body>
</html>
"""
