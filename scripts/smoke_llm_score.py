"""手动真实 LLM 只读评分冒烟；不访问数据库或浏览器，不产生招聘平台写操作。"""

from decimal import Decimal

from apps.api.app.core.config import get_settings
from apps.api.app.core.llm import build_llm_provider
from packages.job_parser.models import JobInput, WorkMode
from packages.scoring.evidence import with_evidence_catalog
from packages.scoring.llm_engine import validate_llm_score
from packages.scoring.models import (
    CandidateProfile,
    CandidateSkill,
    IndustryRule,
    IndustryRuleType,
    RuleType,
    SalaryBand,
    SalaryRule,
    ScoringContext,
    Strategy,
    TitleRule,
    WorkModeRule,
)


def main() -> None:
    provider = build_llm_provider(get_settings())
    job = JobInput(
        title="高级 Java 后端工程师",
        company_name="只读冒烟测试公司",
        industry="互联网",
        location="远程",
        work_mode=WorkMode.REMOTE,
        salary_text="35K-45K",
        description=(
            "负责 Java 和 Spring Boot 服务开发，要求 MySQL、Redis，"
            "有 Kafka 和金融系统经验优先。"
        ),
    )
    parsed_result = provider.parse_job(job)
    context = with_evidence_catalog(
        ScoringContext(
            job=job,
            parsed_job=parsed_result.data,
            candidate=CandidateProfile(
                name="只读测试候选人",
                total_years=Decimal(8),
                has_core_system_experience=True,
                skills=[
                    CandidateSkill(name="Java", years=Decimal(8), source="测试夹具"),
                    CandidateSkill(
                        name="Spring Boot", years=Decimal(6), source="测试夹具"
                    ),
                    CandidateSkill(name="MySQL", years=Decimal(8), source="测试夹具"),
                ],
                industry_experiences=["互联网", "金融科技"],
            ),
            strategy=Strategy(
                name="只读 Java 远程策略",
                title_rules=[
                    TitleRule(
                        rule_type=RuleType.INCLUDE,
                        pattern="Java 后端",
                    )
                ],
                work_mode_rules=[
                    WorkModeRule(work_mode=WorkMode.REMOTE, enabled=True)
                ],
                salary_rules=[
                    SalaryRule(
                        work_mode=WorkMode.REMOTE,
                        minimum_monthly_k=Decimal(25),
                        expected_monthly_k=Decimal(35),
                        bands=[
                            SalaryBand(
                                lower_bound_k=Decimal(0),
                                upper_bound_k=Decimal(35),
                                min_score=Decimal(0),
                                max_score=Decimal(14),
                            ),
                            SalaryBand(
                                lower_bound_k=Decimal(35),
                                min_score=Decimal(15),
                                max_score=Decimal(15),
                            ),
                        ],
                    )
                ],
                industry_rules=[
                    IndustryRule(
                        industry="互联网",
                        rule_type=IndustryRuleType.PREFERRED,
                        score=Decimal(10),
                    )
                ],
                core_required_skills=["Java"],
            ),
        )
    )
    score_result = provider.score_job(context)
    validated = validate_llm_score(context, score_result.data)
    print(  # noqa: T201
        {
            "provider": score_result.metadata.provider,
            "model": score_result.metadata.model,
            "parse_prompt": parsed_result.metadata.prompt_version,
            "score_prompt": score_result.metadata.prompt_version,
            "total_score": validated.total_score,
            "dimensions": {
                item.dimension: {
                    "score": str(item.score),
                    "evidence_refs": item.evidence_refs,
                }
                for item in validated.details
            },
        }
    )


if __name__ == "__main__":
    main()
