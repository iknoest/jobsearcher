"""R3-02b harvester: read jd_skills_cache.json, find phrases that
matched no canonical in skills.yaml, cluster them so we can propose
targeted synonym additions.

Does NOT modify skills.yaml. Output is a JSON report for human review.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKILLS_YAML = ROOT / "config" / "personal_fit" / "skills.yaml"
CACHE = ROOT / "output" / "jd_skills_cache.json"
OUT = ROOT / "output" / "missed_skills_harvest.json"


def _word_boundary_re(phrases):
    if not phrases:
        return None
    return re.compile(r"(?i)\b(" + "|".join(re.escape(p) for p in phrases if p) + r")\b")


def _build_entry(e):
    phrases = [e["canonical_name"]] + list(e.get("synonyms") or [])
    return {
        "canonical_name": e["canonical_name"],
        "strength": e.get("evidence_strength"),
        "regex": _word_boundary_re(phrases),
    }


cfg = yaml.safe_load(open(SKILLS_YAML, encoding="utf-8"))
buckets = {
    "verified": [_build_entry(e) for e in (cfg.get("verified") or [])],
    "adjacent": [_build_entry(e) for e in (cfg.get("adjacent") or [])],
    "forbidden": [_build_entry(e) for e in (cfg.get("forbidden") or [])],
    "negative": [_build_entry(e) for e in (cfg.get("negative_signal") or [])],
}

if not CACHE.exists():
    print(f"No cache at {CACHE}")
    sys.exit(1)

cache = json.load(open(CACHE, encoding="utf-8"))
print(f"Loaded {len(cache)} cached JD extractions.")

# Collect all phrases with frequency + importance + job context
phrase_freq = Counter()
phrase_jobs = defaultdict(list)
for key, val in cache.items():
    title = val.get("title", "")
    for s in val.get("skills", []) or []:
        name = s.get("name", "").strip()
        if not name:
            continue
        phrase_freq[name.lower()] += 1
        phrase_jobs[name.lower()].append({"title": title, "importance": s.get("importance")})

print(f"Unique phrases across all jobs: {len(phrase_freq)}")

# For each unique phrase, check which bucket (if any) it matches
matched = []  # (phrase, bucket, canonical_name)
unmatched = []

for phrase, freq in phrase_freq.most_common():
    best = None
    for bucket_name, entries in buckets.items():
        for e in entries:
            if e["regex"] and e["regex"].search(phrase):
                best = (bucket_name, e["canonical_name"])
                break
        if best:
            break
    if best:
        matched.append((phrase, freq, best[0], best[1]))
    else:
        unmatched.append((phrase, freq))

# Group unmatched by plausible canonical target (first-pass clustering by shared keyword)
# These clusters are HEURISTIC — human review required. We use simple keyword groupings.
CLUSTER_HINTS = {
    "Voice of Customer (VOC)": ["voice of customer", "voc", "customer insight", "customer feedback"],
    "User research (general)": ["user research", "ux research", "research study", "user insight"],
    "Qualitative research": ["qualitative"],
    "Quantitative research": ["quantitative", "survey"],
    "Usability & user journey mapping": ["usability", "journey", "user flow"],
    "Product strategy input": ["product strategy", "strategy input", "strategic"],
    "Feature requirements definition": ["requirements", "prd", "feature definition", "specification"],
    "Backlog prioritization (Jira / Agile)": ["backlog", "prioritization", "agile", "scrum", "jira", "kanban", "sprint"],
    "MVP / prototype development": ["mvp", "prototype", "proof of concept", "poc"],
    "Stakeholder management": ["stakeholder"],
    "Data analysis (Python / SQL)": ["python", "sql", "data analysis", "excel"],
    "Business intelligence (Power BI)": ["power bi", "tableau", "looker", "business intelligence"],
    "Data visualization": ["data visualization", "dashboard", "chart"],
    "3D CAD (SolidWorks / Creo)": ["cad", "solidworks", "creo", "3d design", "3d model"],
    "Sensor integration (ToF / wireless)": ["sensor", "tof", "wireless", "bluetooth", "bluetooth low energy"],
    "Prototyping (hardware + software)": ["prototyp", "hardware prototype", "rapid prototyp"],
    "Mechanical engineering fundamentals": ["mechanical", "injection mold", "stamping", "assembly", "cad/cam"],
    "Research synthesis": ["synthesis", "insight synthesis", "research synthesis"],
    "Automotive domain (EV / PHEV)": ["automotive", "ev", "phev", "electric vehicle", "powertrain"],
    "Consumer electronics / wearables domain": ["consumer electronics", "wearable", "iot device"],
    "Product vision": ["product vision", "vision"],
    "Design thinking": ["design thinking", "human-centered design", "design sprint"],
    "Experimentation / A/B testing": ["a/b test", "experimentation", "ab test"],
    "Market & competitive intelligence": ["market research", "competitive", "competitor", "market intelligence"],
    "Mechanical simulation / FEA / CFD depth": ["fem", "fea", "finite element", "cfd", "thermal simulation", "stress analysis"],
    "PCB design (as primary role)": ["pcb"],
    "Low-level firmware / RTOS engineering": ["firmware", "rtos", "embedded c"],
    "Full-stack / frontend framework depth": ["react", "vue", "angular", "frontend framework"],
    "Backend software engineering depth": ["backend", "microservice", "django", "rails", "spring boot"],
    "DevOps / infrastructure engineering": ["kubernetes", "k8s", "terraform", "docker swarm", "ansible", "ci/cd pipeline"],
    "Domain-locked science engineering": ["chemical process", "nuclear", "petroleum"],
    "German language requirement": ["german", "deutsch"],
    "Dutch language requirement (nice-to-have)": ["dutch preferred", "nederlands preferred"],
}


def _cluster_phrase(phrase):
    """Word-boundary match only — prevents 'ev' matching 'reviews'/'beverage'."""
    p = phrase.lower()
    matches = []
    for canonical, keywords in CLUSTER_HINTS.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", p):
                matches.append((canonical, kw))
                break
    return matches


clustered = defaultdict(list)  # canonical → [(phrase, freq, matched_keyword)]
truly_unmatched = []  # phrases that didn't cluster to anything

for phrase, freq in unmatched:
    hits = _cluster_phrase(phrase)
    if not hits:
        truly_unmatched.append((phrase, freq))
    else:
        for canonical, kw in hits:
            clustered[canonical].append({"phrase": phrase, "freq": freq, "via_keyword": kw})

report = {
    "n_jobs_in_cache": len(cache),
    "unique_phrases": len(phrase_freq),
    "matched_current_yaml": len(matched),
    "unmatched_total": len(unmatched),
    "unmatched_clustered_into_canonicals": sum(len(v) for v in clustered.values()),
    "truly_unmatched": len(truly_unmatched),
    "already_matched_samples": [
        {"phrase": p, "freq": f, "bucket": b, "canonical": c}
        for p, f, b, c in matched[:15]
    ],
    "proposed_synonym_additions_by_canonical": {
        canonical: sorted(entries, key=lambda x: -x["freq"])
        for canonical, entries in sorted(
            clustered.items(), key=lambda kv: -sum(e["freq"] for e in kv[1])
        )
    },
    "truly_unmatched_top30": [
        {"phrase": p, "freq": f} for p, f in sorted(truly_unmatched, key=lambda x: -x[1])[:30]
    ],
}

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False, default=str)

print(f"\nSummary:")
print(f"  Matched to existing YAML:    {len(matched)}")
print(f"  Unmatched but clustered:     {sum(len(v) for v in clustered.values())}")
print(f"  Truly unmatched (no hint):   {len(truly_unmatched)}")
print(f"\nTop canonicals with proposed additions (by total frequency):")
for canonical, entries in sorted(
    clustered.items(), key=lambda kv: -sum(e["freq"] for e in kv[1])
)[:15]:
    total = sum(e["freq"] for e in entries)
    top_phrases = ", ".join(e["phrase"] for e in sorted(entries, key=lambda x: -x["freq"])[:3])
    print(f"  [{total:3d} hits] {canonical}")
    print(f"      phrases: {top_phrases}")

print(f"\nTruly unmatched top 10 (no cluster — either genuine new canonical needed or noise):")
for p, f in sorted(truly_unmatched, key=lambda x: -x[1])[:10]:
    print(f"  [{f}x]  {p}")

print(f"\nFull report -> {OUT}")
