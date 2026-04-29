# Phase 1 路线图 v1（billing_data → billing_summary 改名 + 月分区 + taiji_log_raw 改名）

> **角色**：主规划  
> **范围**：仅 Phase 1（Silver 表 rename + 月分区 + taiji 表改名）  
> **不改代码、不调用 Edit/Write**，仅输出规划

---

## §1 Phase 1 目标

把 Silver 层数据表的命名与物理结构调整到位，为后续 Bronze 层接入做准备。具体而言：把 `billing_data` 改名为 `billing_summary` 并按 `date` 月分区、把 `taiji_log_raw` 改名为 `billing_raw_taiji`，且**对所有读路径、写路径、API 行为零破坏**。本 Phase **不**新建任何 Bronze 表、**不**改动 collector 行为、**不**触碰前端与 ETL 调度。

---

## §2 任务拆分

| 编号 | 任务 | 目的 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| T1.1 | 全量盘点 `billing_data` 引用 | 确认改名爆炸面 | 当前代码库 | 引用清单（已知 50 文件 426 处） | – |
| T1.2 | 盘点 `taiji_log_raw` 引用 | 确认改名爆炸面 | 当前代码库 | 引用清单（已知 5 文件） | – |
| T1.3 | 决定 ORM 改名策略 | 让应用层无感 | T1.1、T1.2 | 决策记录（见 §4） | T1.1, T1.2 |
| T1.4 | 决定分区方案 | 选定 declarative / pg_partman / inherits | – | 决策记录（见 §5） | – |
| T1.5 | 编写 alembic 迁移 020 — `billing_summary` 改名 + 分区化 | 落库变更 | T1.3, T1.4 | `<hash>_020_billing_summary_partition.py` | T1.3, T1.4 |
| T1.6 | 编写 alembic 迁移 021 — `taiji_log_raw → billing_raw_taiji` | 落库变更 | T1.3 | `<hash>_021_rename_taiji_log_raw.py` | T1.5 |
| T1.7 | 改 ORM model 文件名与 `__tablename__` | 配合改名 | T1.5, T1.6 | `app/models/billing.py`、`app/models/taiji_log_raw.py` 的修改 | T1.5, T1.6 |
| T1.8 | 兼容 VIEW 落地（可选，见 §4 决策） | 兜底未识别的旧 SQL | T1.5 | DDL `CREATE VIEW billing_data` 写入迁移 020 | T1.5 |
| T1.9 | 月分区维护任务 | 每月初创建下月分区 | T1.4 | Celery beat 任务（写在 tasks/，不在本 Phase 启用 collector 改动） | T1.5 |
| T1.10 | 文档同步 | 防止文档与代码分叉 | – | `DATABASE_SCHEMA.md`、`docs/*` 引用更新 | T1.5, T1.6 |
| T1.11 | 回归验证 | 确认零破坏 | T1.5–T1.9 | 验收清单（见 §6） | 全部 |

---

## §3 数据库迁移设计

### 3.1 迁移文件

参考现有命名风格（见 `alembic/versions/g3a4b5c6d7e8_019_billing_thorough_fields.py`），建议：

- `<hash>_020_billing_summary_partition.py`（`down_revision = "g3a4b5c6d7e8"`）
- `<hash>_021_rename_taiji_log_raw.py`（`down_revision = "<020 hash>"`）

### 3.2 DDL 思路 — 迁移 020（核心难点）

PostgreSQL 的限制：**普通表不能直接 `ALTER` 为分区表**。必须新建分区父表后搬数据。建议步骤（思路，非完整 SQL）：

