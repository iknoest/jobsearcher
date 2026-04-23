"""Generate a sample R4-02 trust-digest HTML for visual sanity-checking.

Run: `python scripts/render_digest_preview.py`
Output: `output/r4_02_preview.html`

This is a PREVIEW — Keep / Reject / Note buttons point at a sample
bot username and won't work in your Telegram. Open-JD links point at
real LinkedIn URLs that may be stale.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.digest.bottleneck import compute_bottleneck, compute_funnel
from src.digest.render import render_trust_digest


def _row(**kw):
    base = {
        "job_url": "https://www.linkedin.com/jobs/view/4400000000",
        "title": "", "company": "", "location": "Amsterdam, NL",
        "work_mode": "hybrid", "travel_minutes": 45,
        "recommendation": "Apply", "final_score": 0,
        "score_role_fit": 0, "score_hard_skill": 0, "score_seniority_fit": 0,
        "score_industry_proximity": 0, "score_desirability": 0, "score_evidence": 0,
        "top_reasons": "", "top_risks": "", "review_flags": "", "blockers": "",
        "drop_reason": "",
        "role_family": "product_manager",
        "industry": "robotics", "industry_display": "Robotics",
        "_score_raw_hard_skill_signals": {},
        "_score_raw_role_fit_signals": {},
        "_score_raw_evidence_signals": {},
    }
    base.update(kw)
    return base


def build_sample_df() -> pd.DataFrame:
    return pd.DataFrame([
        _row(
            job_url="https://www.linkedin.com/jobs/view/4400000001",
            title="Senior Product Manager, Robotics",
            company="Acme Robotics",
            recommendation="Apply", final_score=82,
            score_role_fit=22, score_hard_skill=24, score_seniority_fit=14,
            score_industry_proximity=9, score_desirability=7, score_evidence=6,
            top_reasons="Role fit: 22/25; Hard skills: 24/30; Seniority fit: 14/15",
            role_family="product_manager",
            industry="robotics", industry_display="Robotics",
            _score_raw_hard_skill_signals={
                "verified_matches": [
                    {"canonical_name": "Product strategy input"},
                    {"canonical_name": "User research synthesis"},
                    {"canonical_name": "Prototyping"},
                ],
                "adjacent_matches": [{"canonical_name": "Design validation tools familiarity"}],
                "forbidden_optional_hits": [],
                "forbidden_core_hits": [],
            },
            _score_raw_evidence_signals={
                "matched_proofs": [
                    {"topic": "BYD VOC research", "strength": "High", "matched_via": "industry"},
                    {"topic": "TomTom data visualisation", "strength": "Medium", "matched_via": "role"},
                ]
            },
        ),
        _row(
            job_url="https://www.linkedin.com/jobs/view/4400000002",
            title="Innovation PM — Connected Products",
            company="Smart Devices BV",
            location="Utrecht, NL", work_mode="hybrid", travel_minutes=55,
            recommendation="Review", final_score=63,
            score_role_fit=18, score_hard_skill=8, score_seniority_fit=14,
            score_industry_proximity=7, score_desirability=9, score_evidence=7,
            top_reasons="Role fit: 18/25; Desirability: 9/10",
            top_risks="Hard skills: 8/30",
            review_flags="low hard_skill_guardrail (8/30 <= 8)",
            role_family="innovation_engineer",
            industry="consumer_electronics", industry_display="Consumer Electronics",
            _score_raw_hard_skill_signals={
                "verified_matches": [{"canonical_name": "Concept validation"}],
                "adjacent_matches": [{"canonical_name": "Embedded firmware familiarity"}],
                "forbidden_optional_hits": [{"canonical_name": "Deep firmware C/C++"}],
                "forbidden_core_hits": [],
            },
            _score_raw_evidence_signals={
                "matched_proofs": [{"topic": "IoT product trial (HK)", "strength": "Medium", "matched_via": "industry"}]
            },
        ),
        _row(
            job_url="https://www.linkedin.com/jobs/view/4400000003",
            title="CFD Simulation Engineer",
            company="SimCorp",
            location="Eindhoven, NL", work_mode="onsite", travel_minutes=95,
            recommendation="Skip", final_score=28,
            score_role_fit=5, score_hard_skill=0, score_seniority_fit=4,
            score_industry_proximity=5, score_desirability=3, score_evidence=1,
            blockers="forbidden_core_skill: CFD simulation",
            drop_reason="forbidden_core_skill: CFD simulation",
            role_family="system_engineer",
            industry="automotive", industry_display="Automotive",
            _score_raw_hard_skill_signals={
                "forbidden_core_hits": [{"canonical_name": "CFD simulation"}],
                "verified_matches": [],
            },
        ),
        _row(
            job_url="https://www.linkedin.com/jobs/view/4400000004",
            title="Product Engineer — Sustainable Packaging",
            company="Fringe Tech",
            location="Rotterdam, NL", work_mode="hybrid", travel_minutes=70,
            recommendation="Skip", final_score=47,
            score_role_fit=12, score_hard_skill=10, score_seniority_fit=10,
            score_industry_proximity=6, score_desirability=5, score_evidence=4,
            top_reasons="Role fit: 12/25",
            role_family="product_engineer_hardware",
            industry="sustainability", industry_display="Sustainability / CleanTech",
            _score_raw_hard_skill_signals={
                "verified_matches": [
                    {"canonical_name": "Product development for physical products"},
                ],
            },
        ),
    ])


def main() -> None:
    df = build_sample_df()
    funnel = compute_funnel(
        total_scraped=180, pre_scoring_filtered=45, scored_total=125,
        scored_but_skip=80, below_max_jobs_cap=0,
        apply_count=1, review_count=1,
    )
    bh = compute_bottleneck(funnel, avg_hard_skill=10.5)
    html = render_trust_digest(
        df,
        funnel=funnel,
        bottleneck_headline=bh,
        pending_feedback=[{
            "job_url": "https://www.linkedin.com/jobs/view/4399999999",
            "title": "Product Value Manager", "company": "Polaroid",
            "recommendation": "Apply", "final_score": 81,
        }],
        feedback_summary={
            "kept": 2, "rejected": 3, "notes": 1,
            "themes": [
                {"label": "Too technical / engineering-heavy", "count": 2},
                {"label": "Wrong industry / sector", "count": 1},
            ],
        },
        tg_bot_username="jobsearch_bot",
        total_new=125, role_groups=7, hours_window=24,
        llm_evaluated=12, llm_skipped_by_gates=33,
        sub_counters={"skill_llm_calls": 8, "skill_cache_hits": 4, "skill_extraction_failures": 0},
        feedback_history_url="https://docs.google.com/spreadsheets/example",
        preview_mode=True,
    )
    os.makedirs("output", exist_ok=True)
    out = Path("output") / "r4_02_preview.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({len(html)} chars)")


if __name__ == "__main__":
    main()
