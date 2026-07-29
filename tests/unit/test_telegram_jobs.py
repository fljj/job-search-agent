from unittest.mock import MagicMock

from adapters.browser.playwright_actions import PlaywrightActionExecutor
from adapters.browser.telegram_jobs import (
    parse_telegram_job_post,
    parse_telegram_job_posts,
)


def test_parses_remote_java_job_and_telegram_username() -> None:
    post = parse_telegram_job_post(
        "-1001698016813",
        "DeJob求职交流群",
        "36466",
        """
#招聘
🏡 BOT Chain #Layer1
🛵 合作方式：#全职 #远程
📚 待招岗位：#Java工程师 #技术
💰 薪酬福利：$4000 - $6000 / month
🌱 岗位职责：
负责矿池后端核心系统设计与开发
🌵 岗位要求：
3年以上 Java 开发经验
📮 申请方式：
Telegram: t.me/dbaozi
""",
    )

    assert post is not None
    assert post.contact_username == "@dbaozi"
    assert post.job.external_job_id == "-1001698016813:36466"
    assert post.job.title == "Java工程师"
    assert post.job.company_name == "BOT Chain"
    assert post.job.work_mode == "REMOTE"
    assert post.job.location == "远程"
    assert post.job.salary_text == "$4000 - $6000 / month"


def test_parses_at_username_contact() -> None:
    post = parse_telegram_job_post(
        "-1001698016813",
        "DeJob求职交流群",
        "36455",
        """
#招聘
🏡 Tomo #Wallet
🛵 合作方式：#全职 #远程
📚 待招岗位：#技术负责人 #技术
Telegram: @Lindaya12
""",
    )

    assert post is not None
    assert post.contact_username == "@Lindaya12"


def test_skips_post_without_unique_telegram_contact() -> None:
    assert (
        parse_telegram_job_post(
            "-1001698016813",
            "DeJob求职交流群",
            "36452",
            """
#招聘
🏡 Binance #CEX
📚 待招岗位：#创作者合作经理 #市场
Email: hr@example.com
""",
        )
        is None
    )


def test_skips_non_recruitment_channel_message() -> None:
    assert (
        parse_telegram_job_post(
            "-1001572924402",
            "abetterpath 招聘求职",
            "1",
            "提交招聘表单的通知",
        )
        is None
    )


def test_splits_numbered_remote_job_collection_without_cross_job_pollution() -> None:
    posts = parse_telegram_job_posts(
        "-1001572924402",
        "abetterpath 招聘求职",
        "coinmarketcap-1",
        """
#招聘
CoinMarketCap - 数据
https://x.com/CoinMarketCap

#全职 #开发 #运维 #测试 #智能合约 #BD #AI #java
1. DevOps Engineer (contractor) - 偏Ops，熟悉AWS
2. Finance Manager(contractor) - 4-8年财务相关经验
3. Java Developer (contractor) - 5-10年Java开发经验，可renew
4. PR & Communications Associate - 英文可以作为工作语言
5. AI Agentic Trading Expert - 熟悉trading
6. Senior Marketing Specialist - 中英文双语

【Atlas Oracle 岗位】
1. 智能合约测试工程师 - 相关经验加分
2. 智能合约开发(Solana) - 3-5年工作经验

✅远程 #global
Telegram: @EvanSun0212
""",
    )

    assert len(posts) == 8
    assert len({post.job.external_job_id for post in posts}) == 8
    java = next(post for post in posts if post.job.title == "Java Developer")
    assert java.job.company_name == "CoinMarketCap"
    assert java.job.work_mode == "REMOTE"
    assert java.job.location == "远程"
    assert "5-10年Java开发经验" in java.job.description
    assert "Finance Manager" not in java.job.description
    assert java.contact_username == "@EvanSun0212"


def test_opens_only_exact_telegram_contact_with_trusted_click() -> None:
    page = MagicMock()
    page._evaluate.side_effect = [
        True,
        {"x": 12.5, "y": 34.5},
        True,
    ]

    assert PlaywrightActionExecutor._open_telegram_contact(
        page,
        "Lindaya12",
    )
    assert page._command.call_args_list[0].args == (
        "Input.insertText",
        {"text": "@Lindaya12"},
    )
    assert page._command.call_args_list[1].args[0] == "Input.dispatchMouseEvent"
    assert page._command.call_args_list[2].args[0] == "Input.dispatchMouseEvent"
    assert ".replace(/^@/, '')" in page._evaluate.call_args_list[1].args[0]
    assert ".ListItem-button" in page._evaluate.call_args_list[1].args[0]