1. `CREATE TABLE billing_summary (...所有列...) PARTITION BY RANGE (date)` — 父表，schema 与 `billing_data` 完全一致（含字段、注释、`server_default`）。
2. 为历史已覆盖的每个月创建子分区：`billing_summary_YYYYMM`（命名约定见 §5）。范围可由 `SELECT MIN(date), MAX(date) FROM billing_data` 推出，迁移内动态生成 `CREATE TABLE ... PARTITION OF ... FOR VALUES FROM ('YYYY-MM-01') TO ('next-month-01')`。
3. `INSERT INTO billing_summary SELECT * FROM billing_data` —— 单事务批量搬运（数据量量级若大于千万级行可改成按月分批 INSERT，但目前账单表不会很大，单事务可接受）。
4. 把所有索引、唯一约束在父表上重建（PostgreSQL declarative partitioning 会自动级联到子分区），见 3.4 / 3.5。
5. 在父表上重建 `id` 序列（分区表的全局 `SERIAL` 在跨分区上有坑）—— 改为 **保留 `id` 但弃用 `autoincrement`**，或者**把 unique 由 `(id)` 退到逻辑唯一键 `uix_billing_dedup`**。对照现有代码 `BillingData.id` 是 `primary_key=True, autoincrement=True`，分区表要求分区键 `date` **必须**进主键；因此主键得改成 `(id, date)` 或者直接以 `uix_billing_dedup` 做主键。本项需在 §10 拍板。
6. `DROP TABLE billing_data` —— 但这一步会破坏外部裸 SQL 兼容。若选 §4 方案 B：替换为 `ALTER TABLE billing_data RENAME TO _billing_data_old; CREATE VIEW billing_data AS SELECT * FROM billing_summary; DROP TABLE _billing_data_old`（在事务末）。
7. 重建 `data_sources` 上 `relationship("BillingData", back_populates="data_source")` 不需要 DDL 调整（Python 层）。

### 3.3 历史数据搬运策略

- **首选：新建分区父表 + INSERT FROM SELECT + DROP 旧表**（单事务）。账单类表数据量在百万级以下时单事务安全；DDL 在 PG 里走 ACCESS EXCLUSIVE 锁，写流量本来就要短暂阻塞，可接受。
- **备选（数据量更大时）：双写过渡**。新表先建，应用层临时双写（这超出 Phase 1 范围，列入 §10）。
- 强制零停机不可达时，建议**约定停写窗口 < 2 分钟**（迁移在维护窗口跑），避免写竞争。

### 3.4 索引迁移

旧表上 5 个索引（`ix_billing_ds_date`、`ix_billing_project_date_cost`、`ix_billing_provider_date_cost`、`ix_billing_invoice_month`、`ix_billing_account_id_date`）+ 004 迁移补的 `ix_billing_provider`、`ix_billing_ds_id`。

- 在父表 `billing_summary` 上重建同名索引（PostgreSQL 12+ declarative partitioning 自动下放到所有分区）。
- 对**只在 date 范围内查询很少跨月**的场景，分区裁剪本身已经替代了部分 `(date, ...)` 索引前缀的过滤价值，但本 Phase 不优化，**保持现有索引集合不变**。

### 3.5 唯一约束迁移

`UniqueConstraint("date", "data_source_id", "project_id", "product", "usage_type", "region", "cost_type", name="uix_billing_dedup")` —— 分区表的 unique 约束**必须包含分区键**。当前约束已含 `date`，天然合规，可原样迁移到父表，自动下放。

### 3.6 外键依赖

经全库 grep 确认：**没有任何表通过 ForeignKey 指向 `billing_data`**（`grep -i "REFERENCES billing_data"` / `ForeignKey.*billing_data` 均无命中）。`billing_data` 仅作为 `data_sources.id` 的下游存在。改名只需改一处 `relationship` 字符串，不涉及级联 FK 重建。

### 3.7 DDL 思路 — 迁移 021（taiji_log_raw 改名）

简单 DDL：

```
ALTER TABLE taiji_log_raw RENAME TO billing_raw_taiji;
ALTER INDEX ix_taiji_log_raw_ds_date RENAME TO ix_billing_raw_taiji_ds_date;
ALTER INDEX ix_taiji_log_raw_ds_ingested RENAME TO ix_billing_raw_taiji_ds_ingested;
ALTER TABLE billing_raw_taiji RENAME CONSTRAINT pk_taiji_log_raw TO pk_billing_raw_taiji;
```

无历史数据搬运、无 FK 调整。

---

## §4 应用层兼容策略

### 4.1 现状证据

