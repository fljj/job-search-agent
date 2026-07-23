# job-search-agent

无人值守求职 Agent。系统自动发现和评分职位、主动沟通、处理普通招聘消息，并根据
对话证据发送网站附件简历；电话与面试具体时间、安全异常和平台验证仍由用户处理。

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

`.env.example` 默认配置智谱 `glm-5.2`。`LLM_PROVIDER=FAKE` 可完全离线运行；使用
智谱时只将 `ZHIPU_API_KEY` 写入本机 `.env`，不得提交。智谱模式不会读取或发送既有
千问 `LLM_API_KEY`。

Mac 默认使用 `CALENDAR_PROVIDER=APPLE`，通过系统 Calendar 读取所有日历的忙碌时间，
并把用户明确授权的面试事件写入 `APPLE_CALENDAR_NAME` 指定的日历。首次访问时 macOS
会请求“自动化/日历”权限；拒绝权限、目标日历不存在或 Calendar 不可用时，系统统一
返回日历不可用。也可以改为 `MOCK` 做本地测试，或配置 `GOOGLE` 供应商。

统一 LLM 适配器的默认测试不会访问网络。配置密钥后可显式执行一次模型消息分类冒烟：

```bash
python scripts/smoke_llm.py
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
16. 真实 BOSS 无人值守必须先在“安全自动化”页初始化灰度。从一级只读消息开始，
    每级至少运行 24 小时且全部安全指标为零后，才可人工升级一级；指标失败会自动
    回退一级，一级失败则暂停。

本地短轮询 Worker 可独立启动。执行器必须通过 `AGENT_EXECUTOR_MODE` 显式隔离：
真实 BOSS 使用 `REAL`，离线 `MOCK` 测试使用 `FAKE`，配置交叉时 Worker 拒绝执行。
BOSS 平台会通过本机 CDP 自动扫描唯一的消息列表页，按未读会话切换并复核会话 ID、
招聘人、公司和职位，只有成功绑定当前策略有效评分后才导入消息和执行普通回复。
列表游标、最后消息标识及最多 500 个去重键保存在 Agent 运行记录中；虚拟滚动会在
后续轮询继续加载。启用“主动扫描职位”后，Worker 还会读取唯一的 BOSS 职位搜索列表，
逐个新标签核验详情，并执行导入、GLM 评分、程序授权、去重/冷却和主动招呼。职位扫描
仅在配置的工作时段和限额内运行，紧急停止优先阻断所有自动动作。Worker 使用本机进程
锁避免重复启动，并记录执行器类型。专用浏览器需同时保留一个消息列表页和一个职位
搜索列表页：

```bash
python scripts/run_agent_worker.py
```

Worker 启动时检查数据库迁移、LLM、选择器、执行器，以及真实运行需要的 CDP 和登录
会话；同时登记 PID、心跳和停止状态。结果未知动作自动进入只读对账队列，持续未知超过
配置时限后升级人工处理。Agent 控制台展示自检、Worker、游标、未知动作和审计差异。
灰度日志默认轮转写入 `~/Desktop/job-search-agent-gray/logs/agent-gray.log`，记录
Worker 周期、消息/职位扫描数量、动作计数、暂停原因及灰度状态，不记录消息正文、
Cookie 或密钥。

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

正式运行由 Worker 自动读取职位与消息列表。底层只读 API 仍用于测试和诊断，但不再
作为正式前端页面；只允许 `localhost/127.0.0.1/::1` CDP 端点，不保存账号、密码或 Cookie。

前端采用“总览、职位中心、消息中心、面试确认、求职策略、候选人中心、系统设置”
导航。总览展示 Agent、Worker、灰度和安全指标；职位与消息中心用于观察自动化结果；
候选人中心统一维护资料、可信知识和网站附件简历；只有电话与面试具体时间进入确认页。
系统设置只显示 LLM 配置状态，不返回密钥。
控制台同时展示 BOSS 六级灰度、升级剩余时间和安全指标。未初始化或已暂停灰度时，
服务端拒绝真实平台写操作；前端开关不能绕过该门禁。灰度一至六依次开放消息只读、
职位只读评分、每日最多 5 个普通回复、每日最多 3 个主动招呼、明确索要后的简历发送
和正式配置限额。

脉脉系统推荐由独立流程处理：只扫描带未读标记的系统推荐卡片，跳过官方通知；程序
按 `config/recommendation-policy.json` 的受控词表判断同意或拒绝。执行前会重新核对
招聘人、岗位和唯一按钮，执行后必须回读平台成功证据。启用前需要在全局自动化配置
中同时设置 `maimai_recommendation_enabled=true`；同意并发送平台资料还需设置
`maimai_recommendation_resume_enabled=true`，并将 MAIMAI 灰度推进到第五级。

推荐记录可通过以下接口查询和受控操作：

- `GET /api/v1/platform-recommendations`
- `POST /api/v1/platform-recommendations/scan`
- `POST /api/v1/platform-recommendations/{id}/dispatch`
- `POST /api/v1/platform-recommendations/{id}/reconcile`

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

### 备份与恢复演练

备份文件可能包含个人信息，默认目录已被 Git 忽略，文件权限设为仅当前用户：

```bash
DATABASE_URL='postgresql+psycopg://...' scripts/backup_database.sh
```

恢复脚本只允许目标数据库名称包含 `_restore_test`：

```bash
RESTORE_DATABASE_URL='postgresql+psycopg://.../job_agent_restore_test' \
  scripts/restore_rehearsal.sh backups/job-search-agent-YYYYMMDD-HHMMSS.dump
