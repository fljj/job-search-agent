# job-search-agent

面向 BOSS 直聘、脉脉和猎聘的无人值守求职 Agent。BOSS 可发现职位、处理招聘消息并
按规则发送网站内已有简历；脉脉仅处理系统推荐卡片，不读取或回复普通私信；猎聘可发现消息和职位、评分，并在发布授权后
执行“聊一聊”、普通回复及平台已有简历投递。L5 真实页面验收前猎聘写入默认关闭。电话和面试的
具体时间、登录验证、验证码及安全异常仍由用户处理。

## 1. 系统组成

- API：FastAPI、SQLAlchemy、Alembic、PostgreSQL
- Web：React、TypeScript、Ant Design、Vite
- Worker：通过 Playwright 连接本机已登录的 Chrome CDP 会话
- LLM：默认使用智谱 `glm-5.2`
- BOSS 入站卡片：匹配岗位的“索要附件简历”和“交换联系方式”请求直接同意，不调用
  LLM；工作地点卡片属于当前策略允许范围时直接接受；平台的“已查看简历”通知不会
  生成回复
- 对话记忆：从最近 200 条消息构建已讨论主题、最近确认内容、待回答问题和已完成动作，
  同时携带最近 20 条带说话人方向的原文；模型生成后程序再次移除重复询问
- 消息关联职位：首次读取或职位 ID 变化时打开完整 JD；已绑定且 ID 未变化时复用本地
  JD，不在消息兜底轮询中反复打开同一职位页
- 日历：macOS 默认使用 Apple Calendar
- 回复路由：到岗、薪资、地点和工作模式优先使用规则，候选人经历优先使用知识库，
  其余消息才调用 LLM；消息中心展示每条最新草稿的回复来源
- 职位分析：先执行不消耗 Token 的硬性规则；命中后直接记录排除原因，只有通过的职位
  才进入完整 JD 解析和 AI 评分
- LLM 熔断：只暂停依赖 LLM 的解析、评分、主动招呼和开放式回复；规则/知识库回复、
  符合条件的入站简历卡片和只读维护继续运行。
- 人工确认：普通低置信度或敏感回复在“人工确认”页面单独处理；电话和面试具体时间只在
  “面试确认”页面处理。总览分别统计两类未过期任务。

API、前端和 Worker 是三个独立进程。一个 Worker 会处理数据库中所有处于 `RUNNING`
状态的平台任务，不需要为各平台分别启动 Worker。

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

打开 `.env`，声明允许使用的供应商、各供应商 Base URL、API Key 和初始模型建议值：

```dotenv
LLM_PROVIDERS=ZHIPU,QWEN
ZHIPU_API_KEY=你的智谱API密钥
ZHIPU_MODEL=glm-5.2
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4
QWEN_API_KEY=你的千问API密钥
QWEN_MODEL=qwen-plus
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT_SECONDS=120
AGENT_EXECUTOR_MODE=REAL
CALENDAR_PROVIDER=APPLE
APPLE_CALENDAR_NAME=求职面试
```

不要提交 `.env`。系统不会在接口或日志中返回 API Key。

`.env` 不固定当前模型。`LLM_PROVIDERS` 决定系统设置中允许切换的供应商，
`ZHIPU_BASE_URL/QWEN_BASE_URL` 和对应 API Key 始终只从环境变量读取。
`ZHIPU_MODEL/QWEN_MODEL` 只是首次使用或切换供应商时的初始建议值，不是模型白名单。
当前供应商通过下拉框选择，模型名称通过文本框输入；点击“应用 LLM 配置”后，后续调用和
健康探针立即生效。模型请求超时时间也可在系统设置中按 1～300 秒调整，保存后下一次
LLM 调用立即生效；`LLM_TIMEOUT_SECONDS` 只提供首次运行或尚无运行时配置时的默认值。
千问结构化业务请求会显式关闭思考模式；职位评分只发送按来源分组的评分证据，使用短证据
编号并要求简洁输出；完整上下文仍保存在数据库用于硬过滤、
结果校验和审计，以降低延迟、Token 消耗和非流式请求超时概率。
LLM 请求不设置 `max_tokens`，避免结构化 JSON 被截断；输出长度由结构契约和简洁性规则约束。
新增或修改 `.env` 中的供应商、密钥或服务
地址后需要重启 API 和 Worker。操作系统环境变量优先于 `.env`；如果 Key 是通过终端
`export` 设置的，仍需重新启动对应进程。仅在系统设置中切换已启用供应商、模型或超时时间时
不需要重启。数据库地址等基础设施配置不支持热重载。

