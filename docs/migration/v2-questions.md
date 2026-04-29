# Phase 1 校验问题清单 v2

> **角色**：校验  
> **范围**：对 v1 + v2 合并后的规划做安全 / 性能 / 合理性三维度挑刺  
> 本清单只挑刺、不给修复方案。每条对应 v1/v2 已有处理 + 推荐答案。

---

## §1 高风险问题（必须解决才能动手）

### Q1: 单事务 INSERT FROM SELECT + DDL 在 Azure PG 上的真实锁时长无人估过
**维度**：【性能】+【安全】  
**触发场景**：v2 §C.3 给的"<200 万行 30 秒以内 / 200-1000 万行 1-3 分钟"是经验值。实际 `INSERT INTO billing_summary SELECT * FROM billing_data` 在分区表上不只是普通 INSERT——PG 要为每一行**计算路由分区**，再走每个子分区的索引/约束检查。当历史月份分区有 30+ 个时，单条 INSERT 内部要触发 30+ 子分区的索引插入。Azure PG 网络与本地 docker 不一致，IOPS 上限受 SKU 限制，真实耗时**可能数倍于经验值**。整个事务期间 `billing_data` 上是 ACCESS EXCLUSIVE 锁，所有读写都会排队，celery worker 的 sync_service / dashboard 查询会全部 hang，超过 statement_timeout 直接 5xx。  
**v1/v2 的处理**：v1 §3.3 仅说"百万级以下单事务安全"；v2 §C.3 给了三档行数表，但既没有"实测 dry-run"步骤，也没有"事务进度可观察"机制。v1 §10 第 4 题"维护窗口能否争取"留作待澄清，没拍板。  
**风险**：高。如果实际锁时长达到 5-10 分钟，期间 Celery beat 触发的 `daily-sync` / `daily-alert-check` 等 4 个任务全部超时堆积，可能引发雪崩；且**事务期间无法 cancel**——一旦 INSERT 走到一半发现太慢，唯一选择就是 `pg_cancel_backend` 触发 ROLLBACK，但 ROLLBACK 自身也要等量时间。  
**推荐回答**：上线前必须做两件事——(a) 在测试环境用**与生产同 SKU、同行数量级**的实例做一次完整 dry-run，记录真实耗时；(b) 上线时强制走维护窗口，**显式 stop 所有 celery worker + beat**（不能只靠"低峰期"），并把 statement_timeout 临时调高到 dry-run 耗时 3 倍。**没有 dry-run 数据就不许上 production。**

---

### Q2: 迁移期间 Celery worker 是否停机，v1/v2 都没拍板
**维度**：【安全】  
**触发场景**：迁移 020 跑的瞬间，假设 GCP collector 任务正在 `INSERT INTO billing_data ON CONFLICT ...`，会被 ACCESS EXCLUSIVE 锁阻塞；阻塞超过 lock_timeout 直接报错并写脏数据到 sync_log；阻塞不超时但 alembic 事务 ROLLBACK 时，worker 端的事务也会被牵连。更糟糕的场景：worker 已经走完 INSERT、还没 COMMIT，alembic 已经 `DROP TABLE billing_data`，worker 后续 COMMIT 会撞到"表不存在"。  
**v1/v2 的处理**：v1 §3.3 提"约定停写窗口 < 2 分钟"是**约定**不是机制；v2 没新增 worker 停机要求。runbook 里也没列"上线前 `docker compose stop celery-worker celery-beat`"这样的硬步骤。  
**风险**：高。Azure Container Apps 的 celery 容器是常驻的，没有显式停就会持续触发 sync。  
**推荐回答**：上线 runbook 必须显式包含——(1) `az containerapp update --replicas 0` 把 celery-worker / celery-beat 实例数缩到 0；(2) 等待最后一个在飞任务结束（看 sync_log）；(3) 才允许 alembic upgrade；(4) 迁移后 + 冒烟通过再恢复 replicas。这是机制，不是"约定"。

---

