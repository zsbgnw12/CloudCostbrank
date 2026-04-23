# CloudCost 后端 HTTP API 说明

本文档根据 `app/main.py` 与各 `app/api/*.py` 路由整理，与运行中的 OpenAPI（`/docs`）一致时可互为补充。

**基础信息**

| 项 | 说明 |
|----|------|
| 框架 | FastAPI |
| 应用标题 / 版本 | 见配置 `APP_TITLE` / `APP_VERSION`（默认 CloudCost / 0.1.0） |
| 全局前缀 | 下表路径均相对于服务根 URL（如 `http://localhost:8000`） |
| CORS | `allow_origins=["*"]`，`allow_credentials=True` |
| 认证 | 除 **Azure 部署** 相关接口外，当前代码中**未**实现统一登录；Azure 相关接口使用请求头 `Authorization: Bearer <ARM Token>` |

**全局错误与状态码（节选）**

- `503`：数据库不可达等（`OperationalError`、`ConnectionResetError`），响应体含中文说明。
- `409`：唯一约束 / 外键冲突（`IntegrityError` 经处理器转换）。
- 各接口另有 `404` / `400` / `401` / `422` / `502` 等业务与校验错误。

---

## 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 返回 `{"status": "ok"}` |

---

## Dashboard（前缀 `/api/dashboard`）

| 方法 | 路径 | 查询参数 | 说明 |
|------|------|----------|------|
| GET | `/overview` | `month`（必填，`YYYY-MM`） | 概览 |
| GET | `/trend` | `start`、`end`（`YYYY-MM`），`granularity`：`daily` \| `weekly` \| `monthly`（默认 `daily`） | 趋势 |
| GET | `/by-provider` | `month` | 按云厂商 |
| GET | `/by-category` | `month` | 按分类 |
| GET | `/by-project` | `month`，`limit`（1–100，默认 20） | 按项目 |
| GET | `/by-service` | `month`，`provider`（可选），`limit`（1–100，默认 20） | 按服务 |
| GET | `/by-region` | `month` | 按区域 |
| GET | `/top-growth` | `period`（默认 `7d`），`limit`（1–50，默认 10） | 增长排行 |
| GET | `/unassigned` | `month` | 未分配项 |
| GET | `/bundle` | `month`，`granularity`，`service_limit`（1–100，默认 10） | 首页聚合（overview + trend + by_provider + by_service） |

---

## Categories（前缀 `/api/categories`）

| 方法 | 路径 | 请求体 / 说明 |
|------|------|----------------|
| GET | `/` | 列表，`CategoryRead[]` |
| POST | `/` | `CategoryCreate`：`name`，`markup_rate`（默认 1.0），`description` |
| GET | `/{category_id}` | 单条 |
| PUT | `/{category_id}` | `CategoryUpdate`；若修改 `markup_rate` 会记审计日志 |
| DELETE | `/{category_id}` | 若仍被数据源 / 项目 / 账单引用则 `400` |

---

## Cloud Accounts（前缀 `/api/cloud-accounts`）

| 方法 | 路径 | 请求体 / 说明 |
|------|------|----------------|
| GET | `/` | 列表（不含密钥） |
| POST | `/` | `CloudAccountCreate`：`name`，`provider`（`aws` / `gcp` / `azure`），`secret_data`（明文，服务端加密存储） |
| GET | `/{account_id}` | 单条 |
| PUT | `/{account_id}` | `CloudAccountUpdate`；可更新 `secret_data`（再次加密） |
| DELETE | `/{account_id}` | 若仍有数据源引用则 `400` |

---

## Data Sources（前缀 `/api/data-sources`）

| 方法 | 路径 | 请求体 / 说明 |
|------|------|----------------|
| GET | `/` | 列表 |
| POST | `/` | `DataSourceCreate`：`name`，`cloud_account_id`，`category_id`（可选），`config`（JSON） |
| GET | `/{ds_id}` | 单条 |
| PUT | `/{ds_id}` | `DataSourceUpdate` |
| DELETE | `/{ds_id}` | 若仍有计费行或项目引用则 `400` |

---

## Projects（前缀 `/api/projects`）

`ProjectRead` 含 `provider`、`supplier_name`（来自 `supply_sources` + `suppliers`）。

| 方法 | 路径 | 查询参数 / 请求体 |
|------|------|-------------------|
| GET | `/` | `status`，`provider`，`page`（≥1），`page_size`（1–500，默认 100） |
| POST | `/` | `ProjectCreate`：`name`，`supply_source_id`，`external_project_id`，`data_source_id`，`category_id`（后两者可选） |
| GET | `/{project_id}` | — |
| PUT | `/{project_id}` | `ProjectUpdate` |
| POST | `/{project_id}/activate` | 自 `inactive` / `standby` → `active` |
| POST | `/{project_id}/suspend` | 自 `active` / `standby` → `inactive` |
| GET | `/{project_id}/assignment-logs` | `ProjectAssignmentLogRead[]` |

