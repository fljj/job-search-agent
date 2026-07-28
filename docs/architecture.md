# 系统架构设计

## 1. 架构目标

项目采用前后端分离的模块化单体架构。当前实现已覆盖离线策略与评分、入站沟通、
浏览器 Worker、灰度治理和排期确认；各阶段边界以 `development-plan.md` 为准。

架构必须保证：

- 确定性业务规则与外部平台操作分离；
- 大模型、招聘平台和日历均可替换、可模拟；
- 浏览器适配器不能自行作出业务授权决定；
- 核心决策具有版本、证据和审计记录；
- 不为尚未进入的阶段创建空模块。

## 2. 当前项目目录

```text
job-search-agent/
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   ├── api/v1/
│   │   │   │   ├── actions.py
│   │   │   │   ├── automation.py
│   │   │   │   ├── conversations.py
│   │   │   │   ├── jobs.py
│   │   │   │   ├── recommendations.py
│   │   │   │   ├── scheduling.py
│   │   │   │   └── ...
│   │   │   ├── core/       # 配置、数据库及供应商装配
│   │   │   ├── models/     # SQLAlchemy 实体
│   │   │   ├── schemas/    # API 请求和响应模型
│   │   │   ├── services/   # 事务及业务流程编排
│   │   │   └── main.py
│   │   ├── alembic/
│   │   ├── alembic.ini
│   │   └── ...
│   └── web/
│       ├── src/
│       │   ├── api/
│       │   ├── components/
│       │   ├── pages/
│       │   │   ├── OverviewPage.tsx
│       │   │   ├── JobPage.tsx
│       │   │   ├── MessagePage.tsx
│       │   │   ├── SchedulingPage.tsx
│       │   │   └── ...
│       │   ├── App.tsx
│       │   └── main.tsx
│       ├── package.json
│       └── vite.config.ts
├── packages/
│   ├── audit/
│   ├── browser_worker/
│   ├── conversation_agent/
│   ├── job_parser/
│   ├── knowledge_base/
│   ├── llm/
│   ├── policy_engine/
│   ├── resume_selector/
│   ├── scheduling/
│   └── scoring/
├── adapters/
│   ├── browser/
│   ├── calendar/
│   └── llm/
├── config/
│   ├── browser-selectors.json
│   ├── conversation-policy.json
│   ├── job-parser.json
│   ├── recommendation-policy.json
│   ├── scheduling-policy.json
│   └── sample-data/
├── docs/
├── scripts/
├── tests/
│   ├── integration/
│   └── unit/
├── AGENTS.md
├── README.md
└── docker-compose.yml
```

统一 LLM 领域端口和智谱/千问 OpenAI 兼容适配器已接入职位评分、招呼、入站回复和
简历反馈判断；当前灰度使用智谱 GLM-5.2，所有模型输出仍需经过领域规则校验。

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

### 3.3 持久化层

- 当前由应用服务通过 SQLAlchemy 2 查询和持久化，不额外保留空的 Repository 层；
- 使用数据库唯一约束、事务和行锁实现一致性；
- 持久化代码不作浏览器写操作授权决定。

### 3.4 `job_parser`

- 规范化职位名称、技能、地点、工作模式和薪资；
- 从模拟 JD 提取结构化字段；
- 合并规则解析和受校验的模型输出；
- 返回解析事实、置信度和警告，不返回最终分数。

### 3.5 `scoring`

- 执行评分范围内的硬性排除；
- 在调用模型前为评分输入生成稳定条目级证据目录；
- 编排 LLM 七维评分并校验结构、范围、求和、证据存在性及维度归属；
- 根据模型分数计算等级，并结合模型建议和 80 分门槛形成主动沟通候选；
- 保存结构化理由、风险和模型调用元数据；
- 不处理重复发送、页面变化等动作阻断。

### 3.6 LLM 适配器