```

灰度前清理运行历史但保留候选人、策略、知识库、附件简历和自动化配置：

```bash
python scripts/reset_gray_data.py --confirm-database job_agent
python scripts/reset_gray_data.py --confirm-database job_agent --execute
```

第一条命令只预览。第二条会清除职位、评分、会话、动作、审计、Worker、浏览器证据和
排期历史，并重建暂停的 BOSS 一级灰度。

## 当前范围限制

- BOSS 已覆盖职位列表/详情、对话列表/详情、文本及站内附件简历的本地夹具；脉脉已
  覆盖系统推荐卡片识别、同意/拒绝、回读和对账，普通职位列表主动发现仍未实现。
- 第六阶段默认测试只使用脱敏 HTML 和本地无头 Chromium，不登录或操作真实账号。
- BOSS 消息和职位发现已接入 Agent 循环；六级灰度控制已实现，但真实无人值守仍须
  逐级完成完整工作日、100 个真实职位、20 个真实会话以及 8/24 小时耐久验收。
- 控制台默认联调限速为每小时 1 次、每日 3 次；真实联调必须按 `docs/development-plan.md` 的顺序逐步人工放行。
- 简历仅能从已登记且平台页面中唯一匹配的附件中选择，不上传本地文件。
- Mac 默认接入 Apple Calendar；Google Calendar 和本地 `MOCK` 可通过环境变量切换。
  Apple 目标写入日历不存在或系统权限未授予时安全降级为不可用。
- 新评分使用 LLM 七维输出；旧 `legacy` 评分只保留历史展示，不能授权新的自动动作。
- 自动招呼最低分配置不得低于80，自动简历最低分配置不得低于60。
- 阶段十四仍需完成至少 20 条真实脉脉推荐的小流量灰度；普通真人入站消息的跨平台
  资格成熟度流程属于阶段十五。
- 策略可配置最高 79 分的猎头岗位分数封顶，使猎头岗位可接收回复但不主动招呼。
- 智谱 GLM-5.2 已接入职位解析、评分、招呼、入站回复和简历反馈判断。首次真实招呼必须先创建
  人工确认任务，批准后再单独执行；执行前会复核职位、公司、招聘人、开放状态和
  外部职位 ID，并使用幂等指纹防止重复发送。
- BOSS 正式运行只允许 `REAL` 执行器，`MOCK` 离线测试只允许 `FAKE`；两者交叉配置时
  Worker 会拒绝运行。

完整设计见 `docs/`。
