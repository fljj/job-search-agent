# job-search-agent

半自动求职 Agent。当前实现前两个阶段：职位策略与评分，以及候选人知识库、简历元数据、模拟消息分析、招呼语/回复草稿和人工确认决策。不连接真实招聘网站。

## 技术栈

- Python 3.13、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL
- React、TypeScript、Ant Design、Vite
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

- 不连接 BOSS 直聘或脉脉。
- 只处理模拟招聘消息，不读取或发送真实消息。
- 只管理简历元数据和候选选择，不上传或发送简历。
- 不接入日历。
- 不调用真实大模型；`FAKE_LLM` 仅用于验证适配器边界。

完整设计见 `docs/`。
