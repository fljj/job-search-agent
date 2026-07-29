# 数据模型设计

## 1. 设计原则

- PostgreSQL 为唯一业务数据库；
- 使用 UUID 作为核心实体主键；
- 时间统一保存带时区时间戳，展示时转换为用户时区；
- 金额使用定点数，不使用浮点数；
- 原始输入、规范化结果和评分结果分开保存；
- 策略、候选人资料、解析和评分均保留版本；
- 审计事件只追加，不覆盖历史；
- 第一阶段只创建本阶段实际使用的表。

## 2. 第一阶段实体关系

```text
users
 ├─ candidate_profiles ─ candidate_skills
 │                      └ candidate_industry_experiences
 ├─ job_strategies ─ job_title_rules
 │                  ├─ work_mode_rules ─ work_mode_locations
 │                  ├─ salary_rules ─ salary_score_bands
 │                  ├─ industry_rules
 │                  └─ company_blacklists
 └─ jobs ─ parsed_job_details
          └─ job_scores ─ job_score_details
                         └─ job_rejections
```

## 3. 第一阶段数据表

### 3.1 `users`

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | UUID | PK |
| `display_name` | VARCHAR(100) | NOT NULL |
| `created_at` | TIMESTAMPTZ | NOT NULL |
| `updated_at` | TIMESTAMPTZ | NOT NULL |

### 3.2 `candidate_profiles`

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID | FK users, NOT NULL |
| `name` | VARCHAR(100) | NOT NULL |
| `total_years` | NUMERIC(4,1) | >= 0 |
| `management_years` | NUMERIC(4,1) | >= 0 |
| `has_architecture_experience` | BOOLEAN | NOT NULL |
| `has_core_system_experience` | BOOLEAN | NOT NULL |
| `version` | INTEGER | NOT NULL, >= 1 |
| `created_at` | TIMESTAMPTZ | NOT NULL |
| `updated_at` | TIMESTAMPTZ | NOT NULL |

### 3.3 `candidate_skills`

字段：`id`、`candidate_profile_id`、`name`、`normalized_name`、`years`、`proficiency`、`source`、`is_core`、`created_at`、`updated_at`。

唯一约束：`(candidate_profile_id, normalized_name)`。

### 3.4 `candidate_industry_experiences`

字段：`id`、`candidate_profile_id`、`industry_code`、`years`、`source`、`created_at`。

唯一约束：`(candidate_profile_id, industry_code)`。

### 3.5 `job_strategies`

字段：`id`、`user_id`、`candidate_profile_id`、`name`、`enabled`、`priority`、`accepted_seniority_levels JSONB`、`max_posted_days`、`accept_outsourcing`、`accept_headhunter`、`headhunter_score_cap`、`version`、`created_at`、`updated_at`。

唯一约束：`(user_id, name)`。

`priority` 为正整数，数值越小优先级越高，用于未绑定策略的入站对话在同分时选择策略。自动化开关保存在 `automation_settings`，不复制到策略聚合中。

`headhunter_score_cap` 可空且最大为 79；用于保留猎头入站沟通能力，同时确定性阻止达到主动招呼分数门槛。

### 3.6 `job_title_rules`

字段：`id`、`strategy_id`、`rule_type`、`pattern`、`normalized_pattern`、`score`、`is_hard_requirement`、`created_at`。

唯一约束：`(strategy_id, rule_type, normalized_pattern)`。

### 3.7 `work_mode_rules`

字段：`id`、`strategy_id`、`work_mode`、`enabled`、`location_restricted`、`location_score`、`unknown_score`、`created_at`、`updated_at`。

唯一约束：`(strategy_id, work_mode)`。

### 3.8 `work_mode_locations`

字段：`id`、`work_mode_rule_id`、`location_code`、`location_name`、`created_at`。

唯一约束：`(work_mode_rule_id, location_code)`。

### 3.9 `salary_rules`

字段：`id`、`strategy_id`、`work_mode`、`currency`、`minimum_monthly_k`、`expected_monthly_k`、`negotiable_score`、`unknown_score`、`exchange_rate`、`exchange_rate_version`、`created_at`、`updated_at`。

约束：最低薪资不得高于期望薪资；固定汇率必须大于 0；`(strategy_id, work_mode, currency)` 唯一。

