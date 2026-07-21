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

## 10. 第一阶段明确不提供的 API

- 对话、消息和回复生成 API；
- 简历上传、选择和发送 API；
- 确认队列和动作执行 API；
- 浏览器会话和平台操作 API；
- 日历、电话和面试 API；
- 真实大模型供应商配置 API。

这些接口只能在对应开发阶段、状态和权限模型完成后增加。
