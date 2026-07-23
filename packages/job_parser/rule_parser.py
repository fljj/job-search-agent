import re
from decimal import Decimal

from packages.job_parser.models import JobInput, ParsedJob, RuleParserConfig, SeniorityLevel
from packages.job_parser.normalizers import SKILL_ALIASES, normalize_skill, parse_salary


class RuleJobParser:
    """使用确定性词典和正则解析第一阶段模拟 JD。"""

    version = "1.0.0"

    def __init__(self, config: RuleParserConfig) -> None:
        self.config = config

    def parse(self, job: JobInput) -> ParsedJob:
        text = f"{job.title}\n{job.company_name}\n{job.description}"
        skills = self._extract_skills(text)
        preferred_section = self._preferred_skills(text, skills)
        required = [skill for skill in skills if skill not in preferred_section]
        years_match = re.search(r"(\d+(?:\.\d+)?)\s*年(?:以上)?", text)
        warnings: list[str] = []
        salary = parse_salary(job.salary_text)
        if job.salary_text and salary is None:
            warnings.append("薪资无法规范化")
        if salary and salary.inferred_months:
            warnings.append("薪数未注明，按 12 薪估算")
        return ParsedJob(
            required_skills=required,
            preferred_skills=preferred_section,
            years_required=Decimal(years_match.group(1)) if years_match else None,
            management_required=any(word in text for word in ("团队管理", "带领团队", "团队负责人")),
            architecture_required=any(word in text for word in ("架构设计", "系统架构", "架构师")),
            seniority_level=self._seniority(job.title),
            responsibilities=[line.strip(" -•") for line in job.description.splitlines() if line.strip()][:10],
            salary=salary,
            outsourcing_detected=any(
                word in text for word in self.config.outsourcing_keywords
            ),
            headhunter_detected=any(
                word in text for word in self.config.headhunter_keywords
            ),
            internship_detected=any(
                word in text for word in self.config.internship_keywords
            ),
            confidence=Decimal("0.85"),
            warnings=warnings,
            parser_type="RULE",
            parser_version=self.version,
        )

    @staticmethod
    def _extract_skills(text: str) -> list[str]:
        lowered = text.lower()
        found = {normalize_skill(alias) for alias in SKILL_ALIASES if alias.lower() in lowered}
        return sorted(found)

    @staticmethod
    def _preferred_skills(text: str, skills: list[str]) -> list[str]:
        lowered = text.lower()
        preferred: list[str] = []
        for skill in skills:
            index = lowered.find(skill.lower())
            if index < 0:
                continue
            nearby = lowered[index : index + len(skill) + 12]
            if any(word in nearby for word in ("优先", "加分", "更佳")):
                preferred.append(skill)
        return preferred

    @staticmethod
    def _seniority(title: str) -> SeniorityLevel:
        if "实习" in title:
            return SeniorityLevel.INTERN
        if any(word in title for word in ("初级", "助理")):
            return SeniorityLevel.JUNIOR
        if "架构" in title:
            return SeniorityLevel.ARCHITECT
        if "经理" in title:
            return SeniorityLevel.MANAGER
        if any(word in title for word in ("负责人", "主管", "Lead")):
            return SeniorityLevel.LEAD
        if any(word in title for word in ("高级", "资深", "专家")):
            return SeniorityLevel.SENIOR
        return SeniorityLevel.MIDDLE
