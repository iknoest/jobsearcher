"""Full R3 dry-run: classifiers → 6 sub-scorers → aggregate → Apply/Review/Skip.

Uses the EXTRACTED-SKILLS cache so we don't burn LLM quota re-extracting.
Only calls hard_skill extraction for jobs already in output/jd_skills_cache.json;
everything else gets a synthetic empty skills list (score 0/30) and will
underrank — this is EXPECTED and called out in the report so the user
can tell which jobs had real LLM extraction.
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402

from src.classify.role_family import classify_dataframe as clf_role  # noqa: E402
from src.classify.industry import classify_dataframe as clf_industry  # noqa: E402
from src.classify.seniority import classify_dataframe as clf_seniority  # noqa: E402
from src.score.role_fit import score_role_fit  # noqa: E402
from src.score.hard_skill import score_hard_skills, cache_key_for  # noqa: E402
from src.score.seniority_fit import score_seniority_fit  # noqa: E402
from src.score.industry_proximity import score_industry_proximity  # noqa: E402
from src.score.desirability import score_desirability  # noqa: E402
from src.score.evidence import score_evidence  # noqa: E402
from src.score.aggregate import aggregate_score  # noqa: E402

CACHE = ROOT / "output" / "enriched_jobs.pkl"
SKILLS_CACHE = ROOT / "output" / "jd_skills_cache.json"
OUT = ROOT / "output" / "aggregate_dryrun.json"

if not CACHE.exists():
    print(f"No cache at {CACHE}")
    sys.exit(1)

df = pd.read_pickle(CACHE)
print(f"Loaded {len(df)} jobs.")

# Stage 3b
df = clf_role(df)
df = df[df["role_hard_reject"] != True].reset_index(drop=True)  # noqa: E712
df = clf_industry(df)
df = clf_seniority(df)
df = df[df["seniority_hard_reject"] != True].reset_index(drop=True)  # noqa: E712
print(f"After Stage 3b: {len(df)} jobs.")

# Load jd_skills cache to detect which jobs have LLM extraction available
skills_cache = json.load(open(SKILLS_CACHE, encoding="utf-8")) if SKILLS_CACHE.exists() else {}
print(f"Pre-extracted skill cache has {len(skills_cache)} entries.")


def _row_classified_role(row):
    return {
        "family": row.get("role_family", "unknown"),
        "bucket": row.get("role_bucket", "unknown"),
        "confidence": float(row.get("role_confidence", 0.0) or 0.0),
        "display_name": row.get("role_family", "Unknown"),
        "evidence_spans": {"title_match": [], "confirming_jd": [],
                           "rescuing_jd": [], "negative_jd": []},
        "would_hard_reject": bool(row.get("role_hard_reject", False)),
    }


def _row_classified_seniority(row):
    return {
        "seniority_level": row.get("seniority_level", "unknown"),
        "seniority_years_required": row.get("seniority_years_required"),
        "seniority_hard_reject": bool(row.get("seniority_hard_reject", False)),
        "seniority_mismatch_flag": bool(row.get("seniority_mismatch_flag", False)),
    }


def _row_classified_industry(row):
    return {
        "industry": row.get("industry", "unknown"),
        "display_name": row.get("industry_display", "Unknown"),
        "proximity_to_ava": row.get("industry_proximity"),
        "source": row.get("industry_source", "unknown"),
    }


results = []
llm_hits = 0
for _, row in df.iterrows():
    job = {
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "company": row.get("company", ""),
        "url": row.get("job_url", "") or row.get("url", ""),
        "location": row.get("location", ""),
        "work_mode": row.get("work_mode", ""),
        "is_remote": row.get("is_remote"),
        "travel_minutes": row.get("travel_minutes"),
        "salary_min": row.get("salary_min") or row.get("min_amount"),
        "salary_max": row.get("salary_max") or row.get("max_amount"),
        "km_visa_sponsor": row.get("km_visa_sponsor"),
    }

    # Reuse cached skill extraction if available; otherwise pass empty list (no LLM).
    key = cache_key_for(job["url"], job["description"])
    cached = skills_cache.get(key)
    if cached and isinstance(cached.get("skills"), list):
        jd_skills = cached["skills"]
        llm_hits += 1
    else:
        jd_skills = []

    rc = _row_classified_role(row)
    sc = _row_classified_seniority(row)
    ic = _row_classified_industry(row)

    rf = score_role_fit(job, rc)
    hs = score_hard_skills(job, jd_skills=jd_skills, use_cache=False)
    sn = score_seniority_fit(job, sc)
    ip = score_industry_proximity(job, ic)
    de = score_desirability(job)
    ev = score_evidence(job, role_family=rc["family"], industry=ic["industry"])

    agg = aggregate_score(rf, hs, sn, ip, de, ev,
                          role_classified=rc, seniority_classified=sc,
                          industry_classified=ic)

    results.append({
        "title": str(job["title"])[:70],
        "company": str(job["company"])[:30],
        "role_family": rc["family"],
        "industry": ic["industry"],
        "seniority_level": sc["seniority_level"],
        "role_fit": rf["score"],
        "hard_skill": hs["score"],
        "seniority_fit": sn["score"],
        "industry_proximity": ip["score"],
        "desirability": de["score"],
        "evidence": ev["score"],
        "final_score": agg["final_score"],
        "recommendation": agg["recommendation"],
        "review_flags": agg["review_flags"],
        "blockers": agg["blockers"],
        "top_reasons": agg["top_reasons"],
        "top_risks": agg["top_risks"],
        "had_llm_extraction": cached is not None,
    })

sub_df = pd.DataFrame(results)
print(f"\nScored {len(sub_df)} jobs. LLM-extracted skills available for {llm_hits}.")

# Tier distribution
rec_counts = sub_df["recommendation"].value_counts().to_dict()
print(f"\nRecommendation distribution:")
for rec in ("Apply", "Review", "Skip"):
    n = rec_counts.get(rec, 0)
    pct = 100 * n / len(sub_df) if len(sub_df) else 0
    print(f"  {rec:8s}  {n:4d}  ({pct:.1f}%)")

# Score stats
print(f"\nFinal score stats:")
print(f"  mean={sub_df['final_score'].mean():.1f}  median={sub_df['final_score'].median():.0f}  "
      f"stdev={sub_df['final_score'].std():.1f}  min={sub_df['final_score'].min()}  "
      f"max={sub_df['final_score'].max()}")

# Apply tier examples (highest + lowest within tier)
apply_df = sub_df[sub_df["recommendation"] == "Apply"].sort_values("final_score", ascending=False)
if len(apply_df):
    print(f"\nApply tier — top 5:")
    for _, r in apply_df.head(5).iterrows():
        llm = " [LLM]" if r["had_llm_extraction"] else ""
        print(f"  [{r['final_score']}/100] {r['title'][:50]} @ {r['company'][:24]}{llm}")

# Review tier
review_df = sub_df[sub_df["recommendation"] == "Review"].sort_values("final_score", ascending=False)
if len(review_df):
    print(f"\nReview tier — top 5 (why demoted):")
    for _, r in review_df.head(5).iterrows():
        flags = "; ".join(r["review_flags"][:2])[:80]
        print(f"  [{r['final_score']}/100] {r['title'][:45]} @ {r['company'][:20]}")
        print(f"      flags: {flags}")

# Skip tier (blocked)
blocked = sub_df[sub_df["blockers"].apply(lambda x: len(x) > 0)]
if len(blocked):
    print(f"\n{len(blocked)} jobs Skipped by hard blocker:")
    for _, r in blocked.head(5).iterrows():
        print(f"  {r['title'][:50]} @ {r['company'][:24]}  ← {r['blockers'][0][:60]}")

# Save
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({
        "n_jobs": len(sub_df),
        "llm_extracted_count": llm_hits,
        "recommendation_distribution": rec_counts,
        "score_stats": {
            "mean": round(float(sub_df["final_score"].mean()), 2),
            "median": float(sub_df["final_score"].median()),
            "stdev": round(float(sub_df["final_score"].std()), 2),
            "min": int(sub_df["final_score"].min()),
            "max": int(sub_df["final_score"].max()),
        },
        "apply_samples_top20": apply_df.head(20).to_dict(orient="records"),
        "review_samples_top20": review_df.head(20).to_dict(orient="records"),
        "blocked_samples_top20": blocked.head(20).to_dict(orient="records"),
    }, f, indent=2, ensure_ascii=False, default=str)

print(f"\nArtifact -> {OUT}")
