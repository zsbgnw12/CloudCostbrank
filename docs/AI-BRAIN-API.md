# AI 大脑 · 对内程序调用接口说明

本文档供 **内部 AI 大脑 / 自动化程序** 在回答 **费用、用量、账单、资源归属** 等问题时调用 CloudCost 后端使用。  

- **范围**：以 **只读（GET）** 为主，标注请求参数与响应 JSON 结构（与 FastAPI 序列化一致：`date` 多为 `YYYY-MM-DD` 字符串，`datetime` 为 ISO8601；金额/用量在 JSON 中一般为 **number**，若见字符串多为 Decimal 序列化，按数值解析即可）。  
- **完整路由清单**：见 [API.md](./API.md)。  
- **鉴权**：当前后端 **未实现统一 API Key / OAuth**。生产环境应由 **网关或内网策略** 限制仅 AI 网关可访问；下文仍建议仅开放「推荐接口」列表。

---

## 1. 调用约定

| 项 | 说明 |
|----|------|
| Base URL | 例：`https://orange-wave-09002e800.7.azurestaticapps.net/`，路径均以 `/api` 开头 |
| 方法 | 下文推荐接口均为 **GET**（除健康检查外） |
| 错误 | `4xx/5xx` 响应体多为 `{"detail": "..."}` 或列表（校验错误）；数据库不可达可能为 `503` |
| 分页 | 带 `page` / `page_size` 的接口：页码从 **1** 起 |
| 大数据量 | 优先用 **聚合接口**（Dashboard、Metering summary）；明细用 `page_size` 控制，避免一次拉全表 |

---

## 2. 接口分级

| 级别 | 含义 |
|------|------|
| **P0 推荐** | 费用总览、趋势、计量聚合、账单明细分页、数据新鲜度 |
| **P1 补充** | 维度拆分（分类/区域/项目排行）、服务账号上下文、月度账单、告警阈值执行态 |
| **P2 可选** | 资源清单、汇率、分类字典、导出类流式接口（适合落盘，不适合直接塞进模型上下文） |
| **勿对 AI 开放** | 任何 **写操作**、**凭据解密**、**同步触发**、**删除**、**Azure 部署**、以及返回 **webhook/邮箱** 等敏感配置的接口 |

---

## 3. P0 推荐接口（入参 / 出参）

### 3.1 `GET /api/health`

**用途**：连通性探测。

**入参**：无。

**出参**：

```json
{ "status": "ok" }
```

---

### 3.2 `GET /api/sync/last`

**用途**：回答「数据同步到什么时候」。

**入参**：无。

**出参**：

```json
{ "last_sync": "2026-04-12T08:30:00" | null }
```

`last_sync` 为最近一次 **成功** 同步结束时间（ISO8601）；无记录或异常降级时为 `null`。

---

### 3.3 `GET /api/dashboard/bundle`

**用途**：**单次请求**拿首页级总览（等价于分别调 overview + trend + by_provider + by_service，减少往返）。

**入参（Query）**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `month` | string | 是 | `YYYY-MM`，统计月 |
| `granularity` | string | 否 | `daily` \| `weekly` \| `monthly`，默认 `daily`（仅影响 `trend`） |
| `service_limit` | int | 否 | 1–100，默认 10，`by_service` 返回条数上限 |

**出参**：

```json
{
  "overview": {
    "total_cost": 0,
    "prev_month_cost": 0,
    "mom_change_pct": 0,
    "active_projects": 0
  },
  "trend": [
    {
      "date": "2026-04-01",
      "cost": 0,
      "cost_by_provider": { "aws": 0, "gcp": 0 }
    }
  ],
  "by_provider": [
    { "provider": "aws", "cost": 0, "percentage": 0 }
  ],
  "by_service": [
    { "product": "Compute", "cost": 0, "percentage": 0 }
  ]
}
```

说明：`trend[].cost` 为该周期内各云厂商费用之和；`cost_by_provider` 为按厂商拆分。

---

### 3.4 `GET /api/dashboard/overview`

**用途**：只要月度总览卡片数据。

**入参（Query）**：`month`（必填，`YYYY-MM`）。

**出参**（对象，同 `bundle.overview`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `total_cost` | number | 当月总费用 |
| `prev_month_cost` | number | 上月总费用 |
| `mom_change_pct` | number | 环比变化百分比 |
| `active_projects` | integer | 状态为 active 的项目数 |

---

### 3.5 `GET /api/metering/summary`

