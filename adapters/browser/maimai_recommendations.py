import hashlib
import json
import re
import time
from urllib.parse import urlparse
from urllib.request import urlopen

from pydantic import BaseModel

from adapters.browser.playwright_reader import RawCdpPageReader, validate_local_cdp_url
from packages.browser_worker.actions import ExecutionOutcome, ExecutionResult
from packages.policy_engine.recommendation import RecommendationRules


class MaimaiRecommendationCard(BaseModel):
    external_recommendation_id: str
    recruiter_name: str
    recruiter_title: str
    company_name: str
    job_title: str
    location: str | None = None
    salary_text: str | None = None
    description_summary: str | None = None
    card_text: str

    @property
    def card_hash(self) -> str:
        return hashlib.sha256(self.card_text.encode()).hexdigest()


class MaimaiRecommendationAdapter:
    """读取并执行已授权的脉脉系统推荐；不参与业务判断。"""

    def scan(
        self, cdp_url: str, rules: RecommendationRules, limit: int = 20
    ) -> list[MaimaiRecommendationCard]:
        validate_local_cdp_url(cdp_url)
        target = self._target(cdp_url)
        with RawCdpPageReader(target) as page:
            raw_items = page._evaluate(
                """(() => Array.from(document.querySelectorAll('.message-item[data-msg]'))
                .filter(item => item.querySelector('.message-badge'))
                .slice(0, __LIMIT__).map(item => ({
                  dataMsg: item.getAttribute('data-msg') || '',
                  recruiter: (item.querySelector('.message-user-name')?.textContent || '').trim(),
                  title: (item.querySelector('.message-user-title')?.textContent || '').trim(),
                  preview: (item.querySelector('.message-latest-text')?.textContent || '').trim()
                })))()""".replace("__LIMIT__", str(limit))
            )
            cards: list[MaimaiRecommendationCard] = []
            for item in raw_items:
                recruiter = str(item.get("recruiter") or "")
                preview = str(item.get("preview") or "")
                if recruiter in rules.official_accounts:
                    continue
                if not all(marker in preview for marker in rules.recommendation_markers):
                    continue
                external_id = _external_id(str(item.get("dataMsg") or ""))
                detail = self._open_and_read(page, external_id)
                if detail:
                    cards.append(detail)
            return cards

    def execute(
        self,
        cdp_url: str,
        card: MaimaiRecommendationCard,
        *,
        accept: bool,
        rules: RecommendationRules,
    ) -> ExecutionResult:
        validate_local_cdp_url(cdp_url)
        performed = False
        try:
            with RawCdpPageReader(self._target(cdp_url)) as page:
                if not self._select(page, card.external_recommendation_id):
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="APPROVED_TARGET_PAGE_NOT_FOUND",
                    )
                time.sleep(0.3)
                recruiter = page.text(".dialogue-header-username") or ""
                panel = page.text(".dialogue_list_container") or ""
                if recruiter != card.recruiter_name or card.job_title not in panel:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="CONVERSATION_TARGET_MISMATCH",
                    )
                success_markers = (
                    rules.accept_success_markers
                    if accept
                    else rules.reject_success_markers
                )
                if any(marker in panel for marker in success_markers):
                    return _success(card, panel)
                label = "同意" if accept else "拒绝"
                count = page._evaluate(
                    _control_expression(label, click=False)
                )
                if count != 1:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="ACTION_CONTROL_AMBIGUOUS",
                    )
                performed = bool(page._evaluate(_control_expression(label, click=True)))
                if not performed:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="ACTION_CLICK_NOT_PERFORMED",
                    )
                for _ in range(30):
                    panel = page.text(".dialogue_list_container") or ""
                    if any(marker in panel for marker in success_markers):
                        return _success(card, panel)
                    time.sleep(0.1)
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="RESULT_NOT_OBSERVED",
                )
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=(
                    ExecutionOutcome.OUTCOME_UNKNOWN
                    if performed
                    else ExecutionOutcome.FAILED_RETRYABLE
                ),
                error_code="RAW_CDP_ACTION_ERROR",
            )

    def observe(
        self,
        cdp_url: str,
        card: MaimaiRecommendationCard,
        *,
        accept: bool,
        rules: RecommendationRules,
    ) -> ExecutionResult:
        """只读回查推荐结果，不触发任何页面写操作。"""
        validate_local_cdp_url(cdp_url)
        try:
            with RawCdpPageReader(self._target(cdp_url)) as page:
                if not self._select(page, card.external_recommendation_id):
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="APPROVED_TARGET_PAGE_NOT_FOUND",
                    )
                time.sleep(0.3)
                recruiter = page.text(".dialogue-header-username") or ""
                panel = page.text(".dialogue_list_container") or ""
                if recruiter != card.recruiter_name or card.job_title not in panel:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="CONVERSATION_TARGET_MISMATCH",
                    )
                markers = (
                    rules.accept_success_markers
                    if accept
                    else rules.reject_success_markers
                )
                if any(marker in panel for marker in markers):
                    return _success(card, panel)
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="RESULT_CONFIRMED_NOT_SENT",
                )
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                error_code="RAW_CDP_READBACK_ERROR",
            )

    def _open_and_read(
        self, page: RawCdpPageReader, external_id: str
    ) -> MaimaiRecommendationCard | None:
        if not self._select(page, external_id):
            return None
        time.sleep(0.3)
        recruiter = page.text(".dialogue-header-username") or ""
        recruiter_title = page.text(".dialogue-header-career") or ""
        panel = page.text(".dialogue_list_container") or ""
        if not recruiter or "可以要一份你的简历吗" not in panel:
            return None
        job_title = _job_title(panel)
        company = recruiter_title.split("·", 1)[0].strip(" ·")
        salary = _first_match(r"\d+[kK]-\d+[kK](?:/月)?", panel)
        location = _first_match(r"(北京|上海|深圳|广州|杭州|济南|远程)", panel)
        return MaimaiRecommendationCard(
            external_recommendation_id=external_id,
            recruiter_name=recruiter,
            recruiter_title=recruiter_title,
            company_name=company or "未知公司",
            job_title=job_title,
            location=location,
            salary_text=salary,
            description_summary=panel[:1000],
            card_text=panel,
        )

    @staticmethod
    def _select(page: RawCdpPageReader, external_id: str) -> bool:
        return bool(page._evaluate(
            """(() => { const expected = __EXTERNAL_ID__;
            const matches = Array.from(document.querySelectorAll('.message-item[data-msg]'))
              .filter(item => { try { return String(JSON.parse(item.getAttribute('data-msg') || '{}').mid) === expected; }
                                catch { return false; } });
            if (matches.length !== 1 || !matches[0].getClientRects().length) return false;
            matches[0].click(); return true; })()""".replace(
                "__EXTERNAL_ID__", json.dumps(external_id)
            )
        ))

    @staticmethod
    def _target(cdp_url: str) -> str:
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            targets = json.loads(response.read())
        matches = [
            str(item["webSocketDebuggerUrl"])
            for item in targets
            if item.get("type") == "page"
            and item.get("webSocketDebuggerUrl")
            and urlparse(str(item.get("url") or "")).hostname in {"maimai.cn", "www.maimai.cn"}
            and "/chat" in str(item.get("url") or "")
        ]
        if len(matches) != 1:
            raise ValueError("未找到唯一脉脉消息列表页")
        return matches[0]