- `BillingData` 类被引用 426 次跨 50 个文件，其中应用代码（`app/`）约 50 处真正读 / 写（其余在脚本和文档）。
- 真正用裸 SQL 字符串 `billing_data` 的写入只有 2 处：`app/services/sync_service.py:192,705`（INSERT、DELETE），1 处读：`app/api/sync.py:68`（`MIN/MAX date`）。
- ORM 写入主入口走 `BillingData(...)` 对象 + session.add（很多处）。

### 4.2 决策建议

**ORM model：保留类名 `BillingData`，只改 `__tablename__`。** 这样所有 `from app.models.billing import BillingData` 不动，`relationship("BillingData", ...)` 不动。代价是命名层面"类名 vs 表名不一致"，但比一次性 sed 改 50 处更安全。后续 Phase 收尾期再统一。

**裸 SQL：3 处必须同步改。** 改成 `billing_summary`，列在 T1.5 同一 PR 里。

**SQL VIEW 兜底：建议建一个只读 VIEW `billing_data`**，目的是兜住：
- 运维直接连库手敲的旧 SQL；
- 可能遗漏的脚本（`cloudcost/` 根目录下大量 `_*.py`、`verify_*.py` 等一次性脚本）；
- 第三方 BI / Metabase 已经书签的查询。

PostgreSQL 默认 VIEW 可读不可写。我们的应用写入只走 ORM（`BillingData`）和 sync_service 两条裸 SQL，都会被改到 `billing_summary`，VIEW 不需要可写。**只读 VIEW 够用。**

**兼容层下线时机：** 至少保留到 Phase 5 完成（即 Bronze + Silver 全栈跑稳两个完整账期之后）。建议设置一个明确日期 `T+90 天`，期间用 PG `pg_stat_user_tables` 监控 `billing_data` VIEW 的访问次数是否归零，归零后下线。

### 4.3 文件级改动清单

`app/models/billing.py` —— 改 `__tablename__ = "billing_summary"`、类名保留。  
`app/models/taiji_log_raw.py` —— 文件改名为 `billing_raw_taiji.py`、类改名 `BillingRawTaiji`、`__tablename__` 同步。  
`app/models/__init__.py` —— 5 处 import 与 `__all__` 同步。  
`app/services/sync_service.py` —— 3 处裸 SQL `billing_data` → `billing_summary`，1 处 `taiji_log_raw` → `billing_raw_taiji`。  
`app/api/sync.py` —— 1 处裸 SQL。  
`app/api/metering.py`、`app/collectors/taiji_collector.py` —— 全部 `TaijiLogRaw` 类引用改 `BillingRawTaiji`。  
`docs/*.md`、`DATABASE_SCHEMA.md` —— 文档跟改。  
**根目录 `_*.py`、`verify_*.py`、`scripts/*.py` 一次性脚本不改**（VIEW 兜底）。

---

## §5 分区方案选型

| 方案 | 优点 | 缺点 | 评估 |
|---|---|---|---|
| **A. PG 12+ declarative partitioning（`PARTITION BY RANGE`）** | 原生、零依赖、ORM 透明、索引/约束自动下放、`pg_partman` 不需要 | 需要自己写"创建下月分区"的 cron | **推荐** |
| B. `pg_partman` 扩展 | 自动创建/淘汰分区、retention 策略现成 | 引入扩展，部署面变大；本项目 docker-compose 内 PG 镜像未必带；未来云厂商托管 PG（如 RDS）支持参差不齐 | 否决 |
| C. 老式 `INHERITS` + 触发器 | 兼容 PG 9.x | PG 14 起官方文档已建议迁出；触发器维护成本高；ORM 不友好 | 否决 |

**推荐方案 A**。理由：项目已用 PG 13+（`Dockerfile` / `docker-compose.yml` 可后续验证），declarative partitioning 完全够用；额外维护代码量极小（一个 30 行 Celery 任务）。

### 5.1 命名约定

子分区命名：`billing_summary_YYYYMM`，例：`billing_summary_202604`。  
分区范围：`FROM ('2026-04-01') TO ('2026-05-01')`。  
默认分区：建一个 `billing_summary_default` 兜底（防止数据写入未提前创建的月份导致 INSERT 失败），但要在监控里告警 default 分区行数 > 0。

### 5.2 未来月份分区谁创建

