# job-search-agent

半自动求职 Agent。当前已实现职位评分、LLM 解析与沟通、Agent 安全循环、BOSS 本地脱敏页面验证，以及用于评分、对话、自动动作和运行状态观察的前端控制台。

## 技术栈

- Python 3.13、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL
- React、TypeScript、Ant Design、Vite
- Playwright（连接用户手动登录的本机 Chromium CDP 会话）
- pytest、Ruff、mypy、Vitest

## 本地启动

### 1. 准备环境

```bash
cp .env.example .env
docker compose up -d postgres
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

项目 PostgreSQL 映射到本机 `55432` 端口，避免与已有本地 PostgreSQL 冲突。

`.env.example` 已预留千问配置。`LLM_PROVIDER=FAKE` 可完全离线运行；使用千问时将
`LLM_PROVIDER=QWEN` 和 `LLM_API_KEY` 只写入本机 `.env`，不得提交。

日历默认使用 `CALENDAR_PROVIDER=MOCK`，仅用于本地测试，不代表真实空闲。接入 Google
Calendar 时配置 `CALENDAR_PROVIDER=GOOGLE`、`GOOGLE_CALENDAR_ACCESS_TOKEN` 和
`GOOGLE_CALENDAR_ID`。访问令牌只写入本机 `.env`；忙闲查询只读取时间范围，事件写入
仍需用户在时间确认卡片中独立授权。令牌缺失或失效时系统返回日历不可用。

统一 LLM 适配器的默认测试不会访问网络。如需显式执行一次千问消息分类冒烟：

```bash
python scripts/smoke_qwen.py
```

### 2. 初始化数据库

```bash
alembic upgrade head
```

### 3. 启动 API

```bash
uvicorn apps.api.app.main:app --reload
```

API 文档：`http://localhost:8000/docs`。

### 4. 启动前端

```bash
cd apps/web
npm install
npm run dev
```

前端地址：`http://localhost:5173`。

## 核心流程

1. `PUT /api/v1/profile` 保存候选人评分资料。
2. `POST /api/v1/strategies` 创建完整求职策略。
3. `POST /api/v1/jobs/import` 或 `/import/batch` 导入模拟 JD。
4. `POST /api/v1/jobs/{job_id}/parse` 使用 `LLM` 模式生成模型结构化解析记录；`RULE` 仅保留为离线兼容模式。
5. `POST /api/v1/jobs/{job_id}/scores` 执行“确定性硬排除 + LLM 七维评分 + 程序校验”并保存结果；`/scores/re-evaluate` 配合 `Idempotency-Key` 显式重新评估。
6. `GET /api/v1/scores/{score_id}` 查看维度明细、排除原因和风险。
7. `POST /api/v1/knowledge-items` 录入有来源、敏感度和自动引用权限的事实。
8. `POST /api/v1/resumes` 登记网站内附件简历元数据。
9. `POST /api/v1/conversations` 和消息接口创建模拟对话。
10. `POST /api/v1/drafts/reply`、`/drafts/greeting` 或 `/drafts/resume` 生成 LLM 事实受限草稿及权限决策；只有具体时间创建确认任务。
11. `POST /api/v1/browser/read-current` 只读解析当前 BOSS/脉脉页面并幂等导入。
12. 在“人工确认与发送”页批准、修改或拒绝任务，再单独执行已批准动作。
13. 在“安全自动化”页按全局、平台或策略配置开关与阈值，通过 `/api/v1/automation/dispatch` 执行服务端授权。
14. Agent 自动识别电话和面试邀请；在“电话与面试安排”页检查日历、确认或拒绝具体
    回复，并独立授权创建日历事件。新的改期任务会替代尚未完成的旧任务。
15. 通过 `/api/v1/automation/runs` 启动 Agent，并由 `/runs/{id}/tick` 执行受数据库短租约保护的离线轮询；可暂停、恢复和查看心跳、计数及熔断原因。

本地短轮询 Worker 可独立启动。执行器必须通过 `AGENT_EXECUTOR_MODE` 显式隔离：
真实 BOSS 使用 `REAL`，离线 `MOCK` 测试使用 `FAKE`，配置交叉时 Worker 拒绝执行。
BOSS 平台会通过本机 CDP 自动扫描唯一的消息列表页，按未读会话切换并复核会话 ID、
招聘人、公司和职位，只有成功绑定当前策略有效评分后才导入消息和执行普通回复。
列表游标、最后消息标识及最多 500 个去重键保存在 Agent 运行记录中；虚拟滚动会在
后续轮询继续加载。启用“主动扫描职位”后，Worker 还会读取唯一的 BOSS 职位搜索列表，
逐个新标签核验详情，并执行导入、千问评分、程序授权、去重/冷却和主动招呼。职位扫描
仅在配置的工作时段和限额内运行，紧急停止优先阻断所有自动动作。Worker 使用本机进程
锁避免重复启动，并记录执行器类型。专用浏览器需同时保留一个消息列表页和一个职位
搜索列表页：

