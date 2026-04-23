"""Dry-run R3-03 sub-scorers (seniority_fit + industry_proximity + desirability + evidence)
on Stage-3b survivors.

No LLM calls — all 4 are rule-based. Runs classifiers once, then all 4 scorers,
reports distribution + 10 sample cards.
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

import pandas as pd  # noqa: E402

from src.classify.role_family import classify_dataframe as clf_role  # noqa: E402
from src.classify.industry import classify_dataframe as clf_industry  # noqa: E402
from src.classify.seniority import classify_dataframe as clf_seniority  # noqa: E402
from src.score.seniority_fit import score_seniority_fit  # noqa: E402
from src.score.industry_proximity import score_industry_proximity  # noqa: E402
from src.score.desirability import score_desirability  # noqa: E402
from src.score.evidence import score_evidence  # noqa: E402

CACHE = ROOT / "output" / "enriched_jobs.pkl"
OUT = ROOT / "output" / "subscorers_dryrun.json"

if not CACHE.exists():
    print(f"No cache at {CACHE}")
    sys.exit(1)

df = pd.read_pickle(CACHE)
print(f"Loaded {len(df)} jobs.")

df = clf_role(df)
df = df[df["role_hard_reject"] != True].reset_index(drop=True)  # noqa: E712
df = clf_industry(df)
df = clf_seniority(df)
df = df[df["seniority_hard_reject"] != True].reset_index(drop=True)  # noqa: E712
print(f"After Stage 3b: {len(df)} jobs.\n")


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
        "nace_code": row.get("industry_nace"),
        "proximity_to_ava": row.get("industry_proximity"),
        "source": row.get("industry_source", "unknown"),
    }


results = []
for _, row in df.iterrows():
    job = {
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "company": row.get("company", ""),
        "location": row.get("location", ""),
        "work_mode": row.get("work_mode", ""),
        "is_remote": row.get("is_remote"),
        "travel_minutes": row.get("travel_minutes"),
        "salary_min": row.get("salary_min") or row.get("min_amount"),
        "salary_max": row.get("salary_max") or row.get("max_amount"),
        "km_visa_sponsor": row.get("km_visa_sponsor"),
    }
    sen = score_seniority_fit(job, _row_classified_seniority(row))
    ind = score_industry_proximity(job, _row_classified_industry(row))
    des = score_desirability(job)
    evd = score_evidence(
        job,
        role_family=row.get("role_family"),
        industry=row.get("industry"),
    )
    results.append({
        "title": str(job["title"])[:70],
        "company": str(job["company"])[:30],
        "role_family": row.get("role_family"),
        "industry": row.get("industry"),
        "seniority_level": row.get("seniority_level"),
        "work_mode_detected": des["signals"]["work_mode"],
        "seniority_fit": sen["score"],
        "industry_proximity": ind["score"],
        "desirability": des["score"],
        "evidence": evd["score"],
        "subtotal": sen["score"] + ind["score"] + des["score"] + evd["score"],
    })

sub_df = pd.DataFrame(results)

print(f"Summary stats (n={len(sub_df)}):")
for col in ("seniority_fit", "industry_proximity", "desirability", "evidence", "subtotal"):
    s = sub_df[col]
    print(f"  {col:22s}  mean={s.mean():5.2f}  median={s.median():5.1f}  stdev={s.std():5.2f}  min={s.min()}  max={s.max()}")

print(f"\nTop 10 by subtotal (seniority+industry+desirability+evidence, out of 45):")
top = sub_df.nlargest(10, "subtotal")
for _, r in top.iterrows():
    print(f"  [{r['subtotal']:2d}/45  sen={r['seniority_fit']:2d} ind={r['industry_proximity']:2d} "
          f"des={r['desirability']:2d} evd={r['evidence']:2d}]  "
          f"{r['title'][:50]} @ {r['company'][:25]}")

print(f"\nBottom 5 by subtotal:")
bot = sub_df.nsmallest(5, "subtotal")
for _, r in bot.iterrows():
    print(f"  [{r['subtotal']:2d}/45  sen={r['seniority_fit']:2d} ind={r['industry_proximity']:2d} "
          f"des={r['desirability']:2d} evd={r['evidence']:2d}]  "
          f"{r['title'][:50]} @ {r['company'][:25]}")

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({
        "n_jobs": len(sub_df),
        "stats": {
            col: {
                "mean": round(float(sub_df[col].mean()), 2),
                "median": float(sub_df[col].median()),
                "stdev": round(float(sub_df[col].std()), 2),
                "min": int(sub_df[col].min()),
                "max": int(sub_df[col].max()),
            }
            for col in ("seniority_fit", "industry_proximity", "desirability", "evidence", "subtotal")
        },
        "samples_top10": top.to_dict(orient="records"),
        "samples_bottom5": bot.to_dict(orient="records"),
    }, f, indent=2, ensure_ascii=False, default=str)

print(f"\nArtifact -> {OUT}")