**Celery beat 周期任务**：每月 25 日 02:00 执行 `ensure_billing_summary_partition(next_month, next_next_month)`，预创建未来 2 个月。
- 写在 `tasks/` 下新文件 `tasks/partition_maintenance.py`。
- 幂等：`CREATE TABLE IF NOT EXISTS ... PARTITION OF ... FOR VALUES FROM ... TO ...`。
- 不做自动 DROP 旧分区（数据保留策略不在 Phase 1 范围）。

不选 trigger / event scheduler 的原因：DB-side 调度可观测性差、报警链路分散，和我们已有的 Celery + Sentry 链路不一致。

---

## §6 验收口径

判定 Phase 1 完成的可观测条件（每条都要在验收报告里勾选）：

1. `\d billing_summary` 显示 `Partitioned table` 标识，子分区数 ≥ 历史月数 + 当月 + 未来 2 个月。
2. `SELECT COUNT(*) FROM billing_summary` 等于迁移前 `billing_data` 的 COUNT。
3. `SELECT 1 FROM billing_data LIMIT 1` 不报错（VIEW 存在且可读）。
4. `SELECT 1 FROM taiji_log_raw LIMIT 1` —— **此项允许失败**（无 VIEW，由 §10 拍板是否补 VIEW）。
5. 应用启动无 ORM mapper 错误，`alembic current` 在新 head。
6. 关键 API 返回完全等价：
   - `GET /api/billing/detail` 任选 1 个 ds、1 个月，迁移前后 JSON 字节级一致（或排序后一致）。
   - `GET /api/dashboard/*` 关键看板数值一致。
   - `GET /api/metering/*` 数值一致。
7. 跑一次 GCP / Taiji 同步：`POST /api/sync/...` 成功；新数据落入正确的当月分区（`SELECT tableoid::regclass, COUNT(*) FROM billing_summary GROUP BY 1` 验证）。
8. Taiji ingest：`POST /api/metering/taiji/ingest` 成功，写入 `billing_raw_taiji`，并触发 `billing_summary` 重算。
9. Celery beat 列表里能看到 `partition_maintenance` 任务，手动触发一次后 `billing_summary_<未来月>` 出现。
10. 全量 pytest 绿（`tests/` 目录）。

---

## §7 回滚方案

### 7.1 alembic downgrade 可行性

- **020（分区化）downgrade 复杂但可行**：新建普通表 `billing_data_restore`、`INSERT FROM SELECT * FROM billing_summary`、`DROP VIEW billing_data`、`ALTER TABLE billing_data_restore RENAME TO billing_data`、`DROP TABLE billing_summary CASCADE`。需要在迁移脚本里完整写好 `downgrade()`。
- **021（taiji 改名）downgrade 是简单 RENAME**，无风险。

### 7.2 失败半途回滚

由于 020 在单事务内完成（`CREATE TABLE` + `INSERT FROM SELECT` + `DROP TABLE`），事务级回滚由 PG 保证：
- 如果 INSERT 中途失败 → 事务 ROLLBACK，旧 `billing_data` 完好。
- 如果迁移在 Python 层抛异常但 SQL 已 COMMIT → 用 alembic downgrade 跑回滚步骤。
- 极端情况（部分 DDL 已 commit 部分未跑）→ 从迁移前的 PG 备份恢复。**Phase 1 上线前必须有一份 pg_dump 兜底**，并写进 runbook。

### 7.3 风险点 + 缓解

| 风险 | 缓解 |
|---|---|
| `id` 列与分区键冲突，PK 必须含分区键 | §10 决策项；推荐改 PK = `(id, date)` |
| 单事务 INSERT 大表锁时间过长 | 提前 `SELECT count(*) FROM billing_data` 评估；超 500 万行改为按月分批迁移 |
| 旧 SQL 写 `billing_data`（VIEW 不可写）导致写失败 | 上线前对应用代码 grep 一遍 INSERT/UPDATE/DELETE billing_data，确认全部改完 |
| Celery beat 漏跑导致下月分区未建 | 加 default 分区兜底 + 监控告警 default 行数 > 0 |
| 历史数据 `date` 范围不连续/空 | 迁移内 `MIN/MAX` 失败时 fallback 到当月 |

