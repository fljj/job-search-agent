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
│   ├── llm/
│   │   ├── models.py
│   │   └── ports.py
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
│       ├── llm_engine.py
│       ├── models.py
│       ├── reasons.py
│       └── strategy_selector.py
├── adapters/
│   └── llm/
│       ├── base.py
│       ├── errors.py
│       ├── fake.py
│       ├── http.py
│       └── qwen.py
├── config/sample-data/
├── docs/
├── tests/
│   ├── integration/
│   └── unit/
├── AGENTS.md
├── README.md
└── docker-compose.yml
```

统一 LLM 领域端口和千问适配器已接入职位评分、招呼、入站回复和简历反馈判断；所有模型输出仍需经过领域规则校验后才能形成动作授权。

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
- `agent_service` 使用 PostgreSQL 短租约编排单次轮询、暂停、恢复和熔断；不在 API 请求内启动常驻线程；
- `rollout_service` 在真实写操作和职位扫描前执行六级灰度门禁，计算零容忍安全指标，
  校验逐日人工升级，并在指标失败时自动回退或暂停；
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
- 编排 LLM 七维评分并校验结构、范围、求和和证据；
- 根据模型分数计算等级，并结合模型建议和 80 分门槛形成主动沟通候选；
- 保存结构化理由、风险和模型调用元数据；
- 不处理重复发送、页面变化等动作阻断。

### 3.6 LLM 适配器

- `packages/llm` 定义供应商无关的解析、评分、消息意图、招呼、回复和对话评估端口；
- 开发测试阶段提供千问 OpenAI 兼容适配器及本地假实现；
- 适配器负责 Pydantic 结构校验；维度上限、求和和事实证据等业务校验由评分或对话领域在后续阶段执行；
- 适配器只返回模型结果，不写业务表、不执行浏览器动作；切换智谱不影响领域层。

### 3.7 后续领域模块

- `policy_engine`：统一返回 `ALLOW_AUTO/REQUIRE_CONFIRMATION/DENY`；
- `knowledge_base`：维护有来源和敏感度的用户事实；
- `conversation_agent`：意图识别、事实检索和回复草稿；
- `resume_selector`：选择附件并判断发送前置条件；
- `scheduling`：解析邀请、查询冲突和创建确认任务；
- `browser_worker`：读取页面或执行已授权操作；
- `audit`：追加记录决策、状态转换和外部结果。

### 3.8 第十阶段职位发现模块

- `adapters/browser/job_discovery.py`：只负责读取当前唯一 BOSS 职位列表、驱动虚拟滚动、
  在新标签打开详情并复核外部职位 ID、公司和标题；不评分、不授权发送。
- `job_discovery_service`：持久化发现记录，编排导入、解析、评分、去重、冷却和主动招呼
  授权，并推进职位发现游标。
- `automation_service`：在发送前统一复核分数、硬排除、模型建议、猎头封顶、安全字段、
  开关、限速和幂等指纹。
- `run_agent_worker.py`：串联消息发现与职位发现；页面结构不符合预期时暂停对应平台运行。

### 3.9 第十一阶段日历与后半程沟通

- `packages/scheduling/calendar.py`：定义忙闲查询和事件创建的供应商无关端口。
- `adapters/calendar/apple.py`：通过 macOS JXA 调用系统 Calendar；只返回忙闲区间，
  事件写入需要上层已经取得独立授权。
- `adapters/calendar/google.py`：调用 Google Calendar FreeBusy 和 Events API；不参与
  邀请解析、冲突判断或确认授权。
- `scheduling_service`：解析邀请、合并本地/真实忙碌时段、创建确认任务、处理替代/拒绝，
  并在发送前重新查询冲突。
- `agent_service`：时间意图转交排期服务；其他入站消息同时评估明确索要简历和有证据的
  积极反馈。

### 3.10 第十二阶段运行治理

- `operations_service`：Worker 登记/心跳/接管、启动自检、自动对账队列、状态聚合和
  审计差异检查。
- `worker_instances` 与本机 `flock` 共同限制单实例；`agent_runs` 短租约限制具体运行
  的并发处理。
- `reconciliation_tasks` 只调用浏览器只读观察能力，不能授权或重发动作；超时转人工。
- `packages/audit/redaction.py`：日志输出前移除 Bearer Token、API Key 和数据库密码。

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

JD、招聘方消息和网页文本均视为不可信数据，不得与系统指令拼接成可执行提示。LLM 适配器必须使用固定系统提示、结构化数据边界和输出模型，忽略外部文本中要求泄露提示词、修改分数规则或执行工具的指令。模型不获得浏览器、数据库或文件工具权限。

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
→ 调用 LLM 生成七维评分、证据和主动沟通建议
→ 校验分值并由程序计算总分、等级和自动化资格
→ 保存模型、提示词、版本与输入快照
→ 返回评分详情
```

