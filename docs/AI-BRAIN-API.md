# AI 大脑 · 对内程序调用接口说明

本文档供 **内部 AI 大脑 / 自动化程序** 在回答 **费用、用量、账单、资源归属** 等问题时调用 CloudCost 后端使用。

- **范围**：以 **只读（GET）** 为主。
- **响应 JSON 约定**：
  - `date` → `YYYY-MM-DD` 字符串
  - `datetime` → 本地时间 ISO8601（**无时区后缀**，例：`2026-04-18T18:01:19.142386`）
  - 金额 / 用量（**重要**）：
    - 聚合型 / 仪表盘类接口（`/api/dashboard/*`、`/api/service-accounts/{id}/costs`、`/api/service-accounts/daily-report`、`/api/dashboard/overview`）→ **JSON number**（float）
    - 原始明细类接口（`/api/metering/*`、`/api/billing/detail`）→ **JSON string**（后端保留 Decimal 精度），需 `float(...)` / `parseFloat(...)` 解析
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

角色 → 前端页面映射

  ┌────────────────────────────────────────────┬─────────────┬───────────────┬───────────────┬───────────────┐
  │                  前端页面                  │ cloud_admin │   cloud_ops   │ cloud_finance │ cloud_viewer  │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 仪表盘 / (Dashboard)                       │     ✅      │      ✅       │      ✅       │      ✅       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 统计 /daily-report (日报/账单明细)         │     ✅      │      ✅       │      ✅       │      ✅       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 计量 /metering                             │     ✅      │      ✅       │      ✅       │      ✅       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 告警 /alerts                               │     ✅      │      ✅       │      ✅       │      ✅       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 货源管理 /accounts (云账号管理)            │ ✅ 可增删改 │ ❌ 只能看列表 │ ❌ 只能看列表 │ ❌ 只能看列表 │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 供应商管理 /suppliers                      │ ✅ 可增删改 │      ❌       │      ❌       │      ❌       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 模型管理 /azure-deploy                     │     ✅      │      ❌       │      ❌       │      ❌       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ Azure 接入 /azure-onboard                  │     ✅      │      ❌       │      ❌       │      ❌       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ 项目详情 /projects/[id]                    │  ✅ 可管理  │    ✅ 只读    │    ✅ 只读    │    ✅ 只读    │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ Header 中的 同步按钮                       │     ✅      │      ✅       │      ❌       │      ❌       │
  ├────────────────────────────────────────────┼─────────────┼───────────────┼───────────────┼───────────────┤
  │ Header 中的 账单管理（生成/调整/确认账单） │     ✅      │      ❌       │      ✅       │      ❌       │
  └────────────────────────────────────────────┴─────────────┴───────────────┴───────────────┴───────────────┘

  ---
  各角色详细说明

  cloud_admin — 超级管理员

  能用所有页面的全部功能，额外还有：
  - 用户管理、权限分配（/api/admin/users/*）
  - 云账号增删改（/api/cloud-accounts/ POST/PUT/DELETE）
  - 模块开关（/api/api-permissions/）
  - API Key 管理
  - Azure 部署和 OAuth 授权
  - 数据范围：看到全量数据，不受 cloud_account_grant 限制

  cloud_ops — 运维

  - 仪表盘：✅ 全部看板数据
  - 统计/日报：✅ 账单明细查看和导出
  - 同步操作：✅ 触发数据同步、查看同步日志（/api/sync/*）
  - 告警：✅ 查看
  - 计量：✅ 查看
  - ❌ 不能管理云账号/供应商/Azure 部署/账单
  - 数据范围：仅限被授权的云账号（通过 UserCloudAccountGrant）

  cloud_finance — 财务

  - 仪表盘：✅ 全部看板数据
  - 统计/日报：✅ 账单明细查看和导出
  - 账单管理：✅ 生成、调整、确认、标记已付（/api/bills/*）
  - 告警：✅ 查看
  - 计量：✅ 查看
  - ❌ 不能触发同步、不能管理云账号/供应商/Azure
  - 数据范围：仅限被授权的云账号

  cloud_viewer — 只读查看者

  - 仪表盘：✅ 只读
  - 统计/日报：✅ 只读 + 导出
  - 告警：✅ 只读
  - 计量：✅ 只读
  - ❌ 其他所有管理操作都不可用
  - 数据范围：仅限被授权的云账号

  ---

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

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | 总是 `"ok"`；仅此一字段。失败时会被 FastAPI 500 覆盖，不返回此对象 |

---

### 4.2 `GET /api/sync/last`

**用途**：回答「数据同步到什么时候」。模块：`sync`，角色：`cloud_ops`（含 admin）。

**出参**（真实样例）：

```json
{ "last_sync": "2026-04-18T18:01:19.142386" }
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `last_sync` | string \| null | 最近一次 **成功** 同步结束时间 ISO8601（本地时间，无时区）。从未同步过时为 `null` |

---

### 4.3 `GET /api/dashboard/bundle`

**用途**：**单次请求**拿首页级总览。模块：`dashboard`，角色：`cloud_viewer` / `cloud_ops` / `cloud_finance`（任一即可，`cloud_admin` 自动通过）。

**入参（Query）**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `month` | string | 是 | `YYYY-MM`，统计月 |
| `granularity` | string | 否 | `daily` \| `weekly` \| `monthly`，默认 `daily` |
| `service_limit` | int | 否 | 1–100，默认 10 |

**出参**（真实样例，截断）：

```json
{
  "overview": {
    "total_cost": 149785.098288,
    "prev_month_cost": 938396.218412,
    "mom_change_pct": -84.04,
    "active_projects": 7
  },
  "trend": [
    { "date": "2026-04-01", "cost": 7399.860517,
      "cost_by_provider": { "aws": 466.038035, "azure": 962.156064, "gcp": 5971.666418 } }
  ],
  "by_provider": [
    { "provider": "gcp",   "cost": 124735.119318, "percentage": 83.28 },
    { "provider": "azure", "cost": 18058.538325,  "percentage": 12.06 }
  ],
  "by_service": [
    { "product": "Vertex AI",        "cost": 124410.727567, "percentage": 83.06 },
    { "product": "Virtual Machines", "cost": 6202.288351,   "percentage": 4.14 }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `overview.total_cost` | number | 当月总费用（USD，对当前身份可见范围求和） |
| `overview.prev_month_cost` | number | 上月同口径总费用 |
| `overview.mom_change_pct` | number | 环比变化 %，保留两位小数，可能负值 |
| `overview.active_projects` | integer | `status=active` 的服务账号数 |
| `trend[].date` | string | `YYYY-MM-DD` |
| `trend[].cost` | number | 当日全 provider 合计费用 |
| `trend[].cost_by_provider` | object | 形如 `{ "aws":…, "gcp":…, "azure":… }`；只包含当日有数据的 provider |
| `by_provider[]` | array | 按 provider 当月累计，`cost` + `percentage`（占 total_cost 百分比，保留两位小数）|
| `by_service[]` | array | Top N 服务（`product` 原样字符串），N 由 `service_limit` 控制 |

---

### 4.4 `GET /api/dashboard/overview`

**用途**：只要月度总览卡片数据。

**入参（Query）**：`month`（必填，`YYYY-MM`）。

**出参**（真实样例）：

```json
{
  "total_cost": 149785.098288,
  "prev_month_cost": 938396.218412,
  "mom_change_pct": -84.04,
  "active_projects": 7
}
```

字段同 §4.3 `overview`。

---

### 4.5 `GET /api/metering/summary`

**用途**：按条件汇总用量/费用。模块：`metering`，角色：任意登录即可。

**入参（Query）**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date_start` | string | 否 | `YYYY-MM-DD` |
| `date_end` | string | 否 | `YYYY-MM-DD` |
| `provider` | string | 否 | `aws` / `gcp` / `azure` / `taiji` |
| `product` | string | 否 | 产品/服务/模型名（单值，保留兼容）|
| `products` | list[string] | 否 | **v1.1 新增**；多个服务名，重复 query：`products=gpt-4o&products=claude`。非空时覆盖 `product` |
| `account_id` | int | 否 | 服务账号 ID（单值，保留兼容）|
| `account_ids` | list[int] | 否 | **v1.1 新增**；多个服务账号 ID，重复 query：`account_ids=1&account_ids=2`。非空时覆盖 `account_id` |
| `supply_source_id` | int | 否 | 货源 ID |
| `supplier_name` | string | 否 | 供应商名称 |
| `data_source_id` | int | 否 | 数据源 ID |

**作用域优先级**：`account_ids > account_id > supply_source_id > supplier_name`（满足前面的就不再叠加后面的）。

**出参**（真实样例，注意 `total_cost` / `total_usage` 是**字符串**）：

```json
{
  "total_cost": "149785.098288",
  "total_usage": "55555350118.525461",
  "record_count": 62016,
  "service_count": 43
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_cost` | **string**（Decimal）| 筛选范围内的费用合计，USD。调用方自行 `float(...)` |
| `total_usage` | **string**（Decimal）| 用量合计。注意不同 `product` 的 `usage_unit` 不同，直接求和意义有限 |
| `record_count` | integer | 命中的 billing_data 行数 |
| `service_count` | integer | 不同 `product` 去重数 |

---

### 4.6 `GET /api/metering/daily`

**用途**：按日聚合费用与用量。入参同 `metering/summary`。

**出参**（真实样例）：

```json
[
  { "date": "2026-04-01", "usage_quantity": "2327753993.353517",
    "cost": "7399.860517", "record_count": 2745 },
  { "date": "2026-04-02", "usage_quantity": "1300687882.204744",
    "cost": "6175.577245", "record_count": 2974 }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | string | `YYYY-MM-DD` |
| `cost` | **string**（Decimal）| 当日费用合计 |
| `usage_quantity` | **string**（Decimal）| 当日用量合计（跨 product；单位不定）|
| `record_count` | integer | 当日命中的明细行数 |

---

### 4.7 `GET /api/metering/by-service`

**用途**：按服务聚合（Top N 分析）。入参同 summary（无 `product` 过滤）。

**出参**（真实样例）：

```json
[
  { "product": "Vertex AI", "usage_quantity": "55518458336.183727",
    "usage_unit": "month", "cost": "124410.727567", "record_count": 58924 },
  { "product": "Cloud Text-to-Speech API", "usage_quantity": "18315835.000000",
    "usage_unit": "count", "cost": "315.170942", "record_count": 101 }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `product` | string | 原样服务名（含空格大小写；同名但不同 `usage_unit` 会分别出现，后端按 `(product, unit)` 分组再在此合并，见 `usage_unit`）|
| `cost` | **string**（Decimal）| 该服务筛选范围内的费用合计 |
| `usage_quantity` | **string**（Decimal）| 用量合计；仅在**该服务使用单一 `usage_unit`** 时可直接阅读 |
| `usage_unit` | string \| null | 该服务的用量单位；如果同名 product 有多种单位，后端取任一 |
| `record_count` | integer | 行数 |

---

### 4.8 `GET /api/metering/detail`

**用途**：原始明细行分页。入参在 summary 基础上增加 `page`（默认 1）、`page_size`（默认 50，最大 500）。

**出参**（真实样例）：

```json
[
  {
    "id": 1819145,
    "date": "2026-04-18",
    "provider": "gcp",
    "data_source_id": 3,
    "project_id": "ysgemini-20260324",
    "product": "Cloud Text-to-Speech API",
    "usage_type": "Cloud TTS API text input token count for Gemini 2.5 Pro",
    "region": "asia-southeast1",
    "cost": "0.031815",
    "usage_quantity": "31815.000000",
    "usage_unit": "count",
    "currency": "USD"
  }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 明细主键（`billing_data.id`）|
| `date` | string | `YYYY-MM-DD` 账单日期 |
| `provider` | string | `aws` / `gcp` / `azure` |
| `data_source_id` | integer | 数据源 FK |
| `project_id` | string | **云厂商侧项目 ID**（GCP project / Azure subscription / AWS account），不是本地 `projects.id` |
| `product` | string \| null | 服务名 |
| `usage_type` | string \| null | 计费类型 / SKU 名 |
| `region` | string \| null | 区域 |
| `cost` | **string**（Decimal）| 该条费用 USD |
| `usage_quantity` | **string**（Decimal）| 用量 |
| `usage_unit` | string \| null | 用量单位 |
| `currency` | string | 总是 `"USD"` |

---

### 4.9 `GET /api/metering/detail/count`

**用途**：与 `detail` 同筛选条件下的总条数。

**出参**：`{ "total": 62016 }`

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | integer | 行数；分页计算用 |

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

**出参**（真实样例）：

```json
[
  {
    "id": 1759843,
    "date": "2026-04-18",
    "provider": "azure",
    "data_source_id": 2,
    "project_id": "45d7a360-af09-40fc-9afc-56dc475245ec",
    "project_name": "Xmind运营学习专用2026",
    "product": "API Management",
    "usage_type": "Consumption Calls",
    "region": "southeastasia",
    "cost": "0.000000",
    "usage_quantity": "0.083300",
    "usage_unit": "10K",
    "currency": "USD"
  }
]
```

字段大致同 `/metering/detail`，额外字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `project_name` | string \| null | 云厂商侧返回的项目显示名（AWS 未必有；GCP/Azure 多数有）|

`cost` / `usage_quantity` 仍为 **string (Decimal)**。

---

### 4.11 `GET /api/billing/detail/count`

**用途**：与 `billing/detail` 相同筛选下的总行数。

**出参**：`{ "total": 62016 }`

---

## 5. P1 补充接口

### 5.1 Dashboard 维度拆分

模块：`dashboard`，角色：`cloud_viewer` / `cloud_ops` / `cloud_finance`（含 admin）。数据按可见数据源过滤。**所有 cost 字段均为 JSON number（float）**。

#### `GET /api/dashboard/trend`

入参：`start`、`end`（均 `YYYY-MM`）；`granularity`（`daily` 默认 / `weekly` / `monthly`）。

出参：`[{ date, cost, cost_by_provider }]`，结构同 §4.3 `trend[]`。

#### `GET /api/dashboard/by-provider`

入参：`month`。出参真实样例：

```json
[
  { "provider": "gcp",   "cost": 124735.119318, "percentage": 83.28 },
  { "provider": "azure", "cost": 18058.538325,  "percentage": 12.06 }
]
```

| 字段 | 说明 |
|---|---|
| `provider` | `aws`/`gcp`/`azure` |
| `cost` | 当月累计 USD |
| `percentage` | 占当月总费用百分比（两位小数）|

#### `GET /api/dashboard/by-category`

入参：`month`。出参真实样例：

```json
[{ "category_id": 2, "name": "default", "original_cost": 24846.617897,
   "markup_rate": 1.15, "final_cost": 28573.61058155 }]
```

| 字段 | 说明 |
|---|---|
| `category_id`、`name` | 费用分类 |
| `original_cost` | 原始成本（未加价）|
| `markup_rate` | 加价率（1.0 = 不加价）|
| `final_cost` | = `original_cost × markup_rate` |

#### `GET /api/dashboard/by-project`

入参：`month`，`limit`（1–100，默认 10）。出参真实样例：

```json
[
  { "project_id": "xianlong-2", "name": "xianlong-2",
    "provider": "gcp", "cost": 58749.779907 }
]
```

| 字段 | 说明 |
|---|---|
| `project_id` | **云厂商侧项目 ID（字符串）**，即 GCP project / Azure subscription / AWS account。**不是**本地 `projects.id` |
| `name` | 账单中的项目名（云厂商返回）|
| `provider` | `aws`/`gcp`/`azure` |
| `cost` | 当月累计 USD |

#### `GET /api/dashboard/by-service`

入参：`month`，`provider`（可选），`limit`（1–100）。出参真实样例：

```json
[
  { "product": "Vertex AI", "cost": 124410.727567, "percentage": 83.06 },
  { "product": "Virtual Machines", "cost": 6202.288351, "percentage": 4.14 }
]
```

字段同 `dashboard/by-provider`（`percentage` 相对当月 total）。

#### `GET /api/dashboard/by-region`

入参：`month`。出参真实样例：

```json
[
  { "region": "asia-east1",      "provider": "gcp", "cost": 38836.928387 },
  { "region": "asia-southeast1", "provider": "gcp", "cost": 9817.383452 }
]
```

#### `GET /api/dashboard/top-growth`

入参：`period`（默认 `7d`），`limit`（1–50）。出参真实样例：

```json
[
  { "project_id": "lyww-01", "name": "lyww-01",
    "current_cost": 3775.070699, "previous_cost": 5e-05,
    "growth_pct": 7550141298.0 }
]
```

| 字段 | 说明 |
|---|---|
| `project_id` | 外部项目 ID 字符串 |
| `current_cost` / `previous_cost` | 本周期 / 对比周期累计 USD |
| `growth_pct` | 增幅 %，`previous` 接近 0 时可能非常大 |

#### `GET /api/dashboard/unassigned`

入参：`month`。出参真实样例（未与本地 `projects` 表关联上的外部项目）：

```json
[
  { "project_id": "xianlong-2", "name": "xianlong-2",
    "provider": "gcp", "cost": 58749.779907, "status": null }
]
```

| 字段 | 说明 |
|---|---|
| `project_id` / `name` / `provider` | 同 by-project |
| `cost` | 当月累计 USD |
| `status` | 总是 `null`（`projects` 表里没有对应行），保留字段以便未来扩展 |

---

### 5.2 `GET /api/service-accounts/`

**模块：`service_accounts`，角色：任意登录**（列表查看所有角色可用）。路径需带尾部斜杠。

**入参（Query）**：`provider`、`status`、`customer_code`（按客户编号反查）、`page`、`page_size`。

**出参**（真实样例）：

```json
[
  {
    "id": 7,
    "name": "AWS-Main",
    "supply_source_id": 2,
    "supplier_name": "神州泰岳",
    "provider": "aws",
    "external_project_id": "675139393309",
    "status": "standby",
    "order_method": null,
    "customer_codes": [],
    "created_at": "2026-04-08T10:19:32.565267"
  }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 本地服务账号主键 |
| `name` | string | 账号显示名 |
| `supply_source_id` / `supplier_name` | integer / string | 所属货源 + 供应商名 |
| `provider` | string | `aws`/`gcp`/`azure`（来自 supply_source）|
| `external_project_id` | string | 云厂商侧 ID（AWS account ID / GCP project / Azure subscription）|
| `status` | string | `active` / `standby` / `inactive` |
| `order_method` | string \| null | 仅 Azure 账号有意义（MCCL-EA / HK CSP 等下单方式）|
| `customer_codes` | string[] | 销售系统下发的客户编号，大写归一化；空数组 = 未分配 |
| `created_at` | string | 创建时间 ISO8601 |

**`status` 派生规则**（后端 `_recompute_status`）：

- `inactive`（人工停用）→ 不动，最高优先级
- 否则：`customer_codes` 非空 → `active`；为空 → `standby`
- 触发时机：`PUT /{id}` 带 `customer_codes`、`POST /{id}/activate`、`POST /customer-assignments/sync` 后

---

### 5.3 `GET /api/service-accounts/{account_id}`

**出参**（真实样例，截取）：

```json
{
  "id": 7,
  "name": "AWS-Main",
  "supply_source_id": 2,
  "supplier_id": 3,
  "supplier_name": "神州泰岳",
  "provider": "aws",
  "external_project_id": "675139393309",
  "status": "standby",
  "notes": null,
  "order_method": null,
  "customer_codes": [],
  "secret_fields": ["aws_access_key_id", "aws_secret_access_key", "account_id"],
  "created_at": "2026-04-08T10:19:32.565267",
  "history": [
    { "id": 39, "action": "activated", "from_status": "standby", "to_status": "standby",
      "operator": "xiaohei", "customer_code": null, "notes": null,
      "created_at": "2026-04-18T19:21:38.081899" },
    { "id": 36, "action": "customer_unbound", "from_status": "active", "to_status": "active",
      "operator": "sales", "customer_code": "SYNC_C1", "notes": "sales batch sync",
      "created_at": "2026-04-18T19:10:00.000000" }
  ]
}
```

列表字段同 §5.2。详情额外字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `supplier_id` | integer | 供应商 FK |
| `notes` | string \| null | 账号备注 |
| `secret_fields` | string[] | 账号凭据字段**名**（不含值）；例如 aws_access_key_id、client_secret |
| `history[]` | array | 状态变更 + 客户编号变更历史，按 `created_at DESC` |
| `history[].action` | string | 见下表 |
| `history[].from_status` / `to_status` | string \| null | 仅状态类事件非空 |
| `history[].operator` | string \| null | 触发者用户名；销售系统同步走 `"sales-sync"`/机器账号名 |
| `history[].customer_code` | string \| null | 仅 `customer_*` 事件非空 |
| `history[].notes` | string \| null | 销售批量同步会写 `"sales batch sync"` |

`action` 枚举：

| 值 | 含义 |
|---|---|
| `created` | 创建账号（注：老数据可能记为 `assigned`）|
| `suspended` | 人工停用 |
| `activated` | 从停用/备用恢复 |
| `customer_bound` | 绑定一个客户编号 |
| `customer_unbound` | 解除一个客户编号 |
| `customer_batch_synced` | 销售批量同步（此事件单独记录时使用；通常逐个写 bound/unbound）|

---

### 5.4 `GET /api/service-accounts/{account_id}/costs`

**角色**：任意登录（已放宽）。

**入参（Query）**：`start_date`、`end_date`（必填，`YYYY-MM-DD`）。

**出参**（真实样例，截取）：

```json
{
  "total_cost": 6991.440645000001,
  "total_usage": 5303.442484,
  "services": [
    { "service": "Claude Opus 4.6 (Amazon Bedrock Edition)",
      "cost": 5280.532382, "usage_quantity": 2690.11845, "usage_unit": "Units" },
    { "service": "Claude Sonnet 4.6 (Amazon Bedrock Edition)",
      "cost": 1246.2185079999997, "usage_quantity": 1582.6575039999998, "usage_unit": "Units" }
  ],
  "daily": [
    { "date": "2026-04-01", "cost": 45.23, "usage_quantity": 12.0 }
  ],
  "daily_by_service": [
    { "date": "2026-04-01", "service": "AWS Cost Explorer",
      "cost": 0.01, "usage_quantity": 1.0, "usage_unit": "Requests" }
  ]
}
```

**本接口的 cost/usage 是 JSON number（float）**（与 `/metering/*` 不同）。

| 字段 | 说明 |
|---|---|
| `total_cost` / `total_usage` | 本账号在 date 区间内的汇总 |
| `services[].service` | 产品名（对应 billing_data.product）|
| `services[].usage_unit` | 该产品的用量单位；该产品有多个单位时，后端取其一 |
| `daily[]` | 按日费用+用量合计 |
| `daily_by_service[]` | 按日 × 服务拆分（用于堆叠图）|

---

### 5.5 `GET /api/service-accounts/daily-report`

**角色：任意登录**（前端『统计』页调用，viewer/ops/finance 都可访问）。

**入参（Query）**：`start_date`、`end_date`（必填），`provider`（可选）。

**出参**（真实样例）：

```json
[
  {
    "account_id": 88,
    "account_name": "chuhai 自用",
    "provider": "azure",
    "external_project_id": "09e4b3a6-8159-4f14-b108-e4a18ace9212",
    "date": "2026-04-01",
    "product": "Foundry Models",
    "cost": 2.438673
  }
]
```

每行 = (账号, 日期, 产品) 聚合后的 cost，**JSON number（float）**。按 `date`→`external_project_id`→`product` 排序。

| 字段 | 说明 |
|---|---|
| `account_id` | 本地 `projects.id` |
| `account_name` | 账号显示名（本地 `projects.name`）|
| `provider` | `aws`/`gcp`/`azure` |
| `external_project_id` | 云厂商侧 ID |
| `date` / `product` | 账单日期 + 服务 |
| `cost` | USD |

> 注：此接口的"每条记录按 (账号, 日期, 产品) 聚合"；业务含义是前端『统计』页。物理挂在 `service_accounts` 路由下，但已做端点级权限区分——只读端点（列表 / 详情 / costs / daily-report）任意登录可用，敏感端点（创建/删除/暂停/恢复/凭据/批量同步）需 `cloud_admin`。

---

### 5.6 `GET /api/projects/` 与 `GET /api/projects/{project_id}`

**模块：`projects`，角色：任意登录（读）；写需 `cloud_admin`**。

`/api/projects` 是老的 Project CRUD，**前端不使用**。和 `/api/service-accounts` 指向同一个 `projects` 表，但返回字段和交互语义略有差异。AI 通常调 `service-accounts/` 即可；这组接口仅供历史集成。

**列表入参**：`status`、`provider`、`page`、`page_size`。

**出参**（真实样例，单对象）：

```json
{
  "id": 7,
  "name": "AWS-Main",
  "supply_source_id": 2,
  "provider": "aws",
  "supplier_name": "神州泰岳",
  "external_project_id": "675139393309",
  "data_source_id": 1,
  "category_id": 2,
  "status": "standby",
  "notes": null,
  "created_at": "2026-04-08T10:19:32.565267",
  "updated_at": "2026-04-18T18:53:30.265359"
}
```

| 字段 | 说明 |
|---|---|
| `data_source_id` | FK → data_sources（对应 /api/data-sources/ 条目）|
| `category_id` | FK → categories（费用分类 markup_rate）；可为 null |
| `updated_at` | 最近一次 update 时间 |
| 其它字段 | 同 §5.2 |

> 注意：本接口**不返回** `customer_codes`、`order_method`、`history`。需要这些用 `/api/service-accounts/{id}`。

---

### 5.7 `GET /api/bills/`

**模块：`bills`，角色：`cloud_finance`（含 admin）**。

**入参（Query）**：`month`（`YYYY-MM`）、`status`、`page`、`page_size`。

**出参**：实际调用该账号当月为空 `[]`（尚未生成账单）。字段表（来自后端 schema）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 账单 id |
| `month` | string | `YYYY-MM` |
| `category_id` | integer | 费用分类 FK |
| `provider` | string | aws/gcp/azure |
| `original_cost` | string/number | 原始成本 USD |
| `markup_rate` | string/number | 加价率（1.0=不加价）|
| `final_cost` | string/number | 加价后金额 |
| `adjustment` | string/number | 人工调整额（正负皆可）|
| `status` | string | `draft` / `confirmed` / `paid` |
| `confirmed_at` | string \| null | 确认时间 |
| `notes` | string \| null | 备注 |
| `created_at` | string | 创建时间 |

---

### 5.8 `GET /api/bills/{bill_id}`

单张月度账单详情（同上结构，单对象）。

---

### 5.9 `GET /api/alerts/rule-status`

**模块：`alerts`，角色：任意登录**。

**入参（Query）**：`month`（可选，`YYYY-MM`，默认当月）。

**出参**：空数组 `[]`（该环境当前未配置告警规则）。字段表：

| 字段 | 类型 | 说明 |
|---|---|---|
| `rule_id` | integer | 规则 ID |
| `rule_name` | string | 规则显示名 |
| `threshold_type` | string | `monthly_budget` / `daily_budget` / `growth_rate` / `monthly_minimum_commitment` 等 |
| `threshold_value` | number | 阈值 |
| `actual` | number | 当前实际值 |
| `pct` | number | `actual / threshold_value × 100`（增长率类型为差额 %）|
| `triggered` | boolean | 是否触发 |
| `account_name` | string | 关联的服务账号名（可能为 null，表示全局规则）|
| `provider` | string | aws/gcp/azure |
| `external_project_id` | string | 云厂商侧 ID |

---

### 5.10 `GET /api/suppliers/supply-sources/all`

**模块：`suppliers`，角色：`cloud_admin`**（整个 suppliers 模块都是 admin）。

**入参**：`supplier_id`（可选，过滤某个供应商）。

**出参**（真实样例）：

```json
[
  { "id": 3, "supplier_id": 1, "supplier_name": "长虹佳华",
    "provider": "azure", "account_count": 3 },
  { "id": 5, "supplier_id": 2, "supplier_name": "内部测试专用",
    "provider": "gcp", "account_count": 1 }
]
```

| 字段 | 说明 |
|---|---|
| `id` | 货源 ID（`supply_sources.id`）|
| `supplier_id` / `supplier_name` | 所属供应商 |
| `provider` | 该货源对应的云 |
| `account_count` | 该货源下的服务账号数 |

---

### 5.11 `GET /api/metering/products`

**用途**：产品去重列表（下拉/消歧）。

**入参**：`provider`、`account_id`、`supply_source_id`、`supplier_name`、`data_source_id`。

**出参**（真实样例，截取）：

```json
[
  { "product": "Amazon Simple Notification Service" },
  { "product": "Amazon Simple Queue Service" },
  { "product": "Vertex AI" }
]
```

单字段 `product`，排重 + 排序。用于前端 Metering 页的服务过滤下拉。

---

## 6. P2 可选接口

### 6.1 `GET /api/categories/`

**角色：`cloud_admin`**（整个 categories 模块 admin-only）。

**出参**（真实样例）：

```json
[
  { "id": 1, "name": "????", "markup_rate": "1.0000",
    "description": "test",
    "created_at": "2026-04-08T03:43:02.546432",
    "updated_at": "2026-04-08T03:43:02.546432" },
  { "id": 2, "name": "default", "markup_rate": "1.1500",
    "description": "default channel",
    "created_at": "2026-04-08T03:43:28.568331",
    "updated_at": "2026-04-08T10:23:02.934844" }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `markup_rate` | **string**（Decimal，4 位小数）| 加价率 |
| `name` | string | 分类名；历史数据可能含占位符 |

### 6.2 `GET /api/exchange-rates/`

**角色：任意登录**（读）。入参：`date`（可选）、`from_currency`。

**出参**：数组形如 `[{ from_currency, to_currency, rate, date }]`；该环境返回 `[]`。

### 6.3 `GET /api/data-sources/`

**角色：`cloud_admin`**。

**出参**（真实样例）：

```json
[
  {
    "id": 1,
    "name": "AWS-675139393309",
    "cloud_account_id": 1,
    "category_id": 2,
    "config": { "account_id": "675139393309" },
    "last_sync_at": "2026-04-18T18:00:19.046951",
    "sync_status": "success",
    "is_active": true,
    "created_at": "2026-04-08T03:48:09.812203"
  }
]
```

| 字段 | 说明 |
|---|---|
| `config` | 采集器配置 JSONB；Azure 会有 `subscription_id`/`collect_mode`/`cost_metric`；AWS 仅 `account_id` |
| `last_sync_at` / `sync_status` | 最近一次同步时间与结果 |

### 6.4 `GET /api/resources/` + `GET /api/resources/{id}`

**角色：任意登录**（读）。入参：`provider`、`project_id`、`resource_type`、`page`、`page_size`。数据按可见数据源过滤。该环境返回 `[]`。字段表（资源清单 schema）：`id`、`provider`、`resource_type`、`resource_id`、`name`、`region`、`project_id`、`monthly_cost`（number，最近一个月估算）、`tags`。

### 6.5 `GET /api/billing/export` / `/api/metering/export`

CSV 流式下载，大流量，适合工具落盘，不建议作为模型上下文。入参同 `detail`，响应 `Content-Type: text/csv`。

---

## 7. 销售系统专用接口（客户编号下发）

> 面向"销售系统对接"，**AI 大脑不调用**。模块：`service_accounts`；**这两条是写操作，需 `cloud_admin`**（`service_accounts` 只读端点已放宽到任意登录，但写仍然 admin-only）。建议通过 Casdoor `client_credentials` 拿 token。

### 7.1 `POST /api/service-accounts/customer-assignments/sync`

**用途**：销售系统把"客户编号 ↔ 服务账号"关联批量下发。幂等。

定位键：`(supplier_name, provider, external_project_id)`。匹配不到的记录进 `unmatched` 返回，不阻断整批。

**入参（Body）**：

```json
{
  "mode": "patch",
  "scope_customer_codes": ["C001", "C002"],
  "assignments": [
    {
      "customer_code": "C001",
      "supplier_name": "xxx 供应商",
      "provider": "azure",
      "external_project_id": "5550d5e0-56d3-46b2-8c08-bb9834d8b349"
    }
  ]
}
```

- `mode=patch`：只做 upsert，从不删除。推荐作为默认模式。
- `mode=full`：对 `scope_customer_codes` 这批做差分（多删少插）。若该字段留空，后端回退为"按 `assignments` 里出现的客户编号作为 scope"——这种隐式行为建议明确传一份。
- `customer_code` 自动 `upper().strip()` 归一化。

**出参**：

```json
{
  "inserted": 1,
  "deleted": 0,
  "unchanged": 0,
  "unmatched": [
    {
      "customer_code": "C002",
      "supplier_name": "bogus",
      "provider": "azure",
      "external_project_id": "does-not-exist",
      "reason": "service account not found"
    }
  ]
}
```

### 7.2 `PUT /api/service-accounts/{account_id}`（仅 `customer_codes` 字段）

**用途**：单个服务账号的客户编号**全量覆盖**（和 `secret_data` 同语义：`undefined` 不动；`[]` 清空；`[...]` 替换）。

**入参（Body）**：

```json
{ "customer_codes": ["C001"] }
```

**出参**：整个 `ServiceAccountDetail`（见 §5.3）。

副作用：
- 新增/删除会写 `project_assignment_logs`，action=`customer_bound` / `customer_unbound`，带 operator。
- 清空或首次绑定都会触发 `_recompute_status`，自动把 status 从 `active` → `standby` 或反向切换。

---

## 8. 禁止对 AI 开放的接口

以下接口 **不应** 加入 AI 可调工具列表：

| 类别 | 路径 | 原因 |
|---|---|---|
| 凭据明文 | `GET /api/service-accounts/{id}/credentials` | 会解密云账号凭据 |
| 写操作 | 所有 `POST` / `PUT` / `PATCH` / `DELETE` | 含同步触发、账单调整、告警规则变更、服务账号变更等 |
| 客户编号分配（销售专用） | `POST /api/service-accounts/customer-assignments/sync`、`PUT /api/service-accounts/{id}` 带 `customer_codes` | 仅销售系统走 Casdoor client_credentials 下发；AI 不应写 |
| 同步触发 | `/api/sync/*`（除 `GET /last`） | 需 `cloud_ops`，触发后台任务 |
| Azure 部署 | `/api/azure-deploy/*` | 需 `cloud_admin`，涉及 ARM Token 与资源创建 |
| 跨租户授权 | `/api/azure-consent/*` | 需 `cloud_admin`，改订阅授权态 |
| 认证管理 | `/api/admin/users/*` · `/api/api-keys/*` · `/api/api-permissions/*` | 需 `cloud_admin`，管理员专用 |
| Taiji 数据回灌 | `POST /api/metering/taiji/ingest` | v1.1 新增；Taiji 平台专用写入通道，严格 `X-API-Key` 鉴权 + 绑定到 taiji CloudAccount，不对 AI 开放 |

`service_accounts` 模块混合了只读和凭据接口，如 AI 需要 §5.2–5.5 的数据，**工具清单不得包含 `/credentials`**。

---

## 8.5 Taiji 平台 Push 入口（对外独立契约）

`POST /api/metering/taiji/ingest` 供 Taiji AI 聚合平台回灌请求级日志（不是给 AI 大脑的读接口），独立 `X-API-Key` 鉴权、独立契约文档：

📄 [taiji-ingest-api.md](./taiji-ingest-api.md)

要点：
- 批量上限 2000 条/次
- 以 `id` 为幂等主键，重推自动去重
- 处理后自动 upsert `billing_data` + `token_usage`，自动发现新 token 作为服务账号
- `provider=taiji` 出现在所有 metering 查询结果中，AI 大脑无需额外处理

---

## 9. 建议调用顺序

1. `GET /api/health` → 连通性探测
2. `GET /api/auth/me` → 确认身份与可见范围
3. `GET /api/sync/last` → 数据新鲜度
4. `GET /api/dashboard/bundle` 或 `metering/summary` → 总览
5. 钻取 → `metering/detail` + `detail/count` 分页，或 `billing/detail`
6. 业务主体 → `service-accounts/` 或 `projects/`
7. 是否超支 → `alerts/rule-status`

---

## 10. 各模块权限速查

| 模块 | URL 前缀 | 所需角色 | 数据范围过滤 |
|------|----------|----------|-------------|
| `dashboard` | `/api/dashboard/*` | viewer / ops / finance（含 admin） | 按可见数据源 |
| `billing` | `/api/billing/*` | 任意登录 | 按可见数据源 |
| `metering` | `/api/metering/*` | 任意登录 | 支持筛选参数 |
| `bills` | `/api/bills/*` | `cloud_finance`（含 admin） | 无 |
| `sync` | `/api/sync/*` | `cloud_ops`（含 admin） | 无 |
| `cloud_accounts` | `/api/cloud-accounts/*` | 读=任意登录；写=`cloud_admin` | 按可见云账号 |
| `resources` | `/api/resources/*` | 任意登录 | 按可见数据源 |
| `projects` | `/api/projects/*` | 读=任意登录；写=`cloud_admin` | 无 |
| `alerts` | `/api/alerts/*` | 读=任意登录；写告警规则=`cloud_ops` | 无 |
| `categories` | `/api/categories/*` | **`cloud_admin`**（整个模块）| 无 |
| `suppliers` | `/api/suppliers/*` | **`cloud_admin`**（整个模块）| 无 |
| `exchange_rates` | `/api/exchange-rates/*` | 读=任意登录；写=`cloud_admin` / `cloud_finance` | 无 |
| `data_sources` | `/api/data-sources/*` | **`cloud_admin`**（整个模块）| 无 |
| `service_accounts` | `/api/service-accounts/*` | 读=任意登录；写=`cloud_admin` | 无 |
| `azure_deploy` | `/api/azure-deploy/*` | `cloud_admin` | 无 |
| `azure_consent` | `/api/azure-consent/*` | `cloud_admin` | 无 |

**匿名可访问**（不需要任何凭据）：`/api/health`、`/api/auth/*`、`/docs`、`/redoc`、`/openapi.json`

---

## 11. OpenAPI

运行时通过 `GET /openapi.json` 或 `/docs` 获取与部署版本一致的 Schema（匿名可访问）。若本文与 OpenAPI 冲突，**以 OpenAPI 为准**。