### Q3: `pg_dump` 备份的位置/时机/验证完全空白
**维度**：【安全】  
**触发场景**：v1 §7.2 说"Phase 1 上线前必须有一份 pg_dump 兜底"，但没说——dump 到哪台机器？容量多大？dump 是在 Azure PG 内部 snapshot 还是从外部跳板机 pg_dump 到本地？是否做了 restore 演练验证 dump 可用？如果只 dump 没 restore 测试，等真的回滚那一刻发现 dump 文件损坏/缺列/字符集不对，工作就全废了。  
**v1/v2 的处理**：v1 §7.2 单句提及；v2 §A.2 / §E T1.0 列了"备份 pg_dump"但没给验证标准。  
**风险**：高。备份不可恢复 = 没备份。  
**推荐回答**：runbook 必须明确——(a) 用 Azure PG 自带的 PITR / restore point（这是最可靠的，不依赖额外脚本）作为**主**回滚机制；(b) `pg_dump` 仅作辅助，且 dump 完后**必须立即在临时实例上 restore 一次**，验 schema 与行数；(c) dump 文件存放路径、保留期、清理责任人都要写明。

---

### Q4: 跨分区 keyset pagination 的 `id` 单调性未实证
**维度**：【性能】+【合理性】  
**触发场景**：v2 §A.11 / §C.9 提到 PK 改 (id, date) 后，**id 是从同一个全局序列分配的**（PG 分区表的 BIGSERIAL 仍是 schema 级 sequence），所以新插入数据 id 仍单调递增。但**历史搬运**这一步是 `INSERT INTO billing_summary SELECT * FROM billing_data`——SELECT 没有显式 ORDER BY，PG 路由到不同子分区时 id 顺序在子分区内**可能不再连续**（虽然值不变）。当 keyset pagination 走 `WHERE (date, id) < (?, ?) ORDER BY date DESC, id DESC` 时，跨月翻页会**正确**（因为 (date, id) 复合 key 只要 date 全局有序就行）；但**同 date 内** id 跳跃在分区表上没有问题（因为同 date 必落同分区），所以理论上 OK——**但这是推理，不是验证**。v2 §C.9 只说"翻 3 页验证"，3 页太少，覆盖不到边界月（月初/月末跨分区）。  
**v1/v2 的处理**：v2 §C.9 加了"翻 3 页验证 tuple_(date, id) 单调"，但没指定**必须翻到月-月边界**。  
**风险**：中-高（如果跨月边界翻页错乱，前端"加载更多"会出现重复或漏数据，但 bug 隐蔽难复现）。  
**推荐回答**：验收第 6 条改为"必须验证至少跨 3 个月边界的 keyset 翻页结果，与未分区前快照逐行 diff"，且用 production 真实月数据，不用合成数据。

---

### Q5: 迁移 021（taiji_log_raw 改名）从 RENAME 改为 CREATE+搬数据+DROP，**复杂度反而升高**
**维度**：【合理性】+【安全】  
**触发场景**：v2 §C.4 把 v1 的简单 `ALTER TABLE RENAME` 改为"`CREATE TABLE IF NOT EXISTS billing_raw_taiji` + `INSERT INTO ... SELECT * FROM taiji_log_raw IF EXISTS` + `DROP`"。理由是"taiji_log_raw 不在 alembic、生产可能不存在"。但：**如果生产实际存在该表（极大概率，因为 main.py 的 create_all 启动时一定跑过）**，v2 这套就比 RENAME 多了一次完整数据搬运 + DROP，**多一次出错点**。RENAME 在 PG 是元数据级操作（瞬时、原子），CREATE+INSERT+DROP 不是原子（分多步 DDL，索引/约束/序列名都要重新对齐）。  
**v1/v2 的处理**：v2 §C.4 给了"或者更干净的做法"作为优先选项。  
**风险**：中-高。把简单问题复杂化。  
**推荐回答**：在迁移 021 开头加一行 `SELECT to_regclass('public.taiji_log_raw')`——**存在则走 RENAME 路径**（v1 原方案，最简单原子）；**不存在才走 CREATE 新表路径**。两路二选一，不要无条件走 CREATE+搬运。`to_regclass` 检查的成本可忽略。

---

### Q6: 删除 `app/main.py:54` 的 `create_all` 后，本地开发与 CI 的建表责任无人接手
**维度**：【合理性】  
**触发场景**：v2 §A.13 / §C.7 / T1.8b 建议直接删 `await conn.run_sync(Base.metadata.create_all)`。但项目里**没有任何"启动时自动 alembic upgrade"机制**——开发者第一次 `git pull` 拉到新 model（比如 Phase 2 的 billing_raw_gcp）之后，启动应用会因为表不存在而 5xx；CI 跑 pytest 也会失败（除非 conftest 显式调 alembic）。v2 §D 第 5 题列了"是否可以直接删"作为待澄清，但**没列"删了之后谁建表"这个跟进问题**。  
**v1/v2 的处理**：v2 §A.13 推荐"删 + 在 README 写一句"，但 README 字面上的提示没有强制力。  
**风险**：高（开发体验直接破裂，新人入职第一天就踩坑）。  
**推荐回答**：删 `create_all` 必须**同时**做以下二选一——(a) 在 lifespan 里改为 `await conn.run_sync(lambda c: alembic_upgrade(c, "head"))`，启动时自动 upgrade（生产环境用 env 开关关掉）；(b) 在 `tests/conftest.py` 和 `Makefile`/`scripts/dev_setup.sh` 里显式 `alembic upgrade head`，并在 README 强制说明。**纯靠 README 一句话不够**。建议方案 (b)，因为 (a) 在多 worker 启动时会有竞态。

