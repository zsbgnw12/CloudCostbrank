# CloudCost 数据库设计文档

> 数据库: `cloudcost` @ Azure PostgreSQL (`dataope.postgres.database.azure.com:5432`)
> 建表脚本: `python create_tables.py` 或 `python init_database.py`
> 共 14 张表

---

## ER 关系总览

```
categories 1──N data_sources 1──N billing_data
    │               │                 
    │               └──N sync_logs
    │               └──N resource_inventory
    │
    └──N projects ←── data_sources (FK)
    │      │
    │      └──N project_assignment_logs
    │
    └──N monthly_bills

customers 1──N projects
    │
    └──N monthly_bills

cloud_accounts 1──N data_sources

alert_rules 1──N alert_history

exchange_rates (独立)
operation_logs (独立)
```

---

## 1. categories（渠道/货源）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| name | VARCHAR(100) | UNIQUE NOT NULL | 渠道名称，如"香港代理-01" |
| markup_rate | DECIMAL(5,4) | DEFAULT 1.0 | 加价比例，1.10 = 加价10% |
| description | TEXT | | 备注 |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), ON UPDATE | |

**关联:** → data_sources.category_id, projects.category_id, monthly_bills.category_id

---

## 2. cloud_accounts（云凭据）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| name | VARCHAR(100) | NOT NULL | 别名，如"主力AWS账号" |
| provider | VARCHAR(10) | NOT NULL | `aws` / `gcp` / `azure` |
| secret_data | TEXT | NOT NULL | Fernet(AES) 加密后的 JSON |
| is_active | BOOLEAN | DEFAULT TRUE | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), ON UPDATE | |

**secret_data 解密后结构：**
```json
// AWS
{"aws_access_key_id": "AKIA...", "aws_secret_access_key": "...", "role_arn": null, "external_id": null}
// GCP
{"service_account_json": { ... }}
// Azure
{"tenant_id": "...", "client_id": "...", "client_secret": "..."}
```

**关联:** → data_sources.cloud_account_id

---

## 3. data_sources（数据源配置）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| name | VARCHAR(100) | NOT NULL | 数据源名称 |
| cloud_account_id | INT | FK → cloud_accounts.id, NOT NULL | |
| category_id | INT | FK → categories.id, 可为空 | |
| config | JSONB | NOT NULL | 采集参数 |
| last_sync_at | TIMESTAMPTZ | | 最近同步完成时间 |
| sync_status | VARCHAR(20) | DEFAULT 'pending' | pending/running/success/failed |
| is_active | BOOLEAN | DEFAULT TRUE | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

**config 结构：**
```json
// GCP
{"project_id": "share-service-nonprod", "dataset": "xmind", "table": "billing_report", "cost_field": "cost_at_list", "usage_field": "amount_in_pricing_unit", "billing_account_id": "01DE67-975828-40894C", "is_native": false}
// AWS
{"account_id": null, "end_date": null}
// Azure
{"subscription_id": "45d7a360-...", "collect_mode": "subscription", "cost_metric": "ActualCost"}
```

**关联:** → billing_data.data_source_id, sync_logs.data_source_id, projects.data_source_id, resource_inventory.data_source_id

---

## 4. customers（客户）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| name | VARCHAR(100) | NOT NULL | 客户名称 |
| contact_person | VARCHAR(50) | | 联系人 |
| phone | VARCHAR(20) | | |
| email | VARCHAR(100) | | |
| billing_type | VARCHAR(10) | DEFAULT 'postpaid' | `prepaid` / `postpaid` |
| credit_limit | DECIMAL(14,2) | DEFAULT 0 | 后付费授信额度 |
| balance | DECIMAL(14,2) | DEFAULT 0 | 预付费余额 |
| status | VARCHAR(15) | DEFAULT 'active' | `active` / `suspended` / `terminated` |
| notes | TEXT | | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), ON UPDATE | |

**关联:** → projects.customer_id, monthly_bills.customer_id

---

