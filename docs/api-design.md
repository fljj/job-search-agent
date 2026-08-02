# 当前 API 设计

## 1. 通用约定

- 基础路径：`/api/v1`；健康检查为 `GET /health`。
- 请求和响应 JSON 字段使用英文，时间使用带时区 ISO 8601，主键使用 UUID。
- 成功响应：`{"data": ..., "meta": {"request_id": "..."}}`。
- 列表响应：`data.items/page/page_size/total`；稳定顺序为业务时间倒序加 UUID 倒序。
- 错误响应：`{"error":{"code","message","details","request_id"}}`。
- API 默认只接受本机请求；非本机部署必须设置 `API_ACCESS_TOKEN`，并携带
  `X-Local-Access-Token`。
- 资源均限制为当前单用户数据；前端权限不能替代服务端策略检查。

### 1.1 人工确认与排期统计

- `GET /confirmation-tasks` 返回普通人工确认任务，包含目标会话、回复内容、原因、到期时间
  和当前状态；待确认但已过期的任务按 `EXPIRED` 展示。
- `GET /automation/operations/status` 同时返回
  `pending_human_confirmation_count` 和 `pending_schedule_confirmation_count`。前者只统计未
  过期的普通人工任务，后者只统计未过期的电话或面试时间确认，两者不得混用。

## 2. 候选人、知识库和简历

| 方法与路径 | 用途 |
| --- | --- |
| `GET /profile` | 获取当前候选人资料和版本 |
| `PUT /profile` | 使用乐观版本更新完整资料 |
| `GET /knowledge-items` | 获取可信事实 |
| `POST /knowledge-items` | 新增可信事实 |
| `PUT /knowledge-items/{id}` | 更新、启用或停用事实，必须提交版本 |
| `GET /resumes` | 列出已登记的平台附件 |
| `POST /resumes` | 登记附件元数据 |
| `PUT /resumes/{id}` | 更新附件元数据 |
| `GET /resumes/select` | 按平台和方向选择附件 |

知识库事实必须记录来源、敏感级别、有效期、版本及是否允许自动引用。简历 API 只登记招聘
网站内已有附件，不上传任意本地文件。

## 3. 求职策略

| 方法与路径 | 用途 |
| --- | --- |
| `POST /strategies` | 创建策略 |
| `GET /strategies` | 列出策略，可按启用状态筛选 |
| `GET /strategies/{id}` | 获取完整策略 |
| `PUT /strategies/{id}` | 使用当前版本完整更新策略 |
| `PATCH /strategies/{id}/status` | 启用或停用策略 |

策略包含岗位方向、地点、工作模式、薪资、行业、公司黑名单、兼职、学历和到岗口径。服务端
校验规则冲突和版本，不接受前端计算结果。

## 4. 职位、解析和沟通决策

| 方法与路径 | 用途 |
| --- | --- |
| `POST /jobs/import` | 导入单个模拟或适配器 JD |
| `POST /jobs/import/batch` | 批量导入，逐项返回结果 |
| `GET /jobs` | SQL 筛选和稳定分页，可按策略及沟通决策过滤 |
| `GET /jobs/{id}` | 获取职位 |
| `POST /jobs/{id}/parse` | 执行 RULE 或 LLM 解析 |
| `GET /jobs/{id}/parsed-details` | 分页查看解析版本 |
| `GET /jobs/{id}/parsed-details/{detail_id}` | 获取解析快照 |
| `POST /jobs/{id}/decisions` | 硬过滤后创建沟通决策 |
| `POST /jobs/{id}/decisions/re-evaluate` | 使用幂等键显式重新决策 |
| `POST /jobs/decisions/batch` | 批量创建沟通决策 |
| `GET /jobs/{id}/decisions` | 获取沟通决策历史 |
| `GET /decisions/{id}` | 获取决策、依据、不确定项和硬排除信息 |

职位导入可携带可选 `source_url`。服务端根据 `source` 校验平台域名并规范化链接；
`POST /jobs/import`、`GET /jobs` 和 `GET /jobs/{id}` 的职位响应均返回可选
`source_url`。非对应招聘平台的链接返回 `400 VALIDATION_ERROR`。

硬性排除在调用 LLM 前执行。LLM 决策必须通过枚举、置信度、字段长度、模型及提示版本校验。

## 5. 对话、资格和草稿

| 方法与路径 | 用途 |
| --- | --- |
| `GET /conversations` | 按平台/职位稳定分页 |
| `POST /conversations` | 导入或创建会话 |
| `POST /conversations/{id}/messages` | 幂等导入招聘方消息 |
| `POST /conversations/{id}/reopen` | 有明确新证据时重开终态会话 |
| `POST /messages/{id}/replay` | 人工恢复隔离消息 |
| `GET /conversations/{id}/qualification` | 获取资格快照 |
| `POST /conversations/{id}/qualification/evaluate` | 基于当前证据重新评估资格 |
| `POST /drafts/reply` | 经 Reply Router 创建回复草稿 |
| `POST /drafts/greeting` | 基于有效 `CONTACT` 决策创建招呼草稿 |
| `POST /drafts/resume` | 基于明确入站请求创建简历草稿 |
| `PATCH /drafts/{id}` | 人工编辑尚未执行的草稿 |

草稿记录 `RULE_TEMPLATE/KNOWLEDGE_BASE/LLM/HUMAN` 来源。客户端不能直接指定资格结论、
策略授权或发送状态。

## 6. 动作、确认和对账

