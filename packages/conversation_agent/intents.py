from packages.conversation_agent.models import Intent

INTENT_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.TECH_STACK: ("技术栈", "java", "spring", "redis", "kafka", "mysql", "kubernetes"),
    Intent.WORK_EXPERIENCE: ("工作经验", "几年", "年限"),
    Intent.PROJECT_EXPERIENCE: ("项目", "做过"),
    Intent.MANAGEMENT_EXPERIENCE: ("管理", "团队", "带人"),
    Intent.EDUCATION: ("学历", "本科", "全日制", "统招", "学历性质"),
    Intent.SALARY: ("薪资", "薪水", "期望", "最低"),
    Intent.LOCATION: ("地点", "城市", "坐标"),
    Intent.REMOTE_POLICY: ("远程", "remote", "居家"),
    Intent.ARRIVAL_DATE: ("到岗", "入职日期"),
    Intent.RESUME_REQUEST: ("简历", "附件"),
    Intent.PHONE_CALL: ("电话", "通话"),
    Intent.INTERVIEW_INVITATION: ("面试", "视频面"),
    Intent.INTERVIEW_TIME: ("几点", "什么时候", "时间", "周一", "周二", "周三", "周四", "周五"),
    Intent.COMPANY_INTRODUCTION: ("公司介绍", "业务介绍"),
    Intent.SENSITIVE: ("身份证", "银行卡", "密码", "验证码", "家庭信息", "客户名单"),
}


def classify_intents(content: str) -> list[Intent]:
    lowered = content.lower()
    found = [intent for intent, words in INTENT_KEYWORDS.items() if any(word in lowered for word in words)]
    return found or [Intent.UNCLEAR]
