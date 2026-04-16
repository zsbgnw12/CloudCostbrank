# AI 大脑 · 对内程序调用接口说明

本文档供 **内部 AI 大脑 / 自动化程序** 在回答 **费用、用量、账单、资源归属** 等问题时调用 CloudCost 后端使用。

- **范围**：以 **只读（GET）** 为主，标注请求参数与响应 JSON 结构（与 FastAPI 序列化一致：`date` 多为 `YYYY-MM-DD` 字符串，`datetime` 为 ISO8601；金额/用量在 JSON 中一般为 **number**，若见字符串多为 Decimal 序列化，按数值解析即可）。
- **完整路由清单**：见 [API.md](./API.md)。

---

## 1. 认证体系概述

云管后端已接入 **Casdoor 统一认证**，所有请求（除匿名白名单外）必须携带合法凭据。

### 1.1 三种认证方式

| 方式 | Header | 适用场景 | 角色来源 |
|------|--------|----------|----------|
| Casdoor OAuth Cookie | 浏览器自动携带 `cc_access_token` | 人类用户通过前端登录 | Casdoor token 中的 roles |
| Casdoor Bearer Token | `Authorization: Bearer <token>` | 内部系统间调用（client_credentials） | token roles 非空时用 token；为空时 fallback 到 DB `users.roles` |
| API Key | `X-API-Key: cck_xxx` | 三方对接 / 细粒度权限控制 | DB `users.roles`（owner 用户） |

**AI 大脑 / 内部系统推荐使用方式 2（Casdoor Bearer Token）**，因为所有内部系统已在 Casdoor 注册了应用，统一走 `client_credentials` 拿 token 即可。

### 1.2 四个角色

| 角色 | 权限范围 |
|------|----------|
| `cloud_admin` | 全部功能 + 全量数据（超级角色，自动满足任何角色要求） |
| `cloud_ops` | dashboard + 触发同步 |
| `cloud_finance` | dashboard + 账单管理 |
| `cloud_viewer` | dashboard 只读 |

角色管理方式：
- **人类用户**：在 Casdoor 后台 → Roles → 给用户分配角色，登录时自动带入
- **机器应用**（client_credentials）：Casdoor token 不携带角色，管理员通过 SQL 设置 DB 中的 `users.roles`

### 1.3 模块开关

每个业务路由绑定一个模块名（如 `dashboard`、`billing`）。管理员可通过 `PATCH /api/api-permissions/<module>` 全局关停某模块，关停后所有身份调用都会 `403`。AI 应优雅降级，不要把 `403` 当故障。

### 1.4 数据可见范围

同一个 URL，不同身份返回的**数值不同**：
- `cloud_admin`（无额外限制）→ 全量数据
- 非 admin → 仅看到 `user_cloud_account_grants` 表里被授权的云账号数据
- Dashboard 聚合接口在 **SUM 之前**加 WHERE 过滤，百分比/增长率基于过滤后数据重算
- 如果没有可见数据，返回空数组或零值（不是 403）

AI 回答时应说明"当前视角内"的数据，避免把有限视角误报为全量。

---

## 2. AI 大脑接入指南

### 2.1 获取 Token（Casdoor client_credentials）

```bash
CASDOOR=https://casdoor.ashyglacier-8207efd2.eastasia.azurecontainerapps.io

TOKEN=$(curl -s -X POST "$CASDOOR/api/login/oauth/access_token" \
  -d "grant_type=client_credentials&client_id=<你的APP_CLIENT_ID>&client_secret=<你的APP_CLIENT_SECRET>" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

- token 默认有效期 **24 小时**，调用方应缓存并在过期前刷新
- 首次使用该 token 调用云管 API 时，后端会自动在 `users` 表创建一条记录（`casdoor_sub=admin/<app_name>`，`roles=[]`）
- **管理员需提前设置角色**（否则所有需要角色的接口返回 403）：
  ```sql
  UPDATE users SET roles='["cloud_admin"]'::jsonb WHERE casdoor_sub='admin/<app_name>';
  ```

### 2.2 调用接口

```bash
BASE=https://cloudcost-brank.yellowground-bf760827.southeastasia.azurecontainerapps.io

# 确认身份与可见范围
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/auth/me"

# 当月首页 bundle
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/dashboard/bundle?month=2026-04"