常用 Worker 配置：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `AGENT_EXECUTOR_MODE` | `REAL` | BOSS、脉脉和猎聘 Run 使用 `REAL`；离线 MOCK 使用 `FAKE` |
| `AGENT_CDP_URL` | `http://127.0.0.1:9222` | 专用 Chrome 的调试地址 |
| `AGENT_POLL_INTERVAL_SECONDS` | `10` | Worker 轮询间隔 |
| `AGENT_TICK_BATCH_SIZE` | `10` | 每轮最多处理数量 |
| `BOSS_JOB_BATCH_SIZE` | `5` | BOSS 每批最多处理的职位数 |
| `BOSS_JOB_SCAN_INTERVAL_SECONDS` | `180` | 完成一批后到下一批的最短间隔 |
| `LIEPIN_JOB_BATCH_SIZE` | `1` | 猎聘每次只保留一个临时详情页完成评分和动作回读 |
| `LIEPIN_JOB_SCAN_INTERVAL_SECONDS` | `180` | 猎聘完成一批后到下一批的最短间隔 |
| `LIEPIN_WRITES_ENABLED` | `false` | L5 单次真实验收并明确授权前保持关闭；修改后需重启 Worker |
| `BOSS_LLM_RETRY_BASE_SECONDS` | `300` | 职位详情等局部失败的首次等待时间 |
| `BOSS_LLM_RETRY_MAX_SECONDS` | `3600` | 职位局部重试的最长等待时间 |
| `BOSS_JOB_RETRY_MAX_ATTEMPTS` | `5` | 单个职位非全局故障的最多尝试次数 |
| `BOSS_JOB_SEARCH_LABELS` | `推荐,Java,区块链工程师` | BOSS 职位入口名称，必须与页面文字一致 |
| `AGENT_LOG_DIR` | `~/Desktop/job-search-agent/logs` | Worker 日志目录 |
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
uvicorn apps.api.app.main:app --host 127.0.0.1 --reload
```

- API：<http://localhost:8000>
- API 文档：<http://localhost:8000/docs>

API 默认只接受本机访问。若确需监听非回环地址，必须同时配置
`API_ACCESS_TOKEN`，调用方通过 `X-Local-Access-Token` 请求头传递令牌。

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

求职策略支持独立配置是否接受兼职。接受兼职时，明确要求异地现场办公的岗位仍会
排除；未写明办公方式的兼职岗位会保留并在后续沟通中确认，不会仅按公司所在地误判。

LLM 配置显示“未配置”时，确认对应供应商的 API Key 已写入项目根目录 `.env`，然后
重启 API 和 Worker。供应商和模型的日常切换不需要重启。

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

- 一个包含系统推荐的页面：`https://maimai.cn/chat`
- Worker 只识别并处理系统推荐卡片，不扫描普通私信

### 猎聘（L4 能力已就绪）

- 一个已登录首页：`https://c.liepin.com/`
- 不要另外打开第二个猎聘首页；Worker 不需要独立职位搜索页
- Worker 会先读取首页消息抽屉，再发现、硬过滤和评分职位；不主动刷新首页
- L5 真实页面验收前保持 `LIEPIN_WRITES_ENABLED=false`，此时只准备不可派发草稿和确认任务
- 明确授权并完成 L5 验收后可设为 `true`；新消息可普通回复或投递已登记的站内现有简历，
  满足主动门槛的职位可使用“聊一聊”
- Agent 不新增、编辑、刷新、上传或删除猎聘简历；如页面要求选择，只使用名称完全一致的
  已登记默认简历
- Agent 只收起本轮自己打开的会话弹窗和消息抽屉；用户原本打开的抽屉不会被关闭

同一平台不要打开多个消息列表或职位列表，否则 Worker 无法唯一确认操作目标并会暂停。
遇到登录验证、验证码或页面结构无法确认时，Worker 会停止该平台写操作。

可检查 CDP 是否可用：

```bash
curl http://127.0.0.1:9222/json/version
```

## 6. 配置并启动 Agent Run

Worker 进程和平台 Run 是两回事：

- Run：保存在数据库中，表示某个平台是否应该运行。
- Worker：本机唯一执行进程，轮询并处理所有 `RUNNING` Run。

### 6.1 配置自动化

打开前端“系统设置”：

1. 创建或保存全局自动化配置，设置“启用”为开。
2. 按需要开启自动回复、按请求发送简历和主动扫描职位。
3. 主动招呼最低分不得低于 80。
4. 设置工作时间以及公司和招聘人去重冷却时间。
5. 保持“紧急停止”为关闭。

电话和面试的具体时间无论日历是否空闲，都
不会由 Worker 自动确认。

新环境也可以通过 API 一次创建全局配置。下面的示例开启职位扫描、普通回复、主动招呼、
按明确请求发送简历和脉脉推荐；正式写操作仍需通过平台控制和安全检查：

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
    "auto_resume_enabled": true,
    "maimai_recommendation_enabled": true,
    "maimai_recommendation_resume_enabled": true,
    "emergency_stop": false,
    "job_scan_enabled": true,
    "company_cooldown_hours": 24,
    "recruiter_cooldown_hours": 24,
    "work_start_hour": 8,
    "work_end_hour": 22
  }'
