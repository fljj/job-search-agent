# 当前数据模型设计

## 1. 通用约定

- PostgreSQL 是唯一业务数据库；主键使用 UUID，时间使用带时区时间戳。
- 金额和分数使用定点数；外部原始输入、规范化结果和决策快照分别保存。
- 用户可编辑对象使用 `version` 乐观锁；审计和尝试记录只追加。
- ORM 变化必须创建 Alembic 迁移，并从空 PostgreSQL 数据库验证完整迁移链。
- JSONB 只保存结构化快照和原因，不保存密码、Cookie、Token 或无必要的完整页面内容。

## 2. 用户、候选人和策略

### `users`

单用户所有权根。所有业务资源直接或通过父实体归属用户。

### `candidate_profiles`

保存姓名、工作与管理年限、架构/核心系统经验、学历培养形式、版本及时间。关联：

- `candidate_skills`：技能名、年限、来源、是否核心；
- `candidate_industry_experiences`：候选人真实行业经历。

### `job_strategies`

保存策略名称、优先级、启用状态、兼职、学历、到岗口径、有效期和版本。子表：

- `job_title_rules`：岗位方向规则；
- `work_mode_rules` / `work_mode_locations`：远程、现场、混合及允许地点；
- `salary_rules` / `salary_score_bands`：币种、最低/期望薪资和评分带；
- `industry_rules`：偏好或排除行业；
- `company_blacklists`：公司黑名单。

策略绑定一个候选人资料；同一用户的启用策略按优先级选择，评分保存策略和资料版本。

## 3. 职位、解析和评分

### `jobs` 与 `job_observations`

`jobs` 保存平台、外部职位 ID、规范化原职位链接 `source_url`、链接最后观测时间
`source_url_observed_at`、内容哈希、标题、公司、地点、工作模式、薪资、正文、状态、
结构化招聘人身份 `recruiter_role` 及原始结构。招聘人身份取值为
`HEADHUNTER / DIRECT_EMPLOYER / UNKNOWN`；旧数据默认 `UNKNOWN`，不能根据姓名猜测身份。
平台外部 ID 和内容哈希用于幂等；URL 可变且不参与唯一性或内容哈希。URL 只允许
对应招聘平台域名，并移除跟踪参数和 fragment。正文变化更新当前职位，并在
`job_observations(job_id, content_hash)` 追加唯一快照。

### `parsed_job_details`

保存 RULE/LLM 解析结果、技能、职责、招聘身份、兼职/现场要求、置信度、警告、输入指纹、
provider/model/prompt/schema 版本及 `llm_invocation_id`。只有输入和全部版本一致才允许复用。

### `job_scores`

一条评分绑定职位、策略、候选人和解析版本，保存：

- 七个维度分、总分、A/B/C 等级；
- `eligibility`、`hard_rejected`、职位有效状态和动作阻断；
- LLM 主动沟通建议、匹配理由、风险提示；
- 输入指纹、规则/模型/提示版本和输入快照。

子表 `job_score_details` 保存每维证据和解释，`job_rejections` 保存硬性排除原因。
`llm_invocations` 只追加模型调用元数据、输入哈希、状态和类型化错误。

## 4. 对话、知识和简历

### `knowledge_items`

可信事实包含分类、键、事实、来源、敏感级别、是否允许自动引用、核验/失效时间和版本。

### `resumes`

保存平台内附件名称、适用方向、默认标记、来源及版本。系统不保存任意本地上传文件。

### `conversations` 与 `messages`

会话绑定平台、招聘人、可空职位、当前策略/评分、资格状态、episode、终态证据和身份可靠性。
消息绑定会话和外部消息 ID，保存方向、正文、接收时间、处理状态、重试、错误和身份快照。
平台发现同时持久化 `INBOUND` 与 `OUTBOUND`；同一会话和 episode 中，源入站消息之后的
`OUTBOUND` 记录是抑制历史草稿再次调度的对账证据。
新的 MAIMAI 会话和消息不再写入这两张表；存量脉脉记录仅作审计保留，
查询和 Worker 调度均排除这些记录。脉脉推荐继续使用 `platform_recommendations`、
`action_queue` 和审计表，不转换为普通会话。

`(conversation_id, external_message_id)` 唯一。消息状态数据库约束为：

`RECEIVED / AWAITING_IDENTITY / SUPERSEDED / PROCESSING / RETRY_WAIT /
WAITING_FOR_LLM / QUARANTINED / COMPLETED / MISMATCH_DECLINED`。

### `generated_drafts`

保存 `GREETING/REPLY/RESUME/MISMATCH_DECLINE` 草稿、内容、事实 ID、输入指纹、版本、决策和
`RULE_TEMPLATE/KNOWLEDGE_BASE/LLM/HUMAN` 回复来源。`dispatch_enabled` 是持久化派发边界：
猎聘 L3 只读流程生成的草稿固定为 `false`，不能在当前或未来阶段进入动作队列；历史数据迁移
默认保持 `true`。L4 代码不会修改该历史标志；只有发布开关已授权时，基于新入站消息新生成
或显式重新生成且重新通过策略的草稿才可派发。相同有效输入不得重复生成草稿。

