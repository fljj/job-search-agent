import re
import unicodedata
from decimal import Decimal

from packages.job_parser.models import SalaryRange

SKILL_ALIASES: dict[str, str] = {
    "java": "Java",
    "springboot": "Spring Boot",
    "spring boot": "Spring Boot",
    "springcloud": "Spring Cloud",
    "spring cloud": "Spring Cloud",
    "mysql": "MySQL",
    "redis": "Redis",
    "kafka": "Kafka",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "微服务": "微服务",
    "rest api": "REST API",
    "ci/cd": "CI/CD",
    "高并发": "高并发系统",
}


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"\s+", " ", normalized)


def normalize_location(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_text(value)
    return normalized.removesuffix("市")


def normalize_company(value: str) -> str:
    normalized = normalize_text(value)
    for suffix in ("有限公司", "有限责任公司", "股份有限公司"):
        normalized = normalized.removesuffix(suffix)
    return normalized.strip()


def normalize_skill(value: str) -> str:
    normalized = normalize_text(value).replace("-", " ")
    return SKILL_ALIASES.get(normalized, value.strip())


def parse_salary(text: str | None) -> SalaryRange | None:
    if not text:
        return None
    normalized = normalize_text(text)
    if "面议" in normalized or "薪资开放" in normalized:
        return SalaryRange(negotiable=True)

    monthly = re.search(
        r"(\d+(?:\.\d+)?)\s*[kK千]?\s*[-~—至]\s*(\d+(?:\.\d+)?)\s*[kK千]",
        text,
    )
    if monthly:
        months_match = re.search(r"(\d{2})\s*薪", text)
        return SalaryRange(
            minimum_monthly_k=Decimal(monthly.group(1)),
            maximum_monthly_k=Decimal(monthly.group(2)),
            salary_months=int(months_match.group(1)) if months_match else 12,
            inferred_months=months_match is None,
            is_pre_tax=False if "税后" in text else True if "税前" in text else None,
        )

    monthly_yuan = re.search(
        r"(\d{4,6}(?:\.\d+)?)\s*[-~—至]\s*(\d{4,6}(?:\.\d+)?)\s*元(?:/月|每月)?",
        text,
    )
    if monthly_yuan:
        return SalaryRange(
            minimum_monthly_k=Decimal(monthly_yuan.group(1)) / Decimal(1000),
            maximum_monthly_k=Decimal(monthly_yuan.group(2)) / Decimal(1000),
            salary_months=12,
            inferred_months=True,
            is_pre_tax=False if "税后" in text else True if "税前" in text else None,
        )

    annual = re.search(r"(\d+(?:\.\d+)?)\s*[-~—至]\s*(\d+(?:\.\d+)?)\s*[wW万]/?年?", text)
    if annual:
        months_match = re.search(r"(\d{2})\s*薪", text)
        months = int(months_match.group(1)) if months_match else 12
        return SalaryRange(
            minimum_monthly_k=Decimal(annual.group(1)) * Decimal(10) / months,
            maximum_monthly_k=Decimal(annual.group(2)) * Decimal(10) / months,
            salary_months=months,
            inferred_months=months_match is None,
            is_pre_tax=False if "税后" in text else True if "税前" in text else None,
        )
    return None
