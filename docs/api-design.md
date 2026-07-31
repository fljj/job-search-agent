# API 设计

## 1. 通用约定

- API 前缀：`/api/v1`；
- 请求和响应使用 JSON；
- 字段使用英文 `snake_case`；
- 资源 ID 使用 UUID；
- 时间使用 ISO 8601 且包含时区；
- 分页使用 `page` 和 `page_size`；
- 第一阶段为单用户运行，但服务端仍从当前用户上下文限制数据归属。

成功响应：

```json
{
  "data": {},
  "meta": {
    "request_id": "uuid"
  }
}
```

错误响应：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "策略已被其他请求修改",
    "details": {},
    "request_id": "uuid"
  }
}
```

## 2. 候选人资料 API

### `GET /api/v1/profile`

返回当前候选人评分资料、技能、行业经验和 `version`。

### `PUT /api/v1/profile`

请求包含完整资料和当前 `version`。成功后版本加一；版本不一致返回 `409 VERSION_CONFLICT`。

## 3. 求职策略 API

### `POST /api/v1/strategies`

创建完整策略。请求包含：

- `name`、`enabled`、`priority`、`candidate_profile_id`；
- `title_rules`、`accepted_seniority_levels`；
- `work_mode_rules` 及允许地点；
- `salary_rules` 及计分区间；
- `industry_rules`、`company_blacklist`；
- `accept_outsourcing`、`accept_part_time`、`accept_headhunter`、`headhunter_score_cap`、`max_posted_days`。

`accept_part_time` 控制是否接受兼职岗位。接受兼职时，JD 明确要求现场办公的岗位仍按现场地点规则判断；JD 未明确办公方式时不因平台地点推断而硬性排除，评分和沟通中标记为需要确认办公方式。

`headhunter_score_cap` 可为空；配置时范围为 0–79。它只在可靠识别为猎头岗位时限制最终分数，不等同于 `accept_headhunter=false` 的硬性排除。

候选人资料的 `bachelor_full_time` 为可空布尔值；`null` 表示未知。策略字段
`reject_full_time_bachelor_required` 控制是否把明确的全日制/统招本科要求作为硬性排除。

`priority` 为正整数，数值越小优先级越高，用于未绑定策略的入站对话在同分时选择策略。

### `GET /api/v1/strategies`

查询参数：`enabled`、`page`、`page_size`。

### `GET /api/v1/strategies/{strategy_id}`

返回完整聚合，不要求客户端分别读取子规则。

### `PUT /api/v1/strategies/{strategy_id}`

使用完整替换语义并携带 `version`。标题、地点、薪资、行业和黑名单在一个事务中更新。

### `PATCH /api/v1/strategies/{strategy_id}/status`

请求：

```json
{
  "enabled": false,
  "version": 3
}
```

## 4. 模拟 JD API

### `POST /api/v1/jobs/import`

请求至少包含：`external_job_id`、`title`、`company_name`、`industry`、`location`、`work_mode`、`salary_text`、`description`、`published_at`、`source_status`。

响应包含：

```json
{
  "data": {
    "result": "CREATED",
    "job": {}
  }
}
```

重复请求返回 HTTP 200、`result = DUPLICATE` 和已有职位，不返回硬性排除。

### `POST /api/v1/jobs/import/batch`

请求包含 `items` 数组。每条独立返回 `CREATED/DUPLICATE/VALIDATION_FAILED`；部分失败时整体返回 HTTP 200，并在条目中报告错误。

### `GET /api/v1/jobs`

查询参数：`job_id`、`strategy_id`、`grade`、`eligibility`、`effective_job_status`、
`hard_rejected`、`work_mode`、`page`、`page_size`。评分结果类筛选必须与
`strategy_id` 一起使用。

当指定 `strategy_id` 时，返回该策略的最新评分摘要。每条职位同时返回
`communication`，区分尚未发起、等待重试、已发送待同步和已有会话，并提供可选
`conversation_id`、动作状态、失败码及原因码；评分通过不等于消息已经发送成功。

### `GET /api/v1/jobs/{job_id}`

返回原始职位、规范化字段、最新解析和可选评分摘要。

## 5. JD 解析 API

### `POST /api/v1/jobs/{job_id}/parse`

请求：

```json
{
  "mode": "LLM"
}
```

允许 `LLM/RULE`。`LLM` 使用服务端配置的 `ZHIPU/QWEN/FAKE` 供应商；`RULE` 仅用于
离线兼容。API 不接收 API Key、基础地址或任意提示词。

### `GET /api/v1/jobs/{job_id}/parsed-details`

分页返回历史解析记录。

### `GET /api/v1/jobs/{job_id}/parsed-details/{parsed_detail_id}`

返回指定解析版本及警告。

## 6. 评分 API

### `POST /api/v1/jobs/{job_id}/scores`

请求：

```json
{
  "strategy_id": "uuid",
  "candidate_profile_id": "uuid",
  "parsed_job_detail_id": "uuid"
}
```

`parsed_job_detail_id` 可省略，服务端优先复用职位最新解析记录；不存在解析记录时调用当前
LLM Provider 创建。普通重复请求按输入指纹返回已有评分。

### `POST /api/v1/automation/llm-circuit/retry`

重新读取 `.env` 中的 LLM 配置后发起一次最小健康探测，不执行职位评分或消息回复。
仅当熔断器处于 `OPEN` 时用于人工提前恢复；并发请求中只有一个进入 `PROBING`。成功
返回 `CLOSED`，Worker 下一轮自动恢复业务；失败保持 `OPEN` 并更新失败码和下一次自动
探测时间。自动健康探测同样会重新读取配置。操作系统环境变量仍按标准优先级覆盖
`.env`，接口不得接收或返回 API Key。

`GET /api/v1/automation/operations/status` 的 `llm_circuit` 返回状态、供应商、模型、
失败码、探测次数以及最近/下次探测时间，不返回 API Key。

响应包含：

- `total_score`、`grade`；
- `eligibility`、`hard_rejected`；
- `source_status`、`effective_job_status`、`action_blockers`；
- 七个维度得分和规则明细；
- `rejection_reasons`、`match_reasons`、`risk_notes`；
- `llm_recommends_proactive_contact`、`llm_contact_reason`、`automation_eligible`；
- `strategy_version`、`profile_version`、`parser_version`、`scoring_version`、`prompt_version`；
- `llm_provider`、`llm_model`；不返回 API Key。

### `POST /api/v1/jobs/scores/batch`

请求包含 `job_ids`、`strategy_id`、`candidate_profile_id`。逐条返回成功或失败结果。

### `POST /api/v1/jobs/{job_id}/scores/re-evaluate`

显式创建新的模型调用和评分版本，用于策略、资料、提示词或模型未变化但用户要求重新评估的场景。必须携带 `Idempotency-Key`；服务端生成新的评估批次 ID，不能通过普通重复请求绕过评分幂等。失败只产生 `llm_invocations` 记录，不产生有效 `job_scores`。

### `GET /api/v1/jobs/{job_id}/scores`

查询参数：`strategy_id`、`page`、`page_size`。

### `GET /api/v1/scores/{score_id}`

返回完整评分、明细、排除证据和输入快照摘要。评分明细的 `evidence_refs` 为
`evidence:<sha256>` 条目级 ID，`matched_facts.evidence_items` 返回对应来源路径、
具体值和允许维度。敏感原始数据按权限裁剪。

## 7. HTTP 状态和错误码

| HTTP | 错误码 | 含义 |
|---:|---|---|
| 400 | `INVALID_REQUEST` | 请求语义不合法 |
| 401 | `UNAUTHENTICATED` | 未建立用户身份 |
| 403 | `FORBIDDEN` | 无资源访问权限 |
| 404 | `RESOURCE_NOT_FOUND` | 资源不存在或不属于当前用户 |
| 422 | `LLM_OUTPUT_INVALID` | 模型输出缺字段、分值越界、求和不一致、证据不存在、跨维度或已过期 |
| 429 | `LLM_RATE_LIMITED` | 模型供应商限流，本轮不执行自动写操作 |
| 502 | `LLM_PROVIDER_ERROR` | 模型供应商返回错误或无有效响应 |
| 503 | `LLM_UNAVAILABLE` | 模型未配置、超时或暂时不可用 |
| 409 | `VERSION_CONFLICT` | 乐观锁版本冲突 |
| 409 | `STRATEGY_RULE_CONFLICT` | 行业、地点或其他策略规则冲突 |
| 422 | `VALIDATION_ERROR` | Pydantic 字段校验失败 |
| 422 | `SALARY_BAND_OVERLAP` | 薪资区间重叠 |
| 422 | `INVALID_SCORE_CONFIGURATION` | 配置分数超出维度上限 |
| 503 | `DEPENDENCY_UNAVAILABLE` | 数据库或未来外部依赖不可用 |
| 500 | `INTERNAL_ERROR` | 未分类服务端错误 |

错误信息不得泄露数据库语句、堆栈、Token 或敏感原始数据。

## 8. 权限要求

- 所有资源查询按当前用户过滤；
- 不存在和无权访问均可统一返回 404，避免资源枚举；
- 服务端校验候选人资料、策略、职位和评分是否属于同一用户；
- 第一阶段没有浏览器写操作和人工确认 API；
- 后续动作 API 必须再次调用策略引擎，不能仅信任客户端提交的批准状态。

## 9. 幂等与并发要求

- JD 导入由数据库唯一约束保证幂等；
- `PUT` 和状态更新必须携带 `version`；
- 批量请求逐条标识输入索引，便于安全重试；
- 评分请求根据输入版本生成请求指纹，相同指纹可以返回已有评分；
- 后续外部写操作必须要求 `Idempotency-Key` 请求头，并在数据库建立唯一约束；
- 幂等响应应返回原资源和原执行状态，不得重新执行外部写操作。

## 10. 第二阶段 API

### 知识库

- `POST /api/v1/knowledge-items`：新增知识项，包含 `category/key/fact/source/allowed_for_auto_reply/sensitivity/verified_at/valid_until`。
- `PUT /api/v1/knowledge-items/{item_id}`：携带 `version` 完整替换，版本不一致返回 409。
- `GET /api/v1/knowledge-items`：分页返回知识项。

### 附件简历元数据

- `POST /api/v1/resumes`：登记平台内已存在的附件名和适用方向，不上传文件。
- `PUT /api/v1/resumes/{resume_id}`：携带 `version` 更新可用状态和适用方向。
- `GET /api/v1/resumes`：分页返回简历元数据。
- `GET /api/v1/resumes/select?job_id=...`：按职位名称返回可用的候选附件，不产生发送动作。

### 模拟对话和草稿

- `POST /api/v1/conversations`：为已导入职位创建幂等模拟对话。
- `POST /api/v1/conversations/{conversation_id}/messages`：幂等导入模拟招聘方消息并识别多意图。
- `POST /api/v1/drafts/reply`：请求包含 `message_id`；服务端先更新入站对话资格成熟度。
  `MISMATCH` 返回礼貌婉拒，其他状态返回事实受限的澄清或普通回复。存在正式评分时
  作为上下文使用，但评分不是入站回复前置条件；只有具体时间意图返回确认任务 ID。
- `POST /api/v1/drafts/greeting`：请求包含 `job_score_id`，仅基于 JD 与已验证知识生成个性化招呼草稿。
- `POST /api/v1/drafts/resume`：请求包含 `message_id`；服务端以该条当前招聘方消息中的
  明确索要简历意图作为证据，校验入站资格快照、确定性阻断规则、附件唯一匹配和重复
  发送后返回 `ALLOW_AUTO/DENY` 及 `resume_id`，不要求正式评分，也不创建普通确认任务。
- `PATCH /api/v1/drafts/{draft_id}`：人工编辑尚未产生动作的草稿；服务端重新执行内容安全检查，创建新草稿和新策略决策并审计，不覆盖原记录。
- `GET /api/v1/confirmation-tasks`：只查看电话、面试具体时间和日历写操作的待确认数据。

草稿接口按输入、知识版本和生成器版本幂等。生成草稿和动作授权不代表已经向真实平台执行发送。

## 11. 第三阶段浏览器只读 API

- `POST /api/v1/browser/read-current`：连接用户手动启动的本机 CDP 会话，读取当前聚焦页面。
- `GET /api/v1/browser/sessions`：查看 BOSS/脉脉最后只读检查状态和原因码。

`read-current` 请求包含 `platform`、`cdp_url`，并可选包含 `job_id/expected_company/expected_job_title/expected_recruiter`。读取对话详情页时 `job_id` 必填。响应通过 `page_type` 区分 `JOB_LIST/JOB/CONVERSATION_LIST/CONVERSATION`；列表页返回 `cursor` 以及 `jobs` 或 `conversations`，重复读取返回首次保存的相同脱敏列表快照。

### 7.8 控制台只读接口

- `GET /api/v1/system/llm-status`：返回当前 `provider/model/configured` 和环境允许的
  `options`，禁止返回 API Key、Base URL 凭证或完整环境配置。
- `PUT /api/v1/system/llm-status`：保存当前 `provider/model`。只能选择环境允许且已
  配置对应 API Key 的选项；保存后后续调用与健康探针立即生效。
- `GET /api/v1/automation/runs`：按时间倒序返回 Agent 状态、心跳、计数、租约和暂停原因。
- `GET /api/v1/automation/actions`：仅返回 `authorization_source=AUTO` 的普通自动动作及发送结果/失败证据。
- `GET /api/v1/conversations`：使用 `page/page_size` 服务端分页，可按 `platform/job_id`
  筛选；返回对话绑定策略、可选最新评分、页面观察到的公司/职位、资格成熟度、
  最新草稿和简历动作证据。脉脉系统推荐不进入该列表，普通招聘私信进入该列表。

以上接口均为只读展示接口。启动、暂停和恢复仍分别使用既有的
`POST /automation/runs`、`POST /automation/runs/{id}/pause` 和
`POST /automation/runs/{id}/resume`，服务端继续执行自动化配置和状态转换校验。

消息列表发现由 BOSS 和脉脉 Worker 内部执行，不新增可绕过策略的浏览器导航 API。
各平台扫描进度通过既有 `GET /api/v1/automation/runs` 的 `cursor` 返回；职位未绑定
或缺少评分只记录原因并继续安全入站流程，页面身份不一致仍暂停当前平台运行，状态
转换记录在 Agent 运行事件及会话状态中。脉脉系统推荐开关不影响普通入站消息扫描。

只允许本机 `localhost/127.0.0.1/::1` HTTP(S) CDP 端点且 URL 不得包含凭证。返回会话状态、页面类型、原因码、导入资源 ID、证据 ID 和重复标记。

未登录、验证页、页面结构变化或目标不一致时只保存失败证据，不导入职位或消息。

## 12. 时间确认、历史确认与动作执行 API

- `GET /api/v1/confirmation-tasks`：返回草稿、动作类型、状态、原因码、置信度和过期时间。
- `POST /api/v1/confirmation-tasks/{id}/approve`：必须携带 `Idempotency-Key`，仅生成 `APPROVED` 动作，不立即发送。
- `POST /api/v1/confirmation-tasks/{id}/modify`：敏感检查后废弃旧任务，创建新的 `PENDING_APPROVAL` 任务。
- `POST /api/v1/confirmation-tasks/{id}/reject`：将未批准任务终止为 `CANCELLED`。
- `POST /api/v1/actions/{id}/execute`：原子占用已批准动作，执行前重新复核页面目标并发送。
- `POST /api/v1/actions/{id}/retry`：允许用户将 `FAILED_RETRYABLE` 重新批准；兼容
  历史上误记为 `FAILED_FINAL` 的点击前页面定位失败白名单。`OUTCOME_UNKNOWN`
  和任何点击后失败不可重试。
- `POST /api/v1/actions/{id}/reconcile`：请求包含本机 `cdp_url`，仅对
  `OUTCOME_UNKNOWN` 动作执行只读平台回读。确认已发送转为 `SUCCEEDED`；确认未发送
  转为 `FAILED_RETRYABLE`；目标不唯一或仍无法判断时保持 `OUTCOME_UNKNOWN`。
  同一动作使用数据库行锁串行对账。

通用招呼和简历确认创建接口已移除；已有历史任务仍可查询和按原状态处理。新的确认
任务只由电话/面试具体时间流程创建，普通招呼、回复、不匹配婉拒和按策略允许的简历
发送不使用本组确认 API。

## 13. 第五阶段安全自动化 API

- `GET /api/v1/automation/settings`：查看全局、平台和策略范围配置。
- `PUT /api/v1/automation/settings`：按 `(scope_type, scope_key)` 幂等创建或更新自动化配置。
- `POST /api/v1/automation/dispatch`：对指定评分、草稿、对话和可选网站内简历执行“LLM 建议 + 确定性约束”决策；只有 `ALLOW_AUTO` 才立即复用安全执行链路。
- `POST /api/v1/automation/runs`：启动指定平台和策略的 Agent 运行；服务端校验模型配置和自动化开关。
- `POST /api/v1/automation/runs/{id}/pause`：暂停发现、招呼和自动回复。
- `POST /api/v1/automation/runs/{id}/resume`：自动化配置恢复安全状态后恢复运行。
  `RESULT_NOT_OBSERVED` 导致的平台暂停只有在该平台不存在 `OUTCOME_UNKNOWN` 动作时
  才能恢复；恢复时清除系统设置的对应平台暂停标记，但不绕过全局、策略或紧急停止开关。
- `POST /api/v1/automation/runs/{id}/tick`：由短周期工作进程携带 `worker_id` 执行一次受租约保护的轮询。
- `GET /api/v1/automation/runs/{id}`：返回运行状态、最近心跳、动作计数和暂停原因。

`dispatch` 返回 `decision`、`reason_codes`，允许执行时同时返回 `action_id` 和
`action_status`。调用方不能直接指定决策、分数、模型建议、置信度或资格成熟度，这些
数据全部由服务端读取。主动招呼必须同时满足 80 分和模型建议。招聘方入站明确索要
简历时不设分数门槛，但必须引用当前入站消息、资格快照并通过保险销售、完全无关、
黑名单、欺诈、附件和重复发送检查。

入站资格 API：

- `GET /api/v1/conversations/{id}/qualification`：返回成熟度、已知/缺失字段、明确冲突和
  最近判断证据；
- `POST /api/v1/conversations/{id}/qualification/evaluate`：根据新增消息和岗位信息
  幂等更新成熟度，客户端不能直接指定结果。实现使用
  `qualification_status/evidence/message_ids/version` 返回当前快照；
- 电话沟通动作要求 `ROUGH_MATCH` 或 `FULL_MATCH`；面试动作只允许 `FULL_MATCH`；
  两者涉及具体时间时仍必须进入排期确认 API。

BOSS 主动招呼由服务端固定使用 `PLATFORM_DEFAULT` 发送模式，客户端不能覆盖。
动作响应和审计保存实际观察文案；未固定预期文案时接受任意非空平台招呼，固定预期
文案时必须逐字一致。两种模式均不得追加发送千问生成文本。

第十阶段的配置请求增加 `job_scan_enabled`、`emergency_stop`、
`company_cooldown_hours`、
`recruiter_cooldown_hours`、`work_start_hour` 和 `work_end_hour`。职位发现由 Worker
内部编排，不提供绕过策略引擎的公开“扫描并发送”接口。BOSS 运行只允许真实 CDP
执行器，`MOCK` 运行只允许假执行器。

第十二阶段运行治理 API：

- `GET /api/v1/automation/operations/status`：聚合迁移、LLM、选择器、执行器、日历、
  Worker、未知动作、待确认、对账任务和审计差异。
- `GET /api/v1/automation/operations/reconciliation`：查看最近100个对账任务。
- `POST /api/v1/automation/operations/reconciliation/run`：执行一批只读平台回账；不能
  直接重发，超时升级 `MANUAL_REQUIRED`。
- `GET /api/v1/automation/operations/discrepancies`：检查内部来源和状态差异。
- `POST /api/v1/automation/operations/audit/run`：对近期成功动作执行只读平台抽查。

正式运行不提供级别初始化、升级或回退 API。BOSS 和脉脉 Run 直接使用
`/automation/settings` 的全局、平台和策略配置；暂停/恢复 Run、紧急停止、
LLM 熔断、幂等、回读对账及电话/面试时间确认规则继续独立生效。

第十四阶段脉脉推荐 API：

- `GET /api/v1/platform-recommendations`：按平台、判断和状态查询推荐记录；
- `GET /api/v1/platform-recommendations/{id}`：返回脱敏卡片快照、简化判断、原因码、
  动作状态和回读证据；
- `POST /api/v1/platform-recommendations/scan`：触发一次受 Worker 租约和自动化配置约束的
  只读扫描，不直接接受或拒绝；
- `POST /api/v1/platform-recommendations/{id}/dispatch`：重新核对当前卡片后执行服务端
  已保存的 `ACCEPT_AND_SEND_PROFILE` 或 `REJECT_RECOMMENDATION` 判断；请求方不能覆盖
  判断结果；
- `POST /api/v1/platform-recommendations/{id}/reconcile`：只读回查结果未知的推荐动作，
  不允许直接重试点击。

扫描和执行要求启用脉脉平台配置及 `maimai_recommendation_enabled`。同意并发送平台
资料还要求 `maimai_recommendation_resume_enabled`。重复请求必须携带
`Idempotency-Key` 并返回既有动作；页面招聘人、岗位或推荐身份变化返回
`TARGET_MISMATCH`，控件不唯一返回 `ACTION_CONTROL_AMBIGUOUS`，点击后无法回读返回
`OUTCOME_UNKNOWN`。

## 14. 第六阶段排期 API

- `GET/PUT /api/v1/scheduling/settings`：读取或按版本更新时区、工作时间、缓冲、默认时长、通勤和有效期配置。
- `POST /api/v1/scheduling/calendar-events`：导入本地假日历忙闲事件；重复外部 ID 返回原事件。
- `POST /api/v1/scheduling/analyze`：按招聘方消息创建幂等排期请求，解析邀请、检查日历并生成确认回复。
- `GET /api/v1/scheduling/requests`：返回时间确认卡片、平台、招聘方、公司、职位、
  资质状态与证据、冲突状态、候选时间、风险和建议回复。
- `POST /api/v1/scheduling/requests/{id}/approve`：批准或修改具体时间回复。时间必须
  携带时区且严格符合该沟通类型的配置时长；冲突、模糊或信息不完整时只能选择服务端
  返回的候选时间，且招聘方确认改期前不得预先创建日历事件。
- `POST /api/v1/scheduling/requests/{id}/execute`：验证批准状态、任务有效期和日历快照后发送；新冲突退回待确认。
- `POST /api/v1/scheduling/requests/{id}/reject`：幂等拒绝待确认或已批准但尚未执行的时间安排。
- `GET /api/v1/system/calendar-status`：返回供应商、日历 ID 和是否配置，不返回 OAuth 令牌。

`calendar_available=false` 只用于当前本地假适配器模拟供应商不可用，此时必须返回 `UNAVAILABLE`，不得声称日历空闲。批准接口不直接发送；执行接口仍复用浏览器目标复核和动作幂等保护。
配置 Apple 或 Google 真实日历后，客户端提交的 `calendar_available` 不再生效，服务端
以供应商查询结果为准；供应商不可用时安全降级为 `UNAVAILABLE`。

## 15. 当前明确不提供的 API

- 简历文件上传 API；
- 绕过策略引擎直接发送消息或简历的 API；
- 验证码、反检测或任意页面操作 API；
- Outlook 或其他尚未实现的外部日历供应商 API；
- 通过 HTTP 请求传入 API Key 或任意系统提示词的 API。

智谱 GLM-5.2 通过服务端 `ZHIPU_API_KEY` 接入，不提供前端读取密钥的接口；既有千问
密钥不会在智谱模式下复用。供应商切换继续复用相同领域 API。

## 16. R2 消息恢复 API

- `POST /api/v1/messages/{message_id}/replay`：人工重放已隔离消息。
  仅允许 `QUARANTINED`、身份可靠且尚无草稿的消息恢复为 `RECEIVED`；其他状态返回
  `409 INVALID_STATE`。接口写入审计事件，不直接发送任何平台消息。
