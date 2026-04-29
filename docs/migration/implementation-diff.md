# Phase 1 实施 diff

> 实施依据：`docs/migration/v3-final.md`
> 实施日期：2026-04-29
> 实施 agent：修改角色

## 改动统计

| 文件 | 改动类型 | 行数变化 | 摘要 |
|---|---|---|---|
| `alembic/versions/h4b5c6d7_020_billing_summary_partition.py` | 新建 | +200 | 020 迁移：建分区父表 `billing_summary` + default 分区 + 历史月分区 + 索引/约束 + 数据搬运 + 旧表 RENAME → `_billing_data_legacy` + 只读 VIEW + setval 序列同步 |
| `alembic/versions/i5c6d7e8_021_rename_taiji_log_raw.py` | 新建 | +85 | 021 迁移：用 PL/pgSQL DO 块 + `to_regclass` 检测，存在则 RENAME（连同索引、PK 约束），不存在则 CREATE 新表 |
| `tasks/partition_maintenance.py` | 新建 | +160 | Celery 任务两个：`ensure_billing_summary_partition`（幂等创建未来 3 个月分区，default 有数据则触发修复）和 `fix_default_partition`（detach default → INSERT/DELETE → re-attach default 流程把数据迁回正确月分区） |
| `tests/conftest.py` | 新建 | +24 | session-scoped autouse fixture 跑 `alembic upgrade head` |
| `Makefile` | 新建 | +5 | `make dev-setup` target |
| `scripts/dev_setup.sh` | 新建 | +12 | bash 版本的 dev setup 脚本 |
| `app/models/billing.py` | 修改 | +1/-0 | `__tablename__` 由 `billing_data` 改为 `billing_summary`；上方加 `# TODO(Phase5): rename class BillingData → BillingSummary` 注释 |
| `app/models/billing_raw_taiji.py` | 新建（重命名） | +71 | 内容由 `taiji_log_raw.py` 拷贝来；类名 `TaijiLogRaw` → `BillingRawTaiji`；`__tablename__` 改 `billing_raw_taiji`；约束 `pk_billing_raw_taiji`；索引 `ix_billing_raw_taiji_*` |
| `app/models/taiji_log_raw.py` | 删除 | -72 | 被 `billing_raw_taiji.py` 取代 |
| `app/models/__init__.py` | 修改 | +1/-1 / +1/-1 | import + `__all__` 改 `BillingRawTaiji` |
| `app/services/sync_service.py` | 修改 | 7 处 | import 改 `BillingRawTaiji`；`select(TaijiLogRaw)` → `select(BillingRawTaiji)` 等 3 处；裸 SQL `INSERT INTO billing_data` → `billing_summary` (192)；log message (244)；`FROM billing_data` (270) → `billing_summary`；`INSERT INTO taiji_log_raw` (550) → `billing_raw_taiji`；`DELETE FROM billing_data` (705) → `billing_summary`；`DELETE FROM taiji_log_raw` (740) → `billing_raw_taiji`；docstring 1 处 |
| `app/api/sync.py` | 修改 | 1 处 | line 68 `FROM billing_data` → `billing_summary` |
| `app/api/metering.py` | 修改 | 5 处 | docstring (1)、Field description (58)、注释 (413)、taiji 流程 docstring (550, 552) |
| `app/services/dashboard_service.py` | 修改 | 3 处 | 注释 / docstring 行 4 / 381 / 484 |
| `app/schemas/metering.py` | 修改 | 1 处 | docstring 第一行 |
| `app/main.py` | 修改 | -3/+5 | 删 lifespan 内的 `Base.metadata.create_all` 调用；保留 lifespan 函数与 logger 信息（schema 全权交给 alembic） |
| `tasks/celery_app.py` | 修改 | +13/-0 | `include` 列表加 `tasks.partition_maintenance`；`beat_schedule` 加每月 1/15/25 02:00 三条 cron |
| `consolidate_null_region.py` | 修改 | +4 | docstring 后插入 `raise RuntimeError(...)` |
| `import_csv_history.py` | 修改 | +4 | 同上 |
| `_exec_step1_delete.py` | 修改 | +4 | 同上 |
| `_exec_step3_backfill.py` | 修改 | +4 | 同上 |
| `DATABASE_SCHEMA.md` | 修改 | replace_all + 新增段 | 表名 `billing_data` → `billing_summary`；新增"Phase 1 迁移说明"段（含分区表说明 + taiji 改名说明）|
| `docs/migration/out-of-scope.md` | 新建 | +30 | 列出本次发现但未修的项 |
| `docs/migration/implementation-diff.md` | 新建 | 本文件 | – |

## 新建文件

- `alembic/versions/h4b5c6d7_020_billing_summary_partition.py` —— 主分区迁移
- `alembic/versions/i5c6d7e8_021_rename_taiji_log_raw.py` —— taiji 表 rename
- `tasks/partition_maintenance.py` —— 月分区维护 + default 自动修复
- `tests/conftest.py` —— pytest 启动前 alembic upgrade
- `Makefile` —— `make dev-setup`
- `scripts/dev_setup.sh` —— bash 版本 dev setup
- `app/models/billing_raw_taiji.py` —— 由 `taiji_log_raw.py` 重命名
- `docs/migration/out-of-scope.md` —— 范围外的发现
- `docs/migration/implementation-diff.md` —— 本文件

## 删除文件

- `app/models/taiji_log_raw.py` —— 被 `billing_raw_taiji.py` 取代

## 自由心证

> 列出 v3 没明说但执行时做了选择的地方。

1. **revision hash 选取**：v3 §4/§5 让用 8 字符 hex 风格。我用 `h4b5c6d7`（020）和 `i5c6d7e8`（021），延续 019 的 `g3a4b5c6d7e8` 风格但只取 8 字符，保证 down_revision 链正确（`g3a4b5c6d7e8 → h4b5c6d7 → i5c6d7e8`）。