---

## §8 时间盒

- T1.1–T1.4 探查与决策：0.5 人日
- T1.5 迁移 020 编写 + 本地验证：1.5 人日（含分区维护任务调试）
- T1.6 迁移 021 编写：0.25 人日
- T1.7 ORM 与代码改造：0.5 人日
- T1.8 VIEW + 文档：0.25 人日
- T1.9 Celery 任务：0.25 人日
- T1.10 联调 + pytest：1 人日
- 测试环境上线 + 回归：0.5 人日

**编码工作量合计：约 4.75 人日。** 

**生产观察期建议：** 上线后保留 14 天密集观察（每天看 `pg_stat_user_tables` 命中、分区行数分布、Sentry 是否有新错误）。第 30 天评估是否进入 Phase 2。

---

## §9 严格不动什么（边界声明）

**绝对不动的文件 / 行为：**

- `app/collectors/gcp_collector.py`、`aws_collector.py`、`azure_collector.py`、`taiji_collector.py` 的**业务逻辑**（只允许改类名 import）。GCP 仍 GROUP BY 到天，AWS / Azure 仍走原有日粒度 API，本 Phase 不引入小时级。
- 不创建 `billing_raw_gcp` / `billing_raw_aws` / `billing_raw_azure` 任意表。
- 不改 `billing_daily_summary` / `BillingDailySummary` 模型与表（这个表是 Silver 之上的二级聚合，独立议题）。
- 前端 `frontend/`（如果存在）、所有 OpenAPI schema、`/api/billing/*` / `/api/dashboard/*` / `/api/metering/*` 的请求与响应字段。
- Celery 现有 sync 任务的调度周期与参数。
- `cloudcost/` 根目录的一次性运维脚本（`_*.py`、`verify_*.py`、`backfill_*.py` 等）—— 由 VIEW 兜底，不动其裸 SQL。

**留给后续 Phase：**

- Phase 2：建 `billing_raw_gcp`（小时级）+ 改 GCP collector 写 Bronze。
- Phase 3：建 `billing_raw_aws` / `billing_raw_azure` + 改对应 collector。
- Phase 4：建 Bronze→Silver 的 ETL job（取代 collector 直写 Silver）。
- Phase 5：下线 `billing_data` VIEW、删兼容层、统一类名 `BillingData`→`BillingSummary`。

---

## §10 待确认问题（请用户拍板）

1. **分区键与主键冲突如何解决？** PG 要求分区表 PK 必须包含分区键 `date`。两种走法：
   - (a) PK 改为 `(id, date)`，对应用层 `id` 单值定位场景几乎无影响（应用基本不用 `WHERE id = ?`）。
   - (b) 干脆放弃 `id` 主键，把 `uix_billing_dedup` 升级为 PK。  
   推荐 (a)。**需用户确认。**

2. **`taiji_log_raw` 是否也建一个兼容 VIEW？** 该表只被应用内 5 个文件引用，外部脚本几乎不碰。建 VIEW 是零成本兜底；不建则更干净。**默认建议不建，等用户拍板。**

3. **历史数据搬运是否要分批？** 取决于当前 `billing_data` 行数。若 < 500 万行单事务 OK；若 ≥ 1000 万行需要分批。**需用户提供当前 row count。**

4. **维护窗口是否能争取到？** 单事务迁移期间表会持有 ACCESS EXCLUSIVE 锁，估计 30 秒至几分钟。能否安排一个低峰窗口？或要求"零停机"（那就要走双写过渡，工作量翻倍）。

5. **Celery beat 是否已经有 base 调度框架？** 若没有，T1.9 需要先在 Phase 1 内引入 beat schedule 配置；如已有，只需注册一个新任务。**需确认 `tasks/` 目录现状。**

6. **VIEW 下线日期（建议 T+90 天）是否接受？** 还是更短 / 更长。

7. **是否要在迁移 020 里**同时**把 `tasks/partition_maintenance.py` 任务也启用（注册到 beat schedule）**？还是迁移只动 DB、Celery 改动放单独一个 PR？推荐分 PR，便于按需回滚。