---

### Q7: 016/017/018/019 这 4 个迁移在 production 的具体内容与冲突风险，无评估
**维度**：【安全】  
**触发场景**：v2 §A.2 发现 production 在 015，要先把 016→019 上掉。但 v2 **没读这 4 个迁移的内容**——它们是不是也在改 billing_data 加列、加索引？017/018/019 的 down_revision 链是不是无误？4 个迁移连续跑会不会有相互依赖问题？v1 §10 完全没列这一项，v2 §A.2 列了但只说"必须先上"，没评估每个迁移的风险等级、耗时、是否有数据搬运。  
**v1/v2 的处理**：v2 §E T1.0 给了 0.5 人日，但**0.5 人日是"跑一次 upgrade"的时间，不包括"读懂这 4 个迁移在干什么"**。  
**风险**：高。如果 017 给 billing_data 加了一个 NOT NULL 列但没 default，对存量行就会失败；如果 019 的字段定义跟 020 期望的"列数对齐"对不上，整个 INSERT FROM SELECT 会爆。  
**推荐回答**：T1.0 拆成两步——(1) **审 016/017/018/019 4 个迁移文件**，列出每个迁移的 up/down 内容、是否含数据搬运、是否含 NOT NULL 加列、是否在事务内、预期耗时；(2) 才开始跑 upgrade。审完才能给真实的人日估算。**未审之前的 0.5 人日是占位符，不是承诺。**

---

## §2 中风险问题

### Q8: 索引在每个子分区上的重建工作量被低估
**维度**：【性能】  
**触发场景**：v1 §3.4 说"PG 12+ declarative partitioning 自动下放索引到所有分区"。但**自动下放 ≠ 零成本**：父表 `CREATE INDEX` 会被 PG 拆成"父表元数据 + 每个子分区一个 CREATE INDEX"。如果历史有 36 个月分区，5 个索引 × 36 = 180 次 CREATE INDEX，每次都是 ACCESS EXCLUSIVE（除非用 `CONCURRENTLY`，但 `CONCURRENTLY` 不能在事务内、也不能用于分区父表）。如果在迁移单事务里跑，整个事务会锁更久。  
**v1/v2 的处理**：v1 §3.4 一句带过；v2 §C 没修订。  
**风险**：中。数据量小时影响不大；Q1 的 dry-run 必须包含索引重建时长。  
**推荐回答**：dry-run 时分别测"INSERT 时长"和"索引建立时长"。可以考虑迁移内**先建表+插数+再建索引**（PG 在空数据 → 索引快、有数据 → 索引慢，但顺序选择影响总时长），由 dry-run 数据决定。

---

### Q9: 跨分区查询的 planner 退化未做 EXPLAIN 对比
**维度**：【性能】  
**触发场景**：现有代码大量 `WHERE date BETWEEN ? AND ?` 跨多月查询（dashboard 类 API 跨 6/12 个月）。分区表上 planner 走 partition pruning，但当 WHERE 子句含**非常量**（如 `date >= some_subquery`）或**函数**（如 `date_trunc`）时，pruning 可能失效，退化为全分区扫描。dashboard_service 的查询里大概率有这类模式（比如"近 12 个月"按当前时间算）。  
**v1/v2 的处理**：v1 §3.4 只说"保持现有索引集合不变"；v2 §C.9 验收口径里没有 EXPLAIN ANALYZE 对比项。  
**风险**：中。性能退化在小数据时不会暴露，等数据涨上来才发现。  
**推荐回答**：验收第 6 条增加"对 dashboard 关键 5 条 SQL 跑 EXPLAIN ANALYZE，迁移前后对比，若新计划走全分区扫描则 block 上线"。

---