2. **分区父表是否带 FK**：v3 §4 只说"字段 = billing_data 当前所有字段 + etl_run_id"。`billing_data.data_source_id` 原本有 FK → `data_sources.id`。我**保留 FK**（DDL 中带 `REFERENCES data_sources(id)`）。这是最不破坏行为的选择；若 FK 阻碍未来跨表搬数据，可在后续迁移单独 drop。

3. **VIEW 列列表去掉 `etl_run_id`**：v3 §A 提到"VIEW 列要明确，不用 `*` 以防 etl_run_id 让旧调用方意外看到新列"。我严格按此实现，VIEW 显式列出 30 个原 `billing_data` 列，去掉 `etl_run_id`。

4. **PK 用 `(id, date)` 而非 `(date, id)`**：v3 §4 明确写"PK = (id, date)"。我严格按此 DDL（`PRIMARY KEY (id, date)`）。

5. **历史月分区"外加未来 3 个月"**：v3 §4 步骤 4 说"外加未来 3 个月（防 default 兜底）"。我在 020 里基于 `MAX(date)` 所属月之后再连续创建 3 个月。

6. **空表场景的兜底**：如果生产 `billing_data` 是空表（MIN/MAX 返回 NULL），我让 020 从当月开始创建分区。v3 没说这个 corner case，我做最保守选择避免迁移崩溃。

7. **`fix_default_partition` 实现策略**：v3 §6 提到 PG 不允许"default 有匹配数据时建重叠分区"。我用 `DETACH PARTITION ... DEFAULT` → `CREATE` 月分区 → `INSERT/DELETE` 搬数据 → `ATTACH ... DEFAULT` 流程，且把 detach 提到循环外（一次性 detach/attach），减少锁开销。

8. **default 分区告警走降级方案**：v3 §C 明确说"如果 alert_service 接入复杂，**降级方案**：只用 logger.warning + sync_log 记录，告警留 Phase 2 接，**在 implementation-diff.md 自由心证段标明**"。我选择降级。原因：sync_logs 表 `data_source_id` 是 NOT NULL FK，无 system 行可写；强写需要先建系统数据源行，超出 Phase 1 范围。本次只 `logger.warning` + `logger.info`，告警链路接入留 Phase 2。

9. **`raise RuntimeError` 的位置**：v3 §I 描述"保留原有 docstring 和 imports，但在 imports 之前**第一行可执行代码**插入"。docstring 是表达式语句不是可执行代码，imports 是。我按"docstring → raise → imports"顺序插入，这样脚本一启动 import 之前就抛错，且消息含明确解法。

10. **README.md 处理**：仓库根目录无 `README.md`（只有 `DATABASE_SCHEMA.md` / `API_DOCS.md` 等专题文档）。v3 §K 说"如果 README 不存在则跳过"，所以**未创建** README。

11. **DATABASE_SCHEMA.md 路径**：v3 §3 / §K 写的是 `docs/DATABASE_SCHEMA.md`，但仓库实际是 `cloudcost/DATABASE_SCHEMA.md`（根目录）。我改了实际存在的那个文件。

12. **`back_populates="billing_data"`**：`app/models/billing.py:91` 与 `data_source.py:28` 的 `back_populates="billing_data"` 是 SQLAlchemy 关系访问器名，不是表名，**未改**（改了反而会破坏 ORM）。已记入 out-of-scope.md。

13. **`tasks/celery_app.py` include 行已含字符串数组**：直接在数组里 append `"tasks.partition_maintenance"`，未拆成多行。语义等价。

14. **conftest.py 不存在所以新建**：v3 §J 说不存在则建。`tests/conftest.py` 之前不存在，新建。

15. **Makefile / scripts/dev_setup.sh 不存在所以新建**：同上，原本均不存在，按 v3 §J 新建。

16. **`partition_maintenance.fix_default_partition` 不写 sync_logs**：v3 §C 提到"写 sync_log 一行"作为告警降级方案。但 `sync_logs.data_source_id` 是 NOT NULL FK，没有合法值可写。降级再降级：只用 logger。已在自由心证 #8 标注，行为符合 v3 §C 的"完全降级"路径。

## 偏差

> v3 规划但没做到的、或做超出的。

- **未接入 alert_rules 告警**：v3 §6 期望 default 分区有数据时触发告警。本次只 logger.warning。详见自由心证 #8。这是 v3 §C 明确允许的降级路径，理论上不算偏差，但显式列出以便验收。

其它无偏差。

## 后续部署 checklist 提醒

- [ ] 016-019 先 upgrade 到生产（D7 / v3 §2 前置）
- [ ] dry-run on test instance（D1）
- [ ] PITR + pg_dump backup + restore validation（D3）
- [ ] celery replicas=0（D2）
- [ ] alembic.ini 凭据检查（D17）
- [ ] pg_policies 检查（D20）
- [ ] EXPLAIN ANALYZE on dashboard SQLs（D9）
- [ ] keyset pagination 跨月验证（D4）
- [ ] 020 跑前手动跑 `SELECT COUNT(*) FROM billing_data WHERE cost_type IS NULL`，结果应为 0
- [ ] 020 跑后验证 `SELECT tableoid::regclass, COUNT(*) FROM billing_summary GROUP BY 1`
- [ ] 021 跑前确认 `to_regclass('public.taiji_log_raw')` 在生产的实际状态（存在/不存在两条路径已都实现）
- [ ] 上线后跑 `alembic current` 应指向 `i5c6d7e8`
- [ ] 删 `_billing_data_legacy` 的 30 天观察期排进运维日历
- [ ] pgaudit（D16）按 issue 跟进
