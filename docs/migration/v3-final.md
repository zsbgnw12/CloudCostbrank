# Phase 1 终版规划 v3-final

> **状态**：用户已批准全部 20 条推荐答案；以本文为修改 agent 的唯一输入  
> **来源**：v1-roadmap.md + v2-refined.md + v2-questions.md 合并 + 016-019 审查  
> **范围**：Silver 表 rename + 月分区 + taiji 表改名（不动 collector / 不建 Bronze）

---

## §1 决策一览（已拍板）

| # | 决策 | 落地方式 |
|---|---|---|
| D1 | dry-run 在测试环境跑一遍 | runbook 强制；本次代码修改不涉及，留作部署 checklist |
| D2 | 迁移期间 celery replicas=0 | runbook 强制 |
| D3 | Azure PITR 主 + pg_dump 辅 + 必须 restore 验证 | runbook 强制 |
| D4 | keyset 翻页跨 3 个月边界验证 | 写进验收脚本 |
| D5 | 迁移 021 用 `to_regclass` 运行时检测，存在走 RENAME / 不存在走 CREATE | 写进迁移文件 |
| D6 | conftest.py + Makefile 显式 alembic upgrade | 修改 agent 落地 |
| D7 | 016-019 已审完（见 §2），均无风险 | 已完成 |
| D8 | 索引重建进 dry-run | runbook |
| D9 | dashboard 5 条 SQL EXPLAIN ANALYZE 对比 | 验收 agent 跑 |
| D10 | VIEW 下线判定用 `pg_stat_statements` | 落入 monitoring.md 文档（不在本次代码改动）|
| D11 | 类名重命名建 ticket 绑 Phase 5 + TODO 注释 | 修改 agent 在 model 文件加 TODO |
| D12 | default 分区 1/15/25 三次幂等 + 自动修复任务 | 修改 agent 落到 partition_maintenance.py |
| D13 | 4 个根目录写脚本顶部加 `raise` | 修改 agent 落地 |
| D14 | 工期 10-12 工作日（含 review）| 沟通承诺，无代码 |
| D15 | 预留 `etl_run_id BIGINT NULL` 列 | 写进迁移 020 |
| D16 | pgaudit | Phase 1 后跟进 issue |
| D17 | alembic.ini 凭据检查 | 上线 checklist |
| D18 | 016-019 与 020/021 分两次部署 | runbook |
| D19 | 验收口径标"pytest 不覆盖 billing" | 写进验收报告 |
| D20 | 上线前查 pg_policies | 上线 checklist |

---

## §2 016-019 迁移审查结果（D7 完成）

四个迁移**全部安全**，可一次性 upgrade，无数据搬运风险：

| 迁移 | 操作 | 风险 | 备注 |
|---|---|:-:|---|
| 016 `projects_recycled_at_index` | `CREATE INDEX ix_projects_recycled_at` + drop 重建（if_exists 保护）| 低 | 现有列加索引，纯元数据 |
| 017 `billing_data_extra_fields` | `billing_data` 加 6 列（全部 nullable）+ `billing_daily_summary` 加 2 列 + 加 `ix_billing_sku_id_date` | 低 | 全 nullable，无回填 |
| 018 `credits_split` | 加 `credits_committed/credits_other` + summary 表对应列（全 nullable）| 低 | 全 nullable |
| 019 `billing_thorough_fields` | 删 018 加的 4 列 + 加 9 个新列（全 nullable）+ 加 2 个索引 + `cost_type` 设 NOT NULL DEFAULT 'regular' | 低-中 | 唯一会触及存量数据的是 `cost_type` 加 NOT NULL 约束；PG 用 server_default 不重写表，但要存量已无 NULL（验证 `SELECT COUNT(*) FROM billing_data WHERE cost_type IS NULL` = 0 才放行）|

**预期 016→019 总耗时**：< 30 秒（纯 DDL，无数据搬运）。  
**前置检查**（部署前在生产跑）：`SELECT COUNT(*) FROM billing_data WHERE cost_type IS NULL` —— 必须为 0；非 0 时先 `UPDATE billing_data SET cost_type='regular' WHERE cost_type IS NULL` 再上 019。

---

## §3 修改 agent 的工作清单（白名单内才能动）

### 必须创建的新文件