### Q10: VIEW 下线判定靠 `pg_stat_user_tables` 不准确
**维度**：【合理性】  
**触发场景**：v1 §4.2 说"用 `pg_stat_user_tables` 监控 `billing_data` VIEW 的访问次数是否归零"。**但 `pg_stat_user_tables` 只统计 table，不统计 view**——VIEW 的访问会反映在底层表 `billing_summary` 的统计上，无法区分调用方是直接查 `billing_summary` 还是经 VIEW 转写。这意味着 T+90 天那个判定**实际不可执行**。  
**v1/v2 的处理**：v1 §4.2 + v2 §C.5 都假设这个监控可行，未质疑。  
**风险**：中。判定不可执行 → VIEW 永远拖着 → 永久兼容层。  
**推荐回答**：换成 `pg_stat_statements` 抓 query 文本里含 `billing_data` 的执行次数；或者更直接——给 VIEW 加一个会写日志的规则（`CREATE RULE` + 写专门审计表），统计 90 天访问。**不要用 `pg_stat_user_tables`。**

---

### Q11: "保留类名 BillingData / 改 __tablename__" 的长期债务无明确 deadline
**维度**：【合理性】  
**触发场景**：v1 §4.2 / v2 §C.2 都同意"保留类名 BillingData，后续 Phase 收尾期再统一"。v1 §9 把"统一类名"放到 Phase 5。**但 Phase 5 是 90 天后还是 1 年后？**项目历史上"后续再说"的事最后多半永远不再做。新加入开发者看到 `class BillingData: __tablename__ = "billing_summary"` 会困惑，IDE 跳转、文档检索都会割裂。  
**v1/v2 的处理**：v1 §9 列入 Phase 5 但 Phase 5 无时间盒；v2 §C 没修订。  
**风险**：中（技术债复利）。  
**推荐回答**：Phase 1 上线后**立刻**在 issue tracker 创建一张"重命名 BillingData → BillingSummary"的 ticket，绑定到 Phase 5，并在 model 文件顶上写 TODO 注释带 ticket 号。Phase 5 没启动前，每 90 天回顾一次。

---

### Q12: default 分区只告警不阻拦，可能埋数据
**维度**：【安全】  
**触发场景**：v1 §5.1 说"默认分区 `billing_summary_default` 兜底，监控 default 分区行数 > 0 时告警"。问题是：**告警不阻拦**——如果 Celery beat 漏跑（比如 worker 重启那天恰好是 25 号 02:00），下月分区不存在，下月 1 日新数据自动落 default 分区。等运维收到告警时数据已经在 default 里。然后想补建分区时会报错（PG 不允许在 default 有匹配数据时新建对应范围的子分区，必须先把数据搬出来）。  
**v1/v2 的处理**：v2 §C.8 补了"接 alert_rules"，但没解决"告警太迟"问题。  
**风险**：中。  
**推荐回答**：(a) Celery beat 提前量从"每月 25 日预创建下月+下下月"改为"每月 1/15/25 三次幂等检查未来 3 个月分区"，多重保险；(b) default 分区的告警应该**立即触发自动修复任务**（搬数据+建分区），不仅是通知人；(c) 验收增加一项"模拟 default 分区有数据后的恢复演练"。

---

### Q13: 根目录 4 个写脚本"靠 runbook 提醒人改表名"不可靠
**维度**：【安全】  
**触发场景**：v2 §A.9 + §C.5 列出 `consolidate_null_region.py` 等 4 个含 `INSERT/DELETE billing_data` 的脚本。runbook 说"复用前手动改表名"。但运维真要救火时，第一反应是"上次跑过的脚本就是好脚本"，直接 `python consolidate_null_region.py` —— VIEW 不可写直接报错，运维会以为脚本坏了去 debug 半小时，期间数据问题继续恶化。  
**v1/v2 的处理**：v2 §C.5 仅列入 runbook，未做物理隔离。  
**风险**：中。  
**推荐回答**：要么**直接把这 4 个脚本删掉**（已经运行过的一次性脚本不应留在仓库根目录污染搜索），要么**在脚本顶部硬编码 `raise RuntimeError("table renamed to billing_summary, edit before reuse")`**，让脚本一启动就报错并告诉解法。光靠 runbook 文档不够。

---

