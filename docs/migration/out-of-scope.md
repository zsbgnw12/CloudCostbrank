# Out-of-scope 发现（v3 规划外但本次未修）

执行 v3-final 的 Phase 1 实施过程中发现，但**未修改**的事项。

## 1. `app/models/data_source.py:28` 的 `back_populates="billing_data"`

`relationship("BillingData", back_populates="billing_data")` 中的 `billing_data`
是 SQLAlchemy 关系反向访问器名（attribute name），不是表名，无需改动。
保留现状以避免破坏调用面。Phase 5 类名整体重命名时一并处理。

## 2. `tasks/sync_tasks.py:157` 的 `billing_data` 注释

v2-refined §B.1 显示 `tasks/sync_tasks.py` 有 1 处 `billing_data` 命中（注释）。
v3 §3 没把 `tasks/sync_tasks.py` 列入修改白名单，因此**不动**。
（实质风险：注释不影响运行；修订 OpenAPI/SQL 时可一并 sweep。）

## 3. 根目录除 4 个写脚本以外的 `_*.py` / `verify_*.py` / `backfill_*.py`

v2-refined §A.9 提到根目录还有许多含 `billing_data` 字面量的脚本。
v3 §3 明确"只改 4 个含真实写路径的"，其它一次性脚本（绝大多数是只读探查）
**不动**。此后若要复用，需开发者手工 sweep 表名。

## 4. 历史 alembic 迁移 001-019 中的 `billing_data` 字面量

v3 §3 明确禁止改动历史迁移。所有 66 处旧迁移引用保留原样。

## 5. `docs/taiji-ingest-api.md` / `docs/migration/v1-roadmap.md` 中的 `taiji_log_raw` 描述

未在 v3 §3 白名单内，不动。后续文档维护时可批量 replace。

## 6. `app/services/alert_service.py` 与 default 分区告警接入

v3 §6 / 决策 D12 期望 default 分区数据自动触发告警。
本次按 v3 §C 提供的"降级方案"落地：仅 `logger.warning` + `logger.info`，
不接 `alert_rules`（避免 Phase 1 额外引入跨服务依赖）。
完整告警链路接入留 Phase 2。
