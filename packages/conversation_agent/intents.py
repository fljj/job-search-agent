from packages.conversation_agent.models import Intent

INTENT_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.JOB_DETAIL: ("岗位职责", "职位详情", "工作内容", "技术重点"),
    Intent.TECH_STACK: ("技术栈", "java", "spring", "redis", "kafka", "mysql", "kubernetes"),
    Intent.WORK_EXPERIENCE: ("工作经验", "几年", "年限"),
    Intent.PROJECT_EXPERIENCE: ("项目", "做过"),
    Intent.MANAGEMENT_EXPERIENCE: ("管理", "团队", "带人"),
    Intent.EDUCATION: (
        "学历",
        "专科",
        "本科",
        "硕士",
        "研究生",
        "全日制",
        "统招",
        "在职",
        "学信网",
        "学历性质",
    ),
    Intent.SALARY: ("薪资", "薪水", "期望", "最低"),
    Intent.LOCATION: ("地点", "城市", "坐标"),
    Intent.REMOTE_POLICY: ("远程", "remote", "居家"),
    Intent.ARRIVAL_DATE: ("到岗", "入职日期"),
    Intent.RESUME_REQUEST: (),
    Intent.PHONE_CALL: ("电话", "通话"),
    Intent.INTERVIEW_INVITATION: ("面试", "视频面"),
    Intent.INTERVIEW_TIME: ("几点", "什么时候", "时间", "周一", "周二", "周三", "周四", "周五"),
    Intent.COMPANY_INTRODUCTION: ("公司介绍", "业务介绍"),
    Intent.SENSITIVE: ("身份证", "银行卡", "密码", "验证码", "家庭信息", "客户名单"),
}

PHONE_CALL_EVIDENCE = ("电话", "通话", "语音", "打给", "致电")
INTERVIEW_EVIDENCE = ("面试", "视频面", "到公司聊", "来公司聊")


def classify_intents(content: str) -> list[Intent]:
    lowered = content.lower()
    found = [intent for intent, words in INTENT_KEYWORDS.items() if any(word in lowered for word in words)]
    if is_explicit_resume_request(content):
        found.append(Intent.RESUME_REQUEST)
    return normalize_intents(content, found)


RESUME_NEGATIVE_MARKERS = (
    "简历不合适", "简历不匹配", "看过简历", "查看了您的附件简历",
    "已收到简历", "简历里没看到", "简历中没看到", "不用发简历",
)
RESUME_REQUEST_MARKERS = (
    "发一份简历", "发份简历", "发送一份简历", "发送简历", "提供一份简历",
    "提供简历", "要一份简历", "需要一份简历", "麻烦发简历", "简历发我",
    "附件简历发", "可以发简历", "方便发简历",
)


def is_explicit_resume_request(content: str) -> bool:
    """只识别当前、肯定的索要；平台卡片由独立结构化证据处理。"""
    normalized = "".join(content.casefold().split())
    if any("".join(marker.casefold().split()) in normalized for marker in RESUME_NEGATIVE_MARKERS):
        return False
    return any(
        "".join(marker.casefold().split()) in normalized
        for marker in RESUME_REQUEST_MARKERS
    )


def normalize_intents(content: str, intents: list[Intent]) -> list[Intent]:
    """用确定性规则消除会改变权限边界的模型意图冲突。"""
    found = list(dict.fromkeys(intents))
    lowered = content.lower()
    if (
        Intent.PHONE_CALL in found
        and not any(word in lowered for word in PHONE_CALL_EVIDENCE)
    ):
        # “沟通、交流、聊聊”是普通招聘对话，不能仅凭模型判断升级为电话排期。
        found = [intent for intent in found if intent is not Intent.PHONE_CALL]
    if (
        Intent.INTERVIEW_INVITATION in found
        and not any(word in lowered for word in INTERVIEW_EVIDENCE)
    ):
        found = [
            intent
            for intent in found
            if intent is not Intent.INTERVIEW_INVITATION
        ]
    if (
        Intent.ARRIVAL_DATE in found
        and Intent.PHONE_CALL not in found
        and Intent.INTERVIEW_INVITATION not in found
    ):
        # “到岗时间”是普通事实询问，不能因包含泛化词“时间”而升级为面试排期。
        found = [intent for intent in found if intent is not Intent.INTERVIEW_TIME]
    return found or [Intent.UNCLEAR]
