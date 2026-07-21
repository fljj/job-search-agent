from datetime import datetime

from packages.job_parser.normalizers import normalize_text
from packages.knowledge_base.models import KnowledgeFact, Sensitivity


def retrieve_facts(
    facts: list[KnowledgeFact], query_terms: list[str], now: datetime
) -> list[KnowledgeFact]:
    """仅返回当前有效且与查询词匹配的知识事实。"""
    normalized_terms = [normalize_text(term) for term in query_terms if term]
    matches: list[KnowledgeFact] = []
    for fact in facts:
        haystack = normalize_text(f"{fact.category} {fact.key} {fact.fact}")
        if fact.sensitivity is Sensitivity.PROHIBITED or not fact.is_current(now):
            continue
        if not normalized_terms or any(term in haystack or haystack in term for term in normalized_terms):
            matches.append(fact)
    return matches
