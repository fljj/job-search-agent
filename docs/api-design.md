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

## 4. 职位、解析和评分

| 方法与路径 | 用途 |
| --- | --- |
| `POST /jobs/import` | 导入单个模拟或适配器 JD |
| `POST /jobs/import/batch` | 批量导入，逐项返回结果 |
| `GET /jobs` | SQL 筛选和稳定分页，可按策略评分字段过滤 |
| `GET /jobs/{id}` | 获取职位 |
| `POST /jobs/{id}/parse` | 执行 RULE 或 LLM 解析 |
| `GET /jobs/{id}/parsed-details` | 分页查看解析版本 |
| `GET /jobs/{id}/parsed-details/{detail_id}` | 获取解析快照 |
| `POST /jobs/{id}/scores` | 硬过滤后创建评分 |
| `POST /jobs/{id}/scores/re-evaluate` | 显式重新评估 |
| `POST /jobs/scores/batch` | 批量评分 |
| `GET /jobs/{id}/scores` | 获取评分历史 |
| `GET /scores/{id}` | 获取评分详情、证据和风险 |

硬性排除在调用 LLM 前执行。LLM 分数必须通过维度、总和、证据、模型及提示版本校验。

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
| `POST /drafts/greeting` | 基于有效评分创建招呼草稿 |
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
| `POST/GET /automation/runs` | 创建或列出平台 Run |
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

- `GET/PUT /system/llm-status`：读取或切换环境允许且已配置密钥的供应商和模型；API Key
  只来自环境变量。
- `GET /system/calendar-status`：返回 Apple、Google 或 Mock 日历能力状态。

## 11. 错误码和幂等

常用错误：`RESOURCE_NOT_FOUND`（404）、`VERSION_CONFLICT`（409）、
`INVALID_REQUEST`（400）、`DEPENDENCY_UNAVAILABLE`（503）、`LOCAL_ACCESS_DENIED`
（401/403）及类型化 LLM 错误（503）。

消息以平台外部消息 ID 或稳定内容指纹去重；职位以平台外部职位 ID和内容哈希去重；草稿、
动作、简历发送、推荐和排期均有各自唯一指纹。调用方不得通过更换请求 ID 绕过去重。