| 文件 | 用途 |
|---|---|
| `alembic/versions/<hash>_020_billing_summary_partition.py` | 主迁移：建分区父表 + 搬数据 + 建 VIEW |
| `alembic/versions/<hash>_021_rename_taiji_log_raw.py` | 改名 taiji 表（带 to_regclass 分支）|
| `tasks/partition_maintenance.py` | Celery beat 任务：每月 1/15/25 幂等创建未来 3 个月分区 + default 分区自动修复 |
| `tests/conftest.py`（如不存在则创建）| pytest 启动前 `alembic upgrade head` |
| `Makefile`（如不存在则创建）| `make dev-setup` 跑 alembic upgrade |
| `scripts/dev_setup.sh` | 替代/补充 Makefile 的本地启动脚本 |

### 必须修改的现有文件

| 文件 | 改动 |
|---|---|
| `app/models/billing.py` | `__tablename__ = "billing_summary"`；保留类名 `BillingData`；顶部加 `# TODO(Phase5): rename to BillingSummary, see ticket #...` |
| `app/models/taiji_log_raw.py` → 重命名为 `app/models/billing_raw_taiji.py` | 类名改为 `BillingRawTaiji`；`__tablename__ = "billing_raw_taiji"`；改约束名 `pk_billing_raw_taiji`、索引名 `ix_billing_raw_taiji_*` |
| `app/models/__init__.py` | import 改 `BillingRawTaiji`；`__all__` 同步 |
| `app/services/sync_service.py` | 4 处 `billing_data` 字面量改 `billing_summary`（行 192, 244, 270, 705）；2 处 `taiji_log_raw` 字面量改 `billing_raw_taiji`（行 550, 740）；`from app.models.taiji_log_raw import TaijiLogRaw` 改 `from app.models.billing_raw_taiji import BillingRawTaiji`；`select(TaijiLogRaw)` 改 `select(BillingRawTaiji)` |
| `app/api/sync.py` | 行 68 裸 SQL `billing_data` → `billing_summary` |
| `app/api/metering.py` | docstring/Field description 中的 `billing_data` → `billing_summary`（行 1, 58, 413, 552）；`TaijiLogRaw` 改 `BillingRawTaiji` |
| `app/services/dashboard_service.py` | 注释 `billing_data` → `billing_summary`（行 4, 381, 484）|
| `app/schemas/metering.py` | docstring 第 1 行 `billing_data` → `billing_summary` |
| `app/collectors/taiji_collector.py` | `TaijiLogRaw` 类引用改 `BillingRawTaiji` |
| `app/main.py` | **删第 54 行** `await conn.run_sync(Base.metadata.create_all)` 及上下文（保持 lifespan 函数能跑）|
| `tasks/celery_app.py` | `include` 列表加 `"tasks.partition_maintenance"`；`beat_schedule` 加 3 条 cron（每月 1/15/25 02:00 跑分区维护） |
| `consolidate_null_region.py` | 顶部加 `raise RuntimeError("billing_data 已 rename 为 billing_summary，复用前请改表名")` |
| `import_csv_history.py` | 同上 |
| `_exec_step1_delete.py` | 同上 |
| `_exec_step3_backfill.py` | 同上 |
| `docs/DATABASE_SCHEMA.md` | 表名引用更新 |
| `README.md` | 加一段"本地开发：先跑 `make dev-setup` / `alembic upgrade head`" |

### **绝对不许动**的文件

- `app/collectors/gcp_collector.py / aws_collector.py / azure_collector.py`（业务逻辑层）
- `app/collectors/taiji_collector.py` 的业务逻辑（**只能改 import，不能改聚合 / cost 计算**）
- `app/models/billing_daily_summary.py`（表结构本 Phase 不动）
- `app/api/dashboard.py / billing.py / projects.py / service_accounts.py`（除非只改 docstring）
- 前端目录 `front/`
- `cloudcost/` 根目录除上述 4 个写脚本外的所有 `_*.py` / `verify_*.py` / `backfill_*.py`
- 之前的所有 alembic 迁移（001-019）

---

## §4 迁移 020 详细规格

### 文件名 & revision 链

```
<8字符随机hash>_020_billing_summary_partition.py
revision        = "<8字符hash>"
down_revision   = "g3a4b5c6d7e8"   # 即 019
```