# 明细分页
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/metering/detail?date_start=2026-04-01&date_end=2026-04-30&provider=aws&page=1&page_size=100"
```

### 2.3 `/api/auth/me` 响应说明

```json
{
  "id": 4,
  "username": "sales",
  "email": "",
  "display_name": "Sales App (M2M)",
  "roles": ["cloud_admin"],
  "visible_cloud_account_ids": null
}
```

`visible_cloud_account_ids`：
- `null` → 全量可见（`cloud_admin` 身份）
- `[]` → 零可见（未被授权任何云账号）
- `[1, 2]` → 只能看到这些云账号的数据

### 2.4 错误码

| HTTP 状态码 | 含义 | AI 应对 |
|---|---|---|
| `200` | 成功 | 正常解析 |
| `401` | 未带凭据或 token 过期 | 刷新 token 后重试 |
| `403 missing required role` | 角色不足 | 该接口对当前身份不可用，跳过 |
| `403 Module 'x' is disabled` | 模块被管理员关停 | 优雅降级，不报故障 |
| `422` | 参数校验失败 | 检查 Query 参数格式 |
| `503` | 数据库不可达 | 稍后重试 |

---

## 3. 接口分级

| 级别 | 含义 |
|------|------|
| **P0 推荐** | 费用总览、趋势、计量聚合、账单明细分页、数据新鲜度 |
| **P1 补充** | 维度拆分（分类/区域/项目排行）、服务账号上下文、月度账单、告警阈值执行态 |
| **P2 可选** | 资源清单、汇率、分类字典、导出类流式接口（适合落盘，不适合直接塞进模型上下文） |
| **禁止** | 任何 **写操作**、**凭据解密**、**同步触发**、**删除**、**Azure 部署**、以及返回 **webhook/邮箱** 等敏感配置的接口 |

---

## 4. P0 推荐接口（入参 / 出参）

### 4.1 `GET /api/health`

**用途**：连通性探测（匿名可访问）。

**出参**：

```json
{ "status": "ok" }
```

---

### 4.2 `GET /api/sync/last`

**用途**：回答「数据同步到什么时候」。模块：`sync`，角色：`cloud_ops`。

**出参**：

```json
{ "last_sync": "2026-04-12T08:30:00" | null }
```

`last_sync` 为最近一次 **成功** 同步结束时间（ISO8601）；无记录时为 `null`。

---

### 4.3 `GET /api/dashboard/bundle`

**用途**：**单次请求**拿首页级总览。模块：`dashboard`，角色：`cloud_viewer` / `cloud_ops` / `cloud_finance`（任一即可，`cloud_admin` 自动通过）。

**入参（Query）**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `month` | string | 是 | `YYYY-MM`，统计月 |
| `granularity` | string | 否 | `daily` \| `weekly` \| `monthly`，默认 `daily` |
| `service_limit` | int | 否 | 1–100，默认 10 |

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

---

### 4.4 `GET /api/dashboard/overview`

**用途**：只要月度总览卡片数据。

**入参（Query）**：`month`（必填，`YYYY-MM`）。

**出参**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `total_cost` | number | 当月总费用 |
| `prev_month_cost` | number | 上月总费用 |
| `mom_change_pct` | number | 环比变化百分比 |
| `active_projects` | integer | 状态为 active 的项目数 |

---

### 4.5 `GET /api/metering/summary`

**用途**：按条件汇总用量/费用。模块：`metering`，角色：任意登录即可。

**入参（Query）**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date_start` | string | 否 | `YYYY-MM-DD` |
| `date_end` | string | 否 | `YYYY-MM-DD` |
| `provider` | string | 否 | `aws` / `gcp` / `azure` |
| `product` | string | 否 | 产品/服务名 |
| `account_id` | int | 否 | 服务账号 ID |
| `supply_source_id` | int | 否 | 货源 ID |
| `supplier_name` | string | 否 | 供应商名称 |
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

### 4.6 `GET /api/metering/daily`

**用途**：按日聚合费用与用量。入参同 `metering/summary`。

**出参**：

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

### 4.7 `GET /api/metering/by-service`

**用途**：按服务聚合（Top N 分析）。入参同 summary（无 `product` 过滤）。

**出参**：

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

### 4.8 `GET /api/metering/detail`

**用途**：原始明细行分页。入参在 summary 基础上增加 `page`（默认 1）、`page_size`（默认 50，最大 500）。

