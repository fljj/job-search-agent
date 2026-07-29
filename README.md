# job-search-agent

面向 BOSS 直聘、脉脉和白名单 Telegram 招聘频道的无人值守求职 Agent。系统可以自动发现和评分职位、处理普通招聘
消息，并按规则发送网站内已有简历；电话和面试的具体时间、登录验证、验证码及安全异常
仍由用户处理。

## 1. 系统组成

- API：FastAPI、SQLAlchemy、Alembic、PostgreSQL
- Web：React、TypeScript、Ant Design、Vite
- Worker：通过 Playwright 连接本机已登录的 Chrome CDP 会话
- LLM：默认使用智谱 `glm-5.2`
- BOSS 入站卡片：匹配岗位的“索要附件简历”和“交换联系方式”请求直接同意，不调用
  LLM；工作地点卡片属于策略允许的济南范围时直接接受；平台的“已查看简历”通知不会
  生成回复
- 对话记忆：从最近 200 条消息构建已讨论主题、最近确认内容、待回答问题和已完成动作，
  同时携带最近 20 条带说话人方向的原文；模型生成后程序再次移除重复询问
- 日历：macOS 默认使用 Apple Calendar
- 回复路由：到岗、薪资、地点和工作模式优先使用规则，候选人经历优先使用知识库，
  其余消息才调用 LLM；消息中心展示每条最新草稿的回复来源
- 职位分析：先执行不消耗 Token 的硬性规则；命中后直接记录排除原因，只有通过的职位
  才进入完整 JD 解析和 AI 评分
- LLM 熔断：限流、余额/认证、网络或服务异常时暂停全部 Agent 业务，只保留单飞健康
  探测；总览可查看原因、下次重试时间并手动点击“立即重试 LLM”

API、前端和 Worker 是三个独立进程。一个 Worker 会处理数据库中所有处于 `RUNNING`
状态的平台任务，不需要为 BOSS、脉脉和 Telegram 分别启动 Worker。

## 2. 环境要求

- macOS（Apple Calendar 和下面的 Chrome 启动命令针对 macOS）
- Python `3.13`
- Node.js `20.19+`、`22.12+` 或 `24+`
- npm
- Docker Desktop
- Google Chrome

确认版本：

```bash
python3.13 --version
node --version
npm --version
docker --version
```

所有后端命令都应在项目根目录执行，因为程序会从根目录读取 `.env` 和 `config/`。

## 3. 首次安装

### 3.1 创建环境变量文件

```bash
cp .env.example .env
```

打开 `.env`，至少配置智谱密钥：

```dotenv
LLM_PROVIDER=ZHIPU
ZHIPU_API_KEY=你的智谱API密钥
LLM_MODEL=glm-5.2
LLM_TIMEOUT_SECONDS=120
AGENT_EXECUTOR_MODE=REAL
CALENDAR_PROVIDER=APPLE
APPLE_CALENDAR_NAME=求职面试
```

不要提交 `.env`。系统不会在接口或日志中返回 API Key。

修改 LLM API Key、模型或服务地址后，无需重启项目。在总览的 LLM 异常提示中点击
“重新加载配置并重试 LLM”；Worker 的下一次自动健康探测也会重新读取 `.env`。探测成功
后自动解除全局暂停并继续业务。操作系统环境变量优先于 `.env`；如果 Key 是通过终端
`export` 设置的，仍需重新启动对应进程。数据库地址等基础设施配置不支持热重载。

