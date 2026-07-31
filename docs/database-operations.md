# 数据库备份、迁移与回滚流程

## 1. 适用范围

本文定义真实 `job_agent` PostgreSQL 数据库执行 Alembic 迁移前的备份、恢复演练和失败
回滚流程。候选人资料、策略、职位、消息、动作和审计都属于需要保留的数据。

长期原则：

- Worker 和所有平台 Run 停止后才能迁移；
- 迁移前必须创建 PostgreSQL custom-format 备份；
- 备份必须完成清单校验，并定期恢复到名称包含 `_restore_test` 的独立数据库；
- 真实数据库只执行经验证的向前迁移；
- 迁移失败不在原库盲目执行 `alembic downgrade`，而是保留故障现场，从备份恢复到独立
  数据库，验证后切换连接；
- 备份文件权限为 `600`，不得提交 Git，不得上传到公共位置。

## 2. 迁移前检查

1. 确认没有活动 Worker，BOSS、脉脉和 Telegram Run 均为 `PAUSED`。
2. 全局自动化保持 `paused=true`、`emergency_stop=true`。
3. 记录当前代码提交、`alembic current` 和 `alembic heads`。
4. 确认只有一个 Alembic head。
5. 确认磁盘空间足以同时保存当前库、备份和恢复测试库。

任一条件不满足时停止迁移。

## 3. 创建和校验备份

宿主机已安装 PostgreSQL 客户端时使用项目脚本：

```bash
DATABASE_URL='postgresql+psycopg://...' scripts/backup_database.sh
pg_restore --list backups/job-search-agent-YYYYMMDD-HHMMSS.dump
```

本地 Docker 开发环境没有 `pg_dump` 时，使用容器内工具：

```bash
docker compose exec -T postgres pg_dump \
  --format=custom --no-owner --no-acl \
  --username=job_agent --dbname=job_agent \
  > backups/job-search-agent-YYYYMMDD-HHMMSS.dump
chmod 600 backups/job-search-agent-YYYYMMDD-HHMMSS.dump

docker compose exec -T postgres pg_restore --list \
  < backups/job-search-agent-YYYYMMDD-HHMMSS.dump
```

校验标准：命令退出码为零，清单包含 schema、核心表、约束和 Alembic 版本表，文件非空且
权限不宽于 `600`。仅有备份文件而未完成清单或恢复校验，不能视为可回滚。

## 4. 恢复演练

恢复目标必须是独立数据库，名称必须包含 `_restore_test`。不得把恢复演练指向真实
`job_agent` 数据库。

宿主机工具可用时：

```bash
RESTORE_DATABASE_URL='postgresql+psycopg://.../job_agent_restore_test' \
  scripts/restore_rehearsal.sh backups/job-search-agent-YYYYMMDD-HHMMSS.dump
```

恢复后至少验证：

1. `alembic_version` 与备份时记录一致；
2. 候选人、策略、职位、消息、动作和审计表可以查询；
3. 核心表行数与备份前统计一致；
4. API 使用恢复库启动后，只读健康检查通过；
5. 不启动 Worker，不连接真实招聘页面，不执行外部写操作。

恢复测试库的删除属于破坏性操作，必须明确核对数据库名称并单独执行，不写入日常启动脚本。

## 5. 执行迁移

先在恢复测试库执行：

```bash
DATABASE_URL='postgresql+psycopg://.../job_agent_restore_test' alembic upgrade head
```

运行数据库集成测试和只读 API 验证后，才可在真实数据库执行相同的 `alembic upgrade head`。
迁移期间 API 可以停止或保持维护模式，Worker 和真实平台 Run 必须保持暂停。

迁移成功后验证：

- `alembic current` 等于预期 head；
- 数据回填数量和约束符合迁移说明；
- PostgreSQL 集成测试通过；
- API 只读健康检查和关键查询通过；
- 审计与历史动作仍可查询。

## 6. 迁移失败回滚

1. 保持 Worker 停止、Run 暂停和全局紧急停止，不尝试恢复自动写入。
2. 保留失败数据库和完整错误日志，不继续运行新的迁移命令。
3. 创建新的独立恢复数据库，使用迁移前备份恢复。
4. 验证 Alembic 版本、核心表行数、约束和只读 API。
5. 将 API 的 `DATABASE_URL` 切换到验证通过的恢复数据库并重启 API。
6. 原失败数据库保留到根因确认和数据差异核对完成；是否删除必须另行确认。
7. 修复迁移后重新从恢复演练开始，不能跳过备份和测试库验证。

只有迁移及应用回归全部通过，并完成 R 阶段对应的上线门槛后，才能关闭紧急停止、恢复
平台 Run 并启动 Worker。
