# Phase 1 副规划修订 v2

> **角色**：副规划  
> **范围**：核对 v1 主规划与代码现状，找冲突 + 修订  
> **本文件不写实现代码**

---

## §A 现有代码现状核查（逐条用证据回应 v1 断言）

### A.1 alembic head

- **v1 说**：head 是 `g3a4b5c6d7e8_019_*`，新迁移 `down_revision = "g3a4b5c6d7e8"`。
- **实际是**：✅ 正确。
- **证据**：`alembic/versions/` 下 019 是最新文件，`g3a4b5c6d7e8_019_billing_thorough_fields.py:1`；下一个编号自然是 020。

### A.2 production 实跑到哪一版

- **v1 没说**（v1 默认本地 = 生产）。
- **用户告知 + 文件名推断**：production Azure PG 已跑到 015（`c9d0e1f2a3b4_015_*`）。016 / 017 / 018 / 019 仍未上 production。
- **核查证据**：`alembic.ini:3` 显示 `sqlalchemy.url = postgresql+...@dataope.postgres.database.azure.com:5432/cloudcost`，说明 alembic 直接指向 Azure PG。仓库内没有自动化部署脚本会跑 `alembic upgrade head`，所以 production 状态不会被代码同步推进。
- **结论**：**Phase 1 的迁移上线前必须先把 016→019 在生产上 upgrade 到位**，否则 020/021 起跑时既要补字段又要分区化，事务会变得很危险。这是 v1 §10 没列入的关键前置项，**必须新增到待澄清表里**。

### A.3 `billing_data` schema 现状

- **v1 说**：列、5 个索引（`ix_billing_ds_date` 等）、`uix_billing_dedup` 含 7 列、`id` 为 `primary_key=True, autoincrement=True`。
- **实际是**：✅ 完全一致。
- **证据**：`app/models/billing.py:20-90`。其中：
  - 5 索引：`ix_billing_ds_date`、`ix_billing_project_date_cost`、`ix_billing_provider_date_cost`、`ix_billing_invoice_month`、`ix_billing_account_id_date`（行 29-33）。
  - 唯一约束 `uix_billing_dedup`：含 `date, data_source_id, project_id, product, usage_type, region, cost_type` 7 列（行 25-28）。
  - `id` PK + autoincrement（行 36）。
  - `data_source_id` 有 FK 指向 `data_sources.id`（行 39）。
- **v1 §3.4 说"004 迁移补的 `ix_billing_provider`、`ix_billing_ds_id`"未验证**：在 model 里没看到这两个名字（model 里只有 `ix_billing_provider_date_cost`、`ix_billing_ds_date`）。需要 grep 004 迁移本体确认。**列入 §D 待澄清**。

### A.4 `billing_data` 当前行数

- **v1 §10 说"需用户提供"**。
- **核查代码内是否有近期 COUNT 输出**：grep 没有找到任何脚本输出文件含具体行数。`_exec_step0_baseline.py` 看名字像是有过基线统计，但内容未读。
- **结论**：**未验证，列入 §D 待用户跑一次 `SELECT COUNT(*)`**。这是 §3.3 单事务 vs 分批策略的硬决策依据。

### A.5 `taiji_log_raw` schema + 引用

- **v1 §3.7 说**：约束名 `pk_taiji_log_raw`、索引 `ix_taiji_log_raw_ds_date` / `ix_taiji_log_raw_ds_ingested`、有 `data_source_id ON DELETE CASCADE`。
- **实际是**：✅ 全部正确。
- **证据**：`app/models/taiji_log_raw.py:30-72`。
  - `__tablename__ = "taiji_log_raw"`（行 31）
  - `PrimaryKeyConstraint("data_source_id", "id", name="pk_taiji_log_raw")`（行 33）
  - 两个索引完全对得上（行 34-35）
  - `ForeignKey("data_sources.id", ondelete="CASCADE")`（行 41）
- **隐藏的关键事实（v1 没提）**：**`taiji_log_raw` 表在 `alembic/versions/` 里没有任何迁移创建它**。证据：`grep -rln "taiji_log_raw" alembic/versions/` 0 命中。它是被 `app/main.py:54` 的 `Base.metadata.create_all` 在启动时建出来的。这意味着：
  1. 改名迁移 021 在生产上得**先确认**该表实际存在（否则 RENAME 会找不到对象）。
  2. ORM 改名后，`create_all` 会按新名字 `billing_raw_taiji` 重新建表，但旧名表如果还在就会出现"两张表并存"。
  3. 这一条是 v1 漏掉的根本风险，必须列进 §D。