常用 Worker 配置：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `AGENT_EXECUTOR_MODE` | `REAL` | BOSS/脉脉必须使用 `REAL`；离线 MOCK 使用 `FAKE` |
| `AGENT_CDP_URL` | `http://127.0.0.1:9222` | 专用 Chrome 的调试地址 |
| `AGENT_POLL_INTERVAL_SECONDS` | `10` | Worker 轮询间隔 |
| `AGENT_TICK_BATCH_SIZE` | `10` | 每轮最多处理数量 |
| `BOSS_JOB_BATCH_SIZE` | `5` | BOSS 每批最多处理的职位数 |
| `BOSS_JOB_SCAN_INTERVAL_SECONDS` | `180` | 完成一批后到下一批的最短间隔 |
| `BOSS_LLM_RETRY_BASE_SECONDS` | `300` | 职位详情等局部失败的首次等待时间 |
| `BOSS_LLM_RETRY_MAX_SECONDS` | `3600` | 职位局部重试的最长等待时间 |
| `BOSS_JOB_RETRY_MAX_ATTEMPTS` | `5` | 单个职位非全局故障的最多尝试次数 |
| `BOSS_JOB_SEARCH_LABELS` | `推荐,Java,区块链工程师` | BOSS 职位入口名称，必须与页面文字一致 |
| `AGENT_LOG_DIR` | `~/Desktop/job-search-agent-gray/logs` | Worker 日志目录 |
| `WORKER_STALE_SECONDS` | `60` | 超过该时间没有心跳则标记 Worker 异常 |

完整配置及默认值见 [.env.example](.env.example)。

### 3.2 安装后端

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
```

以后重新打开终端，只需在项目根目录执行：

```bash
source .venv/bin/activate
```

### 3.3 启动 PostgreSQL

先启动 Docker Desktop，再执行：

```bash
docker compose up -d postgres
docker compose ps
```

PostgreSQL 使用本机端口 `55432`，数据保存在 Docker volume `postgres_data` 中。

### 3.4 执行数据库迁移

```bash
alembic upgrade head
```

每次拉取包含数据库变更的新代码后，都应再次运行该命令。

### 3.5 安装前端

```bash
cd apps/web
npm install
cd ../..
```

## 4. 启动 API 和前端

开发环境建议分别打开两个终端。

终端一，在项目根目录启动 API：

```bash
source .venv/bin/activate
uvicorn apps.api.app.main:app --reload
```

- API：<http://localhost:8000>
- API 文档：<http://localhost:8000/docs>

终端二启动前端：

```bash
cd apps/web
npm run dev
```

- 前端：<http://localhost:5173>

首次使用时在前端完成：

1. 在“候选人中心”维护候选人资料、可信事实和网站内简历名称。
2. 在“求职策略”创建并启用策略。
3. 在“系统设置”确认数据库、LLM 和日历状态正常。

LLM 配置显示“未配置”时，确认 `ZHIPU_API_KEY` 写在项目根目录 `.env`，然后重启 API
和 Worker。修改 `.env` 不会自动刷新已经运行的进程。

可在不访问招聘网站的情况下检查智谱连接：

```bash
source .venv/bin/activate
python scripts/smoke_llm.py
python scripts/smoke_llm_score.py
```

这两个命令会真实调用模型并消耗少量 Token。

## 5. 准备招聘平台专用 Chrome

Worker 不启动浏览器，也不保存账号密码、Cookie 或验证码。必须由用户手动打开专用
Chrome 并登录。

如果已经有使用端口 `9222` 的专用 Chrome，不要重复启动。首次启动：

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/job-agent-browser-profile
```

在这个 Chrome 中手动登录并保留以下唯一标签页：

### BOSS

- 一个消息列表页：`https://www.zhipin.com/web/geek/chat`
- 一个职位列表页：`https://www.zhipin.com/web/geek/jobs`

### 脉脉

- 一个消息页：`https://maimai.cn/chat`

同一平台不要打开多个消息列表或职位列表，否则 Worker 无法唯一确认操作目标并会暂停。
遇到登录验证、验证码或页面结构无法确认时，Worker 会停止该平台写操作。

可检查 CDP 是否可用：

```bash
curl http://127.0.0.1:9222/json/version
```

## 6. 配置并启动 Agent Run

Worker 进程和平台 Run 是两回事：

- Run：保存在数据库中，表示 BOSS 或脉脉是否应该运行。
- Worker：本机唯一执行进程，轮询并处理所有 `RUNNING` Run。

### 6.1 配置自动化

打开前端“系统设置”：

1. 创建或保存全局自动化配置，设置“启用”为开。
2. 按需要开启自动回复、按请求发送简历和主动扫描职位。
3. 主动招呼最低分不得低于 80。
4. 设置工作时间、小时/每日限额以及公司和招聘人冷却时间。
5. 保持“紧急停止”为关闭。

建议先使用较低限额观察日志，再逐步增加。电话和面试的具体时间无论日历是否空闲，都
不会由 Worker 自动确认。

