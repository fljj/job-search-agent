# R5 遗留清理证据

本文记录 R5 清理时的静态引用结论，不作为当前业务规范。

## 已删除

`automation_settings` 的以下字段仅在 ORM、Pydantic 自动化规则、配置合并和序列化之间
自循环，没有动作授权读取者：

- `low_score_decline_enabled`；
- `auto_reply_min_confidence`；
- `auto_resume_min_score`。

真正的 LLM 回复置信度由 `packages/conversation_agent/models.py` 的 Reply Router 配置读取，
不属于外部动作设置。入站简历由明确请求、资格、附件和幂等控制，不读取分数门槛。

删除范围包括生产模型、API schema、合并逻辑、ORM、README 示例和测试夹具；数据库列由
迁移 `20260731_0039` 删除。新 API 使用 `extra=forbid`，旧字段会返回校验错误，不会静默写入。

## 保留

- `ActionType.LOW_SCORE_DECLINE`：只有历史兼容测试和状态机对账集合读取；生产策略对该动作
  返回 `DENY`。保留枚举是为了读取旧审计记录，不代表可创建新动作。
- `adapters/browser/playwright_actions.py`：当前唯一真实 CDP 写执行器，同时被 Worker、人工动作
  和对账服务引用，不是可删除 fallback。`fake_actions.py` 仅用于测试。
- `packages/scoring/engine.py`：仍被硬过滤职位的有效状态计算、Fake LLM Provider 和评分单测
  读取。未满足“无生产读取者”条件，因此不删除或搬迁。
- 历史 Alembic 文件：完整迁移链需要，禁止因字段后来删除而改写。

## 文档处理

旧阶段版本移动到：

- `development-history.md`；
- `product-requirements-history.md`；
- `architecture-history.md`；
- `api-history.md`；
- `data-model-history.md`。

根级专项文档只描述当前真实行为；历史文件只用于追溯决策和迁移背景。

## 动作和展示统一

- 生产服务中的动作类型比较和赋值统一引用 `ActionType.*.value`，数据库和 API 字符串值不变；
- 职位、消息、总览和自动化页面的业务状态中文名称统一由
  `apps/web/src/pages/business-labels.ts` 提供；
- 评分维度名称仍保留在职位页，因为它们是页面字段标题，不是运行状态映射。