### A.6 ORM `BillingData` 关系字符串

- **v1 §3.6/§4.2 说**：`relationship("BillingData", ...)` 在 Python 层不需要 DDL 调整、改名只动一处。
- **实际是**：✅ 全库**只有 1 处** `relationship("BillingData"...)`。
- **证据**：`app/models/data_source.py:28: billing_data = relationship("BillingData", back_populates="data_source")`。

### A.7 PostgreSQL 版本

- **v1 §5 说**：项目已用 PG 13+，"`Dockerfile` / `docker-compose.yml` 可后续验证"。
- **实际是**：**docker-compose.yml 里根本没有 PG 服务**，只有 app / celery-worker / celery-beat 三个容器。生产是 Azure Database for PostgreSQL（`alembic.ini:3` 域名 `postgres.database.azure.com`）。
- **证据**：`docker-compose.yml`（全文 24 行无 postgres）。Dockerfile / requirements.txt 里也没限制 PG server 版本。
- **结论**：本地开发时连的是开发者各自的 Azure PG（或某个共享实例），不是 docker。Azure Database for PostgreSQL Flexible Server 默认是 PG 13/14/15/16，declarative partitioning（PG 12+）肯定够用。**但 v1 §5 那句"`docker-compose.yml` 可后续验证"是错的，要从规划里删掉**。具体的 production PG 版本得用户跑 `SELECT version()` 确认（列入 §D）。

### A.8 `tasks/` 目录现状

- **v1 §10 第 5 题**：需确认是否已有 Celery beat 调度框架。
- **实际是**：**已有，且 beat schedule 已注册 4 个任务**。
- **证据**：`tasks/celery_app.py:28-45` —— `celery_app.conf.beat_schedule = {"daily-sync": ..., "daily-alert-check": ..., "monthly-bill-generate": ..., "daily-taiji-raw-gc": ...}`。`autodiscover_tasks(["tasks"])` 已开（行 48）。`docker-compose.yml:18` 里有 `celery-beat` 服务在跑。
- **结论**：T1.9 只需在 `tasks/partition_maintenance.py` 新文件里写一个新任务，再在 `celery_app.py:beat_schedule` 加一条 `crontab(day_of_month=25, hour=2, minute=0)` 即可。**v1 §10 第 5 题可直接定调："已有 base 框架，只需注册新任务"**。

### A.9 写路径（INSERT / UPDATE / DELETE billing_data）盘点

- **v1 §4.1 说**：裸 SQL 写入只有 2 处（`sync_service.py:192,705`），1 处读（`api/sync.py:68`）。
- **实际**：
  - 应用层裸 SQL 写：✅ 2 处一致（`app/services/sync_service.py:192` INSERT + `:705` DELETE）。但 `:244` 还有一条 log message 含 "billing_data" 字面量、`:270` 还有一条 SELECT。所以**应用层裸 SQL 引用 billing_data 一共 4 处**（2 写 + 1 读 + 1 log），不是 v1 说的 3 处。
  - 应用层裸 SQL 读：✅ 1 处（`app/api/sync.py:68`）。
  - **v1 漏掉**的写路径——**根目录一次性脚本里有真实 INSERT/DELETE billing_data**：
    - `consolidate_null_region.py:89,96` — `DELETE FROM billing_data` + `INSERT INTO billing_data`
    - `import_csv_history.py:40` — `INSERT INTO billing_data`
    - `_exec_step1_delete.py:28` — `DELETE FROM billing_data`
    - `_exec_step3_backfill.py:69` — `INSERT INTO billing_data ({cols_str})`
  - 这些脚本即使是"一次性运维脚本"，PG 默认 VIEW 不可写 → **它们再跑会全部失败**，v1 §4.2 "VIEW 不需要可写"的结论建立在这些脚本"以后绝对不再跑"的假设上。**这个假设没有证据**。
- **结论**：必须为 v1 §4.2 加一句"VIEW 兜底**只覆盖读**，根目录一次性脚本若以后还要复用必须事先改写表名或者改走 ORM"，并把这条风险列进 runbook。或者用 `INSTEAD OF` 触发器把 VIEW 做成可写——但工作量直接翻倍，不建议。