- `packages/llm` 定义供应商无关的解析、评分、消息意图、招呼、回复和对话评估端口；
- 提供智谱、千问 OpenAI 兼容适配器及本地假实现；
- 适配器负责 Pydantic 结构校验；维度上限、求和、条目证据和事实真实性等业务校验由
  评分或对话领域执行；
- 适配器只返回模型结果，不写业务表、不执行浏览器动作；切换供应商不影响领域层。

### 3.7 领域模块

- `policy_engine`：统一返回 `ALLOW_AUTO/REQUIRE_CONFIRMATION/DENY`；
- `knowledge_base`：维护有来源和敏感度的用户事实；
- `conversation_agent`：意图识别、事实检索和回复草稿；
- `resume_selector`：选择附件并判断发送前置条件；
- `scheduling`：解析邀请、查询冲突和创建确认任务；
- `browser_worker`：读取页面或执行已授权操作；
- `audit`：追加记录决策、状态转换和外部结果。

### 3.8 第十阶段职位发现模块

- `adapters/browser/job_discovery.py`：只负责读取当前唯一 BOSS 职位列表、按配置切换
  搜索入口、完整轮询后刷新页面、驱动虚拟滚动、
  在新标签打开详情并复核外部职位 ID、公司和标题；不评分、不授权发送。
- `job_discovery_service`：持久化发现记录，编排导入、解析、评分、去重、冷却和主动招呼
  授权，并推进职位发现游标。
- `telegram_jobs`：只读轮询白名单招聘频道，以频道 ID 和消息 ID 去重，提取完整帖子与
  唯一 Telegram 联系人；通过 Telegram 原生会话入口切换频道，不读取 Cookie、账号密码
  或 Telegram 内部存储。
- `automation_service`：在发送前统一复核分数、硬排除、模型建议、猎头封顶、安全字段、
  开关、限速和幂等指纹。
- `run_agent_worker.py`：串联全量会话消息发现与职位发现；已读但最后由招聘方发言的
  会话仍按消息证据和幂等记录处理，页面结构不符合预期时暂停对应平台运行。

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
- Worker 使用独立线程和独立数据库会话定时更新实例心跳，LLM 调用或单轮任务较慢时
  不依赖主循环结束才续报状态；退出时先停止心跳线程再登记停止状态。
- `reconciliation_tasks` 只调用浏览器只读观察能力，不能授权或重发动作；超时转人工。
- `packages/audit/redaction.py`：日志输出前移除 Bearer Token、API Key 和数据库密码。
- `packages/audit/gray_logging.py`：将结构化灰度事件轮转写入配置目录，并复用脱敏过滤器。

## 4. 模块依赖

允许的依赖方向：

```text
Web UI → API
API → Application Services
Application Services → Domain Packages + Repositories
Domain Packages → Domain Models / Ports
Adapters → Domain Ports
Application Services → SQLAlchemy Models
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
→ 生成来源路径、具体 JSON 值、允许维度和稳定 ID 组成的证据目录
→ 调用 LLM 生成七维评分、条目证据 ID 和主动沟通建议
→ 校验分值、证据存在性和维度归属并由程序计算总分、等级和自动化资格
→ 保存模型、提示词、版本与输入快照
→ 返回评分详情
```

证据目录与完整评分上下文共同保存在 `job_scores.input_snapshot`。历史审计从快照恢复
`ScoringContext` 后重新生成目录并逐项比较；目录与快照不一致时不能视为可复现评分。

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
→ 当前配置的 LLM 结构化解析和七维评分
→ 程序执行硬排除、分值校验、猎头封顶和去重/冷却
→ policy_engine 授权
→ browser_worker 执行并回读平台默认招呼
→ 保存动作结果、审计事件和下一游标
```

## 7. 浏览器适配器与策略引擎边界

### 策略引擎负责

- 判断主动招呼的 80 分门槛，以及入站对话的资格成熟度、动作开关和模型建议；
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
入站消息 → 程序初步识别意图 → 更新 UNKNOWN / ROUGH_MATCH / FULL_MATCH / MISMATCH
→ 在存在有效职位评分和 LLM 时生成事实受限回复，否则使用确定性安全澄清
→ MISMATCH 生成婉拒；否则检索当前有效知识项并继续沟通
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
→ 主动动作检查 80 分；入站动作检查成熟度、明确请求、事实、附件和重复记录
→ 检查平台小时/每日限额
→ DENY：记录策略决策，不执行
→ REQUIRE_CONFIRMATION：仅时间或日历动作进入确认
→ ALLOW_AUTO：创建 authorization_source=AUTO 的唯一动作
→ 复用第四阶段原子占用和浏览器执行前复核
→ 异常或身份不一致：停止动作并暂停对应平台
```