---

## Billing（前缀 `/api/billing`）

| 方法 | 路径 | 查询参数 | 说明 |
|------|------|----------|------|
| GET | `/detail` | `date_start`、`date_end`（ISO 日期），`provider`，`project_id`，`product`，`page`，`page_size`（1–500，默认 50） | 分页明细，`BillingListRead[]` |
| GET | `/detail/count` | 同上（无分页） | `{"total": number}` |
| GET | `/export` | 同上 | 流式 CSV，`Content-Disposition: billing_export.csv` |

---

## Sync（前缀 `/api/sync`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/last` | 最近一次成功同步结束时间：`{"last_sync": string \| null}`；异常时仍返回 `last_sync: null` |
| POST | `/all` | Body：`SyncRequest`（`start_month` `YYYY-MM`，`end_month` 可选，`provider` 可选）→ Celery，`{"task_id", "status": "dispatched"}` |
| POST | `/refresh-summary` | Query：`start_date`、`end_date`（可选；省略则从 `billing_data` 全表 min/max 日期重建日汇总） |
| POST | `/{data_source_id}` | 同上 Body，单数据源同步任务 |
| GET | `/status/{task_id}` | Celery 任务状态：`task_id`，`status`，`result`（完成时） |
| GET | `/logs` | `data_source_id`，`status`，`limit`（1–200，默认 50）→ `SyncLogRead[]` |

---

## Resources（前缀 `/api/resources`）

| 方法 | 路径 | 查询参数 |
|------|------|----------|
| GET | `/` | `provider`，`project_id`，`resource_type`，`page`，`page_size`（1–500，默认 50） |
| GET | `/{resource_id}` | — |

---

## Alerts（前缀 `/api/alerts`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/rules/` | 告警规则列表 |
| POST | `/rules/` | Body：`AlertRuleCreate` |
| PUT | `/rules/{rule_id}` | Body：`AlertRuleUpdate` |
| DELETE | `/rules/{rule_id}` | 204 |
| GET | `/history` | `rule_id`，`limit`（1–200，默认 50） |
| GET | `/notifications` | `unread_only`，`limit`（1–100，默认 30） |
| GET | `/notifications/unread-count` | `{"count": number}` |
| POST | `/notifications/{notification_id}/read` | 204 |
| POST | `/notifications/read-all` | 204 |
| GET | `/rule-status` | `month`（`YYYY-MM`，默认当月）；返回各规则与项目实际用量对比状态（见实现内 `RuleStatus`） |

---

## Monthly Bills（前缀 `/api/bills`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | `month`，`status`，`page`，`page_size` |
| POST | `/generate` | Body：`MonthlyBillGenerate`（`month`）→ `{"generated", "month"}` |
| GET | `/{bill_id}` | 单条账单 |
| PUT | `/{bill_id}/adjust` | Body：`MonthlyBillAdjust`（`adjustment`，`notes`）；仅 `draft` 或 `confirmed` |
| POST | `/{bill_id}/confirm` | 仅 `draft` → `confirmed` |
| POST | `/{bill_id}/mark-paid` | 仅 `confirmed` → `paid` |
| DELETE | `/{bill_id}` | 仅可删 `draft` |
| POST | `/regenerate` | Body：`MonthlyBillGenerate`；删除该月所有 `draft` 后重新生成 |

---

## Exchange Rates（前缀 `/api/exchange-rates`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | `date`（ISO），`from_currency` |
| POST | `/` | `ExchangeRateCreate`：`date`，`from_currency`，`to_currency`，`rate` |
| PUT | `/{rate_id}` | `ExchangeRateUpdate`：仅 `rate` |

---

## Suppliers（前缀 `/api/suppliers`）

供应商与货源（`supply_sources`）；`provider` 为 `aws` / `gcp` / `azure`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 供应商列表 |
| POST | `/` | `{"name": string}` |
| PATCH | `/{supplier_id}` | `{"name"}`；系统保留供应商不可随意改名/删 |
| DELETE | `/{supplier_id}` | 仍有服务账号则 `409` |
| GET | `/{supplier_id}/supply-sources` | 该供应商下货源及 `account_count` |
| POST | `/{supplier_id}/supply-sources` | `{"provider": "aws"|"gcp"|"azure"}` |
| DELETE | `/supply-sources/{supply_source_id}` | 仍有项目则 `409` |
| GET | `/supply-sources/all` | Query：`supplier_id`（可选）；全部货源下拉用 |

---

## Service Accounts（前缀 `/api/service-accounts`）