## 5. projects（项目）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| name | VARCHAR(200) | NOT NULL | 项目显示名 |
| provider | VARCHAR(10) | NOT NULL | `aws` / `gcp` / `azure` |
| external_project_id | VARCHAR(200) | NOT NULL | GCP project.id / AWS account_id / Azure subscription_id |
| data_source_id | INT | FK → data_sources.id | |
| category_id | INT | FK → categories.id | |
| customer_id | INT | FK → customers.id | |
| status | VARCHAR(15) | DEFAULT 'standby' | 见状态机 |
| assigned_at | TIMESTAMPTZ | | 分配给客户的时间 |
| recycled_at | TIMESTAMPTZ | | 回收时间 |
| notes | TEXT | | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), ON UPDATE | |

**唯一约束:** `UNIQUE (provider, external_project_id)`

**状态机:**
```
                  assign
    standby ──────────────► active
       ▲                      │
       │ recover              │ suspend
       │                      ▼
       │                  suspended
       │ recover              │
       │                      │ recycle
       └──────────────── recycled
                              │ delete
                              ▼
                           deleted
```

| 动作 | 从 | 到 | 说明 |
|---|---|---|---|
| assign | standby | active | 分配客户 |
| suspend | active | suspended | 暂停 |
| resume | suspended | active | 恢复 |
| recycle | active/suspended | recycled | 回收，清空 customer_id |
| recover | recycled | standby | 重新激活为备用 |
| delete | recycled | deleted | 标记删除 |

---

## 6. billing_data（费用明细 — 核心大表）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGSERIAL | PK | |
| date | DATE | NOT NULL | 费用日期 |
| provider | VARCHAR(10) | NOT NULL | aws/gcp/azure |
| data_source_id | INT | FK → data_sources.id, NOT NULL | |
| project_id | VARCHAR(200) | | GCP project.id / AWS account_id / Azure sub_id |
| project_name | VARCHAR(200) | | |
| product | VARCHAR(200) | | GCP service / AWS service / Azure meterCategory |
| usage_type | VARCHAR(300) | | GCP sku / AWS usage_type / Azure meterName |
| region | VARCHAR(50) | | |
| cost | DECIMAL(20,6) | NOT NULL | |
| usage_quantity | DECIMAL(20,6) | DEFAULT 0 | |
| usage_unit | VARCHAR(50) | | |
| currency | VARCHAR(10) | DEFAULT 'USD' | |
| tags | JSONB | DEFAULT '{}' | |
| additional_info | JSONB | DEFAULT '{}' | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

**唯一约束（防重复 upsert）:**
```sql
UNIQUE (date, data_source_id, project_id, product, usage_type, region)
```

**索引:**
- `ix_billing_date (date)`
- `ix_billing_ds_date (data_source_id, date)`
- `ix_billing_project_date (project_id, date)`
- `ix_billing_provider_date (provider, date)`

---

## 7. resource_inventory（资产清单）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| provider | VARCHAR(10) | NOT NULL | |
| project_id | VARCHAR(200) | | |
| data_source_id | INT | FK → data_sources.id | |
| resource_id | VARCHAR(500) | | 云厂商资源 ID |
| resource_name | VARCHAR(200) | | |
| resource_type | VARCHAR(100) | | VM/Disk/DB/Network 等 |
| product | VARCHAR(200) | | 对应 billing_data.product |
| region | VARCHAR(50) | | |
| status | VARCHAR(20) | DEFAULT 'active' | |
| tags | JSONB | DEFAULT '{}' | |
| metadata | JSONB | DEFAULT '{}' | 实例规格等特有信息 |
| monthly_cost | DECIMAL(14,2) | DEFAULT 0 | 最近月汇总费用 |
| first_seen_at | TIMESTAMPTZ | | |
| last_seen_at | TIMESTAMPTZ | | |

**唯一约束:** `UNIQUE (provider, resource_id)`

---

## 8. sync_logs（同步日志）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| data_source_id | INT | FK → data_sources.id, NOT NULL | |
| celery_task_id | VARCHAR(100) | | Celery 任务 ID |
| start_time | TIMESTAMPTZ | NOT NULL | |
| end_time | TIMESTAMPTZ | | |
| status | VARCHAR(15) | | running/success/failed |
| query_start_date | DATE | | 采集起始日期 |
| query_end_date | DATE | | 采集结束日期 |
| records_fetched | INT | DEFAULT 0 | 拉取行数 |
| records_upserted | INT | DEFAULT 0 | 入库行数 |
| error_message | TEXT | | |

