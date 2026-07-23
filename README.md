# job-search-agent

半自动求职 Agent。当前已实现职位评分、LLM 解析与沟通、Agent 安全循环，以及 BOSS 本地脱敏页面夹具的读取和受控写入验证。

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
14. 在“电话与面试安排”页解析招聘消息、检查日历、确认具体回复，并独立授权创建日历事件。
15. 通过 `/api/v1/automation/runs` 启动 Agent，并由 `/runs/{id}/tick` 执行受数据库短租约保护的离线轮询；可暂停、恢复和查看心跳、计数及熔断原因。

阶段五本地短轮询 Worker 使用 Fake 执行器，可独立启动：

```bash
python scripts/run_agent_worker.py
```

### 本机浏览器只读接入

由用户使用独立 Chromium/Chrome 配置目录手动启动调试端口并登录招聘平台，例如：

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir=/tmp/job-agent-browser-profile
```

然后手动打开职位列表、职位详情、对话列表或对话详情页，在前端“招聘网站只读”页执行读取。只允许 `localhost/127.0.0.1/::1` CDP 端点，不保存账号、密码或 Cookie。

前端提供候选人资料编辑、策略创建/编辑、模拟 JD 导入、解析、评分和结果明细查看。

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
- 真实平台写操作尚未接入 Agent 循环，需在第七阶段按限速步骤受控联调。
- 简历仅能从已登记且平台页面中唯一匹配的附件中选择，不上传本地文件。
- 不接入日历。
- 新评分使用 LLM 七维输出；旧 `legacy` 评分只保留历史展示，不能授权新的自动动作。
- 自动招呼最低分配置不得低于80，自动简历最低分配置不得低于60。
- 千问已接入职位解析、评分、招呼、入站回复和简历反馈判断；真实平台写操作仍不会由本阶段直接执行。
- Agent 自动循环当前固定使用 Fake 执行器，不连接真实招聘网站；真实适配在后续阶段受控接入。

完整设计见 `docs/`。