### upgrade() 步骤（按序执行，单事务）

1. **前置检查**：`SELECT COUNT(*) FROM billing_data WHERE cost_type IS NULL` —— 非 0 报错退出（应在 019 后已为 0）。
2. **建分区父表** `billing_summary`：
   - 字段集合 = `billing_data` 当前所有字段 + 新增 `etl_run_id BIGINT NULL`（D15）
   - PK = `(id, date)`（含分区键，D 预案 a）
   - `PARTITION BY RANGE (date)`
   - server_default、注释保留
3. **建 default 分区** `billing_summary_default`。
4. **算历史月范围**：`SELECT date_trunc('month', MIN(date)), date_trunc('month', MAX(date)) FROM billing_data`，循环建 `billing_summary_YYYYMM` 子分区，**外加未来 3 个月**（防 default 兜底）。
5. **重建索引/约束** —— 在父表上 declare（自动下放到子分区）：
   - `uix_billing_dedup`（含 `cost_type`，分区键 `date` 已含）
   - `ix_billing_ds_date`、`ix_billing_project_date_cost`、`ix_billing_provider_date_cost`、`ix_billing_invoice_month`、`ix_billing_account_id_date`、`ix_billing_sku_id_date`
6. **搬数据**：`INSERT INTO billing_summary (col1, col2, ..., etl_run_id) SELECT col1, col2, ..., NULL FROM billing_data`。
7. **重命名旧表**：`ALTER TABLE billing_data RENAME TO _billing_data_legacy`（不 DROP，30 天观察期后人工清）。
8. **建只读 VIEW**：`CREATE VIEW billing_data AS SELECT * FROM billing_summary`。
9. **同步序列**：`SELECT setval('billing_summary_id_seq', (SELECT MAX(id) FROM billing_summary))`，确保新插入的 `id` 不冲突。

### downgrade() 步骤

1. `DROP VIEW billing_data`
2. `ALTER TABLE _billing_data_legacy RENAME TO billing_data`
3. `DROP TABLE billing_summary CASCADE`（连带子分区）

如果 `_billing_data_legacy` 已被人工 DROP（30 天后），`downgrade()` 必须报错明确告知"无法回滚，请从 PITR 恢复"。

---

## §5 迁移 021 详细规格

```
<8字符hash>_021_rename_taiji_log_raw.py
revision        = "<8字符hash>"
down_revision   = "<020 的 hash>"
```

### upgrade()

```sql
-- 用 sa.text 执行
DO $$
BEGIN
  IF to_regclass('public.taiji_log_raw') IS NOT NULL THEN
    -- 表存在：原子 RENAME 路径
    ALTER TABLE taiji_log_raw RENAME TO billing_raw_taiji;
    ALTER INDEX ix_taiji_log_raw_ds_date RENAME TO ix_billing_raw_taiji_ds_date;
    ALTER INDEX ix_taiji_log_raw_ds_ingested RENAME TO ix_billing_raw_taiji_ds_ingested;
    ALTER TABLE billing_raw_taiji RENAME CONSTRAINT pk_taiji_log_raw TO pk_billing_raw_taiji;
  ELSE
    -- 表不存在（生产从未 boot 过应用）：直接建新表
    CREATE TABLE billing_raw_taiji (...完整字段...);
    -- 索引 / 约束按 model 定义建
  END IF;
END $$;
```

### downgrade()

镜像反向（RENAME 回 / DROP 新表）。

---

## §6 partition_maintenance.py 详细规格

```python
# tasks/partition_maintenance.py 接口约定（不写实现）

@celery_app.task(name="tasks.partition_maintenance.ensure_billing_summary_partition")
def ensure_billing_summary_partition(months_ahead: int = 3):
    """
    幂等：检查未来 N 个月分区是否存在，缺则 CREATE TABLE IF NOT EXISTS ... PARTITION OF billing_summary。
    检查 default 分区行数：> 0 则触发 fix_default_partition。
    """

@celery_app.task(name="tasks.partition_maintenance.fix_default_partition")
def fix_default_partition():
    """
    自动修复：把 default 分区的数据按 date 移到对应正确月分区。
    1. SELECT DISTINCT date_trunc('month', date) FROM billing_summary_default
    2. 对每个月：CREATE 分区（如不存在） + INSERT FROM SELECT + DELETE
    3. 写日志 + 触发告警
    """
```

