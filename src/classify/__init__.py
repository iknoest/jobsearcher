"""Classification stage (PRD §8 Stage 4 / Jobsearcher Stage 3b).

Rule-based classifiers that sit between hard filters (Stage 2/2.5/3) and
travel / prerank / LLM scoring (Stage 4+). Each classifier consumes a YAML
under `config/personal_fit/` and returns a structured classification without
LLM calls.
"""

from src.classify.role_family import classify_role_family
from src.classify.industry import classify_industry
from src.classify.seniority import classify_seniority

__all__ = ["classify_role_family", "classify_industry", "classify_seniority"]
