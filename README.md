# job-search-agent

半自动求职 Agent。当前实现前六个阶段：职位评分、知识库与沟通草稿、BOSS/脉脉只读接入、人工确认发送、安全自动沟通，以及电话/面试时间解析与本地假日历冲突检查。

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
4. `POST /api/v1/jobs/{job_id}/parse` 生成结构化解析记录。
5. `POST /api/v1/jobs/{job_id}/scores` 计算并保存评分。
6. `GET /api/v1/scores/{score_id}` 查看维度明细、排除原因和风险。
7. `POST /api/v1/knowledge-items` 录入有来源、敏感度和自动引用权限的事实。
8. `POST /api/v1/resumes` 登记网站内附件简历元数据。
9. `POST /api/v1/conversations` 和消息接口创建模拟对话。
10. `POST /api/v1/drafts/reply` 或 `/drafts/greeting` 生成草稿及权限决策。
11. `POST /api/v1/browser/read-current` 只读解析当前 BOSS/脉脉页面并幂等导入。
12. 在“人工确认与发送”页批准、修改或拒绝任务，再单独执行已批准动作。
13. 在“安全自动化”页按全局、平台或策略配置开关与阈值，通过 `/api/v1/automation/dispatch` 执行服务端授权。
14. 在“电话与面试安排”页解析招聘消息、检查日历、确认具体回复，并独立授权创建日历事件。

### 本机浏览器只读接入

由用户使用独立 Chromium/Chrome 配置目录手动启动调试端口并登录招聘平台，例如：

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir=/tmp/job-agent-browser-profile
```

然后手动打开职位详情或对话页，在前端“招聘网站只读”页执行读取。只允许 `localhost/127.0.0.1/::1` CDP 端点，不保存账号、密码或 Cookie。

前端提供候选人资料编辑、策略创建/编辑、模拟 JD 导入、解析、评分和结果明细查看。

示例候选人和职位数据位于 `config/sample-data/`。

## 验证

```bash
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

- BOSS 直聘和脉脉只允许用户主动触发的当前页读取，不做后台扫描。
- 只有人工确认且执行前复核通过的动作才能发送；没有自动发送。
- 简历仅能从已登记且平台页面中唯一匹配的附件中选择，不上传本地文件。
- 不接入日历。
- 当前评分仍是标记为 `legacy` 的旧规则评分，不能授权新的自动动作。
- 自动招呼最低分配置不得低于80，自动简历最低分配置不得低于60。
- 已提供千问 OpenAI 兼容适配器，但尚未接入职位评分和对话业务链路；只有手动冒烟命令会调用真实模型。

完整设计见 `docs/`。