```

已有配置优先在前端修改。调用 `PUT` 会按提交内容更新对应范围。

### 6.2 平台自动化控制

BOSS 和脉脉 Run 启用后直接按“系统设置”中的正式自动化配置运行，不需要初始化或升级
运行级别。全局、平台和策略开关、紧急停止、平台异常暂停、LLM 熔断、幂等
去重和电话/面试时间人工确认仍然生效。

脉脉 MAIMAI Run 仅处理系统推荐。系统推荐是否启用、是否允许同意后
发送平台资料，分别由：

- `maimai_recommendation_enabled`
- `maimai_recommendation_resume_enabled`

控制。推荐卡片使用确定性规则判断，不调用 LLM。推荐后的私信不导入消息中心，
也不生成或发送自动回复。
具体推荐规则见
[config/recommendation-policy.json](config/recommendation-policy.json)。

### 6.3 创建平台 Run

在“系统设置”底部的“平台任务管理（高级）”区域：

1. 选择平台 `BOSS`。
2. 选择已经启用的求职策略。
3. 点击“启动”。
4. 再选择平台 `MAIMAI`，使用同一策略点击“启动”。
5. 如需猎聘，只保留唯一猎聘首页后选择 `LIEPIN` 启动；该 Run 先处理消息，再发现和评分
   职位。L5 授权前保持 `LIEPIN_WRITES_ENABLED=false`，不会执行平台写动作。

运行中的平台可在表格中暂停，已暂停的平台可恢复，已停止等终态任务可
重新启动。页面连接异常不等于任务停止，应在总览使用“重新连接”。

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
| `PAUSED (RESULT_NOT_OBSERVED)` | 写操作结果未能即时回读，平台已安全暂停 | 先执行对账；未知动作清零后点击“重新连接” |
| `STALE` | Worker 心跳超时 | 检查 Worker 终端，确认旧进程停止后重新启动 |
| `OUTCOME_UNKNOWN` | 无法确认一次写操作是否成功 | 不要直接重试，先在系统设置执行对账 |

Worker 日志默认写入：

```text
~/Desktop/job-search-agent/logs/agent.log
```

查看最新日志：

```bash
tail -f ~/Desktop/job-search-agent/logs/agent.log
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

如果宿主机未安装 PostgreSQL 客户端，使用 Docker 容器内工具：

```bash
docker compose exec -T postgres pg_dump \
  --format=custom --no-owner --no-acl \
  --username=job_agent --dbname=job_agent \
  > backups/job-search-agent-YYYYMMDD-HHMMSS.dump
chmod 600 backups/job-search-agent-YYYYMMDD-HHMMSS.dump
```

恢复演练只允许数据库名称包含 `_restore_test`：

```bash
RESTORE_DATABASE_URL='postgresql+psycopg://.../job_agent_restore_test' \
  scripts/restore_rehearsal.sh backups/job-search-agent-YYYYMMDD-HHMMSS.dump
```

迁移前备份、恢复演练和回滚流程见
[`docs/database-operations.md`](docs/database-operations.md)。生产/真实数据迁移只向前执行；
失败时从已验证备份恢复到独立数据库并切换连接，不在原库盲目执行 `alembic downgrade`。

清理运行历史会删除职位、评分、会话、动作、审计、Worker 状态和排期数据；候选人资料、
求职策略、知识库、简历、自动化设置、LLM 模型选择及日历偏好会保留。先预览，确认目标后才执行：

```bash
python scripts/reset_runtime_data.py --confirm-database job_agent
python scripts/reset_runtime_data.py --confirm-database job_agent --execute
```

不要在日常启动过程中执行数据重置。

## 12. 安全边界与当前限制

- 不破解验证码、不绕过登录验证、不进行反检测或无限批量投递。
- 简历只从招聘网站中已经存在且系统已登记的在线简历或附件简历中选择，不自动上传本地文件。
- 主动招呼必须通过硬性规则、80 分门槛、模型建议、职位开放状态和自动化开关。
- 职位中心可打开已保存的原职位链接；主动招呼找不到当前标签页时，可通过该链接
  重新定位，但写入前仍会重新校验职位身份。
- 招聘方当前消息明确、肯定地索要简历时不受主动职位评分门槛限制；职位信息不足保持
  `UNKNOWN`，但已证实的完全无关岗位、黑名单、欺诈、重复发送、错误目标和附件不可用
  仍会阻断。
- 电话和面试具体时间必须由用户确认，日历空闲不等于自动接受。
- BOSS 支持职位发现和消息处理；脉脉仅支持系统推荐，不处理普通私信或职位列表；
  猎聘 L4 已实现首页职位发现、评分、消息处理和三类动作，但正式写入须完成 L5 单次真实验收
  和明确授权。
- 页面身份不一致、登录失效、选择器变化或结果无法确认时，系统会暂停或进入安全对账。

详细设计和业务规则见：

- [产品需求](docs/product-requirements.md)
- [评分规则](docs/scoring-rules.md)
- [沟通策略](docs/conversation-policy.md)
- [排期策略](docs/scheduling-policy.md)
- [架构设计](docs/architecture.md)
- [数据库运维](docs/database-operations.md)
- [数据模型](docs/data-model.md)
- [API 设计](docs/api-design.md)
- [开发计划](docs/development-plan.md)
