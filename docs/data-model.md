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

字段：`id`、`user_id`、`candidate_profile_id`、`name`、`enabled`、`accepted_seniority_levels JSONB`、`max_posted_days`、`accept_outsourcing`、`accept_headhunter`、`version`、`created_at`、`updated_at`。

唯一约束：`(user_id, name)`。

第一阶段不保存自动招呼、自动回复和自动简历配置；这些字段在对应功能进入开发阶段后增加。

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

字段：`id`、`job_id`、`strategy_id`、`candidate_profile_id`、`parsed_job_detail_id`、`strategy_version`、`profile_version`、`scoring_version`、`input_fingerprint`、`effective_job_status`、`action_blockers JSONB`、七个维度分、`total_score`、`grade`、`eligibility`、`hard_rejected`、`match_reasons JSONB`、`risk_notes JSONB`、`input_snapshot JSONB`、`created_at`。

校验：七个维度不超过各自上限；总分为 0–100；等级与总分一致；`hard_rejected = true` 时 `eligibility = FILTERED_OUT`。

唯一约束：`input_fingerprint`。该指纹由职位、策略版本、候选人资料版本、解析记录和评分规则版本的稳定标识生成。

### 3.16 `job_score_details`

字段：`id`、`job_score_id`、`dimension`、`rule_code`、`score_awarded`、`max_score`、`matched_facts JSONB`、`explanation`、`sort_order`。

### 3.17 `job_rejections`

字段：`id`、`job_score_id`、`rule_code`、`message`、`evidence JSONB`、`sort_order`、`created_at`。

唯一约束：`(job_score_id, rule_code)`。

## 4. 第一阶段枚举

- `WorkMode`：`REMOTE/ONSITE/HYBRID/UNKNOWN`
- `SourceJobStatus`：`OPEN/CLOSED/UNKNOWN`
- `EffectiveJobStatus`：`OPEN/CLOSED/EXPIRED/UNKNOWN`
- `Grade`：`A/B/C`
- `Eligibility`：`ELIGIBLE/FILTERED_OUT`
- `TitleRuleType`：`INCLUDE/EXCLUDE`
- `IndustryRuleType`：`PREFERRED/ACCEPTABLE/EXCLUDED`
- `ParserType`：`RULE/FAKE_LLM/HYBRID_TEST`
- `InterpolationType`：`STEP/LINEAR`

数据库建议使用字符串列和检查约束，避免 PostgreSQL 原生枚举升级复杂度。

## 5. 第二阶段数据表

- `knowledge_items`：候选人事实、来源、敏感度、自动引用权限、验证和有效时间；`(user_id, category, normalized_key)` 唯一。
- `resumes`：平台内附件名、适用方向和可用状态；`(user_id, platform, attachment_name)` 唯一。
- `conversations`：模拟平台对话与职位归属；`(user_id, platform, external_conversation_id)` 唯一。
- `messages`：原始消息、方向、多意图和状态；`(conversation_id, external_message_id)` 唯一。
- `generated_drafts`：招呼或回复草稿、事实引用、置信度、风险和生成器版本；`input_fingerprint` 唯一。
- `policy_decisions`：动作类型、权限结果、原因码、策略版本和输入快照。
- `confirmation_tasks`：第二阶段只保存 `PENDING_APPROVAL` 确认数据，不提供执行转移；`decision_id` 唯一。

第二阶段幂等：模拟消息使用外部消息 ID，草稿使用消息/评分、知识版本和生成器版本生成指纹。

## 6. 第三阶段数据表

- `platform_sessions`：用户和平台唯一，仅保存无凭证的本机 CDP 端点、会话状态、原因码和最后检查时间。
- `browser_read_runs`：追加保存平台、状态、页面类型、原因码、导入对象 ID 及输入指纹；`input_fingerprint` 唯一。
- `page_evidence`：每次读取记录唯一，保存已去除 query/fragment 的 URL、页面标题、内容哈希、选择器版本和捕获时间；不保存 Cookie、Token 或完整 HTML。

第三阶段重复读取使用“平台 + 状态 + 页面类型 + 内容哈希 + 原因码”生成指纹。职位、对话和消息同时受已有来源唯一约束保护。

## 7. 第四阶段数据表

- `action_queue`：确认任务、对话、草稿/简历、目标快照、状态、时间、幂等键和发送指纹；`confirmation_task_id`、`idempotency_key` 和 `send_fingerprint` 唯一。
- `action_attempts`：每次外部执行的状态、错误、外部引用和证据哈希；`(action_id, attempt_number)` 唯一。
- `resume_send_records`：已成功发送附件记录；`(conversation_id, resume_id)` 唯一。
- `audit_events`：追加保存行为者、事件、实体、前后状态、原因码和关联 ID，不保存 Cookie、Token 或完敏感内容。

## 8. 后续阶段实体与状态

后续阶段按需增加：`interview_requests`、`calendar_checks`、`calendar_events`。

### 8.1 动作状态

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

### 8.2 平台会话状态

```text
SESSION_READY
SESSION_AUTH_REQUIRED
SESSION_PAGE_CHANGED
SESSION_TARGET_MISMATCH
SESSION_PAUSED
```

### 8.3 对话和消息状态

对话状态：`NEW/ACTIVE/WAITING_RECRUITER/WAITING_USER/SCHEDULING/SCHEDULE_CONFIRMED/ENDED`。

消息状态：`RECEIVED/DRAFT/PENDING_APPROVAL/APPROVED/SENDING/SENT/FAILED/OUTCOME_UNKNOWN`。

对话状态不能替代单条消息或动作状态。

## 9. 幂等设计

### 6.1 第一阶段

- JD 导入使用来源 ID 或内容哈希唯一约束；
- API 重复导入返回既有资源和 `DUPLICATE`，不创建硬性排除；
- 策略更新使用 `version` 乐观锁；
- 同一评分请求可以通过 `(job_id, strategy_version, profile_version, parsed_job_detail_id, scoring_version)` 生成请求指纹，允许返回已有结果或明确创建新版本。

### 6.2 后续写操作

- `action_queue.idempotency_key` 唯一；
- `action_queue.send_fingerprint` 按对话、动作类型、内容或简历生成，防止更换幂等键重复发送；
- `messages(platform, platform_message_id)` 唯一；
- `resume_send_records(conversation_id, resume_id)` 唯一；
- 只有原子条件更新成功将动作转为 `EXECUTING` 的执行者可以调用外部平台；
- `OUTCOME_UNKNOWN` 只能对账，不能直接重试；
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