| 方法与路径 | 用途与约束 |
| --- | --- |
| `GET /confirmation-tasks` | 列出电话、面试等人工确认任务 |
| `POST /confirmation-tasks/{id}/approve` | 批准；要求 `Idempotency-Key` |
| `POST /confirmation-tasks/{id}/modify` | 修改待确认内容，不执行发送 |
| `POST /confirmation-tasks/{id}/reject` | 拒绝并终止任务 |
| `POST /actions/{id}/execute` | 执行已批准动作，服务端重新核验目标 |
| `POST /actions/{id}/retry` | 只批准明确未写入的可重试失败 |
| `POST /actions/{id}/reconcile` | 对未知结果执行只读回查 |
| `POST /automation/dispatch` | 对自动草稿执行统一策略授权和动作创建 |

所有写入动作必须有策略来源和幂等指纹。`OUTCOME_UNKNOWN` 不得通过重试接口直接重发。

## 7. 自动化运行和运维

| 方法与路径 | 用途 |
| --- | --- |
| `GET/PUT /automation/settings` | 获取或局部更新全局、平台、策略配置 |
| `POST/GET /automation/runs` | 创建或列出平台 Run；平台支持 `BOSS/MAIMAI/LIEPIN/MOCK` |
| `GET /automation/runs/{id}` | 获取 Run |
| `POST /automation/runs/{id}/pause` | 暂停 Run |
| `POST /automation/runs/{id}/resume` | 恢复 Run |
| `POST /automation/runs/{id}/tick` | 测试/运维触发一次处理 |
| `GET /automation/actions` | 分页查看自动动作和准确总数 |
| `GET /automation/overview` | 独立数据库聚合总览 |
| `GET /automation/operations/status` | Run、Worker、平台和能力状态 |
| `GET /automation/operations/reconciliation` | 查看对账任务 |
| `POST /automation/operations/reconciliation/run` | 执行一批只读对账 |
| `GET /automation/operations/discrepancies` | 查看审计差异 |
| `POST /automation/operations/audit/run` | 抽查成功动作 |
| `POST /automation/llm-circuit/retry` | 手动执行单次模型健康探测 |

自动化设置不包含小时/每日投递配额、低分婉拒开关、回复置信度或简历最低分。公司和招聘人
冷却仅用于防重复。

猎聘 Run 先执行消息发现、Reply Router 和获授权动作，再执行首页职位发现、硬过滤、沟通决策及
获授权的主动招呼。API 可以
创建、暂停、恢复猎聘 Run；现有消息、职位和总览接口的 `platform/source` 筛选直接支持
`LIEPIN`，不增加平台专用 API。L3 生成的猎聘草稿持久化为不可派发，直接派发、编辑后派发
或人工批准均由服务端拒绝。猎聘正式动作复用现有动作查询、对账和审计接口；是否执行由
Run 状态、自动化设置、策略授权和平台安全状态共同决定，不再使用一次性发布开关。

## 8. 平台推荐和浏览器只读

| 方法与路径 | 用途 |
| --- | --- |
| `GET /platform-recommendations` | 查询脉脉系统推荐 |
| `GET /platform-recommendations/{id}` | 获取推荐证据 |
| `POST /platform-recommendations/scan` | 扫描当前推荐页 |
| `POST /platform-recommendations/{id}/dispatch` | 按简化资格决定同意或拒绝 |
| `POST /platform-recommendations/{id}/reconcile` | 只读回查结果 |
| `POST /browser/read-current` | 读取当前受支持页面，不授权写入 |
| `GET /browser/sessions` | 查看平台会话状态 |

MAIMAI Run 只使用平台推荐接口；`POST /browser/read-current` 不接受 MAIMAI
私信页读取。会话创建和消息导入拒绝 `platform=MAIMAI`，`GET /conversations`
不返回历史脉脉会话。

## 9. 排期和日历

| 方法与路径 | 用途 |
| --- | --- |
| `GET/PUT /scheduling/settings` | 排期偏好 |
| `POST /scheduling/calendar-events` | MOCK 测试事件导入 |
| `POST /scheduling/analyze` | 分析邀请并查询日历 |
| `GET /scheduling/requests` | 查看排期请求 |
| `POST /scheduling/requests/{id}/approve` | 用户批准或修改具体时间 |
| `POST /scheduling/requests/{id}/execute` | 页面复核后发送并按授权写日历 |
| `POST /scheduling/requests/{id}/reject` | 拒绝请求 |

## 10. 系统配置

- `GET/PUT /system/llm-status`：供应商只能从环境允许列表中选择，模型名称为非空自由文本；
  请求和响应包含 `timeout_seconds`（1～300 秒）。供应商、模型和超时时间保存后，后续
  新建的 LLM 调用立即生效。Base URL 和 API Key
  只来自环境变量。
- `GET /system/calendar-status`：返回 Apple、Google 或 Mock 日历能力状态。

## 11. 错误码和幂等

常用错误：`RESOURCE_NOT_FOUND`（404）、`VERSION_CONFLICT`（409）、
`INVALID_REQUEST`（400）、`DEPENDENCY_UNAVAILABLE`（503）、`LOCAL_ACCESS_DENIED`
（401/403）及类型化 LLM 错误（503）。

消息以平台外部消息 ID 或稳定内容指纹去重；职位以平台外部职位 ID和内容哈希去重；草稿、
动作、简历发送、推荐和排期均有各自唯一指纹。调用方不得通过更换请求 ID 绕过去重。
