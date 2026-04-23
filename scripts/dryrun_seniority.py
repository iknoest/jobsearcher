"""Dry-run R2-03 seniority classifier on cached enriched jobs.

Feeds the cache through the FULL Stage 3b (role + industry + seniority)
so numbers mirror the real pipeline. Writes an artifact with:
  - counts by seniority_level
  - role hard-rejects / seniority hard-rejects
  - mismatch-flag distribution
  - samples per level
"""

import json
import os
import sys
from collections import Counter
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

CACHE = ROOT / "output" / "enriched_jobs.pkl"
OUT = ROOT / "output" / "seniority_dryrun.json"

if not CACHE.exists():
    print(f"No cache at {CACHE}")
    sys.exit(1)

df = pd.read_pickle(CACHE)
print(f"Loaded {len(df)} jobs from cache.")

# Stage 3b order: role -> drop role-rejected -> industry -> seniority -> drop seniority-rejected
df = clf_role(df)
before_role = len(df)
role_drops = int((df["role_hard_reject"] == True).sum())  # noqa: E712
df = df[df["role_hard_reject"] != True].reset_index(drop=True)  # noqa: E712

df = clf_industry(df)
df = clf_seniority(df)

before_sen = len(df)
sen_drops = int((df["seniority_hard_reject"] == True).sum())  # noqa: E712

level_counts = Counter(df["seniority_level"].tolist())
mismatch_counts = int((df["seniority_mismatch_flag"] == True).sum())  # noqa: E712
years_counts = df["seniority_years_required"].value_counts(dropna=False).to_dict()

# Samples per level (up to 5 each)
samples = {}
for level in level_counts:
    sub = df[df["seniority_level"] == level].head(5)
    samples[level] = [
        {
            "title": str(r.get("title", "")),
            "company": str(r.get("company", "")),
            "years_required": (
                int(r.get("seniority_years_required"))
                if pd.notna(r.get("seniority_years_required"))
                else None
            ),
            "mismatch_flag": bool(r.get("seniority_mismatch_flag", False)),
            "reasoning": str(r.get("seniority_reasoning", "")),
        }
        for _, r in sub.iterrows()
    ]

# Seniority hard-reject examples
sen_reject_samples = []
for _, r in df[df["seniority_hard_reject"] == True].head(10).iterrows():  # noqa: E712
    sen_reject_samples.append(
        {
            "title": str(r.get("title", "")),
            "company": str(r.get("company", "")),
            "reason": str(r.get("seniority_reasoning", "")),
        }
    )

result = {
    "total_loaded": int(before_role),
    "role_hard_rejects": role_drops,
    "industry_and_seniority_surface": int(before_sen),
    "seniority_hard_rejects": sen_drops,
    "final_kept_by_stage_3b": int(before_sen - sen_drops),
    "mismatch_flag_count": mismatch_counts,
    "level_distribution": dict(level_counts.most_common()),
    "years_required_distribution_top10": dict(
        sorted(years_counts.items(), key=lambda kv: -(kv[1] or 0))[:10]
    ),
    "seniority_hard_reject_samples": sen_reject_samples,
    "samples_per_level": samples,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False, default=str)

print(f"\nStage 3b pipeline (cached):")
print(f"  Loaded:                  {before_role}")
print(f"  Role hard-rejects:       {role_drops}")
print(f"  Enter seniority stage:   {before_sen}")
print(f"  Seniority hard-rejects:  {sen_drops}")
print(f"  Final kept:              {before_sen - sen_drops}")
print(f"  Mismatch-flag count:     {mismatch_counts}")
print(f"\nLevel distribution:")
for lvl, n in level_counts.most_common():
    pct = 100 * n / before_sen if before_sen else 0
    print(f"  {lvl:12s}  {n:4d}  ({pct:.1f}%)")

print(f"\nArtifact -> {OUT}")
