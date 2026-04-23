"""Dry-run R3-01 role_fit scorer on cached enriched jobs.

Feeds cached jobs through the FULL Stage 3b gate (role + industry +
seniority) first so the scorer sees only what the real pipeline
would pass to it. Then scores survivors and reports distribution
+ top/bottom samples.
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
from src.score.role_fit import score_role_fit  # noqa: E402

CACHE = ROOT / "output" / "enriched_jobs.pkl"
OUT = ROOT / "output" / "role_fit_dryrun.json"

if not CACHE.exists():
    print(f"No cache at {CACHE}")
    sys.exit(1)

df = pd.read_pickle(CACHE)
print(f"Loaded {len(df)} jobs from cache.")

# Stage 3b pipeline
df = clf_role(df)
df = df[df["role_hard_reject"] != True].reset_index(drop=True)  # noqa: E712
df = clf_industry(df)
df = clf_seniority(df)
df = df[df["seniority_hard_reject"] != True].reset_index(drop=True)  # noqa: E712

print(f"After Stage 3b: {len(df)} jobs to score.")


def _run(row):
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
    )


results = df.apply(_run, axis=1)
df = df.copy()
df["role_fit_score"] = results.map(lambda r: r["score"])
df["role_fit_hits"] = results.map(lambda r: r["signals"]["deliverable_hits"])
df["role_fit_anti"] = results.map(lambda r: r["signals"]["anti_pattern_hits"])
df["role_fit_reasoning"] = results.map(lambda r: r["reasoning"])

# Distribution
bins = [(0, 0), (1, 5), (6, 10), (11, 14), (15, 18), (19, 22), (23, 25)]
band_labels = ["0 (reject)", "1-5", "6-10 (rescued)", "11-14 (ambig)",
               "15-18 (desired low)", "19-22 (desired strong)", "23-25"]
band_counts = {label: 0 for label in band_labels}
for s in df["role_fit_score"]:
    for (lo, hi), label in zip(bins, band_labels):
        if lo <= s <= hi:
            band_counts[label] += 1
            break

# Score stats
mean = float(df["role_fit_score"].mean())
median = float(df["role_fit_score"].median())
stdev = float(df["role_fit_score"].std())

# Top 10 and bottom 10 samples
top = df.nlargest(10, "role_fit_score")[
    ["title", "company", "role_bucket", "role_fit_score", "role_fit_hits", "role_fit_anti"]
]
bot = df.nsmallest(10, "role_fit_score")[
    ["title", "company", "role_bucket", "role_fit_score", "role_fit_hits", "role_fit_anti"]
]

# Deliverable hit frequencies
all_hits = Counter()
for hits in df["role_fit_hits"]:
    for h in hits:
        all_hits[h.lower()] += 1

all_anti = Counter()
for hits in df["role_fit_anti"]:
    for h in hits:
        all_anti[h.lower()] += 1

result = {
    "total_scored": int(len(df)),
    "stats": {"mean": round(mean, 2), "median": median, "stdev": round(stdev, 2)},
    "band_distribution": band_counts,
    "deliverable_hit_top10": dict(all_hits.most_common(10)),
    "anti_pattern_hit_top10": dict(all_anti.most_common(10)),
    "top10_samples": [
        {
            "title": str(r["title"])[:80],
            "company": str(r["company"])[:40],
            "bucket": r["role_bucket"],
            "score": int(r["role_fit_score"]),
            "deliverable_hits": list(r["role_fit_hits"]),
            "anti_pattern_hits": list(r["role_fit_anti"]),
        }
        for _, r in top.iterrows()
    ],
    "bottom10_samples": [
        {
            "title": str(r["title"])[:80],
            "company": str(r["company"])[:40],
            "bucket": r["role_bucket"],
            "score": int(r["role_fit_score"]),
        }
        for _, r in bot.iterrows()
    ],
}

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False, default=str)

print(f"\nMean: {mean:.2f}  Median: {median}  Stdev: {stdev:.2f}")
print("\nBand distribution:")
for label in band_labels:
    n = band_counts[label]
    pct = 100 * n / len(df) if len(df) else 0
    print(f"  {label:25s}  {n:4d}  ({pct:.1f}%)")

print("\nTop 5 samples:")
for _, r in top.head(5).iterrows():
    print(f"  [{int(r['role_fit_score'])}/25 {r['role_bucket']:9s}] "
          f"{str(r['title'])[:55]} @ {str(r['company'])[:28]}")
    if list(r["role_fit_hits"]):
        print(f"      hits: {', '.join(list(r['role_fit_hits'])[:6])}")

print("\nArtifact -> " + str(OUT))