### Celery beat 注册

`tasks/celery_app.py` 的 `beat_schedule` 增加：

```python
"ensure-billing-partitions-1": {
    "task": "tasks.partition_maintenance.ensure_billing_summary_partition",
    "schedule": crontab(day_of_month=1, hour=2, minute=0),
},
"ensure-billing-partitions-15": {
    "task": "tasks.partition_maintenance.ensure_billing_summary_partition",
    "schedule": crontab(day_of_month=15, hour=2, minute=0),
},
"ensure-billing-partitions-25": {
    "task": "tasks.partition_maintenance.ensure_billing_summary_partition",
    "schedule": crontab(day_of_month=25, hour=2, minute=0),
},
```

`include` 列表加 `"tasks.partition_maintenance"`。

---

## §7 验收口径（验收 agent 检查项）

- [ ] **静态检查**：`python -c "import ast; ast.parse(open(p, encoding='utf-8').read())"` 对每个新建/修改的 .py 文件全绿
- [ ] `app/models/__init__.py` 能导入无 ImportError
- [ ] `BillingRawTaiji` 替换完整：`grep -rn "TaijiLogRaw" app/` 应 0 命中
- [ ] `billing_data` 仅在 docstring/注释中保留，`grep -rn '"billing_data"' app/` 应只剩 model 级别和 metering docstring（已改）
- [ ] 4 个根目录脚本顶部都有 `raise RuntimeError(...)`
- [ ] `app/main.py` 的 `Base.metadata.create_all` 行已删
- [ ] `tasks/celery_app.py` 的 beat_schedule 含 3 条 partition 任务
- [ ] `tests/conftest.py` 含 alembic upgrade fixture（或 session-scoped autouse）
- [ ] Makefile 含 `dev-setup` target
- [ ] `alembic upgrade head` 在本地能跑（如果有 docker PG）
- [ ] 迁移 020 / 021 都有完整 `upgrade()` 和 `downgrade()`
- [ ] 020 的 `upgrade()` 含前置 `cost_type IS NULL` 检查
- [ ] 020 的 `etl_run_id` 列已建（D15）
- [ ] 020 末尾有 `setval('billing_summary_id_seq', ...)`
- [ ] 021 含 `to_regclass` 分支
- [ ] **改动行数统计**（`git diff --stat`）记录到验收报告
- [ ] **偏差报告**：列出哪些是 v3 规划内的 / 哪些是 agent 自由发挥（应该为 0 行自由发挥）

### 不在本次验收范围（由部署 / 用户决定）

- 生产 dry-run（D1）
- 备份 restore 演练（D3）
- 跨月 keyset 翻页验证（D4，需要真实数据）
- EXPLAIN ANALYZE 对比（D9，需要数据库连接）
- pgaudit / pg_policies / alembic.ini 凭据检查（D16/D17/D20）

这些是**部署时的 checklist**，不是代码改动的一部分。

---

## §8 给修改 agent 的指令模板

修改 agent 必须严格遵守：

1. **白名单**：只改本文 §3 列出的文件；新建文件也必须在 §3 范围内
2. **不允许超界**：发现 v3 规划外的 bug → 写到 `docs/migration/out-of-scope.md`，**不修**
3. **每个文件改完跑 `python -m py_compile <file>` 自检**
4. **完成后输出 `docs/migration/implementation-diff.md`**：列出所有改动文件 + 行数 + 一句话摘要
5. **遇到歧义/未规定的细节**：默认按"最保守 / 最不破坏现有行为"的原则做选择，并在 implementation-diff.md 末尾的"自由心证"段落标注
6. **不能跑 alembic upgrade**（修改 agent 不接触 DB）
7. **不能修改 git config / 不能 commit 不能 push**

---

## §9 入场前的 final 检查（修改 agent 启动前 5 分钟）

- [ ] v1-roadmap.md / v2-refined.md / v2-questions.md / v3-final.md 都已落到 docs/migration/
- [ ] 用户已批准全部 20 条决策（**已确认**）
- [ ] 016-019 审查完毕，无 blocker
- [ ] 修改 agent 的 prompt 含明确白名单 + 边界