### Q14: v2 §F 估时未含文档审阅 / code review / 跨时区沟通
**维度**：【合理性】  
**触发场景**：v2 §F 估 6.75 → 7-8 人日，是**纯编码工时**。实际 PR review 在分布式团队里至少 1-2 人日往返；DBA 审核迁移脚本（如果有 DBA 角色）至少 0.5 人日；上线 runbook 走变更评审至少 0.5 人日。  
**v1/v2 的处理**：v1 §8 / v2 §F 都不含。  
**风险**：低-中（不影响技术，但影响交付承诺）。  
**推荐回答**：对外承诺的"完成日期"应该按**编码 7-8 人日 + review/审批 2-3 人日 = 10-12 个工作日（约 2 周）**给。如果对外承诺时压成"1 周"，迁移会被赶时间砍验证步骤，风险叠加。

---

### Q15: 没建 ETL skeleton，下一 Phase 工作量是否真的会翻倍未评估
**维度**：【合理性】  
**触发场景**：v1 §9 + v2 都同意"Phase 1 不做 ETL job"，留给 Phase 4。但 Phase 1 的设计——`billing_summary` 直接被 collector 写入——意味着 Phase 4 真要切到 Bronze→Silver ETL 时，必须**同时**改 collector + 建 ETL + 灰度切换 + 双写一段时间。如果现在能在分区表元数据上预留一个 `etl_run_id` / `source_partition` 字段（哪怕一直为 NULL），Phase 4 就可以无破坏接入。  
**v1/v2 的处理**：v1/v2 都未讨论 forward compatibility。  
**风险**：低-中。  
**推荐回答**：在迁移 020 里**预留**一个 `nullable=True` 的 `etl_run_id BIGINT` 列（成本几乎为零），即便 Phase 1 不用。这是 cheap insurance；不预留则 Phase 4 改表又要走一遍单事务 INSERT 的全部风险。**或者明确决策"不预留，Phase 4 重做一次"——但要白纸黑字接受这个代价**。

---

## §3 低风险问题（可以先做后改）

### Q16: 无审计日志记录 DDL 操作人
**维度**：【安全】  
**触发场景**：迁移由谁执行、什么时间、从哪个 IP，Azure PG 默认不开 pgaudit 时无追踪。出问题事后追责无依据。  
**v1/v2 的处理**：均未提。  
**风险**：低（合规性，不是功能性）。  
**推荐回答**：runbook 要求执行迁移时使用专用 DB 角色（不是共享 admin），并在 Azure PG 启用 pgaudit 至少记录 DDL。Phase 1 不做也行，但要有 issue 跟进。

---

### Q17: 凭据泄漏检查未做
**维度**：【安全】  
**触发场景**：v2 §A.2 提到 `alembic.ini:3` 直接含 `sqlalchemy.url = postgresql+...@dataope.postgres.database.azure.com:5432/cloudcost`——**这条 URL 是不是带密码？是不是被 commit 进 git？** 如果是，迁移的任何 log 输出（PG 报错、Sentry 上报）都可能把 URL 一起带出去。  
**v1/v2 的处理**：均未检查。  
**风险**：低-中（取决于 alembic.ini 是否含密码、是否在 .gitignore）。  
**推荐回答**：单独跑一次 `git log -p alembic.ini` + 检查当前 alembic.ini 内容，确认密码走 env var 不进文件。这是 Phase 1 上线前 10 分钟就能做掉的检查。

---

### Q18: 部署链路（CICD）没法明确"先 016-019 再 020/021"的执行方式
**维度**：【合理性】  
**触发场景**：v2 §D 第 9 条提到"alembic upgrade 是在容器启动 hook 里跑还是手动"。如果在容器启动 hook 里跑，**016→019→020→021 一气呵成**——意味着 020 的 dry-run 数据要包含前 4 个迁移的累积影响；如果是手动，分两次部署，中间隔 review。  
**v1/v2 的处理**：v2 §D 第 9 条列入待澄清。  
**风险**：低（只是流程问题，不是技术问题）。  
**推荐回答**：明确手动两阶段——先部署"含 016-019 但 020/021 还没合"的版本上 production，跑 upgrade，观察 1-2 天；再部署含 020/021 的版本。**不要一次部署 5 个迁移**。

---

### Q19: 单测覆盖空白
**维度**：【合理性】  
**触发场景**：v2 §B.1 表里 `tests/` 命中 0。意味着整个项目对 `billing_data` 相关逻辑**没有单元测试**。Phase 1 改完没办法用 pytest 兜底验回归——v1 §6 第 10 条"全量 pytest 绿"在测试空白的项目里是 trivially true。  
**v1/v2 的处理**：v1 §6 列了 pytest，v2 没修订。  
**风险**：低（不阻塞 Phase 1，但削弱了所有验收信心）。  
**推荐回答**：Phase 1 不强求补测试，但验收口径里必须显式注明"pytest 当前未覆盖 BillingData 路径，回归靠手动 API 调用 + 数据 diff"。不要让"pytest 绿"造成虚假安全感。