统一视图：创建时联动 `CloudAccount` + `DataSource` + `Project`。静态路径优先于 `/{account_id}`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | `provider`，`status`，`page`，`page_size` |
| POST | `/` | `ServiceAccountCreate`：`supply_source_id`，`name`，`external_project_id`，`secret_data`（可选），`notes` |
| DELETE | `/hard/{account_id}` | 物理删除（含关联计费数据等），204 |
| GET | `/daily-report` | `start_date`、`end_date`（`YYYY-MM-DD`），`provider`（可选） |
| GET | `/daily-report/export` | 同上，返回 **xlsx** 流 |
| GET | `/{account_id}` | 详情含 `secret_fields`（密钥字段名，非值）、`history` |
| PUT | `/{account_id}` | `ServiceAccountUpdate` |
| POST | `/{account_id}/suspend` | 暂停 |
| POST | `/{account_id}/activate` | 激活 |
| DELETE | `/{account_id}` | 与 `hard` 相同硬删除 |
| GET | `/{account_id}/costs` | `start_date`、`end_date` → `CostSummary` |
| GET | `/{account_id}/costs/export` | 费用明细 **xlsx** |
| GET | `/{account_id}/credentials` | 解密后的凭据 JSON（敏感） |
| POST | `/discover-gcp-projects` | 为账单中存在但未建档的 GCP 项目创建 standby 项目 |

---

## Azure Deploy（前缀 `/api/azure-deploy`）

除 **`GET /auth/config`** 外，均需 **`Authorization: Bearer <ARM Token>`**（Azure Management 用户模拟 scope，见代码注释）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/auth/config` | MSAL 配置：`MsalConfig` |
| POST | `/auth/validate` | 校验 Token，返回 `AzureUserInfo` |
| GET | `/subscriptions` | 订阅列表 |
| GET | `/resource-groups` | Query：`subscription_id` |
| POST | `/resource-groups` | `CreateResourceGroupRequest` |
| POST | `/ai-resources` | `CreateAIResourceRequest` |
| GET | `/ai-resources` | Query：`subscription_id`，`resource_group` |
| GET | `/models` | Query：`subscription_id`，`region` |
| GET | `/account-models` | Query：`subscription_id`，`resource_group`，`account_name` |
| GET | `/existing-deployments` | Query：`subscription_id`，`resource_group`，`account_name` |
| POST | `/plan` | `PlanRequest` → `PlanResponse` |
| POST | `/execute` | `ExecuteRequest` → `ExecuteResponse`，后台执行任务 |
| GET | `/progress/{task_id}` | `ProgressResponse` |
| POST | `/retry/{task_id}` | `RetryResponse`，并重试失败项 |

请求/响应模型见 `app/schemas/azure_deploy.py`（`DeployItem`、`ExecuteItem`、`PlanResultItem` 等）。

---

## Metering（前缀 `/api/metering`）

基于 `billing_data` 的用量与费用聚合。

**provider 枚举**：`aws` / `gcp` / `azure` / `taiji`。

**作用域参数**（传哪个就按哪个过滤，传多个时按 `account_ids > account_id > supply_source_id > supplier_name` 优先级，详见 `app/api/metering.py` 中 `_metering_scope`）：

| 参数 | 说明 |
|---|---|
| `account_id` | 单个服务账号 ID（`projects.id`，旧，保留兼容）|
| `account_ids` | 多个服务账号 ID（v1.1 新增，推荐；重复 query: `account_ids=1&account_ids=2`） |
| `supply_source_id` | 货源 ID |
| `supplier_name` | 供应商名；`"(未分组)"` 会映射到保留名 "未分组" |
| `data_source_id` | 数据源 ID |

**其他筛选**：

| 参数 | 说明 |
|---|---|
| `product` | 单个服务/模型名（旧，兼容）|
| `products` | 多个服务/模型名（v1.1 新增；重复 query：`products=gpt-4o&products=claude`）|

| 方法 | 路径 | 查询参数 |
|------|------|----------|
| GET | `/summary` | 日期区间、`provider`、`product/products`，及上述作用域参数 |
| GET | `/daily` | 同上 |
| GET | `/by-service` | 同上（自身就是按 product 聚合，但也支持 `products` 收窄）|
| GET | `/products` | `provider` 与作用域 |
| GET | `/detail` | 同上 + `page`，`page_size` |
| GET | `/detail/count` | 返回 `{"total"}` |
| GET | `/export` | 流式 CSV，`metering_billing_export.csv` |
| **POST** | **`/taiji/ingest`** | **v1.1 新增** — Taiji 平台 Push 原始请求日志入口，`X-API-Key` 鉴权；详见 [taiji-ingest-api.md](./taiji-ingest-api.md) |

---

## 相关 Schema 文件

| 模块 | 文件 |
|------|------|
| 分类 / 云账号 / 数据源 / 项目 | `app/schemas/category.py`、`cloud_account.py`、`data_source.py`、`project.py` |
| 计费 / 同步 / 告警 / 账单 / 汇率 | `app/schemas/billing.py` |
| 计量 | `app/schemas/metering.py` |
| Azure 部署 | `app/schemas/azure_deploy.py` |
| 服务账号（部分内联） | `app/api/service_accounts.py` 内 Pydantic 模型 |

---

## OpenAPI

服务启动后访问 **`/docs`**（Swagger UI）或 **`/openapi.json`** 可获取与实现同步的机器可读规范。
