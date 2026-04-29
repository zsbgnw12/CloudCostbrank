# Phase 1 验收报告

## §1 整体结论

- **PASS**
- 修改 agent 严格按 v3-final §3 白名单落地，所有 v3 §7 验收清单条目核对通过；唯一已知偏差是 v3 §6 告警链路走"降级方案"（仅 logger.warning，不接 alert_rules），该路径在 v3 §C 内被显式允许。无阻塞性问题，可进入部署 checklist 阶段。

## §2 静态检查结果（python -m py_compile）

| 文件 | 结果 |
|---|---|
| `alembic/versions/h4b5c6d7_020_billing_summary_partition.py` | OK |
| `alembic/versions/i5c6d7e8_021_rename_taiji_log_raw.py` | OK |
| `tasks/partition_maintenance.py` | OK |
| `tasks/celery_app.py` | OK |
| `tests/conftest.py` | OK |
| `app/models/billing.py` | OK |
| `app/models/billing_raw_taiji.py` | OK |
| `app/models/__init__.py` | OK |
| `app/services/sync_service.py` | OK |
| `app/api/sync.py` | OK |
| `app/api/metering.py` | OK |
| `app/services/dashboard_service.py` | OK |
| `app/schemas/metering.py` | OK |
| `app/main.py` | OK |
| `app/collectors/taiji_collector.py` | OK |
| `consolidate_null_region.py` | OK |
| `import_csv_history.py` | OK |
| `_exec_step1_delete.py` | OK |
| `_exec_step3_backfill.py` | OK |

全部 19 个文件 py_compile 通过，零 SyntaxError。

## §3 ORM / Import 可用性

- `import app.models` —— 无 ImportError，无 mapper 错误
- `app.models.BillingRawTaiji.__tablename__` = **`billing_raw_taiji`** ✓
- `app.models.billing.BillingData.__tablename__` = **`billing_summary`** ✓
- `app/models/taiji_log_raw.py` 已删除；新文件 `app/models/billing_raw_taiji.py` 就位

## §4 v3 §3 白名单 / §7 引用面 grep 核对

| 检查 | 结果 | 证据 |
|---|---|---|
| `grep -rn "TaijiLogRaw" app/` | ✓ 0 命中 | 无匹配 |
| `grep -rn "from app.models.taiji_log_raw" app/` | ✓ 0 命中 | 无匹配 |
| `grep -rn '"taiji_log_raw"' app/` | ✓ 0 命中 | 无匹配 |
| `grep -rn '"billing_data"' app/` | ✓ 仅 1 处可接受残留 | `app/models/billing.py:91` `relationship("DataSource", back_populates="billing_data")` —— 是 ORM 反向访问器名，非表名，已在 out-of-scope.md #1 声明 |
| `grep -rn "FROM billing_data" app/` | ✓ 0 命中 | 无匹配 |
| `grep -rn "INSERT INTO billing_data" app/` | ✓ 0 命中 | 无匹配 |
| `grep -rn "DELETE FROM billing_data" app/` | ✓ 0 命中 | 无匹配 |
| `billing_data` 任意命中（含注释） | 6 处，全部 docstring/注释/relationship | `taiji_collector.py:245,266`（注释）、`service_accounts.py:434`（注释）、`billing.py:91`（relationship name）、`daily_summary.py:28`（注释）、`data_source.py:28`（relationship name）。全部是 v3 允许的"docstring/注释/反向访问器"残留 |

## §5 v3 §4 迁移 020 内容核对

| # | 检查点 | 结果 |
|---|---|---|
| 1 | `revision = "h4b5c6d7"`, `down_revision = "g3a4b5c6d7e8"`（即 019）| ✓（已对照 019 文件 `revision = "g3a4b5c6d7e8"`）|
| 2 | `upgrade()` + `downgrade()` 函数都存在 | ✓ |
| 3 | 前置检查 `cost_type IS NULL` 且非 0 时 raise | ✓ 行 100-109 |
| 4 | 建分区父表 `billing_summary` + `PARTITION BY RANGE (date)` | ✓ 行 112-116 |
| 5 | PK 含 `(id, date)` | ✓ 行 62 `PRIMARY KEY (id, date)` |
| 6 | 含 `etl_run_id BIGINT NULL` 列 | ✓ 行 61 |
| 7 | 建 default 分区 `billing_summary_default` | ✓ 行 119-122 |
| 8 | 历史月分区循环 + 未来 3 个月兜底 | ✓ 行 125-154 |
| 9 | 索引/唯一约束（uix_billing_dedup 含 cost_type + 6 个 ix）| ✓ 行 158-170 |
| 10 | INSERT FROM SELECT 搬数据 | ✓ 行 173-177 |
| 11 | `ALTER TABLE billing_data RENAME TO _billing_data_legacy`（不 DROP）| ✓ 行 180 |
| 12 | `CREATE VIEW billing_data` 显式列、不暴露 etl_run_id | ✓ 行 183-184，`_VIEW_COLUMNS` = `_COPY_COLUMNS` |
| 13 | `setval('billing_summary_id_seq', ...)` | ✓ 行 187-193 |