新环境也可以通过 API 一次创建全局配置。下面的示例开启职位扫描、普通回复、主动招呼、
按明确请求发送简历和脉脉推荐；正式写操作仍需通过平台控制、安全检查和限额：

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/automation/settings \
  -H 'Content-Type: application/json' \
  -d '{
    "scope_type": "GLOBAL",
    "scope_key": "GLOBAL",
    "enabled": true,
    "paused": false,
    "auto_greet_enabled": true,
    "auto_greet_min_score": 80,
    "auto_reply_enabled": true,
    "low_score_decline_enabled": true,
    "auto_reply_min_confidence": 0.9,
    "auto_resume_enabled": true,
    "auto_resume_min_score": 60,
    "maimai_recommendation_enabled": true,
    "maimai_recommendation_resume_enabled": true,
    "hourly_limit": 10,
    "daily_limit": 50,
    "emergency_stop": false,
    "job_scan_enabled": true,
    "hourly_scan_limit": 100,
    "daily_scan_limit": 500,
    "company_cooldown_hours": 24,
    "recruiter_cooldown_hours": 24,
    "work_start_hour": 8,
    "work_end_hour": 22
  }'
```

已有配置优先在前端修改。调用 `PUT` 会按提交内容更新对应范围，不要在不了解当前限额
时直接覆盖生产配置。

### 6.2 初始化平台控制

BOSS 首次运行时，在“系统设置”的“BOSS 无人值守灰度”中点击“初始化”，然后启用
当前级别。系统支持逐级验证；正式接管仍受安全指标和服务端门禁限制。

脉脉的普通消息与系统推荐使用同一个 MAIMAI Run。系统推荐是否启用、是否允许同意后
发送平台资料，分别由：

- `maimai_recommendation_enabled`
- `maimai_recommendation_resume_enabled`

控制。推荐卡片使用确定性规则判断，不调用 LLM；推荐后的真人对话才可能调用 LLM。
具体推荐规则见
[config/recommendation-policy.json](config/recommendation-policy.json)。

### 6.3 创建 BOSS、脉脉和 Telegram Run

在“系统设置”底部的“Agent 运行”区域：

1. 选择平台 `BOSS`。
2. 选择已经启用的求职策略。
3. 点击“启动”。
4. 再选择平台 `MAIMAI`，使用同一策略点击“启动”。
5. 登录 Telegram Web A 后，再选择 `TELEGRAM`，使用同一策略点击“启动”。

Telegram 使用同一个 Agent 专用 Chrome，并要求只保留一个
`https://web.telegram.org/a/` 标签页。系统只扫描
`config/telegram-policy.json` 中的频道；帖子复用现有求职策略评分，只有 80 分以上且
明确提供 Telegram 联系人的职位才允许私聊。联系人必须通过 Telegram 全局搜索精确匹配；
模型限流等暂时性失败按配置延迟重试，不会绕过评分直接发送。

平台显示 `RUNNING` 只表示数据库任务已经启动；必须继续启动 Worker 才会实际扫描页面。

## 7. 启动 Worker

打开第三个终端，在项目根目录执行：

```bash
source .venv/bin/activate
python scripts/run_agent_worker.py
```

Worker 启动前会检查：

- 数据库是否可连接且迁移到最新版本；
- LLM 是否配置；
- `AGENT_EXECUTOR_MODE` 是否与真实平台匹配；
- CDP 是否可连接；
- 是否已经存在另一个 Worker。

Worker 启动后的首轮扫描会继续检查平台登录状态和页面是否可识别；检查失败时只暂停对应
平台 Run。

macOS 长时间运行时，可以让该进程阻止电脑自动睡眠：

```bash
source .venv/bin/activate
caffeinate -dims python scripts/run_agent_worker.py
```

不要同时运行两份 Worker。进程锁会拒绝第二个实例，但重复的进程管理仍会造成状态混乱。

停止 Worker：在 Worker 终端按 `Ctrl+C`。程序会记录停止状态；数据库中的平台 Run
不会因此被删除。

## 8. 日常启动顺序

电脑重启后按以下顺序恢复：