自动化服务负责授权和调度，`playwright_actions` 仍只接收已经授权的单个命令。配置
不能绕过 80 分主动招呼、`MISMATCH` 停止推进、电话具体时间确认、面试必须
`FULL_MATCH`、面试具体时间确认、事实真实性、敏感信息禁止披露、页面身份复核和
`OUTCOME_UNKNOWN` 禁止重试等不变量。

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
职位详情标签页。`message_discovery_service` 负责稳定身份、资格成熟度、可选职位绑定、
会话短租约、消息幂等导入、游标和状态审计。职位暂时不能唯一绑定时允许保留
`UNKNOWN` 对话并澄清，不再仅因缺少评分暂停；身份不一致或结果未知时仍必须暂停。
浏览器适配器无权猜测绑定或发送。

BOSS 和脉脉普通私信共用上述服务层流程和 `message_discovery` 游标，但使用各自带版本
的页面选择器。脉脉适配器从 `data-msg` 提取稳定会话 ID，并以最后消息 ID 或受控预览
指纹去重；官方账号和系统推荐卡片在普通消息入口排除。脉脉扫描异常只暂停对应的
MAIMAI 运行，不修改 BOSS 运行状态。系统推荐开关只控制后续独立推荐流程，不能关闭
普通入站消息发现。

脉脉系统推荐使用独立流程，不能伪装成普通消息或正式职位评分：

```text
虚拟消息列表扫描
→ 跳过官方通知和已处理推荐
→ 读取推荐卡片并生成稳定推荐 ID
→ recommendation_policy 执行受控简化判断
→ ACCEPT_AND_SEND_PROFILE / REJECT_RECOMMENDATION / DENY
→ recommendation_service 在灰度与双开关允许后原子创建唯一动作
→ maimai_adapter 重新核对招聘人、岗位和唯一“同意/拒绝”控件
→ 点击并回读已发送/已拒绝证据
→ 成功追加审计；未知进入对账
→ 后续真人回复转入通用入站流程，更新资格成熟度并逐步补全 JD
```

`maimai_adapter` 只负责真实列表、推荐卡片、控件和结果读取，以及执行已授权的单个
点击；它不得判断岗位是否相关。`recommendation_policy` 只根据受控词表、策略黑名单
和卡片结构化字段决策，不操作页面。普通 `scoring` 模块不为信息不足的推荐卡片生成
虚假分数。

Worker 仅在 MAIMAI 运行、全局/平台/策略自动化均启用、推荐开关开启且灰度达到第五级
时执行推荐动作。同意动作还要求推荐简历开关；扫描、判断和动作执行通过稳定推荐 ID
及 `ActionQueue` 唯一指纹去重，结果未知只能进入只读对账。

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
- 消息中心展示资格成熟度、可选绑定职位和评分、不匹配婉拒及简历动作结果；
- 职位中心独立展示主动沟通动作和会话同步状态；已有会话时可跳转消息中心，消息中心
  也可按 `job_id` 返回对应职位，避免把“评分通过”误解为“消息已发送”；
- 总览和系统设置展示脱敏 LLM 配置状态、运行心跳、限速、暂停原因和自动动作审计；
- 电话及面试时间继续使用独立确认页，不与普通自动动作混合；
- 加载控制台、刷新列表或切换页面不会触发浏览器写操作。