---

### Q20: row-level security / 多租户检查未做
**维度**：【安全】  
**触发场景**：本项目看起来是单租户（公司内部成本平台），但分区表 + 索引下放仍要确认有没有遗留的 RLS policy（`pg_policies` 表里有没有针对 `billing_data` 的 policy）。如果有，迁移到分区表后 policy 不会自动迁移，权限就裂开了。  
**v1/v2 的处理**：均未提。  
**风险**：低（多半没 RLS）。  
**推荐回答**：迁移前跑一次 `SELECT * FROM pg_policies WHERE tablename = 'billing_data'`，0 行则跳过。预防性检查 30 秒。

---

## §4 给用户的最终决策表

| # | 决策点 | 推荐答案 | Confidence |
|---|---|---|---|
| D1 | 上线前是否强制做与生产同 SKU 的 dry-run（Q1） | **强制**，未做不许上 | 高 |
| D2 | 迁移期间是否显式停 celery worker / beat（Q2） | **强制停**，replicas=0 | 高 |
| D3 | 备份用 Azure PITR 还是 pg_dump 还是两者都做（Q3） | **PITR 主 + pg_dump 辅**，且 pg_dump 必须 restore 验证 | 高 |
| D4 | keyset 翻页验收是否要跨月边界（Q4） | **必须**跨至少 3 个月边界 + 用真实数据 | 高 |
| D5 | 迁移 021 走 RENAME 还是 CREATE+搬运（Q5） | **运行时检测**：表存在走 RENAME，不存在走 CREATE | 高 |
| D6 | 删 `create_all` 后建表责任归谁（Q6） | conftest + Makefile 显式 `alembic upgrade`，README 同步 | 高 |
| D7 | T1.0 是否先审 016-019 再估时（Q7） | **先审再估**，0.5 人日是占位符 | 高 |
| D8 | 索引重建是否进 dry-run（Q8） | 进 | 高 |
| D9 | 是否加 EXPLAIN ANALYZE 验收（Q9） | 加，dashboard 5 条关键 SQL | 高 |
| D10 | VIEW 下线判定改用什么（Q10） | `pg_stat_statements` 文本匹配 / 审计表，不用 `pg_stat_user_tables` | 高 |
| D11 | 类名重命名 deadline（Q11） | Phase 1 上线后立即建 ticket 绑 Phase 5；TODO 注释带 ticket | 中 |
| D12 | default 分区是告警还是自动修复（Q12） | 多重幂等 + 自动修复任务 + 演练 | 中 |
| D13 | 根目录 4 个写脚本怎么处理（Q13） | **删掉** 或 顶部 `raise`；不靠 runbook | 中 |
| D14 | 对外承诺工期（Q14） | 编码 7-8 + review/审批 2-3 = 10-12 工作日 | 中 |
| D15 | 是否预留 etl_run_id 列（Q15） | **预留**（cheap insurance）；或明确决策不预留并接受 Phase 4 重做 | 中 |
| D16 | 是否启用 pgaudit（Q16） | Phase 1 后跟进 issue，不阻塞 | 低 |
| D17 | alembic.ini 是否含明文密码（Q17） | 上线前 10 分钟检查 | 高 |
| D18 | 016-019 与 020/021 是否分两次部署（Q18） | 分两次，中间观察 1-2 天 | 中 |
| D19 | 验收口径是否明确"pytest 不覆盖 billing 路径"（Q19） | 明确写入，避免虚假安全感 | 中 |
| D20 | 上线前是否检查 pg_policies（Q20） | 检查，30 秒成本 | 低 |

---

**总评**：v1+v2 合并后核心方向（declarative partitioning、保留类名、VIEW 兜底）合理，**但运维侧的"机制 vs 约定"差距明显**——大量重要操作（worker 停机、备份验证、dry-run、VIEW 下线判定）目前都是"runbook 里写一句"的**约定级**，没有上升为**机制级**（容器 replicas=0、强制 restore 测试、强制 EXPLAIN diff 才放行）。这是 Phase 1 上线最大的隐患，不是技术问题，是流程问题。**Q1/Q2/Q3/Q6/Q7 这 5 条不解决就不该动手。**