## 5. 决策、动作和审计

### `policy_decisions`

保存动作类型、`ALLOW_AUTO/REQUIRE_CONFIRMATION/DENY`、原因码、策略版本及完整受控输入快照。

### `confirmation_tasks`

保存需要人工确认的电话/面试具体时间及其他高风险动作。任务有过期、批准、修改、拒绝和
乐观版本；批准不能绕过执行前页面复核。

### `action_queue`

保存动作、目标平台/公司/职位/招聘人/会话、内容或附件、授权来源、幂等键、发送指纹、
写入前证据、失败码和状态。约束包括：

- `idempotency_key`、`send_fingerprint`、非空 `draft_id`、确认任务 ID 唯一；
- 状态只允许 `PENDING_APPROVAL / APPROVED / EXECUTING / SUCCEEDED /
  FAILED_RETRYABLE / FAILED_FINAL / CANCELLED / EXPIRED / SUPERSEDED / OUTCOME_UNKNOWN`。

`LOW_SCORE_DECLINE` 仅为历史动作值保留，当前代码不能创建新动作。

猎聘沿用通用 `GREETING/REPLY/RESUME` 动作类型，但三类动作分别使用职位、源入站消息或
明确简历请求构造独立幂等键和发送指纹。执行器回读到已执行结果时复用原动作；写入后结果
不可确认时进入 `OUTCOME_UNKNOWN`，不得创建第二个动作或直接重试。

### `action_attempts`、`resume_send_records`、`reconciliation_tasks`

- 每次外部尝试保存开始/结束、结果、写入前后证据和错误；
- 简历发送记录绑定入站证据、资格快照和附件；
- 每个未知动作最多一个对账任务，只读回查并最终解决或升级人工处理。

### `audit_events`

只追加保存 actor、事件、实体、动作/决策关联、前后状态、原因码、脱敏元数据、
`correlation_id`、`request_id` 和时间。请求 ID 建立索引。

## 6. Worker、发现和平台状态

- `agent_runs`：平台期望运行状态、策略、执行器、短租约、游标、计数、暂停原因和版本；
- `agent_run_events`：Run 状态变化历史；
- `worker_instances`：进程身份、主机、PID、心跳和 `RUNNING/STALE/STOPPED`；
- `platform_sessions`：登录、页面和选择器可用状态；
- `browser_page_registrations`：平台页面角色、CDP target、所有权和唯一性；
- `browser_read_runs` / `page_evidence`：脱敏页面读取与证据；
- `job_discovery_records`：职位预筛、处理、重试、正文版本和原因；猎聘发布开关关闭时，满足
  主动条件的记录使用 `SCORED + PROACTIVE_CONTACT_CANDIDATE`，不创建 Action Queue；
- `platform_recommendations`：脉脉推荐卡片、简化判断、动作和回读证据；
- `llm_circuit_breakers`：用户级模型能力状态、探测和失败信息；
- `llm_runtime_settings`：当前 provider/model 和版本，不保存 API Key。

Run、Worker、平台会话和能力是四种独立状态，不能用其中一种替代其他状态。

## 7. 排期和日历

- `scheduling_preferences`：时区、工作时间、默认时长、缓冲和版本；
- `calendar_events`：Mock 或受控导入事件；
- `interview_requests`：电话/视频/现场请求、时间候选、资格、状态和来源消息；
- `calendar_checks`：provider、查询区间、时区、冲突结果和脱敏证据；
- `schedule_confirmations`：用户批准、修改、拒绝、过期和回复来源。

日历查询和日历写入是独立授权；日历空闲不能直接改变确认状态。

## 8. 自动化配置

`automation_settings` 按 `GLOBAL/PLATFORM/STRATEGY` 唯一保存：

- 总开关、暂停、紧急停止；
- 自动招呼、最低分（80–100）、自动回复、自动简历；
- 脉脉推荐及推荐简历开关；
- 职位扫描；
- 公司/招聘人防重复冷却；
- 工作开始和结束小时。

不同范围按最严格规则合并。表中不保存小时/每日配额、旧低分婉拒开关、自动回复置信度或
简历最低分；回复置信度属于 Reply Router 配置，不是外部动作授权配置。

## 9. 幂等原则

- 同一外部职位和正文版本只处理一次；
- 同一外部消息在同一会话只导入一次；
- 同一草稿只能绑定一个动作；
- 同一动作写入结果未知时只能对账，不能创建第二个发送动作；
- 同一明确简历请求、推荐卡片和排期确认均使用独立业务指纹；
- 重启、租约转移或页面重连不能改变上述唯一性。

## 10. 删除和保留

业务记录默认不物理删除。候选人资料、策略、知识和附件通过启用状态或版本管理。历史迁移、
评分、动作、尝试、对账和审计用于复现决策，不因当前代码停止创建某类记录而删除。