## 6. 后续自动操作流程

```text
产生动作候选
→ policy_engine 按固定顺序评估
→ DENY：记录原因并结束
→ REQUIRE_CONFIRMATION：仅电话/面试时间或日历写操作创建确认任务
→ ALLOW_AUTO 或用户批准
→ 原子占用动作
→ browser_worker 执行前重新核对页面目标
→ 执行并回读结果
→ SUCCEEDED / FAILED_* / OUTCOME_UNKNOWN
→ 追加审计事件
```

第十阶段主动职位流程：

```text
读取当前搜索列表和断点游标
→ 新标签打开详情并复核身份
→ 记录发现项并执行安全字段检查
→ 幂等导入 JD
→ 千问结构化解析和七维评分
→ 程序执行硬排除、分值校验、猎头封顶和去重/冷却
→ policy_engine 授权
→ browser_worker 执行并回读平台默认招呼
→ 保存动作结果、审计事件和下一游标
```

## 7. 浏览器适配器与策略引擎边界

### 策略引擎负责

- 判断职位资格、动作开关、80/60 分动作门槛和模型建议；
- 判断是否需要时间确认、是否重复以及内容是否允许发送；
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
模拟消息 → LLM 结构化多意图识别 → 读取绑定策略的最新有效职位评分
→ 低于 60 分生成婉拒；否则检索当前有效知识项
→ 生成事实受限草稿或简历发送建议 → 程序执行权限与证据校验
→ 仅电话/面试具体时间创建 PENDING_APPROVAL 确认任务
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

`playwright_reader` 仅实现定位、可见性、文本、属性和页面焦点读取，不包含 `click/fill/type/upload`。第四阶段的有限写操作单独位于 `playwright_actions`，只接收已批准命令。选择器失效或目标不匹配时只记录失败状态。

职位详情读取以可见且可用的沟通入口判断 `OPEN`，以关闭或失效标记判断
`CLOSED`；两者均无法确认时保持 `UNKNOWN`。职位外部 ID 优先读取页面属性，
缺失时从详情页 URL 的稳定路径段提取。只有 `OPEN` 可以进入自动沟通资格判断。

BOSS 真实职位页在 Playwright 通过 CDP 附加时可能触发平台重定向，因此真实首次招呼
使用原生 CDP 完成同等的目标复核、点击、输入、发送和结果回读；Playwright 执行路径
保留用于本地脱敏夹具。两条路径接收相同的已批准命令，不得绕过策略、幂等或审计。

## 10.1 第六阶段 BOSS 模拟适配流程

```text
职位列表（列表项 + next cursor）→ 职位详情（职位/公司/招聘人身份）
对话列表（未读数 + next cursor）→ 对话详情（消息 ID/方向/时间）
→ 服务端策略产生 ApprovedCommand
→ playwright_actions 执行前重新读取并核对目标
→ 本地夹具模拟文本或站内附件发送
→ 回读出站消息/简历节点 → SUCCEEDED 或 OUTCOME_UNKNOWN
```

选择器集中在带版本号的 `config/browser-selectors.json`。`playwright_actions.execute_on_page`
是受控 CDP 和本地夹具共用的单动作执行边界；它不做评分或授权。页面根节点、
必填字段、消息时间或目标身份变化时，适配器返回安全失败且不执行写操作。

## 11. 第四阶段人工执行流程

```text
草稿/附件候选 → PENDING_APPROVAL
→ 用户拒绝：CANCELLED
→ 用户修改：敏感复检 → 旧任务 SUPERSEDED → 新任务 PENDING_APPROVAL
→ 用户批准：APPROVED
→ 单独执行请求原子占用：EXECUTING
→ 页面、公司、职位、招聘人、对话、内容/附件复核
→ SUCCEEDED / FAILED_RETRYABLE / FAILED_FINAL / OUTCOME_UNKNOWN
```

`policy_engine` 控制状态转换和权限；`action_service` 原子占用并持久化尝试/审计；`playwright_actions` 只执行已批准命令，不自行判断是否应该发送。

第十三阶段在既有权限判断之后、创建真实动作之前增加供应商无关的 `rollout` 规则。
依赖方向为 `automation_service/job worker → rollout_service → policy_engine.rollout`。
浏览器适配器不知道当前灰度级别，也不能提升限额；它只执行已同时通过自动化策略与
灰度门禁的命令。职位扫描在 Worker 进入页面适配器前检查灰度，一级不会读取职位列表。

