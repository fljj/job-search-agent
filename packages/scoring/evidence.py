import hashlib
import json
from collections.abc import Iterable

from packages.scoring.models import ScoringContext, ScoringEvidenceItem


def with_evidence_catalog(context: ScoringContext) -> ScoringContext:
    """为评分输入生成与列表顺序无关的条目级证据目录。"""
    items: dict[str, ScoringEvidenceItem] = {}

    def add(path: str, value: object, dimensions: Iterable[str]) -> None:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        normalized_value = json.loads(serialized)
        evidence_id = "evidence:" + hashlib.sha256(
            f"{path}:{serialized}".encode()
        ).hexdigest()
        requested_dimensions = sorted(set(dimensions))
        existing = items.get(evidence_id)
        if existing:
            existing.dimensions = sorted(
                set(existing.dimensions) | set(requested_dimensions)
            )
            return
        items[evidence_id] = ScoringEvidenceItem(
            id=evidence_id,
            source_path=path,
            value=normalized_value,
            dimensions=requested_dimensions,
        )

    def add_list(path: str, values: list[object], dimensions: Iterable[str]) -> None:
        if not values:
            add(path, [], dimensions)
            return
        for value in values:
            add(path, value, dimensions)

    add("job.title", context.job.title, ["title"])
    add_list(
        "strategy.title_rules",
        [item.model_dump(mode="json") for item in context.strategy.title_rules],
        ["title"],
    )
    add_list(
        "parsed_job.required_skills",
        list(context.parsed_job.required_skills),
        ["skills"],
    )
    add_list(
        "parsed_job.preferred_skills",
        list(context.parsed_job.preferred_skills),
        ["skills"],
    )
    add_list(
        "candidate.skills",
        [item.model_dump(mode="json") for item in context.candidate.skills],
        ["skills"],
    )
    add_list(
        "strategy.core_required_skills",
        list(context.strategy.core_required_skills),
        ["skills"],
    )
    add("parsed_job.years_required", context.parsed_job.years_required, ["experience"])
    add("candidate.total_years", context.candidate.total_years, ["experience"])
    add(
        "candidate.bachelor_full_time",
        context.candidate.bachelor_full_time,
        ["experience"],
    )
    add(
        "parsed_job.full_time_bachelor_required",
        context.parsed_job.full_time_bachelor_required,
        ["experience"],
    )
    add(
        "candidate.has_core_system_experience",
        context.candidate.has_core_system_experience,
        ["experience"],
    )
    add_list(
        "candidate.industry_experiences",
        list(context.candidate.industry_experiences),
        ["experience", "industry"],
    )
    add("job.work_mode", context.job.work_mode, ["location"])
    add("job.location", context.job.location, ["location"])
    add_list(
        "strategy.work_mode_rules",
        [item.model_dump(mode="json") for item in context.strategy.work_mode_rules],
        ["location"],
    )
    add("job.salary_text", context.job.salary_text, ["salary"])
    add(
        "parsed_job.salary",
        (
            context.parsed_job.salary.model_dump(mode="json")
            if context.parsed_job.salary
            else None
        ),
        ["salary"],
    )
    add_list(
        "strategy.salary_rules",
        [item.model_dump(mode="json") for item in context.strategy.salary_rules],
        ["salary"],
    )
    add("job.industry", context.job.industry, ["industry"])
    add_list(
        "strategy.industry_rules",
        [item.model_dump(mode="json") for item in context.strategy.industry_rules],
        ["industry"],
    )
    add(
        "parsed_job.management_required",
        context.parsed_job.management_required,
        ["management"],
    )
    add(
        "parsed_job.seniority_level",
        context.parsed_job.seniority_level,
        ["management"],
    )
    add(
        "candidate.management_years",
        context.candidate.management_years,
        ["management"],
    )
    add(
        "candidate.has_architecture_experience",
        context.candidate.has_architecture_experience,
        ["management"],
    )
    return context.model_copy(
        update={"evidence_items": sorted(items.values(), key=lambda item: item.id)}
    )


def evidence_catalog(context: ScoringContext) -> dict[str, ScoringEvidenceItem]:
    """校验证据目录确实由当前快照生成，并返回按 ID 索引的目录。"""
    expected = with_evidence_catalog(
        context.model_copy(update={"evidence_items": []})
    ).evidence_items
    if context.evidence_items != expected:
        raise ValueError("评分证据目录与当前输入快照不一致")
    return {item.id: item for item in expected}


def scoring_context_from_snapshot(snapshot: dict[str, object]) -> ScoringContext:
    """从历史输入快照恢复并校验证据目录，供审计和复现使用。"""
    context = ScoringContext.model_validate(snapshot)
    evidence_catalog(context)
    return context
