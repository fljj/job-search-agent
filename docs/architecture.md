# 系统架构设计

## 1. 架构目标

项目采用前后端分离的模块化单体架构。第一阶段只建设离线职位策略与评分闭环，后续领域模块按 `development-plan.md` 的阶段逐步加入。

架构必须保证：

- 确定性业务规则与外部平台操作分离；
- 大模型、招聘平台和日历均可替换、可模拟；
- 浏览器适配器不能自行作出业务授权决定；
- 核心决策具有版本、证据和审计记录；
- 不为尚未进入的阶段创建空模块。

## 2. 第一阶段项目目录

```text
job-search-agent/
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   ├── api/v1/
│   │   │   │   ├── profiles.py
│   │   │   │   ├── strategies.py
│   │   │   │   ├── jobs.py
│   │   │   │   └── scores.py
│   │   │   ├── core/
│   │   │   │   ├── config.py
│   │   │   │   └── database.py
│   │   │   ├── models/
│   │   │   ├── repositories/
│   │   │   ├── schemas/
│   │   │   ├── services/
│   │   │   └── main.py
│   │   ├── alembic/
│   │   ├── alembic.ini
│   │   └── pyproject.toml
│   └── web/
│       ├── src/
│       │   ├── api/
│       │   ├── components/
│       │   ├── pages/
│       │   │   ├── profile/
│       │   │   ├── strategies/
│       │   │   ├── job-import/
│       │   │   └── jobs/
│       │   ├── types/
│       │   ├── App.tsx
│       │   └── main.tsx
│       ├── package.json
│       ├── tsconfig.json
│       └── vite.config.ts
├── packages/
│   ├── job_parser/
│   │   ├── models.py
│   │   ├── normalizers.py
│   │   ├── rule_parser.py
│   │   └── service.py
│   └── scoring/
│       ├── dimensions/
│       │   ├── experience.py
│       │   ├── industry.py
│       │   ├── location.py
│       │   ├── management.py
│       │   ├── salary.py
│       │   ├── skills.py
│       │   └── title.py
│       ├── engine.py
│       ├── hard_filters.py
│       ├── models.py
│       └── reasons.py
├── adapters/
│   └── llm/
│       ├── base.py
│       └── fake.py
├── config/sample-data/
├── docs/
├── tests/
│   ├── integration/
│   └── unit/
├── AGENTS.md
├── README.md
└── docker-compose.yml
```

`conversation_agent`、`knowledge_base` 和 `resume_selector` 已在第二阶段实现。`browser_worker` 及 BOSS/脉脉只读适配器已在第三阶段实现；`scheduling` 和平台写操作适配器仍属后续阶段。

第二阶段新增目录：

```text
packages/
├── knowledge_base/      # 带来源和敏感度的事实检索
├── conversation_agent/ # 意图、草稿、置信度和权限决策
└── resume_selector/    # 按职位方向选择可用附件元数据
config/
└── conversation-policy.json
```

## 3. 模块职责

### 3.1 API 层

- 接收 HTTP 请求并执行 Pydantic 校验；
- 调用应用服务；
- 将领域错误映射为统一错误码；
- 不包含评分、权限或浏览器业务规则。

### 3.2 应用服务层

- 编排事务和领域模块；
- 处理策略版本冲突、批量导入和评分持久化；
- 不直接解析页面或实现维度评分公式。

### 3.3 Repository 层

- 封装 SQLAlchemy 查询和持久化；
- 提供必要的锁、唯一约束冲突转换和分页；
- 不作业务授权决定。

### 3.4 `job_parser`

- 规范化职位名称、技能、地点、工作模式和薪资；
- 从模拟 JD 提取结构化字段；
- 合并规则解析和受校验的模型输出；
- 返回解析事实、置信度和警告，不返回最终分数。

### 3.5 `scoring`

- 执行评分范围内的硬性排除；
- 计算七个维度分数；
- 汇总总分、等级和资格状态；
- 生成结构化理由与风险；
- 不处理重复发送、页面变化等动作阻断。

### 3.6 LLM 适配器

- 定义结构化解析端口；
- 第一阶段只提供本地假实现；
- 任何输出必须通过 Pydantic 模型和受控词典校验；
- 不接受或传播模型返回的分数、等级和权限结果。

### 3.7 后续领域模块