def _external_id(data_msg: str) -> str:
    try:
        value = json.loads(data_msg).get("mid")
    except json.JSONDecodeError as exc:
        raise ValueError("脉脉推荐缺少稳定 ID") from exc
    if value is None:
        raise ValueError("脉脉推荐缺少稳定 ID")
    return str(value)


def _job_title(text: str) -> str:
    before_salary = re.split(r"\d+[kK]-\d+[kK]", text, maxsplit=1)[0]
    value = re.sub(r"^.*?消息记录将清除", "", before_salary).strip()
    return value[-200:] or "未知岗位"


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(0) if match else None


def _control_expression(label: str, *, click: bool) -> str:
    action = "matches[0].click(); return true" if click else "return matches.length"
    return """(() => { const matches = Array.from(
      document.querySelectorAll('.dialogue_list_container *'))
      .filter(element => element.children.length === 0
        && (element.textContent || '').trim() === __LABEL__
        && element.getClientRects().length > 0);
      __ACTION__; })()""".replace("__LABEL__", json.dumps(label)).replace(
        "__ACTION__", action
    )


def _success(
    card: MaimaiRecommendationCard, observed: str
) -> ExecutionResult:
    return ExecutionResult(
        outcome=ExecutionOutcome.SUCCEEDED,
        evidence_hash=hashlib.sha256(
            f"{card.external_recommendation_id}:{observed}".encode()
        ).hexdigest(),
        observed_content=observed[-500:],
    )