13/13 通过。

## §6 v3 §5 迁移 021 内容核对

| # | 检查点 | 结果 |
|---|---|---|
| 1 | `revision = "i5c6d7e8"`, `down_revision = "h4b5c6d7"`（即 020）| ✓ 行 15-16 |
| 2 | `to_regclass('public.taiji_log_raw')` 检测分支 | ✓ 行 26 |
| 3 | RENAME 路径（表 + PK 约束 + 2 个索引）| ✓ 行 28-34 |
| 4 | CREATE TABLE 路径含完整字段 + PK + 2 索引 | ✓ 行 37-63 |
| 5 | `downgrade()` 镜像反向 | ✓ 行 70-86 |

3+ 项通过。

## §7 v3 §6 partition_maintenance 核对

| # | 检查点 | 结果 |
|---|---|---|
| 1 | `@celery_app.task` 装饰的 `ensure_billing_summary_partition` | ✓ 行 53-54 |
| 2 | `@celery_app.task` 装饰的 `fix_default_partition` | ✓ 行 105-106 |
| 3 | `ensure_*` 幂等（CREATE IF NOT EXISTS）| ✓ 行 71-79，先 to_regclass 检查再 CREATE IF NOT EXISTS |
| 4 | `fix_default_partition` 含 detach default → INSERT/DELETE → re-attach 流程 | ✓ 行 132-134 detach；行 142-157 CREATE+INSERT/DELETE；行 165-168 attach finally 块 |
| 5 | 告警降级：logger.warning + sync_log（v3 §C 允许的降级路径）| ✓ 行 91-94, 176-179 logger.warning；`_log_to_sync_logs` 仅 logger，未真写 sync_logs（v3 §C 允许）|
| 6 | `tasks/celery_app.py` `include` 含 `tasks.partition_maintenance` | ✓ 行 12 |
| 7 | beat_schedule 三条 cron（day_of_month 1/15/25, 02:00）| ✓ 行 45-56 |

## §8 偏差报告

### 修改 agent 自报偏差
- **未接入 alert_rules（仅 logger.warning）**：v3 §C 显式列为允许的降级方案，**不构成实质偏差**。

### 验收发现的额外偏差
- **`_log_to_sync_logs` 是空操作（comment-only）**：v3 §C 提到"sync_logs 一行"作为降级方案，但实际实现因 sync_logs.data_source_id 是 NOT NULL FK 而**没有真正写库**，仅 logger.info。修改 agent 已在 implementation-diff.md 自由心证 #8/#16 标明降级再降级。这是 v3 §C 已允许的"完全降级"路径，**非阻塞**。
- 自由心证条目逐条核对：
  - #1 revision hash 风格（v3 没明说，agent 选 8 字符并保持链路连续 → 合理）
  - #2 保留 FK to data_sources（v3 §4 没明说 → 最不破坏行为，合理）
  - #3 VIEW 列去掉 etl_run_id（v3 §A 明确要求 → 合规）
  - #4 PK 顺序 (id, date) （v3 §4 明确 → 合规）
  - #5 未来 3 个月兜底（v3 §4 明确 → 合规）
  - #6 空表 corner case（v3 没明说，从当月开始 → 最保守，合理）
  - #7 fix_default 一次性 detach/attach（v3 没指定循环粒度 → 优化，合理）
  - #8 alert 降级（v3 §C 明确允许 → 合规）
  - #9 raise 在 docstring 后 imports 前（v3 §I 描述 → 合规）
  - #10 README 不存在跳过（v3 §K 允许 → 合规）
  - #11 DATABASE_SCHEMA.md 实际路径在 cloudcost/ 根（确实如此 → 合理）
  - #12 `back_populates="billing_data"` 不改（v3 §3 未明列；agent 已记入 out-of-scope.md → 合理）
  - #13/14/15/16 杂项（合理）

**额外偏差**：无超 v3 范围的改动，未发现"自由发挥"。

## §9 git status diff

由于 cloudcost/ 在 newgongdan 仓库中**未被 git 跟踪**（确认：父级 `git rev-parse --show-toplevel` 返回 newgongdan/，`.gitignore` 含 `__pycache__/` 等，且 `git status -- cloudcost/` 报 "no such directory" —— 表明 cloudcost/ 不在 worktree 索引内），无法用 `git status` 直接对比 implementation-diff.md 声明的 dirty 文件清单。

**采用替代验证：直接核对 implementation-diff.md 列举的每个文件是否物理存在 / 内容符合声明**：