### 3.10 `salary_score_bands`

字段：`id`、`salary_rule_id`、`lower_bound_k`、`upper_bound_k`、`min_score`、`max_score`、`interpolation`、`sort_order`。

约束：上下界合法、分数在 0–15、同一薪资规则区间不得重叠。区间重叠由服务层在保存事务中校验。

### 3.11 `industry_rules`

字段：`id`、`strategy_id`、`industry_code`、`industry_name`、`rule_type`、`score`、`created_at`。

唯一约束：`(strategy_id, industry_code)`，保证同一行业不能同时属于偏好和排除类型。

### 3.12 `company_blacklists`

字段：`id`、`strategy_id`、`company_name`、`normalized_name`、`reason`、`created_at`。

唯一约束：`(strategy_id, normalized_name)`。

### 3.13 `jobs`

字段：`id`、`user_id`、`source`、`external_job_id`、`content_hash`、`title`、`company_name`、`industry`、`location`、`work_mode`、`salary_text`、`description`、`published_at`、`source_status`、`raw_data JSONB`、`created_at`、`updated_at`。

`source_status` 只保存来源声明的开放、关闭或未知状态。是否超过发布时间限制取决于具体策略，不持久化为职位自身状态。

唯一约束：

- `(user_id, source, external_job_id)`，仅在 `external_job_id IS NOT NULL` 时生效；
- `(user_id, source, content_hash)`，用于没有外部 ID 的模拟职位幂等导入。

### 3.14 `parsed_job_details`

字段：`id`、`job_id`、`parser_type`、`parser_version`、`required_skills JSONB`、`preferred_skills JSONB`、`years_required`、`management_required`、`architecture_required`、`seniority_level`、`responsibilities JSONB`、`salary_normalized JSONB`、`confidence`、`warnings JSONB`、`created_at`。

解析结果新增不覆盖。建议索引：`(job_id, created_at DESC)`。

### 3.15 `job_scores`

字段：`id`、`job_id`、`strategy_id`、`candidate_profile_id`、`parsed_job_detail_id`、`strategy_version`、`profile_version`、`scoring_version`、`prompt_version`、`llm_invocation_id`、`input_fingerprint`、`effective_job_status`、`action_blockers JSONB`、七个维度分、`total_score`、`grade`、`eligibility`、`hard_rejected`、`llm_recommends_proactive_contact`、`llm_contact_reason`、`automation_eligible`、`match_reasons JSONB`、`risk_notes JSONB`、`input_snapshot JSONB`、`created_at`。候选人资料增加可空的 `bachelor_full_time`，策略增加 `reject_full_time_bachelor_required`；解析记录在 `flags` 中保存 `full_time_bachelor_required`。

校验：七个维度不超过各自上限；总分为 0–100；等级与总分一致；`hard_rejected = true` 时 `eligibility = FILTERED_OUT`。

唯一约束：`input_fingerprint`。该指纹由职位、策略版本、候选人资料版本、解析记录、评分规则版本、提示词版本、供应商、模型名称和影响输出的模型参数生成。`job_scores` 只保存校验成功的评分，失败调用不能被当作有效评分复用。

`input_snapshot` 保存评分上下文及条目级 `evidence_items`。每个条目包含由来源路径和
规范化 JSON 值生成的稳定 ID、`source_path`、具体 `value` 和允许引用的
`dimensions`。审计恢复时必须从上下文重新生成目录并完全一致；列表顺序变化不改变
条目 ID，内容变化会使旧 ID 失效。

### 3.16 `job_score_details`

字段：`id`、`job_score_id`、`dimension`、`rule_code`、`score_awarded`、`max_score`、`evidence_refs JSONB`、`matched_facts JSONB`、`explanation`、`sort_order`。

`evidence_refs` 只保存当前评分快照中的条目级证据 ID；`matched_facts` 保存这些 ID
解析后的来源路径、具体值和允许维度，便于不调用模型完成审计。现有 JSONB 字段足以
承载该结构，本阶段不新增数据库列。

### 3.17 `job_rejections`

字段：`id`、`job_score_id`、`rule_code`、`message`、`evidence JSONB`、`sort_order`、`created_at`。

唯一约束：`(job_score_id, rule_code)`。

### 3.18 `llm_invocations`