### A.10 ORM 写路径

- **v1 §4.1 说**：ORM 写入主入口走 `BillingData(...)` + session.add。
- **实际是**：未在 grep 范围内看到 `session.add(BillingData(...))` 的具体位置（`BillingData` 类有 246 处引用，但绝大多数是 select / column 引用）。**未做 100% 验证**。但 `sync_service.py` 是裸 SQL 走 raw psycopg2 cursor，**不是 ORM**。**v1 这句"ORM 写入主入口走 `BillingData(...)` + session.add"未必准确**，可能整个写路径都是裸 SQL（INSERT INTO + ON CONFLICT 那一段）。这不影响"VIEW 不可写"的结论，因为 sync_service 反正会被改到 `billing_summary`。

### A.11 `BillingData.id` 用法（影响 §3.2 步骤 5）

- **v1 §10 第 1 题猜测**："应用基本不用 `WHERE id = ?`"。
- **实际是**：`BillingData.id` 在 `app/api/billing.py` 和 `app/api/metering.py` 出现 **11 处**，但**全部用于 `order_by` 和 keyset pagination 的 tuple 比较**（`tuple_(BillingData.date, BillingData.id)`），**没有任何 `WHERE BillingData.id = ?` 单值定位**。
- **证据**（关键行）：
  - `app/api/billing.py:94` `order_by(BillingData.date.desc(), BillingData.id)`
  - `app/api/billing.py:209` / `:213` / `:258` / `:262` `tuple_(BillingData.date, BillingData.id)` 做 keyset 翻页
  - `app/api/metering.py:344, 369, 442, 446, 495` 同样用法
- **结论**：**复合 PK `(id, date)` 完全可行**，对现有查询无破坏（甚至 keyset pagination 用的就是 `(date, id)` 组合，正好跟新 PK 列序对得上）。v1 §10 第 1 题推荐方案 (a) 完全成立。

### A.12 外键依赖确认

- **v1 §3.6 说**：没有任何表 ForeignKey 指向 `billing_data`。
- **实际是**：✅ 正确。
- **证据**：`grep -rin "REFERENCES billing_data\|ForeignKey.*billing_data"` 全库 0 命中（只有文档内提到这条 grep 命令）。`app/models/` 下所有 ForeignKey 列在我的核查里：没有任何一个指向 `billing_data.id`。

### A.13 `app/main.py` 的 `Base.metadata.create_all` 与分区表的冲突

- **v1 没提**这件事。
- **实际是**：`app/main.py:54` 在 lifespan 里 `await conn.run_sync(Base.metadata.create_all)`。SQLAlchemy 的 `create_all` 是 `CREATE TABLE IF NOT EXISTS`，**对普通表无害，但对分区表是大问题**：
  1. 迁移把 `billing_summary` 创建为 `PARTITION BY RANGE (date)` 父表后，应用启动时 `create_all` 会发出 `CREATE TABLE IF NOT EXISTS billing_summary (...)`。SQLAlchemy 不知道父表是分区表，发出的是普通表 DDL。`IF NOT EXISTS` 会保护住已存在的分区父表（不会被覆盖），但 SQLAlchemy 不会"看到"分区结构，**ORM 元数据与 DB 实际结构不一致**——这本身不是 SQL 错误，但破坏了 v1 §6 验收里"应用启动无 ORM mapper 错误"的隐含信心。
  2. 类似的，`taiji_log_raw` 改名为 `billing_raw_taiji` 之后，旧表名如果还在 DB 里没被清掉、ORM 元数据已经只剩新名，那 `create_all` 不会去删旧表（这部分是无害的）。
- **结论**：v1 必须新增一节 "§4.4 `Base.metadata.create_all` 与分区表的相容性"，明确决策：
  - **建议方案 A**：迁移 020 跑完后，把 `app/main.py:54` 那一行 lifespan `create_all` 直接删除（项目已经全用 alembic，`create_all` 是历史遗留）。这同时解决了 A.5 提到的 "`taiji_log_raw` 靠 create_all 建表"的尴尬。
  - **方案 B**：保留 `create_all`，但实测 PG 13/14/15 上 `CREATE TABLE IF NOT EXISTS` 对已存在的分区父表的行为（理论上 IF NOT EXISTS 即跳过，但要实测）。
  - 推荐方案 A，工作量极小（删 1 行 + 在 README 写一句"建表全权交给 alembic"）。