**出参**：

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

### 4.9 `GET /api/metering/detail/count`

**用途**：与 `detail` 同筛选条件下的总条数。

**出参**：`{ "total": 0 }`

---

### 4.10 `GET /api/billing/detail`

**用途**：计费明细列表。模块：`billing`，角色：任意登录。数据按可见数据源过滤。

**入参（Query）**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `date_start` | string | `YYYY-MM-DD` |
| `date_end` | string | `YYYY-MM-DD` |
| `provider` | string | 可选 |
| `project_id` | string | 可选 |
| `product` | string | 可选 |
| `page` | int | 默认 1 |
| `page_size` | int | 默认 50，最大 500 |

**出参**：

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

### 4.11 `GET /api/billing/detail/count`

**用途**：与 `billing/detail` 相同筛选下的总行数。

**出参**：`{ "total": 0 }`

---

## 5. P1 补充接口

### 5.1 Dashboard 维度拆分

模块：`dashboard`，角色：`cloud_viewer` / `cloud_ops` / `cloud_finance`。数据按可见数据源过滤。

| 路径 | 入参（Query） | 出参摘要 |
|------|----------------|----------|
| `GET /api/dashboard/trend` | `start`、`end`：`YYYY-MM`；`granularity` | `[{ "date", "cost", "cost_by_provider": {} }]` |
| `GET /api/dashboard/by-provider` | `month` | `[{ "provider", "cost", "percentage" }]` |
| `GET /api/dashboard/by-category` | `month` | `[{ "category_id", "name", "original_cost", "markup_rate", "final_cost" }]` |
| `GET /api/dashboard/by-project` | `month`，`limit` 1–100 | `[{ "project_id", "name", "provider", "cost" }]` |
| `GET /api/dashboard/by-service` | `month`，`provider`，`limit` 1–100 | `[{ "product", "cost", "percentage" }]` |
| `GET /api/dashboard/by-region` | `month` | `[{ "region", "provider", "cost" }]` |
| `GET /api/dashboard/top-growth` | `period` 默认 `7d`，`limit` 1–50 | `[{ "project_id", "name", "current_cost", "previous_cost", "growth_pct" }]` |
| `GET /api/dashboard/unassigned` | `month` | `[{ "project_id", "name", "provider", "cost", "status" }]` |

---

### 5.2 `GET /api/service-accounts/`

**模块：`service_accounts`，角色：`cloud_admin`**。路径需带尾部斜杠。

**入参（Query）**：`provider`、`status`、`page`、`page_size`。

**出参**：

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

### 5.3 `GET /api/service-accounts/{account_id}`

**出参**（含历史，**仅字段名** `secret_fields`，无密钥内容）：

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