## 12. 第五阶段安全自动化流程

```text
LLM 评分、草稿或简历候选
→ 合并全局、平台、策略配置（只允许逐级收紧）
→ 检查资格、状态、80/60 分阈值、模型建议、事实、附件和重复记录
→ 检查平台小时/每日限额
→ DENY：记录策略决策，不执行
→ REQUIRE_CONFIRMATION：仅时间或日历动作进入确认
→ ALLOW_AUTO：创建 authorization_source=AUTO 的唯一动作
→ 复用第四阶段原子占用和浏览器执行前复核
→ 异常或身份不一致：停止动作并暂停对应平台
```

自动化服务负责授权和调度，`playwright_actions` 仍只接收已经授权的单个命令。配置不能绕过硬性排除、80 分主动沟通、60 分婉拒/简历边界、具体时间确认、事实真实性、敏感信息禁止披露、页面身份复核和 `OUTCOME_UNKNOWN` 禁止重试等不变量。

Worker 启动时按平台和 `AGENT_EXECUTOR_MODE` 构造执行器：BOSS 只能使用
`REAL_CDP`，MOCK 只能使用 `FAKE`，并将实际类型写入运行记录。进程锁负责阻止本机
第二个 Worker 同时运行；数据库租约继续负责动作级并发保护。执行器只执行已授权
命令和只读回读，不参与评分、回复决策或重试授权。`OUTCOME_UNKNOWN` 由服务层对账
接口持有动作行锁后调用执行器只读观察，再由服务层决定成功、确认未发送或继续未知。

第九阶段在 Worker 的普通决策循环之前增加消息发现编排：

```text
LIST_READY → OPENING_CONVERSATION → VERIFYING_TARGET → READING_MESSAGES
→ BINDING_JOB → DECIDING → RETURNING_TO_LIST
```

`message_discovery` 适配器只负责列表滚动、点击会话、读取详情、必要时只读打开并关闭
职位详情标签页。`message_discovery_service` 负责稳定职位 ID/公司标题唯一绑定、当前
策略评分校验、会话短租约、消息幂等导入、游标和状态审计。身份不一致、职位不能唯一
绑定、评分版本过期或结果未知时仅暂停当前会话；浏览器适配器无权猜测绑定或发送。

## 13. 第六阶段排期流程

```text
招聘方时间消息 → 确定性解析事件类型、日期、时间、时区和时长
→ 读取日历忙碌事件与排期配置 → 缓冲/午休/工作时间/通勤冲突检查
→ AVAILABLE / CONFLICT / AMBIGUOUS / INCOMPLETE / UNAVAILABLE
→ 生成确认回复、候选时间或澄清问题 → PENDING_APPROVAL
→ 用户确认具体回复，并独立选择是否创建日历事件
→ 发送前验证日历快照和有效期 → 新冲突则退回 PENDING_APPROVAL
→ 复用第四阶段浏览器安全发送 → 成功后按独立授权创建日历事件
```

Mac 默认通过 Apple Calendar 适配器查询忙闲和创建已授权事件；也可以切换为 Google
Calendar 或数据库中的本地假事件。日历读取和写入均由应用服务编排；时间解析器和
冲突引擎不依赖 FastAPI、浏览器或具体日历供应商。

## 13. 无人值守前端控制台

前端使用侧边导航，页面模块通过 `React.lazy` 动态加载，只有当前活动页面进入渲染树。
正式导航固定为：

- 总览：Agent、Worker、灰度、安全指标和异常摘要；
- 职位中心：自动发现、评分、硬排除、动作资格和评分证据；
- 消息中心：会话、Agent 决策、自动回复及简历发送证据；
- 面试确认：唯一常规人工确认入口；
- 求职策略；
- 候选人中心：资料、自动回复知识库和网站附件简历；
- 系统设置：运行、自动化配置、平台状态、灰度、对账和审计。

普通动作人工确认、手动读取当前招聘页面和模拟沟通不属于正式信息架构。底层 API 与
测试夹具可继续用于开发诊断，但不在生产导航中暴露。
控制台只调用服务端聚合接口，不在浏览器内重新计算评分、自动化权限或动作状态。

- 职位中心结构化展示模型版本、七维证据、模型主动沟通建议和程序 80 分授权；
- 消息中心展示绑定职位、最新评分、低分婉拒和简历动作结果；
- 总览和系统设置展示脱敏 LLM 配置状态、运行心跳、限速、暂停原因和自动动作审计；
- 电话及面试时间继续使用独立确认页，不与普通自动动作混合；
- 加载控制台、刷新列表或切换页面不会触发浏览器写操作。