---

## §B 引用面精确统计（修正 v1 数字）

### B.1 `billing_data` 字符串/`BillingData` 类的精确分布

| 维度 | 文件数 | 出现次数 | 备注 |
|---|---|---|---|
| `billing_data` 字符串总命中（含 BQ 数据集名误命中） | 64 | 284 | v1 说 50 文件 426 次，**v1 高估**或者口径含文档/markdown |
| `billing_data` 在 `app/`（应用核心代码） | – | 22 | 仅 app/ 下 .py 文件 |
| `billing_data` 在 `tasks/` | – | 1 | 仅 `tasks/sync_tasks.py:157` 是注释 |
| `billing_data` 在 `scripts/` | – | 23 | **绝大多数是 BQ 数据集名 `spaceone_billing_data_us` 误命中**（`scripts/bq_*.py`），真正访问 PG `billing_data` 表的只有 `scripts/query_subscription_billing.py` |
| `billing_data` 在 `alembic/versions/` | – | 66 | 历史迁移；不需要改 |
| `billing_data` 在 `tests/` | – | 0 | 没有 |
| `billing_data` 在根目录一次性脚本 | – | 172 | `_*.py` / `verify_*.py` / `backfill_*.py` 等。**有些含真实写路径**（A.9） |
| 字面量 `"billing_data"` 字符串 | – | 53 | 总数 |
| 字面量 `"billing_data"` 在 `app/` | – | 2 | 只 `app/models/billing.py:21` (`__tablename__`) 和 `app/models/billing.py:90` (`back_populates="billing_data"`，是 relationship 名不是表名) |
| `BillingData` 类引用 | 11 | 246 | 全部在 `app/` 下 |
| `relationship("BillingData", ...)` | 1 | 1 | 仅 `app/models/data_source.py:28` |

**重大修正**：`scripts/bq_*.py` 里 22 处命中是 BQ **数据集名**（`xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_*`）误命中，**与 PG 表 `billing_data` 无关**。这一点 v1 没区分清楚，让"50 文件 426 处"听上去比实际严重很多。

### B.2 `taiji_log_raw` / `TaijiLogRaw` 分布

| 维度 | 出现位置 | 行号 |
|---|---|---|
| `__tablename__` 定义 | `app/models/taiji_log_raw.py` | 31 |
| 索引/约束名（`pk_taiji_log_raw` / `ix_taiji_log_raw_*`） | 同上 | 33-35 |
| 裸 SQL `INSERT INTO taiji_log_raw` | `app/services/sync_service.py` | 550 |
| 裸 SQL `DELETE FROM taiji_log_raw` | `app/services/sync_service.py` | 740 |
| ORM `from app.models.taiji_log_raw import TaijiLogRaw` | `app/services/sync_service.py:16`、`app/models/__init__.py:18` | – |
| `select(TaijiLogRaw)` | `app/services/sync_service.py:661-663` | – |
| `__init__.py` 的 `__all__` | `app/models/__init__.py:45` | – |
| API 注释 / docstring | `app/api/metering.py:550, 552` | – |
| 文档 | `docs/migration/v1-roadmap.md`、`docs/taiji-ingest-api.md` | – |
| **alembic 迁移里的引用** | **0 处** | **该表纯靠 `Base.metadata.create_all` 创建** |

---

## §C 主规划的修订清单

### C.1 §3.1 迁移文件 — **保留**

`down_revision = "g3a4b5c6d7e8"` 链对，无需改。

### C.2 §3.2 步骤 1-7 — **修订**