```bash
python scripts/run_agent_worker.py
```

CDP 地址默认是 `http://127.0.0.1:9222`，需要调整时设置
`AGENT_CDP_URL`。Worker 不保存浏览器 Cookie 或招聘平台密码。电话和面试具体时间
仍只生成确认任务，不会自动发送。

对 `OUTCOME_UNKNOWN` 动作使用 `POST /api/v1/actions/{id}/reconcile` 回读平台。
只有平台明确确认未发送后，动作才转为可重新批准状态；仍无法判断时保持阻断。

### 本机浏览器只读接入

由用户使用独立 Chromium/Chrome 配置目录手动启动调试端口并登录招聘平台，例如：

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir=/tmp/job-agent-browser-profile
```

然后手动打开职位列表、职位详情、对话列表或对话详情页，在前端“招聘网站只读”页执行读取。只允许 `localhost/127.0.0.1/::1` CDP 端点，不保存账号、密码或 Cookie。

前端提供候选人资料编辑、策略创建/编辑、模拟 JD 导入、解析、评分和结果明细查看。
Agent 控制台只显示 LLM 供应商、模型和配置状态，不返回密钥；可查看并控制运行状态、心跳、处理/动作/失败计数、暂停原因及普通自动动作结果。电话和面试时间仍在独立确认页面处理。

BOSS 职位详情只有在页面存在可见且可用的沟通入口时才记录为 `OPEN`；无法确认
开放状态时保持 `UNKNOWN` 并禁止自动沟通。招呼语可以引用候选人资料中已确认的
工作年限、管理经历和技能事实。

BOSS 首次点击“立即沟通”会直接发送平台默认招呼。系统使用 `PLATFORM_DEFAULT`
模式并回读实际发送文本；默认接受任意非空平台文案并记录审计，如配置固定预期文案
则必须逐字一致。系统不会追加第二条消息。

示例候选人和职位数据位于 `config/sample-data/`。

## 验证

```bash
playwright install chromium
pytest
ruff check .
mypy .
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

默认的 `pytest` 在未配置测试数据库时会跳过 PostgreSQL 集成测试。如需运行完整 API 流程，先创建独立测试库，再执行：

```bash
TEST_DATABASE_URL=postgresql+psycopg://job_agent:job_agent@localhost:55432/job_agent_test pytest
```

```bash
cd apps/web
npm run lint
npm run test
npm run build
```

PostgreSQL 集成测试和迁移验证需要本地 Docker 或可用的 PostgreSQL 16 实例。

## 当前范围限制

- BOSS 已覆盖职位列表/详情、对话列表/详情、文本及站内附件简历的本地夹具；脉脉仍只保留当前页基础适配。
- 第六阶段默认测试只使用脱敏 HTML 和本地无头 Chromium，不登录或操作真实账号。
- BOSS 消息和职位发现已接入 Agent 循环；真实无人值守仍须完成阶段十的 100 职位灰度
  及后续阶段验收。
- 控制台默认联调限速为每小时 1 次、每日 3 次；真实联调必须按 `docs/development-plan.md` 的顺序逐步人工放行。
- 简历仅能从已登记且平台页面中唯一匹配的附件中选择，不上传本地文件。
- 默认不连接真实日历；可选接入 Google Calendar，OAuth 凭证缺失时安全降级为不可用。
- 新评分使用 LLM 七维输出；旧 `legacy` 评分只保留历史展示，不能授权新的自动动作。
- 自动招呼最低分配置不得低于80，自动简历最低分配置不得低于60。
- 策略可配置最高 79 分的猎头岗位分数封顶，使猎头岗位可接收回复但不主动招呼。
- 千问已接入职位解析、评分、招呼、入站回复和简历反馈判断。首次真实招呼必须先创建
  人工确认任务，批准后再单独执行；执行前会复核职位、公司、招聘人、开放状态和
  外部职位 ID，并使用幂等指纹防止重复发送。
- BOSS 正式运行只允许 `REAL` 执行器，`MOCK` 离线测试只允许 `FAKE`；两者交叉配置时
  Worker 会拒绝运行。

完整设计见 `docs/`。
