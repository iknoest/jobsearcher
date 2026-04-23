"""Dry-run R3-02 hard_skill scorer on a small sample of cached jobs.

Picks the top-10 by R3-01 role_fit score (to prioritize high-signal JDs),
runs a single LLM extraction per JD via the Gemini→Groq chain, caches
the result to output/jd_skills_cache.json, then scores against
skills.yaml. Cached results are reused on re-run so this is free on
subsequent invocations.
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
from src.score.hard_skill import score_hard_skills  # noqa: E402

CACHE = ROOT / "output" / "enriched_jobs.pkl"
OUT = ROOT / "output" / "hard_skill_dryrun.json"
SKILLS_CACHE = ROOT / "output" / "jd_skills_cache.json"

N_JOBS = 10

if not CACHE.exists():
    print(f"No cache at {CACHE}")
    sys.exit(1)

df = pd.read_pickle(CACHE)
print(f"Loaded {len(df)} jobs.")

# Stage 3b filter
df = clf_role(df)
df = df[df["role_hard_reject"] != True].reset_index(drop=True)  # noqa: E712
df = clf_industry(df)
df = clf_seniority(df)
df = df[df["seniority_hard_reject"] != True].reset_index(drop=True)  # noqa: E712
print(f"After Stage 3b: {len(df)} jobs.")

# Rank by R3-01 role_fit
def _role_fit(row):
    classified = {
        "family": row.get("role_family", "unknown"),
        "bucket": row.get("role_bucket", "unknown"),
        "confidence": float(row.get("role_confidence", 0.0) or 0.0),
        "display_name": row.get("role_family", "Unknown"),
        "evidence_spans": {"title_match": [], "confirming_jd": [], "rescuing_jd": [], "negative_jd": []},
        "would_hard_reject": bool(row.get("role_hard_reject", False)),
    }
    return score_role_fit(
        {"title": row.get("title", ""), "description": row.get("description", "")},
        classified,
    )["score"]

df["role_fit_score"] = df.apply(_role_fit, axis=1)
top = df.nlargest(N_JOBS, "role_fit_score").reset_index(drop=True)
print(f"Picked top {len(top)} by role_fit score for LLM extraction.")

# Score each (uses cache if available)
results = []
for i, (_, row) in enumerate(top.iterrows(), 1):
    job = {
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "url": row.get("job_url", "") or row.get("url", ""),
    }
    print(f"\n[{i}/{len(top)}] {str(job['title'])[:60]} @ {str(row.get('company', ''))[:30]}")
    try:
        r = score_hard_skills(job, cache_path=SKILLS_CACHE)
    except Exception as e:
        print(f"  EXTRACTION FAILED: {e}")
        results.append({
            "title": str(job["title"])[:80],
            "company": str(row.get("company", ""))[:40],
            "error": str(e)[:200],
        })
        continue

    s = r["signals"]
    print(f"  source={s['extraction_source']}  skills_extracted={s['jd_skills_count']}")
    print(f"  verified={len(s['verified_matches'])}  adjacent={len(s['adjacent_matches'])}  "
          f"forbidden_core={len(s['forbidden_core_hits'])}  "
          f"forbidden_optional={len(s['forbidden_optional_hits'])}  "
          f"negative={len(s['negative_hits'])}")
    print(f"  base={s['base_score']}  final={r['score']}/30  "
          f"{'REJECTED' if s['rejected_by_forbidden_core'] else ''}")
    if s["verified_matches"]:
        names = ", ".join(h["canonical_name"] for h in s["verified_matches"][:4])
        print(f"  verified hits: {names}")
    if s["forbidden_core_hits"]:
        names = ", ".join(h["canonical_name"] for h in s["forbidden_core_hits"])
        print(f"  FORBIDDEN CORE: {names}")

    results.append({
        "title": str(job["title"])[:80],
        "company": str(row.get("company", ""))[:40],
        "role_fit_score": int(row.get("role_fit_score", 0)),
        "hard_skill_score": r["score"],
        "extraction_source": s["extraction_source"],
        "jd_skills_count": s["jd_skills_count"],
        "verified_count": len(s["verified_matches"]),
        "adjacent_count": len(s["adjacent_matches"]),
        "forbidden_core_count": len(s["forbidden_core_hits"]),
        "forbidden_optional_count": len(s["forbidden_optional_hits"]),
        "negative_count": len(s["negative_hits"]),
        "base_score": s["base_score"],
        "rejected_by_forbidden_core": s["rejected_by_forbidden_core"],
        "verified_names": [h["canonical_name"] for h in s["verified_matches"]],
        "forbidden_core_names": [h["canonical_name"] for h in s["forbidden_core_hits"]],
        "forbidden_optional_names": [h["canonical_name"] for h in s["forbidden_optional_hits"]],
        "negative_names": [h["canonical_name"] for h in s["negative_hits"]],
        "reasoning": r["reasoning"],
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"n_jobs": len(results), "results": results}, f, indent=2, ensure_ascii=False, default=str)

print(f"\nArtifact -> {OUT}")
print(f"Skill cache -> {SKILLS_CACHE}  (reused on re-run)")
