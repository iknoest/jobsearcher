"""Trust-first email digest (R4-02).

Renders Apply / Review / Skip from R3-05 sub-score output with:
  - Header trust line ("X Apply, Y Review, Z borderline skips worth audit")
  - Bottleneck headline (e.g. "Main bottleneck today: low hard-skill fit")
  - Per-card sub-score bars + deterministic "why Apply" / "watch-outs" /
    "what's holding this back" — NO LLM used for explanations.
  - 3-band funnel (FILTERED / SCORED BUT NOT SURFACED / SCORED & SURFACED)
  - Skip section collapsed with borderline-audit subsection (score 45-49)
  - Pending feedback from the previous digest + feedback theme summary.

Card actions: Open JD / Keep / Reject / Note — mapped onto the existing
Telegram `/start good_<id>` / `/start skip_<id>` / `/start note_<id>`
deep-link infrastructure in src/tg_bot.py.
"""

from src.digest.bottleneck import compute_bottleneck, compute_funnel
from src.digest.explain import explain_row
from src.digest.render import render_trust_digest
from src.digest.state import (
    save_last_digest_snapshot,
    load_last_digest_snapshot,
    compute_pending_feedback,
    summarize_recent_feedback,
)

__all__ = [
    "compute_bottleneck",
    "compute_funnel",
    "explain_row",
    "render_trust_digest",
    "save_last_digest_snapshot",
    "load_last_digest_snapshot",
    "compute_pending_feedback",
    "summarize_recent_feedback",
]