---

## 9. alert_rules（告警规则）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| name | VARCHAR(100) | NOT NULL | 规则名称 |
| target_type | VARCHAR(20) | NOT NULL | project/category/customer/provider |
| target_id | VARCHAR(200) | | 具体目标 ID，NULL = 全局 |
| threshold_type | VARCHAR(20) | NOT NULL | daily_absolute/daily_increase_pct/monthly_budget |
| threshold_value | DECIMAL(14,2) | NOT NULL | |
| notify_webhook | TEXT | | 飞书/钉钉 webhook URL |
| is_active | BOOLEAN | DEFAULT TRUE | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

---

## 10. alert_history（告警记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| rule_id | INT | FK → alert_rules.id, NOT NULL | |
| triggered_at | TIMESTAMPTZ | NOT NULL | |
| actual_value | DECIMAL(14,2) | | 触发时的实际值 |
| threshold_value | DECIMAL(14,2) | | 触发时的阈值 |
| message | TEXT | | 告警描述 |
| notified | BOOLEAN | DEFAULT FALSE | 是否已推送 |

---

## 11. monthly_bills（月度账单快照）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| month | VARCHAR(7) | NOT NULL | 'YYYY-MM' |
| customer_id | INT | FK → customers.id, NOT NULL | |
| category_id | INT | FK → categories.id, NOT NULL | |
| provider | VARCHAR(10) | | NULL = 全部汇总 |
| original_cost | DECIMAL(14,2) | NOT NULL | 原始成本 |
| markup_rate | DECIMAL(5,4) | NOT NULL | 当月快照比例 |
| final_cost | DECIMAL(14,2) | NOT NULL | 出账金额 |
| adjustment | DECIMAL(14,2) | DEFAULT 0 | 手动调账 |
| status | VARCHAR(15) | DEFAULT 'draft' | draft/confirmed/sent/paid |
| confirmed_at | TIMESTAMPTZ | | |
| notes | TEXT | | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

**唯一约束:** `UNIQUE (month, customer_id, category_id, provider)`

---

## 12. exchange_rates（汇率）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| date | DATE | NOT NULL | |
| from_currency | VARCHAR(5) | NOT NULL | 如 'USD' |
| to_currency | VARCHAR(5) | NOT NULL | 如 'CNY' |
| rate | DECIMAL(12,6) | NOT NULL | |

**唯一约束:** `UNIQUE (date, from_currency, to_currency)`

---

## 13. operation_logs（操作日志）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| operator | VARCHAR(50) | | 操作人（名字或 IP） |
| action | VARCHAR(50) | NOT NULL | create_project / assign_customer 等 |
| target_type | VARCHAR(30) | | 操作对象类型 |
| target_id | VARCHAR(50) | | 操作对象 ID |
| before_data | JSONB | | 变更前 |
| after_data | JSONB | | 变更后 |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

---

## 14. project_assignment_logs（项目分配历史）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | SERIAL | PK | |
| project_id | INT | FK → projects.id, NOT NULL, INDEX | |
| action | VARCHAR(30) | NOT NULL | created/assigned/reassigned/suspended/resumed/recycled/recovered/deleted |
| from_status | VARCHAR(15) | | 变更前状态 |
| to_status | VARCHAR(15) | | 变更后状态 |
| from_customer_id | INT | FK → customers.id | 变更前客户 |
| to_customer_id | INT | FK → customers.id | 变更后客户 |
| operator | VARCHAR(50) | | 操作人 |
| notes | TEXT | | 备注 |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

---

## 建表 SQL 参考（PostgreSQL 防重复建表）

如需手动建表，在 `cloudcost` 数据库中执行：
```sql
-- 使用 SQLAlchemy 自动建表（推荐）
-- python create_tables.py

-- 或使用 init_database.py（会自动创建数据库+建表）
-- python init_database.py
```

> **注意:** `create_tables.py` 使用 `Base.metadata.create_all()`，已存在的表不会被重复创建或覆盖。