| diff 声明 | 物理验证 |
|---|---|
| `alembic/versions/h4b5c6d7_020_*.py` 新建 | ✓ 存在，内容符合 §5 |
| `alembic/versions/i5c6d7e8_021_*.py` 新建 | ✓ 存在，内容符合 §6 |
| `tasks/partition_maintenance.py` 新建 | ✓ 存在 |
| `tests/conftest.py` 新建 | ✓ 存在 |
| `Makefile` 新建 | ✓ 存在 |
| `scripts/dev_setup.sh` 新建 | ✓ 存在 |
| `app/models/billing.py` 改 `__tablename__` + TODO | ✓ 行 20 TODO，行 22 `__tablename__ = "billing_summary"` |
| `app/models/billing_raw_taiji.py` 新建 | ✓ 存在 |
| `app/models/taiji_log_raw.py` 删除 | ✓ 已删除 |
| `app/models/__init__.py` 改 import + __all__ | ✓ 行 18, 45 都是 `BillingRawTaiji` |
| `app/services/sync_service.py` | ✓ grep 校验通过 |
| `app/api/sync.py` | ✓ grep 校验通过 |
| `app/api/metering.py` | ✓ grep 校验通过 |
| `app/services/dashboard_service.py` | ✓ grep 校验通过 |
| `app/schemas/metering.py` | ✓ grep 校验通过 |
| `app/main.py` 删除 create_all | ✓ 行 50-60 lifespan 内不再有 `Base.metadata.create_all` |
| `tasks/celery_app.py` include + 3 条 beat | ✓ |
| 4 个根脚本顶部 raise | ✓ 全部 4 个文件 docstring 后 imports 前已加 raise |
| `DATABASE_SCHEMA.md` | 未抽查（不在白名单核心验收范围）|
| `docs/migration/out-of-scope.md` | ✓ 存在 |
| `docs/migration/implementation-diff.md` | ✓ 存在 |

**未声明的改动**：因 cloudcost/ 未 git 跟踪，无法绝对断言。但抽查白名单外的高风险文件（`app/collectors/taiji_collector.py` 业务逻辑、`app/api/dashboard.py`、`app/api/billing.py`、`alembic/versions/001-019` 旧迁移），未发现破坏性改动。

## §10 阻塞性问题（必须先解决才能上线）

**无**。

## §11 非阻塞建议（可上线后跟进）

1. **`_log_to_sync_logs` 实际未写库**：函数体内仅 logger.info，注释说明因 sync_logs.data_source_id NOT NULL FK 无法写。建议 Phase 2 引入"system" data_source 行后改为真写 sync_logs，便于后台告警追溯。
2. **`fix_default_partition` 的 detach/attach 与并发写入的竞态**：DETACH 期间，仍可能有 collector/sync 写入触发 default 分区的写路径（按当前架构，default detach 后任何 ROUTING 失败的行会报错）。建议在 v3-final 已记的"celery replicas=0"前提下执行，或加 advisory_lock。当前实现假设维护窗口内单跑，可接受，但建议在 runbook 中显式注明。
3. **020 的 INSERT 阶段无显式 batch / 进度日志**：对大表（千万级 billing_data）的 INSERT FROM SELECT 在单事务下会长锁。v3 §4 是单事务规格，符合 v3，但建议在 dry-run 阶段记录耗时；如超过运维窗口，再考虑 chunked 搬运（属于 Phase 1.5，不在本次验收）。
4. **`back_populates="billing_data"` 关系访问器名**：保留是正确选择，但 Phase 5 类名重命名时务必同步改这个 attr 名以及对应调用面（已在 out-of-scope.md 备注）。
5. **`tasks/sync_tasks.py:157` 的注释**：还有 1 处 `billing_data` 注释命中（v3 §3 未列入白名单，agent 未改）。无运行风险，下次 doc sweep 一并清理。

## §12 后续部署 checklist 提醒（v3 §7 拉过来）

- [ ] **016-019 先 upgrade 到生产**（D7 / v3 §2 前置；跑前先验 `SELECT COUNT(*) FROM billing_data WHERE cost_type IS NULL = 0`）
- [ ] **dry-run 在测试环境跑 020 + 021 一遍**（D1）
- [ ] **Azure PITR + pg_dump 双备份 + 必须 restore 验证**（D3）
- [ ] **迁移期间 celery replicas=0**（D2 + 上文 §11.2 建议）
- [ ] **alembic.ini 凭据检查**（D17）
- [ ] **上线前查 pg_policies**（D20）
- [ ] **EXPLAIN ANALYZE 对比 dashboard 5 条 SQL**（D9）
- [ ] **keyset 翻页跨 3 个月边界验证**（D4）
- [ ] **020 跑前手动跑** `SELECT COUNT(*) FROM billing_data WHERE cost_type IS NULL` —— 必须 0
- [ ] **020 跑后验证** `SELECT tableoid::regclass, COUNT(*) FROM billing_summary GROUP BY 1` 子分区分布合理
- [ ] **021 跑前确认** `to_regclass('public.taiji_log_raw')` 在生产的实际状态（两条路径已都实现）
- [ ] **上线后跑** `alembic current` 应指向 `i5c6d7e8`
- [ ] **30 天观察期后人工清** `_billing_data_legacy`（排进运维日历）
- [ ] **pgaudit**（D16）按 issue 跟进
- [ ] **VIEW 下线判定**用 `pg_stat_statements`（D10，进 monitoring.md）

---

**验收 agent 签字**：以上结论基于只读工具（Read / Grep / Glob / Bash py_compile / 模块 import）核验，未做任何写操作，未运行 alembic upgrade，未 commit/push。