字段：`id`、`user_id`、`purpose`、`provider`、`model`、`prompt_version`、`input_hash`、`status`、`provider_response_id`、`latency_ms`、`input_tokens`、`output_tokens`、`failure_code`、`attempt_number`、`created_at`。

只保存受控输入哈希、调用元数据和错误码；默认不保存完整提示词、完整模型响应或 API Key。相同业务评分的每次供应商调用均追加记录，便于审计限流、超时和非法输出。

## 4. 第一阶段枚举

- `WorkMode`：`REMOTE/ONSITE/HYBRID/UNKNOWN`
- `SourceJobStatus`：`OPEN/CLOSED/UNKNOWN`
- `EffectiveJobStatus`：`OPEN/CLOSED/EXPIRED/UNKNOWN`
- `Grade`：`A/B/C`
- `Eligibility`：`ELIGIBLE/FILTERED_OUT`
- `TitleRuleType`：`INCLUDE/EXCLUDE`
- `IndustryRuleType`：`PREFERRED/ACCEPTABLE/EXCLUDED`
- `ParserType`：`RULE/FAKE_LLM/QWEN/ZHIPU`
- `ScoreStatus`：`PENDING/SUCCEEDED/FAILED`
- `LlmProvider`：`FAKE/QWEN/ZHIPU`
- `LlmInvocationPurpose`：`JOB_PARSE/JOB_SCORE/INTENT_CLASSIFY/GREETING/REPLY/CONVERSATION_EVALUATE`
- `InterpolationType`：`STEP/LINEAR`

数据库建议使用字符串列和检查约束，避免 PostgreSQL 原生枚举升级复杂度。

## 5. 第二阶段数据表

- `knowledge_items`：候选人事实、来源、敏感度、自动引用权限、验证和有效时间；`(user_id, category, normalized_key)` 唯一。
- `resumes`：平台内附件名、适用方向和可用状态；`(user_id, platform, attachment_name)` 唯一。
- `conversations`：平台对话、可选职位归属、`strategy_id`、可选
  `latest_job_score_id`、`qualification_status`、资格证据、证据消息 ID、资格版本和状态；
  同时保存页面观察到的公司、职位和外部职位 ID，供尚未绑定正式职位时核对动作目标；
  `(user_id, platform, external_conversation_id)` 唯一。策略、职位绑定和资格成熟度变化
  必须记录审计。删除职位时 `job_id` 置空，不级联删除对话历史。
- `messages`：原始消息、方向、多意图和状态；`(conversation_id, external_message_id)` 唯一。
- `generated_drafts`：招呼或回复草稿、事实引用、置信度、风险、生成器版本和可空
  `reply_source`；`input_fingerprint` 唯一。`reply_source` 取值为
  `RULE_TEMPLATE/KNOWLEDGE_BASE/LLM/HUMAN`。迁移前历史数据保持 `NULL`，迁移后由应用层
  保证所有新草稿写入来源。
- `policy_decisions`：动作类型、权限结果、原因码、策略版本和输入快照。
- `confirmation_tasks`：只保存电话、面试具体时间和日历写操作的 `PENDING_APPROVAL` 数据；`decision_id` 唯一。

第二阶段幂等：模拟消息使用外部消息 ID，草稿使用消息/评分、知识版本和生成器版本生成指纹。

`qualification_status`：

- `UNKNOWN`：岗位信息不足；
- `ROUGH_MATCH`：大体符合，可以推进电话沟通；
- `FULL_MATCH`：关键信息充分且符合，可以推进面试；
- `MISMATCH`：存在明确冲突，只允许婉拒或停止。

入站对话允许 `job_id/latest_job_score_id` 暂时为空。明确索要简历的入站动作必须引用
招聘方消息 ID、资格快照和确定性阻断检查结果，不能伪造 `job_score_id`。

## 6. 第三阶段数据表

- `platform_sessions`：用户和平台唯一，仅保存无凭证的本机 CDP 端点、会话状态、原因码和最后检查时间。
- `browser_read_runs`：追加保存平台、状态、页面类型、原因码、列表游标、脱敏列表提取结果、导入对象 ID 及输入指纹；`input_fingerprint` 唯一。列表结果保存在 `extracted_items` JSONB，职位/对话详情仍通过外键指向正式业务实体。
- `page_evidence`：每次读取记录唯一，保存已去除 query/fragment 的 URL、页面标题、内容哈希、选择器版本和捕获时间；不保存 Cookie、Token 或完整 HTML。