1. 启动 Docker Desktop。
2. `docker compose up -d postgres`
3. 启动 API。
4. 启动前端。
5. 启动端口 `9222` 的专用 Chrome，并确认 BOSS/脉脉仍已登录。
6. 打开所需的消息页和 BOSS 职位页。
7. 在总览确认平台 Run；暂停的平台可在页面恢复后点击“重新连接”。
8. 启动唯一 Worker。

API 和前端用于控制及观察；Worker 才负责持续读取招聘平台和执行自动动作。

## 9. 状态与日志

前端“总览”应同时显示：

- BOSS 和脉脉平台状态；
- 唯一 Worker 及其心跳状态；
- LLM、数据库和安全指标；
- 自动动作、未知结果和待确认事项。

常见状态：

| 状态 | 含义 | 处理 |
|---|---|---|
| `RUNNING` | 平台 Run 正常轮询 | 无需处理 |
| `PAUSED (MESSAGE_DISCOVERY_UNAVAILABLE)` | 消息页关闭或暂时无法识别 | 重新打开唯一消息页，点击“重新连接” |
| `PAUSED (JOB_DISCOVERY_UNAVAILABLE)` | BOSS 职位页关闭或无法识别 | 重新打开唯一职位页，点击“重新连接” |
| `STALE` | Worker 心跳超时 | 检查 Worker 终端，确认旧进程停止后重新启动 |
| `OUTCOME_UNKNOWN` | 无法确认一次写操作是否成功 | 不要直接重试，先在系统设置执行对账 |

Worker 日志默认写入：

```text
~/Desktop/job-search-agent-gray/logs/agent-gray.log
```

查看最新日志：

```bash
tail -f ~/Desktop/job-search-agent-gray/logs/agent-gray.log
```

日志包含扫描、动作、暂停和对账事件，不应包含 API Key、Cookie 或完整消息正文。

## 10. 开发验证

后端：

```bash
source .venv/bin/activate
pytest
ruff check .
mypy .
```

PostgreSQL 集成测试使用独立测试库：

```bash
TEST_DATABASE_URL=postgresql+psycopg://job_agent:job_agent@localhost:55432/job_agent_test pytest
```

浏览器夹具测试首次需要：

```bash
playwright install chromium
```

前端：

```bash
cd apps/web
npm run lint
npm run test
npm run build
```

## 11. 数据备份与测试数据重置

备份：

```bash
DATABASE_URL='postgresql+psycopg://...' scripts/backup_database.sh
```

恢复演练只允许数据库名称包含 `_restore_test`：

```bash
RESTORE_DATABASE_URL='postgresql+psycopg://.../job_agent_restore_test' \
  scripts/restore_rehearsal.sh backups/job-search-agent-YYYYMMDD-HHMMSS.dump
```

清理灰度历史会删除职位、评分、会话、动作、审计和排期数据。先预览，确认目标后才执行：

```bash
python scripts/reset_gray_data.py --confirm-database job_agent
python scripts/reset_gray_data.py --confirm-database job_agent --execute
```

不要在日常启动过程中执行数据重置。

## 12. 安全边界与当前限制

- 不破解验证码、不绕过登录验证、不进行反检测或无限批量投递。
- 简历只从招聘网站中已经存在的附件选择，不自动上传本地文件。
- 主动招呼必须通过硬性规则、80 分门槛、模型建议、职位开放状态、自动化开关和限额。
- 招聘方主动索要简历时不受主动职位评分门槛限制，但仍受资格、重复发送和安全规则约束。
- 电话和面试具体时间必须由用户确认，日历空闲不等于自动接受。
- BOSS 支持职位发现和消息处理；脉脉支持普通消息及系统推荐，暂不支持脉脉职位列表主动发现。
- 页面身份不一致、登录失效、选择器变化或结果无法确认时，系统会暂停或进入安全对账。

详细设计和业务规则见：

- [产品需求](docs/product-requirements.md)
- [评分规则](docs/scoring-rules.md)
- [沟通策略](docs/conversation-policy.md)
- [排期策略](docs/scheduling-policy.md)
- [架构设计](docs/architecture.md)
- [数据模型](docs/data-model.md)
- [API 设计](docs/api-design.md)
- [开发计划](docs/development-plan.md)
