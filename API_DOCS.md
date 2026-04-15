# CloudCost API 接口文档

> Base URL: `http://<host>:8000/api`
> 自动生成 Swagger UI: `http://<host>:8000/docs`

---

## 目录

1. [健康检查](#1-健康检查)
2. [仪表盘 Dashboard](#2-仪表盘-dashboard)
3. [渠道管理 Categories](#3-渠道管理-categories)
4. [云账号 Cloud Accounts](#4-云账号-cloud-accounts)
5. [数据源 Data Sources](#5-数据源-data-sources)
6. [客户管理 Customers](#6-客户管理-customers)
7. [项目管理 Projects](#7-项目管理-projects)
8. [费用明细 Billing](#8-费用明细-billing)
9. [数据同步 Sync](#9-数据同步-sync)
10. [资产清单 Resources](#10-资产清单-resources)
11. [告警 Alerts](#11-告警-alerts)
12. [月度账单 Bills](#12-月度账单-bills)
13. [汇率 Exchange Rates](#13-汇率-exchange-rates)

---

## 1. 健康检查

### `GET /api/health`

**响应:**
```json
{"status": "ok"}
```

---

## 2. 仪表盘 Dashboard

### `GET /api/dashboard/overview`

月度费用概览。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | ✅ | 格式 `YYYY-MM` |

**响应:**
```json
{
  "total_cost": 12345.67,
  "prev_month_cost": 11000.00,
  "mom_change_pct": 12.23,
  "active_projects": 42,
  "active_customers": 8
}
```

---

### `GET /api/dashboard/trend`

费用趋势（按云厂商拆分）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| start | string | ✅ | 起始月 `YYYY-MM` |
| end | string | ✅ | 结束月 `YYYY-MM` |
| granularity | string | | `daily` / `weekly` / `monthly`，默认 `daily` |

**响应:**
```json
[
  {
    "date": "2026-03-01",
    "cost": 1234.56,
    "cost_by_provider": {"aws": 500.00, "gcp": 600.00, "azure": 134.56}
  }
]
```

---

### `GET /api/dashboard/by-provider`

按云厂商汇总。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | ✅ | `YYYY-MM` |

**响应:**
```json
[{"provider": "aws", "cost": 5000.00, "percentage": 45.5}]
```

---

### `GET /api/dashboard/by-category`

按渠道加价汇总。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | ✅ | `YYYY-MM` |

**响应:**
```json
[{"category_id": 1, "name": "香港代理", "original_cost": 1000.00, "markup_rate": 1.10, "final_cost": 1100.00}]
```

---

### `GET /api/dashboard/by-project`

按项目费用排名。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | ✅ | `YYYY-MM` |
| limit | int | | 默认 20，最大 100 |

**响应:**
```json
[{"project_id": "my-project", "name": "My Project", "provider": "gcp", "customer_name": null, "cost": 2000.00}]
```

---

### `GET /api/dashboard/by-service`

按服务/产品费用排名。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | ✅ | `YYYY-MM` |
| provider | string | | 筛选云厂商 |
| limit | int | | 默认 20 |

**响应:**
```json
[{"product": "AmazonEC2", "cost": 3000.00, "percentage": 35.2}]
```

---

### `GET /api/dashboard/by-region`

按区域费用分布。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | ✅ | `YYYY-MM` |

**响应:**
```json
[{"region": "us-east-1", "provider": "aws", "cost": 1500.00}]
```

---

### `GET /api/dashboard/top-growth`

增长最快项目 TOP N。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| period | string | | 默认 `7d` |
| limit | int | | 默认 10 |

**响应:**
```json
[{"project_id": "proj-001", "name": null, "current_cost": 500.00, "previous_cost": 100.00, "growth_pct": 400.0}]
```

---

### `GET /api/dashboard/unassigned`

未分配客户的费用项目。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | ✅ | `YYYY-MM` |

**响应:**
```json
[{"project_id": "orphan-proj", "name": null, "provider": "aws", "cost": 800.00, "status": null}]
```

---

## 3. 渠道管理 Categories

### `GET /api/categories/`

获取所有渠道列表。

**响应:** `CategoryRead[]`

---

### `POST /api/categories/`

创建渠道。

**请求体:**
```json
{"name": "香港代理-01", "markup_rate": 1.10, "description": "备注"}
```

**响应:** `201` + `CategoryRead`

---

### `GET /api/categories/{id}`

获取单个渠道详情。

---

### `PUT /api/categories/{id}`

更新渠道。

**请求体:**
```json
{"name": "新名称", "markup_rate": 1.15, "description": "更新备注"}
```

---

### `DELETE /api/categories/{id}`

删除渠道。**响应:** `204`

---

## 4. 云账号 Cloud Accounts

### `GET /api/cloud-accounts/`

获取所有云账号（**不返回** secret_data）。

---

### `POST /api/cloud-accounts/`

创建云账号（secret_data 自动加密存储）。

**请求体:**
```json
{
  "name": "GCP主账号",
  "provider": "gcp",
  "secret_data": {
    "service_account_json": {
      "type": "service_account",
      "project_id": "xmagnet",
      "private_key_id": "...",
      "private_key": "-----BEGIN PRIVATE KEY-----\n...",
      "client_email": "...",
      "...": "..."
    }
  }
}
```

**响应:** `201` + `CloudAccountRead`（不含 secret_data）

---

### `GET /api/cloud-accounts/{id}`

获取单个云账号（脱敏）。

---

### `PUT /api/cloud-accounts/{id}`

更新云账号。

**请求体:**
```json
{"name": "新名称", "is_active": false}
```

如传入 `secret_data` 会重新加密存储。

---

### `DELETE /api/cloud-accounts/{id}`

删除云账号。**响应:** `204`

---

## 5. 数据源 Data Sources

### `GET /api/data-sources/`

获取所有数据源列表。

---

### `POST /api/data-sources/`

创建数据源。

**请求体（GCP 示例）:**
```json
{
  "name": "xmind",
  "cloud_account_id": 1,
  "category_id": 1,
  "config": {
    "project_id": "share-service-nonprod",
    "dataset": "xmind",
    "table": "billing_report",
    "cost_field": "cost_at_list",
    "usage_field": "amount_in_pricing_unit",
    "billing_account_id": "01DE67-975828-40894C",
    "is_native": false
  }
}
```

**请求体（AWS 示例）:**
```json
{
  "name": "AWS主账号",
  "cloud_account_id": 2,
  "config": {"account_id": null, "end_date": null}
}
```

**请求体（Azure 示例）:**
```json
{
  "name": "Azure订阅",
  "cloud_account_id": 3,
  "config": {"subscription_id": "45d7a360-...", "collect_mode": "subscription", "cost_metric": "ActualCost"}
}
```

---

### `GET /api/data-sources/{id}`

### `PUT /api/data-sources/{id}`

### `DELETE /api/data-sources/{id}`

---

## 6. 客户管理 Customers

### `GET /api/customers/`

获取所有客户列表。

---

### `POST /api/customers/`

创建客户。

**请求体:**
```json
{
  "name": "ABC科技",
  "contact_person": "张三",
  "phone": "13800138000",
  "email": "zhang@abc.com",
  "billing_type": "postpaid",
  "credit_limit": 50000.00
}
```

---

### `GET /api/customers/{id}`

### `PUT /api/customers/{id}`

### `DELETE /api/customers/{id}`

---

## 7. 项目管理 Projects

### `GET /api/projects/`

获取项目列表（支持筛选）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| status | string | | standby/active/suspended/recycled/deleted |
| provider | string | | aws/gcp/azure |
| customer_id | int | | 按客户筛选 |

---

### `POST /api/projects/`

创建项目（初始状态 `standby`，自动记录创建日志）。

**请求体:**
```json
{
  "name": "share-service-nonprod",
  "provider": "gcp",
  "external_project_id": "share-service-nonprod",
  "data_source_id": 1,
  "category_id": 1
}
```

---

### `GET /api/projects/{id}`

### `PUT /api/projects/{id}`

更新项目基本信息（name/data_source_id/category_id/notes）。

---

### `POST /api/projects/{id}/assign`

分配客户（standby → active）。自动记录分配日志。

**请求体:**
```json
{"customer_id": 3}
```

如果项目已有客户（reassigned），日志会记录 from_customer_id。

---

### `POST /api/projects/{id}/suspend`

暂停项目（active → suspended）。

---

### `POST /api/projects/{id}/resume`

恢复项目（suspended → active）。

---

### `POST /api/projects/{id}/recycle`

回收项目（active/suspended → recycled）。自动清空 customer_id。

---

### `POST /api/projects/{id}/recover`

恢复为备用（recycled → standby）。

---

### `POST /api/projects/{id}/delete`

标记删除（recycled → deleted）。

---

### `GET /api/projects/{id}/assignment-logs`

获取项目分配历史日志（按时间倒序）。

**响应:**
```json
[
  {
    "id": 5,
    "project_id": 42,
    "action": "assigned",
    "from_status": "standby",
    "to_status": "active",
    "from_customer_id": null,
    "to_customer_id": 3,
    "operator": null,
    "notes": null,
    "created_at": "2026-03-05T14:30:00"
  },
  {
    "id": 1,
    "project_id": 42,
    "action": "created",
    "from_status": "",
    "to_status": "standby",
    "from_customer_id": null,
    "to_customer_id": null,
    "operator": null,
    "notes": null,
    "created_at": "2026-03-01T10:00:00"
  }
]
```

---

## 8. 费用明细 Billing

### `GET /api/billing/detail`

费用明细查询（分页）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| date_start | string | | `YYYY-MM-DD` |
| date_end | string | | `YYYY-MM-DD` |
| provider | string | | aws/gcp/azure |
| project_id | string | | |
| product | string | | |
| page | int | | 默认 1 |
| page_size | int | | 默认 50，最大 500 |

**响应:** `BillingDetailRead[]`

---

### `GET /api/billing/export`

导出费用明细 CSV（同上筛选参数）。

**响应:** CSV 文件流，`Content-Disposition: attachment; filename=billing_export.csv`

---

## 9. 数据同步 Sync

### `POST /api/sync/all`

触发全部数据源同步。

**请求体:**
```json
{"start_month": "2026-03", "end_month": "2026-03"}
```

**响应:**
```json
{"task_id": "celery-task-uuid", "status": "dispatched"}
```

---

### `POST /api/sync/{data_source_id}`

触发单个数据源同步。

**请求体:** 同上

---

### `GET /api/sync/status/{task_id}`

查询 Celery 任务状态。

**响应:**
```json
{"task_id": "xxx", "status": "SUCCESS", "result": {"data_source_id": 1, "fetched": 500, "upserted": 480}}
```

---

### `GET /api/sync/logs`

查询同步日志。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| data_source_id | int | | 按数据源筛选 |
| status | string | | running/success/failed |
| limit | int | | 默认 50，最大 200 |

**响应:** `SyncLogRead[]`

---

## 10. 资产清单 Resources

### `GET /api/resources/`

资产清单查询（分页）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| provider | string | | |
| project_id | string | | |
| resource_type | string | | |
| page | int | | 默认 1 |
| page_size | int | | 默认 50 |

---

### `GET /api/resources/{id}`

获取单个资源详情。

---

## 11. 告警 Alerts

### `GET /api/alerts/rules/`

获取所有告警规则。

---

### `POST /api/alerts/rules/`

创建告警规则。

**请求体:**
```json
{
  "name": "项目日费用超500",
  "target_type": "project",
  "target_id": "my-project",
  "threshold_type": "daily_absolute",
  "threshold_value": 500.00,
  "notify_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
}
```

---

### `PUT /api/alerts/rules/{id}`

### `DELETE /api/alerts/rules/{id}`

---

### `GET /api/alerts/history`

查询告警历史。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| rule_id | int | | 按规则筛选 |
| limit | int | | 默认 50 |

---

## 12. 月度账单 Bills

### `GET /api/bills/`

查询账单列表。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| month | string | | `YYYY-MM` |
| customer_id | int | | |
| status | string | | draft/confirmed/sent/paid |

---

### `POST /api/bills/generate`

自动生成月度账单（按 customer × category × provider 聚合）。

**请求体:**
```json
{"month": "2026-03"}
```

**响应:**
```json
{"generated": 15, "month": "2026-03"}
```

---

### `GET /api/bills/{id}`

获取单个账单。

---

### `PUT /api/bills/{id}/adjust`

手动调账。

**请求体:**
```json
{"adjustment": -50.00, "notes": "折扣优惠"}
```

会自动重算 `final_cost = original_cost × markup_rate + adjustment`。

---

### `POST /api/bills/{id}/confirm`

确认账单（status → confirmed）。

---

### `POST /api/bills/{id}/mark-paid`

标记已付（status → paid）。

---

## 13. 汇率 Exchange Rates

### `GET /api/exchange-rates/`

查询汇率列表。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| date | string | | `YYYY-MM-DD` |
| from_currency | string | | 如 `USD` |

---

### `POST /api/exchange-rates/`

新增汇率。

**请求体:**
```json
{"date": "2026-03-31", "from_currency": "USD", "to_currency": "CNY", "rate": 7.2345}
```

---

### `PUT /api/exchange-rates/{id}`

更新汇率。

**请求体:**
```json
{"rate": 7.2500}
```

---

## 通用说明

### 错误响应格式

```json
{"detail": "Category not found"}
```

HTTP 状态码:
- `200` 成功
- `201` 创建成功
- `204` 删除成功（无响应体）
- `400` 请求参数错误 / 状态流转非法
- `404` 资源不存在
- `422` 请求体校验失败
- `500` 服务器内部错误

### 定时任务（Celery Beat）

| 任务 | 调度 | 说明 |
|---|---|---|
| `sync_all` | 每天 02:00 | 同步当月所有数据源 |
| `check_alerts` | 每天 03:00 | 运行告警检查 |
| `generate_monthly_bills` | 每月 2 号 05:00 | 自动生成上月账单 |