## 7. LLM 熔断状态

`llm_circuit_breakers` 按用户唯一，记录当前供应商和模型的全局可用性：

- `status`：`CLOSED`、`OPEN` 或 `PROBING`；
- `failure_code`：最近一次认证、余额/限流、超时、网络或服务错误；
- `probe_attempt_count`：独立健康探测连续失败次数；
- `opened_at`、`last_probe_at`、`next_probe_at`、`recovered_at`；
- 同一时刻只允许一个探测将状态从 `OPEN` 改为 `PROBING`。

熔断状态不删除或终结等待中的职位、消息和动作。恢复后由原有幂等状态机继续处理。

第三阶段重复读取使用“平台 + 状态 + 页面类型 + 内容哈希 + 原因码”生成指纹。职位、对话和消息同时受已有来源唯一约束保护。

## 7. 第四阶段数据表

- `action_queue`：确认任务、职位/对话、草稿/简历、目标快照、状态、时间、幂等键和发送指纹；`confirmation_task_id`、`idempotency_key` 和 `send_fingerprint` 唯一。首次招呼使用 `job_id` 且 `conversation_id/target_conversation_key` 可空；回复和简历动作仍必须绑定对话。
- `action_queue.delivery_mode`：`CUSTOM/PLATFORM_DEFAULT`；平台默认模式同时保存
  `expected_platform_content` 和回读到的 `observed_content`。
- `action_attempts`：每次外部执行的状态、错误、外部引用和证据哈希；`(action_id, attempt_number)` 唯一。
- `action_attempts.observed_content`：保存本次执行实际观察到的平台文案，供对账审计。
- `resume_send_records`：已成功发送附件记录；`(conversation_id, resume_id)` 唯一。

### 7.1 脉脉系统推荐扩展

- `platform_recommendations`：保存平台、外部推荐 ID、招聘人、公司、岗位、地点、薪资、
  职责摘要、原始卡片哈希、简化判断、原因码、状态和首次/最后观察时间；
  `(user_id, platform, external_recommendation_id)` 唯一。
- 推荐状态：
  `DISCOVERED/DECIDED/EXECUTING/ACCEPTED/REJECTED/OUTCOME_UNKNOWN/FAILED_FINAL`。
- 推荐判断：
  `ACCEPT_AND_SEND_PROFILE/REJECT_RECOMMENDATION/DENY`。
- `platform_recommendations.action_id` 关联唯一动作；推荐动作类型为
  `PLATFORM_RECOMMENDATION_ACCEPT/PLATFORM_RECOMMENDATION_REJECT`，不能同时绑定普通
  `draft_id`。不在动作表保存反向外键，避免循环依赖。
- `automation_settings` 增加 `maimai_recommendation_enabled` 和
  `maimai_recommendation_resume_enabled`；全局、平台和策略配置按最严格开关合并。
- 推荐同意是平台原子完成“接受并发送资料”的单个动作；成功后在推荐记录和动作尝试中
  保存平台回读证据，不保存无必要的完整个人资料。
- 推荐幂等指纹由平台、外部推荐 ID、招聘人稳定标识、岗位和动作类型生成；同意和拒绝
  只能有一个达到最终成功状态。
- 普通入站简历发送记录允许 `job_score_id` 为空，但必须保存
  `authorization_basis=INBOUND_EXPLICIT_RESUME_REQUEST`、入站消息 ID 和资格快照。
- `audit_events`：追加保存行为者、事件、实体、前后状态、原因码和关联 ID，不保存 Cookie、Token 或完敏感内容。

## 8. 后续阶段实体与状态

### 8.1 第五阶段数据表和字段

- `automation_settings`：按 `GLOBAL/PLATFORM/STRATEGY` 保存开关、暂停状态、动作阈值及
  小时/每日限额；`low_score_decline_enabled` 仅为历史字段，当前策略引擎不会创建
  `LOW_SCORE_DECLINE` 动作；`(user_id, scope_type, scope_key)` 唯一。
