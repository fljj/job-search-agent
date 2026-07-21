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

- `name`、`enabled`、`candidate_profile_id`；
- `title_rules`、`accepted_seniority_levels`；
- `work_mode_rules` 及允许地点；
- `salary_rules` 及计分区间；
- `industry_rules`、`company_blacklist`；
- `accept_outsourcing`、`accept_headhunter`、`max_posted_days`。

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

查询参数：`strategy_id`、`grade`、`eligibility`、`effective_job_status`、`hard_rejected`、`work_mode`、`page`、`page_size`。评分结果类筛选必须与 `strategy_id` 一起使用。

当指定 `strategy_id` 时，返回该策略的最新评分摘要。

### `GET /api/v1/jobs/{job_id}`

返回原始职位、规范化字段、最新解析和可选评分摘要。

## 5. JD 解析 API

### `POST /api/v1/jobs/{job_id}/parse`

请求：

```json
{
  "mode": "RULE"
}
```

第一阶段允许 `RULE/FAKE_LLM/HYBRID_TEST`，不暴露真实外部模型配置。

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

响应包含：

- `total_score`、`grade`；
- `eligibility`、`hard_rejected`；
- `source_status`、`effective_job_status`、`action_blockers`；
- 七个维度得分和规则明细；
- `rejection_reasons`、`match_reasons`、`risk_notes`；
- `strategy_version`、`profile_version`、`parser_version`、`scoring_version`。

### `POST /api/v1/jobs/scores/batch`

请求包含 `job_ids`、`strategy_id`、`candidate_profile_id`。逐条返回成功或失败结果。

### `GET /api/v1/jobs/{job_id}/scores`

查询参数：`strategy_id`、`page`、`page_size`。

### `GET /api/v1/scores/{score_id}`

返回完整评分、明细、排除证据和输入快照摘要。敏感原始数据按权限裁剪。

## 7. HTTP 状态和错误码

| HTTP | 错误码 | 含义 |
|---:|---|---|
| 400 | `INVALID_REQUEST` | 请求语义不合法 |
| 401 | `UNAUTHENTICATED` | 未建立用户身份 |
| 403 | `FORBIDDEN` | 无资源访问权限 |
| 404 | `RESOURCE_NOT_FOUND` | 资源不存在或不属于当前用户 |
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
- `POST /api/v1/drafts/reply`：请求包含 `message_id`，返回草稿、事实 ID、置信度、风险、决策和确认任务 ID。
- `POST /api/v1/drafts/greeting`：请求包含 `job_score_id`，仅基于 JD 与已验证知识生成个性化招呼草稿。
- `GET /api/v1/confirmation-tasks`：查看当前生成的待确认决策数据；本阶段不允许执行或审批。

草稿接口按输入、知识版本和生成器版本幂等。第二阶段不提供“批准后发送”或其他执行 API。

## 11. 第三阶段浏览器只读 API

- `POST /api/v1/browser/read-current`：连接用户手动启动的本机 CDP 会话，读取当前聚焦页面。
- `GET /api/v1/browser/sessions`：查看 BOSS/脉脉最后只读检查状态和原因码。

`read-current` 请求包含 `platform`、`cdp_url`，并可选包含 `job_id/expected_company/expected_job_title/expected_recruiter`。读取对话页时 `job_id` 必填。

只允许本机 `localhost/127.0.0.1/::1` HTTP(S) CDP 端点且 URL 不得包含凭证。返回会话状态、页面类型、原因码、导入资源 ID、证据 ID 和重复标记。

未登录、验证页、页面结构变化或目标不一致时只保存失败证据，不导入职位或消息。

## 12. 第四阶段人工确认与执行 API

- `GET /api/v1/confirmation-tasks`：返回草稿、动作类型、状态、原因码、置信度和过期时间。
- `POST /api/v1/confirmation-tasks/resume`：为对话中的已登记附件创建发送确认。
- `POST /api/v1/confirmation-tasks/{id}/approve`：必须携带 `Idempotency-Key`，仅生成 `APPROVED` 动作，不立即发送。
- `POST /api/v1/confirmation-tasks/{id}/modify`：敏感检查后废弃旧任务，创建新的 `PENDING_APPROVAL` 任务。
- `POST /api/v1/confirmation-tasks/{id}/reject`：将未批准任务终止为 `CANCELLED`。
- `POST /api/v1/actions/{id}/execute`：原子占用已批准动作，执行前重新复核页面目标并发送。
- `POST /api/v1/actions/{id}/retry`：仅允许用户将 `FAILED_RETRYABLE` 重新批准；`OUTCOME_UNKNOWN` 不可重试。

确认任务有效期由 `conversation-policy.json` 的 `confirmation_ttl_hours` 配置。幂等键只能绑定一个确认任务；相同对话中的相同发送动作即使更换幂等键也会被拒绝。

## 13. 当前明确不提供的 API

- 简历文件上传 API；
- 未经人工确认的消息或简历发送 API；
- 验证码、反检测或任意页面操作 API；
- 日历、电话和面试 API；
- 真实大模型供应商配置 API。

这些接口只能在对应开发阶段、状态和权限模型完成后增加。