- **步骤 5（PK 冲突）**：v1 已经把决策列进 §10 第 1 题。基于 §A.11 证据（应用层无 `WHERE id = ?`），**直接定调改为 (id, date) 复合 PK**，不再留作待澄清。
- **步骤 6（DROP 旧表）**：v1 §4.2 说 "VIEW 不需要可写"，但 §A.9 找到 4 个根目录脚本（`consolidate_null_region.py` 等）含 `INSERT INTO billing_data` / `DELETE FROM billing_data`。**修订为**：迁移 020 不在事务内 `DROP TABLE`，而是 `RENAME TO _billing_data_legacy`，并在 runbook 注明"30 天观察期后由人工 DROP"。VIEW 名 `billing_data` 指向新分区表 `billing_summary`。**已知风险：根目录脚本若复用会写 VIEW 失败 → 需要在 §7 风险表加一行**。
- **步骤 2（动态生成历史月分区）**：v1 说迁移内动态 `MIN/MAX(date)` + 循环 `CREATE TABLE ... PARTITION OF`。**新增约束**：迁移前必须先把 016→019 在生产上先 upgrade 到位（§A.2）；否则 020 的 `INSERT INTO billing_summary SELECT * FROM billing_data` 列数会对不上（因为 017/018/019 都是给 billing_data 加列）。

### C.3 §3.3 历史搬运策略 — **修订**

v1 默认"百万级以下单事务安全"。**修订**：必须先拿到生产真实行数（§A.4，列进 §D）。**给一个明确分支决策表**：

| 行数 | 策略 | 维护窗口估计 |
|---|---|---|
| < 200 万 | 单事务 INSERT FROM SELECT | 30 秒以内 |
| 200 万 – 1000 万 | 单事务但放在维护窗口 | 1-3 分钟 |
| > 1000 万 | 按月分批 INSERT，commit 每批 | 不可估，必须双写过渡（出 Phase 1 范围） |

### C.4 §3.7 迁移 021 — **保留 DDL，新增前置检查**

加一句："在执行 RENAME 之前，先 `SELECT to_regclass('public.taiji_log_raw') IS NOT NULL`，确认表存在；不存在则先 `Base.metadata.create_all` 兜底建出旧表。" 因为该表不是由 alembic 创建的（§A.5），生产可能存在 / 不存在两种状态，迁移要兼容。或者更干净的做法：**迁移 021 直接 `CREATE TABLE IF NOT EXISTS billing_raw_taiji (...)`，然后 `INSERT INTO billing_raw_taiji SELECT * FROM taiji_log_raw IF taiji_log_raw EXISTS`，再 DROP 旧表**。

### C.5 §4.2 VIEW 兜底策略 — **修订**

- **结论不变**：建只读 VIEW `billing_data`。
- **但要新增一段**：根目录一次性脚本里有 4 个含 `INSERT/DELETE billing_data` 的（§A.9 列了具体行号）。这些脚本如果未来被复用（哪怕是排查问题手工跑），写操作会因 VIEW 不可写而失败。**runbook 必须列出这 4 个文件，并标注"复用前需手动改表名为 billing_summary"**。
- 不建议改成可写 VIEW（INSTEAD OF 触发器维护成本高）。

### C.6 §4.3 文件级改动清单 — **修订**

v1 漏掉的：
- `app/services/sync_service.py:244` 的 log message 字面量 "billing_data"（语义无害但建议同步改）。
- `app/services/sync_service.py:270` 的 SELECT 裸 SQL（v1 没列）。
- `app/api/metering.py:1, 58, 413, 552` 的 docstring / Field description 含 "billing_data"（建议同步改成 "billing_summary"，避免 OpenAPI 文档误导前端）。
- `app/services/dashboard_service.py:4, 381, 484` 的注释。
- `app/schemas/metering.py:1` 的 docstring。

### C.7 §4.4（**新增**）`Base.metadata.create_all` 与分区表

见 §A.13。建议把 `app/main.py:54` 那一行删掉，并把"建表全部由 alembic 负责"写进 runbook。

### C.8 §5.2 Celery 任务 — **修订**

v1 说"在 `tasks/` 下新文件 `tasks/partition_maintenance.py` + 注册到 beat"。基于 §A.8 现状，**直接给具体改动位置**：
- 新建 `tasks/partition_maintenance.py`，写 `ensure_billing_summary_partition(months_ahead=2)`。
- 在 `tasks/celery_app.py:28` 的 `beat_schedule` 字典里加一条 `"ensure-billing-partitions": {"task": "tasks.partition_maintenance.ensure_billing_summary_partition", "schedule": crontab(day_of_month=25, hour=2, minute=0)}`。
- `include` 列表加 `"tasks.partition_maintenance"`（行 12）。
- `default` 分区也要加监控告警（v1 §5.1 提到了），但**没指定告警走哪条链路**。我们已有 Sentry，可以用 sync_log 表 + alert_rules 现成基础设施。**列进 §D 待澄清**。