### 5.4 `GET /api/service-accounts/{account_id}/costs`

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
    { "date": "2026-04-01", "service": "EC2", "cost": 0, "usage_quantity": 0, "usage_unit": "Hrs" }
  ]
}
```

---

### 5.5 `GET /api/service-accounts/daily-report`

**入参（Query）**：`start_date`、`end_date`（必填），`provider`（可选）。

**出参**：

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

### 5.6 `GET /api/projects/` 与 `GET /api/projects/{project_id}`

**模块：`projects`，角色：任意登录**。

**列表入参**：`status`、`provider`、`page`、`page_size`。

**出参**：

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

### 5.7 `GET /api/bills/`

**模块：`bills`，角色：`cloud_finance`**。

**入参（Query）**：`month`（`YYYY-MM`）、`status`、`page`、`page_size`。

**出参**：

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

`status`：`draft` / `confirmed` / `paid`。

---

### 5.8 `GET /api/bills/{bill_id}`

单张月度账单详情（同上结构，单对象）。

---

### 5.9 `GET /api/alerts/rule-status`

**模块：`alerts`，角色：任意登录**。

**入参（Query）**：`month`（可选，`YYYY-MM`，默认当月）。

**出参**：

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

---

### 5.10 `GET /api/suppliers/supply-sources/all`

**模块：`suppliers`，角色：任意登录**。

**入参**：`supplier_id`（可选）。

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

### 5.11 `GET /api/metering/products`

**用途**：产品去重列表（下拉/消歧）。

**入参**：`provider`、`account_id`、`supply_source_id`、`supplier_name`、`data_source_id`。

**出参**：

```json
[{ "product": "Amazon EC2" }]
```

---

## 6. P2 可选接口

| 路径 | 用途 | 备注 |
|------|------|------|
| `GET /api/categories/` | 费用分类字典 | 含 `markup_rate` |
| `GET /api/exchange-rates/` | 汇率 | Query：`date`，`from_currency` |
| `GET /api/data-sources/` | 数据源列表 | 与同步渠道、分类关联 |
| `GET /api/resources/` | 资源清单分页 | `provider`，`project_id`，`resource_type`。数据按可见数据源过滤 |
| `GET /api/resources/{id}` | 单条资源 | 含 `monthly_cost` |
| `GET /api/billing/export` | CSV 流 | 大流量，适合工具落盘 |
| `GET /api/metering/export` | CSV 流 | 同上 |

---

## 7. 禁止对 AI 开放的接口

以下接口 **不应** 加入 AI 可调工具列表：

| 类别 | 路径 | 原因 |
|---|---|---|
| 凭据明文 | `GET /api/service-accounts/{id}/credentials` | 会解密云账号凭据 |
| 写操作 | 所有 `POST` / `PUT` / `PATCH` / `DELETE` | 含同步触发、账单调整、告警规则变更、服务账号变更等 |
| 同步触发 | `/api/sync/*`（除 `GET /last`） | 需 `cloud_ops`，触发后台任务 |
| Azure 部署 | `/api/azure-deploy/*` | 需 `cloud_admin`，涉及 ARM Token 与资源创建 |
| 跨租户授权 | `/api/azure-consent/*` | 需 `cloud_admin`，改订阅授权态 |
| 认证管理 | `/api/admin/users/*` · `/api/api-keys/*` · `/api/api-permissions/*` | 需 `cloud_admin`，管理员专用 |

`service_accounts` 模块混合了只读和凭据接口，如 AI 需要 §5.2–5.5 的数据，**工具清单不得包含 `/credentials`**。

---

## 8. 建议调用顺序

1. `GET /api/health` → 连通性探测
2. `GET /api/auth/me` → 确认身份与可见范围
3. `GET /api/sync/last` → 数据新鲜度
4. `GET /api/dashboard/bundle` 或 `metering/summary` → 总览
5. 钻取 → `metering/detail` + `detail/count` 分页，或 `billing/detail`
6. 业务主体 → `service-accounts/` 或 `projects/`
7. 是否超支 → `alerts/rule-status`

---

## 9. 各模块权限速查

| 模块 | URL 前缀 | 所需角色 | 数据范围过滤 |
|------|----------|----------|-------------|
| `dashboard` | `/api/dashboard/*` | viewer / ops / finance | 按可见数据源 |
| `billing` | `/api/billing/*` | 任意登录 | 按可见数据源 |
| `metering` | `/api/metering/*` | 任意登录 | 支持筛选参数 |
| `bills` | `/api/bills/*` | `cloud_finance` | 无 |
| `sync` | `/api/sync/*` | `cloud_ops` | 无 |
| `cloud_accounts` | `/api/cloud-accounts/*` | 读=任意；写=`cloud_admin` | 按可见云账号 |
| `resources` | `/api/resources/*` | 任意登录 | 按可见数据源 |
| `projects` | `/api/projects/*` | 任意登录 | 无 |
| `alerts` | `/api/alerts/*` | 任意登录 | 无 |
| `categories` | `/api/categories/*` | 任意登录 | 无 |
| `suppliers` | `/api/suppliers/*` | 任意登录 | 无 |
| `exchange_rates` | `/api/exchange-rates/*` | 任意登录 | 无 |
| `data_sources` | `/api/data-sources/*` | 任意登录 | 无 |
| `service_accounts` | `/api/service-accounts/*` | `cloud_admin` | 无 |
| `azure_deploy` | `/api/azure-deploy/*` | `cloud_admin` | 无 |
| `azure_consent` | `/api/azure-consent/*` | `cloud_admin` | 无 |

**匿名可访问**（不需要任何凭据）：`/api/health`、`/api/auth/*`、`/docs`、`/redoc`、`/openapi.json`

---

## 10. OpenAPI

运行时通过 `GET /openapi.json` 或 `/docs` 获取与部署版本一致的 Schema（匿名可访问）。若本文与 OpenAPI 冲突，**以 OpenAPI 为准**。
