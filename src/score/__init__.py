"""Sub-score engine (PRD §15).

Each sub-scorer returns a bounded integer slice of the final MatchScore.
Weights per PRD §15:
    role_fit           25   (R3-01)
    hard_skill         30   (R3-02)
    seniority_fit      15   (R3-03)
    industry_proximity 10   (R3-03)
    desirability       10   (R3-03)
    evidence           10   (R3-03)

Only hard_skill uses LLM (for JD skill extraction). All others are rule-based.
"""

from src.score.role_fit import score_role_fit
from src.score.hard_skill import score_hard_skills
from src.score.seniority_fit import score_seniority_fit
from src.score.industry_proximity import score_industry_proximity
from src.score.desirability import score_desirability
from src.score.evidence import score_evidence
from src.score.aggregate import aggregate_score

__all__ = [
    "score_role_fit",
    "score_hard_skills",
    "score_seniority_fit",
    "score_industry_proximity",
    "score_desirability",
    "score_evidence",
    "aggregate_score",
]