### C.9 §6 验收口径 — **修订**

- 第 6 条 `GET /api/billing/detail` 验收，要补一句"用 keyset pagination 翻 3 页验证 `tuple_(date, id)` 比较仍正确"（因为 PK 改成 `(id, date)` 之后，跨分区的 `id` 唯一性不再保证，必须验 keyset 仍然单调）。
- 第 7 条 `tableoid::regclass` 验证，建议改成更明确的 SQL：`SELECT tableoid::regclass AS partition, COUNT(*) FROM billing_summary WHERE date >= '<sync 当月-01>' GROUP BY 1`，并要求"看到当月分区行数 > 0、其它分区行数 = 0"。
- **新增第 11 条**：`SELECT version()` 拿到生产 PG 版本，记录到验收报告。
- **新增第 12 条**：删除 `app/main.py:54` 后，`docker-compose up app` 启动不报错、`alembic current` 正常。

### C.10 §7 风险表 — **新增 3 行**

| 风险 | 缓解 |
|---|---|
| 016/017/018/019 未上 production，020 先跑会 schema 不匹配 | 上线流程明确"先 alembic upgrade 016→019 → 跑回归 → 再上 020/021" |
| 根目录一次性脚本含 INSERT/DELETE billing_data，VIEW 不可写 → 复用时失败 | runbook 列出 4 个文件、标注"复用前改表名" |
| `app/main.py:54` 的 `create_all` 在分区表存在时 ORM 元数据与 DB 不一致 | 删除该行；或迁移 020 上线后立刻实测 |

---

## §D 新增的待澄清项（v1 §10 之外）

1. **生产 alembic 实跑到哪一版**（用户已告知 015，但需要在上线 runbook 里以 `SELECT version_num FROM alembic_version` 二次确认）；016→019 谁负责先上、什么时间窗口。
2. **生产 `billing_data` 当前行数**（CH/AS 都需要）— 决定 §3.3 单事务还是分批。
3. **生产 PG 的 `SELECT version()`** — 确认 ≥ 12 即可，但要存证。
4. **`taiji_log_raw` 在生产是否真的存在？** 该表无 alembic 创建迁移、靠 `create_all`，需要确认 `Base.metadata.create_all` 是否在 production 真的跑过。
5. **`app/main.py:54` 的 `create_all` 是否可以直接删？** 需要用户确认"以后建表只走 alembic"这个原则没问题（这意味着新加 model 必须配迁移，不能"启动一次自动建出来")。
6. **default 分区告警走哪条链路？** Sentry / sync_log / alert_rules / 邮件——本仓库已有 alert_rules + 多通道（看 `app/api/alerts.py`），需要拍板用哪个。
7. **`v1 §3.4` 里说 "004 迁移补的 `ix_billing_provider`、`ix_billing_ds_id`"** — model 里没这两个名字。要么 v1 写错了名，要么这些索引只在 DB 不在 ORM。需 grep `alembic/versions/004_add_performance_indexes.py` 确认（本次未读）。
8. **根目录 4 个含 billing_data 写路径的脚本**（`consolidate_null_region.py` / `import_csv_history.py` / `_exec_step1_delete.py` / `_exec_step3_backfill.py`）是否还会被复用？如果"绝对不再用"，可以直接删；否则要在 runbook 标注。
9. **CICD 部署链路**：v1 没提。从 memory 知道 cloudcost 的部署是"ACR build + `az containerapp update --container-name api` on `cloudcost-brank`"，但 alembic upgrade 是不是在容器启动 hook 里跑？还是手动在跳板机跑？这影响"先 016→019 再 020/021"的执行方式。

---

## §E v2 合并版任务表