- `policy_engine`：统一返回 `ALLOW_AUTO/REQUIRE_CONFIRMATION/DENY`；
- `knowledge_base`：维护有来源和敏感度的用户事实；
- `conversation_agent`：意图识别、事实检索和回复草稿；
- `resume_selector`：选择附件并判断发送前置条件；
- `scheduling`：解析邀请、查询冲突和创建确认任务；
- `browser_worker`：读取页面或执行已授权操作；
- `audit`：追加记录决策、状态转换和外部结果。

## 4. 模块依赖

允许的依赖方向：

```text
Web UI → API
API → Application Services
Application Services → Domain Packages + Repositories
Domain Packages → Domain Models / Ports
Adapters → Domain Ports
Repositories → SQLAlchemy Models
```

禁止的依赖：

- 领域模块依赖 FastAPI 路由或 React；
- `scoring` 直接调用浏览器或日历；
- 浏览器适配器调用评分器后自行决定发送；
- LLM 适配器直接写业务表；
- 前端直接决定服务端权限状态。

## 5. 第一阶段核心流程

### 5.1 创建或编辑策略

```text
前端提交完整策略和 version
→ API 校验结构
→ 应用服务校验薪资区间、行业冲突和工作模式规则
→ Repository 条件更新
→ version + 1
→ 返回完整策略
```

旧版本更新返回 `409 VERSION_CONFLICT`。

### 5.2 导入模拟 JD

```text
校验原始输入
→ 计算来源键和规范化内容哈希
→ 检查唯一约束
→ 保存原始职位
→ 返回 CREATED 或 DUPLICATE
```

重复导入不会生成评分硬性排除。

### 5.3 解析并评分

```text
读取职位、策略和候选人资料
→ 规范化并生成解析记录
→ 判断职位生命周期
→ 执行评分硬性排除
→ 计算七个维度
→ 汇总等级和资格
→ 保存版本与输入快照
→ 返回评分详情
```

## 6. 后续自动操作流程

```text
产生动作候选
→ policy_engine 按固定顺序评估
→ DENY：记录原因并结束
→ REQUIRE_CONFIRMATION：创建确认任务
→ ALLOW_AUTO 或用户批准
→ 原子占用动作
→ browser_worker 执行前重新核对页面目标
→ 执行并回读结果
→ SUCCEEDED / FAILED_* / OUTCOME_UNKNOWN
→ 追加审计事件
```

## 7. 浏览器适配器与策略引擎边界

### 策略引擎负责

- 判断职位资格、动作开关、等级门槛和置信度；
- 判断是否敏感、是否需要确认、是否重复；
- 生成稳定原因编码和策略版本；
- 决定 `ALLOW_AUTO`、`REQUIRE_CONFIRMATION` 或 `DENY`。

### 浏览器适配器负责

- 读取页面并转为受校验的外部数据；
- 在执行前核对平台、公司、职位、招聘人、会话和附件；
- 执行已经授权且状态合法的动作；
- 返回外部 ID、页面证据或明确错误；
- 页面变化、登录异常或目标不匹配时停止。

### 浏览器适配器禁止

- 根据分数决定是否发送；
- 绕过或修改策略决策；
- 在确认过期后继续执行；
- 对 `OUTCOME_UNKNOWN` 直接重试；
- 自行替换发送内容或简历。

## 8. 事务与一致性边界

- 策略及其子规则在单个事务中保存；
- 批量 JD 每条独立报告结果，单条失败不回滚其他有效条目；
- 解析记录和评分记录采用新增版本，不覆盖历史记录；
- 外部浏览器调用不能包在长数据库事务中；动作先原子占用，外部执行后再持久化结果；
- 外部结果不明确时保存 `OUTCOME_UNKNOWN`，由对账流程处理。

## 9. 第二阶段草稿流程

```text
模拟消息 → 确定性多意图识别 → 只读检索当前有效知识项
→ 生成事实受限草稿 → 计算置信度和风险
→ policy decision → 必要时创建 PENDING_APPROVAL 确认任务
```

本阶段流程在决策和确认数据处终止，不依赖或调用 `browser_worker`。

## 10. 第三阶段只读浏览器流程

```text
用户手动登录并聚焦目标页
→ API 校验本机 CDP 端点
→ 适配器校验平台域名、登录/验证状态和页面根节点
→ 只读提取并通过 Pydantic 校验
→ 可选核对公司、职位或招聘人
→ 按来源 ID/内容指纹幂等导入
→ 保存读取状态和受控证据元数据
```

`adapters/browser` 仅实现定位、可见性、文本、属性和页面焦点读取。其公开边界不包含 `click/fill/type/upload`。选择器失效或目标不匹配时只记录失败状态，不进入业务导入。