**用途**：按条件汇总 **billing_data** 用量/费用（与云同步明细一致，可筛选账号、货源、供应商）。

**入参（Query）**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date_start` | string | 否 | `YYYY-MM-DD` |
| `date_end` | string | 否 | `YYYY-MM-DD` |
| `provider` | string | 否 | 如 `aws` / `gcp` / `azure` |
| `product` | string | 否 | 产品/服务名，模糊一致于库中 `product` |
| `account_id` | int | 否 | 服务账号（内部 `Project.id`） |
| `supply_source_id` | int | 否 | 货源 ID |
| `supplier_name` | string | 否 | 供应商名称；`"(未分组)"` 表示供应商「未分组」 |
| `data_source_id` | int | 否 | 数据源 ID |

**出参**：

```json
{
  "total_cost": 0,
  "total_usage": 0,
  "record_count": 0,
  "service_count": 0
}
```

---

### 3.6 `GET /api/metering/daily`

**用途**：按日聚合费用与用量（折线/趋势）。

**入参**：与 `metering/summary` 相同的 Query（`product` 会参与过滤）。

**出参**：`DailyUsageStats[]`

```json
[
  {
    "date": "2026-04-01",
    "usage_quantity": 0,
    "cost": 0,
    "record_count": 0
  }
]
```

---

### 3.7 `GET /api/metering/by-service`

**用途**：按 **product（服务）** 聚合用量与费用（Top N 分析）。

**入参**：与 summary 类似，但 **无 `product` 维度过滤**（用于看全服务分布）。

**出参**：`ServiceUsageStats[]`

```json
[
  {
    "product": "Amazon EC2",
    "usage_quantity": 0,
    "usage_unit": "Hours",
    "cost": 0,
    "record_count": 0
  }
]
```

---

### 3.8 `GET /api/metering/detail`

**用途**：**原始明细行**分页（最细粒度，适合钻取）。

**入参（Query）**：在 summary 的筛选基础上增加：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | ≥1 |
| `page_size` | int | 50 | 1–500 |

**出参**：`UsageDetailRead[]`

```json
[
  {
    "id": 1,
    "date": "2026-04-01",
    "provider": "aws",
    "data_source_id": 1,
    "project_id": "proj-xxx",
    "product": "EC2",
    "usage_type": "BoxUsage",
    "region": "ap-east-1",
    "cost": 0,
    "usage_quantity": 0,
    "usage_unit": "Hrs",
    "currency": "USD"
  }
]
```

---

### 3.9 `GET /api/metering/detail/count`

**用途**：与 `detail` 同筛选条件下的总条数（分页用）。

**入参**：同 `metering/detail`（不含 `page` / `page_size`）。

**出参**：

```json
{ "total": 0 }
```

---

### 3.10 `GET /api/billing/detail`

**用途**：计费明细列表（字段与 metering 明细接近，列表视图 **不含** `tags` / `additional_info` 重字段）。

**入参（Query）**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `date_start` | string | `YYYY-MM-DD` |
| `date_end` | string | `YYYY-MM-DD` |
| `provider` | string | 可选 |
| `project_id` | string | 可选，外部项目 ID |
| `product` | string | 可选 |
| `page` | int | 默认 1 |
| `page_size` | int | 默认 50，最大 500 |

**出参**：`BillingListRead[]`

```json
[
  {
    "id": 1,
    "date": "2026-04-01",
    "provider": "aws",
    "data_source_id": 1,
    "project_id": "xxx",
    "project_name": "xxx",
    "product": "EC2",
    "usage_type": "BoxUsage",
    "region": "us-east-1",
    "cost": 0,
    "usage_quantity": 0,
    "usage_unit": "Hrs",
    "currency": "USD"
  }
]
```

---

### 3.11 `GET /api/billing/detail/count`

**用途**：与 `billing/detail` 相同筛选下的总行数。

**入参**：同 `billing/detail`（无 page）。

**出参**：`{ "total": 0 }`

---

## 4. P1 补充接口（入参 / 出参）

### 4.1 Dashboard 其他只读拆分

| 路径 | 入参（Query） | 出参摘要 |
|------|----------------|----------|
| `GET /api/dashboard/trend` | `start`、`end`：`YYYY-MM`；`granularity`：`daily`\|`weekly`\|`monthly` | `[{ "date", "cost", "cost_by_provider": { ... } }]` |
| `GET /api/dashboard/by-provider` | `month` | `[{ "provider", "cost", "percentage" }]` |
| `GET /api/dashboard/by-category` | `month` | `[{ "category_id", "name", "original_cost", "markup_rate", "final_cost" }]` |
| `GET /api/dashboard/by-project` | `month`，`limit` 1–100 | `[{ "project_id", "name", "provider", "cost" }]` |
| `GET /api/dashboard/by-service` | `month`，`provider` 可选，`limit` 1–100 | `[{ "product", "cost", "percentage" }]` |
| `GET /api/dashboard/by-region` | `month` | `[{ "region", "provider", "cost" }]` |
| `GET /api/dashboard/top-growth` | `period` 默认 `7d`，`limit` 1–50 | `[{ "project_id", "name", "current_cost", "previous_cost", "growth_pct" }]` |
| `GET /api/dashboard/unassigned` | `month` | `[{ "project_id", "name", "provider", "cost", "status" }]`（`status` 可能为 null） |

---

### 4.2 `GET /api/service-accounts/`

**用途**：服务账号列表（人读「有哪些账号」）。

**入参（Query）**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | string | 可选 |
| `status` | string | 可选，如 `active` / `inactive` / `standby` |
| `page` | int | 默认 1 |
| `page_size` | int | 默认 100，最大 500 |

**注意**：路径需带尾部斜杠：`/api/service-accounts/`。

**出参**：数组

```json
[
  {
    "id": 1,
    "name": "string",
    "supply_source_id": 1,
    "supplier_name": "string",
    "provider": "aws",
    "external_project_id": "string",
    "status": "active",
    "created_at": "2026-04-01T00:00:00"
  }
]
```

---

### 4.3 `GET /api/service-accounts/{account_id}`

**用途**：单账号详情（含状态变更历史；**仅字段名** `secret_fields`，无密钥内容）。

**入参（Path）**：`account_id`（int）。

**出参**：

```json
{
  "id": 1,
  "name": "string",
  "supply_source_id": 1,
  "supplier_id": 1,
  "supplier_name": "string",
  "provider": "aws",
  "external_project_id": "string",
  "status": "active",
  "notes": null,
  "secret_fields": ["client_id"],
  "created_at": "2026-04-01T00:00:00",
  "history": [
    {
      "id": 1,
      "action": "created",
      "from_status": null,
      "to_status": "active",
      "operator": null,
      "notes": null,
      "created_at": "2026-04-01T00:00:00"
    }
  ]
}
```

---

### 4.4 `GET /api/service-accounts/{account_id}/costs`

**用途**：单账号在日期区间内的费用汇总（按服务、按日）。

**入参（Query）**：`start_date`、`end_date`（必填，`YYYY-MM-DD`）。

**出参**：

```json
{
  "total_cost": 0,
  "total_usage": 0,
  "services": [
    { "service": "EC2", "cost": 0, "usage_quantity": 0, "usage_unit": "Hrs" }
  ],
  "daily": [
    { "date": "2026-04-01", "cost": 0, "usage_quantity": 0 }
  ],
  "daily_by_service": [
    {
      "date": "2026-04-01",
      "service": "EC2",
      "cost": 0,
      "usage_quantity": 0,
      "usage_unit": "Hrs"
    }
  ]
}
```

---

### 4.5 `GET /api/service-accounts/daily-report`

**用途**：多账号按日、按产品汇总的费用（日报类问题）。

**入参（Query）**：

| 参数 | 必填 | 说明 |
|------|------|------|
| `start_date` | 是 | `YYYY-MM-DD` |
| `end_date` | 是 | `YYYY-MM-DD` |
| `provider` | 否 | 云厂商 |

**出参**：数组

```json
[
  {
    "account_id": 1,
    "account_name": "string",
    "provider": "aws",
    "external_project_id": "string",
    "date": "2026-04-01",
    "product": "EC2",
    "cost": 0
  }
]
```

---

### 4.6 `GET /api/projects/` 与 `GET /api/projects/{project_id}`

**用途**：项目维度元数据（供应商、云厂商、外部 ID）。

**列表入参（Query）**：`status`，`provider`，`page`，`page_size`。

**出参（单项，`ProjectRead`）**：

```json
{
  "id": 1,
  "name": "string",
  "supply_source_id": 1,
  "provider": "aws",
  "supplier_name": "string",
  "external_project_id": "string",
  "data_source_id": 1,
  "category_id": null,
  "status": "active",
  "notes": null,
  "created_at": "2026-04-01T00:00:00",
  "updated_at": "2026-04-01T00:00:00"
}
```

---

### 4.7 `GET /api/bills/`

**用途**：月度账单列表（含加价、调整、状态）。

**入参（Query）**：`month`（`YYYY-MM`），`status`，`page`，`page_size`。

**出参**：`MonthlyBillRead[]`

```json
[
  {
    "id": 1,
    "month": "2026-04",
    "category_id": 1,
    "provider": "aws",
    "original_cost": 0,
    "markup_rate": 1,
    "final_cost": 0,
    "adjustment": 0,
    "status": "draft",
    "confirmed_at": null,
    "notes": null,
    "created_at": "2026-04-01T00:00:00"
  }
]
```

`status` 常见：`draft` / `confirmed` / `paid`。

---

### 4.8 `GET /api/bills/{bill_id}`

**用途**：单张月度账单详情（同上结构，单对象）。

---

### 4.9 `GET /api/alerts/rule-status`

**用途**：各活跃规则在当前（或指定）月的 **实际值 vs 阈值**、是否触发。

**入参（Query）**：`month`（可选，`YYYY-MM`，默认当月）。

**出参**：数组

```json
[
  {
    "rule_id": 1,
    "rule_name": "string",
    "threshold_type": "monthly_budget",
    "threshold_value": 0,
    "actual": 0,
    "pct": 0,
    "triggered": false,
    "account_name": "string",
    "provider": "aws",
    "external_project_id": "string"
  }
]
```

`threshold_type` 含：`daily_absolute`、`monthly_budget`、`daily_increase_pct`、`monthly_minimum_commitment` 等（以后端模型为准）。

---

### 4.10 `GET /api/suppliers/supply-sources/all`

**用途**：下拉/推理用：供应商与货源（云类型）对照。

**入参（Query）**：`supplier_id`（可选，筛选某供应商）。

**出参**：

```json
[
  {
    "id": 1,
    "supplier_id": 1,
    "supplier_name": "string",
    "provider": "aws",
    "account_count": 0
  }
]
```

---

### 4.11 `GET /api/metering/products`

**用途**：在筛选条件下出现过的 `product` 去重列表（做下拉或意图消歧）。

**入参**：`provider` 可选；另支持与 summary 相同的 `account_id`、`supply_source_id`、`supplier_name`、`data_source_id`。

**出参**：

```json
[{ "product": "Amazon EC2" }]
```

---

## 5. P2 可选接口

| 路径 | 用途 | 备注 |
|------|------|------|
| `GET /api/categories/` | 费用分类字典 | 含 `markup_rate` |
| `GET /api/exchange-rates/` | 汇率 | Query：`date`，`from_currency` |
| `GET /api/data-sources/` | 数据源列表 | 与同步渠道、分类关联 |
| `GET /api/resources/` | 资源清单分页 | `provider`，`project_id`，`resource_type` |
| `GET /api/resources/{id}` | 单条资源 | 含 `monthly_cost` 等 |
| `GET /api/billing/export` | CSV 流 | 大流量，适合工具落盘 |
| `GET /api/metering/export` | CSV 流 | 同上 |

---

## 6. 禁止对 AI 大脑开放的接口（安全与副作用）

以下类型 **不应** 加入 AI 可调工具列表：

- **凭据**：`GET /api/service-accounts/{id}/credentials`
- **写操作**：所有 `POST` / `PUT` / `PATCH` / `DELETE`（含同步、账单调整、供应商、告警规则、服务账号变更等）
- **同步与任务**：`/api/sync/*` 中除 `GET /last` 外的接口
- **导出敏感或过大**：按需限制；或仅允许服务端托管任务
- **Azure 部署**：`/api/azure-deploy/*`（用户 ARM Token、资源创建）

---

## 7. 给 AI 编排器的建议调用顺序（示例）

1. `GET /api/sync/last` → 说明数据新鲜度。  
2. `GET /api/dashboard/bundle` 或 `metering/summary` → 总览与区间。  
3. 需要钻取 → `metering/detail` + `detail/count` 分页，或 `billing/detail`。  
4. 需要业务主体 → `service-accounts/` 或 `projects/`。  
5. 需要「是否超支/承诺」→ `alerts/rule-status`。  

---

## 8. OpenAPI

运行时可通过 **`GET /openapi.json`** 或 **`/docs`** 获取与部署版本一致的 Schema；字段以线上为准。若本文与 OpenAPI 冲突，**以 OpenAPI 为准**。