- `agent_runs`：保存平台、绑定策略、运行状态、心跳、短租约、游标、处理/动作/失败计数、连续失败数、暂停原因和乐观版本；同一用户和平台通过部分唯一索引最多保留一个 `RUNNING/PAUSED` 运行。
- `agent_runs.executor_type`：记录实际执行器类型 `UNASSIGNED/REAL_CDP/FAKE`，用于启动自检和审计；正式 BOSS 运行不得记录 `FAKE`。
- `agent_runs.cursor`：第九阶段保存消息列表分区、虚拟滚动位置、下一游标、最后会话
  和消息 ID、扫描时间、是否到末尾及最近 500 个 `conversation_id:last_message_id`
  去重键；BOSS 与脉脉运行分别持有自己的游标，扫描到末尾后滚动位置归零，新消息仍
  可被发现。脉脉缺少平台消息 ID时使用稳定会话 ID和受控消息预览生成内容指纹。
- `agent_runs.cursor.job_discovery`：第十阶段保存搜索条件键、虚拟滚动位置、下一游标、
  最后/下次扫描时间、是否到末尾及最近 2000 个已见外部职位 ID。消息游标与职位游标
  使用不同子键，轮询心跳更新不得覆盖两者。
- `job_discovery_records`：按运行保存外部职位 ID、公司、标题、招聘人、内容哈希、处理
  状态、原因码及关联职位/评分/动作；`retry_count` 和 `next_retry_at` 保存单飞重试次数
  与下次允许时间；`(agent_run_id, external_job_id)` 唯一，确保刷新、重排和进程恢复后
  不会重复处理。`(agent_run_id, status, next_retry_at)` 索引用于选择唯一队首重试。
- `conversations.processing_lease_owner/processing_lease_expires_at`：消息发现的会话级
  短租约，防止多个执行者同时导入和决策同一对话。
- `agent_run_events`：追加保存运行启动、租约轮询、草稿、动作、失败、熔断和恢复事件，不覆盖历史记录。
- `automation_settings` 第十阶段增加职位扫描开关、全局紧急停止、小时/每日扫描上限、
  公司/招聘人冷却小时数及工作开始/结束小时；多层配置采用最保守的有效值。
- `action_queue.authorization_source`：区分 `MANUAL/AUTO`。
- `action_queue.policy_decision_id`：自动动作关联“模型建议 + 确定性约束”的最终策略决策。
- `action_queue.strategy_id`：保存自动动作采用的策略范围。
- `action_queue.agent_run_id`：将自动动作追溯到产生它的 Agent 运行。
- 自动动作不创建人工批准，因此 `confirmation_task_id` 可空；人工动作仍必须关联确认任务。

### 8.2 第六阶段数据表

- `scheduling_preferences`：用户时区、工作时间、午休、默认时长、缓冲、通勤和快照有效期配置；用户唯一并使用版本并发控制。
- `calendar_events`：本地假日历忙闲事件，以及用户单独授权后由 Apple/Google Calendar
  创建并回写的面试事件；`(user_id, provider, external_event_id)` 唯一。
- `interview_requests`：原始消息、解析事件、时间、时区、置信度、风险和候选时间；`message_id` 唯一。
- `calendar_checks`：检查状态、快照版本、检查时间和受控冲突摘要，历史检查追加保存。
- `schedule_confirmations`：具体回复、选定时间、独立日历写入授权、有效期和发送动作；排期请求及幂等键唯一。

后续阶段按需增加：`interview_requests`、`calendar_checks`、`calendar_events`。

### 8.3 动作状态

```text
DRAFT
PENDING_APPROVAL
APPROVED
EXECUTING
SUCCEEDED
FAILED_RETRYABLE
FAILED_FINAL
CANCELLED
EXPIRED
SUPERSEDED
OUTCOME_UNKNOWN
```

### 8.4 平台会话状态

```text
SESSION_READY
SESSION_AUTH_REQUIRED
SESSION_PAGE_CHANGED
SESSION_TARGET_MISMATCH
SESSION_PAUSED
```

### 8.5 对话和消息状态

对话状态：`NEW/ACTIVE/WAITING_RECRUITER/WAITING_USER/SCHEDULING/SCHEDULE_CONFIRMED/ENDED`。