| 编号 | 任务 | 状态 vs v1 | 依赖 |
|---|---|---|---|
| T1.0 | **【新增】** 生产先 `alembic upgrade head`（016→019）+ 备份 pg_dump | 新增（§A.2） | – |
| T1.1 | 全量盘点 `billing_data` 引用 | 修订统计：64 文件 / 284 次（不是 50/426），且 `scripts/bq_*` 22 处是误命中 | – |
| T1.2 | 盘点 `taiji_log_raw` 引用 | 修订：补充"该表无 alembic 迁移、靠 create_all 建" | – |
| T1.3 | 决定 ORM 改名策略 | v1 已定（保留类名） | T1.1, T1.2 |
| T1.4 | 决定分区方案 | v1 已定（declarative）；删掉"docker-compose 验证 PG 版本"那段（§A.7） | – |
| T1.5 | 编写迁移 020 | 修订：明确 PK = (id, date)；旧表 RENAME 而非 DROP；列数对齐前置 016-019 | T1.0, T1.3, T1.4 |
| T1.6 | 编写迁移 021 | 修订：加表存在性前置检查 / 或改为 CREATE IF NOT EXISTS + 数据搬运 | T1.5 |
| T1.7 | 改 ORM model + 引用面 | 修订：补 sync_service.py:244,270 + metering docstring + dashboard_service 注释 | T1.5, T1.6 |
| T1.8 | 兼容 VIEW 落地 | 保留；标注只读 + 列出 4 个根目录脚本风险 | T1.5 |
| T1.8b | **【新增】** 删除 `app/main.py:54` 的 `Base.metadata.create_all` | 新增（§A.13） | T1.5 |
| T1.9 | 月分区维护任务 | 保留；明确插入到 `tasks/celery_app.py:28` 的 beat_schedule | T1.5 |
| T1.9b | **【新增】** default 分区监控告警接入 alert_rules | 新增（§D 第 6 条） | T1.9 |
| T1.10 | 文档同步 | 保留 | T1.5, T1.6 |
| T1.11 | 回归验证 | 修订：加 keyset pagination 验证 + PG version 记录 + create_all 删除后启动验证 | 全部 |

**删除项**：v1 §10 第 1 题（PK 冲突）已在本 v2 直接定调 (id, date)，不再列为待澄清。v1 §10 第 5 题（Celery beat 框架）已在 §A.8 验证存在，不再列为待澄清。

---

## §F Phase 1 真实工作量重新估算

v1 估 4.75 人日，**我的判断：低估了 1.5–2 人日**，理由：

| 任务 | v1 估时 | v2 估时 | 差异原因 |
|---|---|---|---|
| T1.0 生产先 upgrade 016→019 + 备份 | 未列 | **+0.5 人日** | 016/017/018/019 未上生产是确定要做的前置工作；含跨网络 alembic upgrade + 验收 |
| T1.1–T1.4 探查决策 | 0.5 | 0.5 | 持平 |
| T1.5 迁移 020 | 1.5 | **2.0** | 加 RENAME 而非 DROP、动态月分区 + 历史月范围处理、单事务 vs 分批分支、列数前置校验 |
| T1.6 迁移 021 | 0.25 | **0.5** | 加表存在性兼容（`taiji_log_raw` 不在 alembic）、CREATE IF NOT EXISTS 兼容路径 |
| T1.7 ORM + 代码改造 | 0.5 | 0.5 | 持平（多改几条注释/docstring，不增加风险） |
| T1.8 VIEW + 文档 | 0.25 | 0.25 | 持平 |
| T1.8b 删 create_all + 启动回归 | 未列 | **+0.25 人日** | 删 1 行 + 全量启动+冒烟 |
| T1.9 Celery 任务 | 0.25 | 0.25 | beat 框架已现成，反而比 v1 估时省 |
| T1.9b default 分区告警 | 未列 | **+0.5 人日** | 接入 alert_rules + 选告警通道 + 测试 |
| T1.10 联调 + pytest | 1.0 | 1.0 | 持平 |
| 测试环境上线 + 回归 | 0.5 | 0.5 | 持平 |
| **合计** | **4.75** | **6.75** | **+2 人日** |

加上不可见风险缓冲 0.5–1 人日（生产首次跑 020 出现锁等待 / 历史月分区边界数据 / FK 异常），**合理预算 7–8 人日**。

观察期建议同 v1（14 天密集 + 30 天评估），不变。

---

**结论**：v1 主体方向（declarative partitioning、保留类名只改 `__tablename__`、VIEW 兜底）正确，但有 4 个事实性差错（引用面数字、docker-compose 没 PG、taiji 表无 alembic、根目录脚本含真实写路径）和 3 个漏项（016-019 前置、`create_all` 冲突、default 分区告警链路）。本 v2 给出修订后的任务表与重新估时 6.75 人日（+1.5–2 人日缓冲到 8）。