消息状态：`RECEIVED/SUPERSEDED/ANALYZING/DRAFT/PENDING_APPROVAL/APPROVED/SENDING/SENT/DECLINED/FAILED/OUTCOME_UNKNOWN`。同一会话中尚未生成草稿的连续旧入站消息标为 `SUPERSEDED`，仅最新消息触发一次草稿；旧消息仍保留作为模型上下文和审计证据。

对话状态不能替代单条消息或动作状态。

### 8.6 第十二阶段运行治理

- `worker_instances`：保存唯一 Worker 标识、主机、PID、`RUNNING/STALE/STOPPED` 状态、
  启动/心跳/停止时间及不含凭证的启动元数据。
- `reconciliation_tasks`：每个动作唯一，保存 `PENDING/IN_PROGRESS/RESOLVED/
  MANUAL_REQUIRED`、尝试次数、下次尝试、截止时间和最后错误码。
- 对账任务只能只读回查平台，不能授权或重发 `OUTCOME_UNKNOWN` 动作。

### 8.7 第十三阶段灰度控制

- `rollout_controls`：按 `(user_id, platform)` 唯一保存真实平台灰度状态。
- `status` 仅为 `ACTIVE/PAUSED`；`current_level/previous_level` 限定为 1 至 6。
- `stage_started_at` 和 `minimum_stage_hours` 决定最早升级时间，最短不得低于 24 小时。
- `reply_daily_limit` 最大为 5，`greeting_daily_limit` 最大为 3；第六级改用正式自动化
  配置的每日上限。
- `version` 用于升级、正式接管、暂停和回退的乐观并发控制。常规升级必须严格加一；
  用户明确授权且安全指标全为零时，正式接管可以审计事件形式直接进入第六级。
- 灰度转换和自动回退追加写入 `audit_events`，保存前后级别及触发指标；不覆盖历史。
- 安全指标从 `action_queue`、`action_attempts`、`reconciliation_tasks`、草稿和评分记录
  计算，不由浏览器或前端自行上报成功。

## 9. 幂等设计

### 6.1 第一阶段

- JD 导入使用来源 ID 或内容哈希唯一约束；
- API 重复导入返回既有资源和 `DUPLICATE`，不创建硬性排除；
- 策略更新使用 `version` 乐观锁；
- 同一评分请求通过职位、策略/资料/解析版本、评分/提示词版本、供应商、模型和模型参数生成请求指纹；只允许复用 `SUCCEEDED` 结果。

### 6.2 后续写操作

- `action_queue.idempotency_key` 唯一；
- `action_queue.send_fingerprint` 按对话、动作类型、内容或简历生成，防止更换幂等键重复发送；
- `messages(platform, platform_message_id)` 唯一；
- 实际消息唯一约束为 `messages(conversation_id, external_message_id)`；消息列表游标
  仅用于减少重复扫描，数据库唯一约束才是最终幂等边界；
- `resume_send_records(conversation_id, resume_id)` 唯一；
- `platform_recommendations(user_id, platform, external_recommendation_id)` 唯一；
- 推荐同意/拒绝共享同一推荐级原子状态；已有最终结果时不得创建相反或重复动作；
- 只有原子条件更新成功将动作转为 `EXECUTING` 的执行者可以调用外部平台；
- `OUTCOME_UNKNOWN` 只能对账，不能直接重试；
- 对账使用动作行锁串行执行；只有平台明确确认未发送后才转为
  `FAILED_RETRYABLE`，继续未知时保持原状态；
- 目标标签页不存在或不唯一属于点击前失败，可以在用户再次确认后重试；该例外必须
  通过固定失败码白名单判断，不能扩展到点击后的 `FAILED_FINAL`；
- 每次重试写入新的 `action_attempts`，不得覆盖历史尝试。

## 10. 审计设计

`audit_events` 后续采用追加写，至少保存：

- `id`、`occurred_at`、`actor_type`、`actor_id`；
- `event_type`、`entity_type`、`entity_id`；
- `action_id`、`policy_decision_id`；
- `before_state`、`after_state`；
- `reason_codes`、`metadata`；
- `correlation_id`、`request_id`。

不得在审计元数据中保存密码、Cookie、Token 或无必要的完整敏感内容。页面证据保存受控引用和哈希，不在普通日志中复制敏感页面全文。
