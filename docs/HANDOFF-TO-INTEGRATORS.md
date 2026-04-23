# CloudCost 对接交付清单

> 发给 AI 大脑 / 网关 / Importer 团队
> 版本 v1.1 · 2026-04-21

---

## 1. 你要的三份文档（本目录下）

| 文档 | 用途 |
|---|---|
| [API.md](./API.md) | **全量路由清单**（所有端点简表，用于 Importer/spec 自动导入）|
| [AI-BRAIN-API.md](./AI-BRAIN-API.md) | 详细版 — 面向 AI 大脑调用方，含入参/出参/分级/禁用接口 |
| [taiji-ingest-api.md](./taiji-ingest-api.md) | Taiji 平台 Push 专用契约（你们如果不对接 Taiji 可以忽略）|

直接把这三份交给 Importer 脚本或人工消费即可。

---

## 2. 认证：CloudCost 读 Casdoor token 的 claim 字段

```
Authorization: Bearer <casdoor_access_token>
```

| Casdoor claim | 必需 | CloudCost 拿来干嘛 | Fallback 顺序 |
|---|---|---|---|
| `sub` | **必填** | 唯一身份键（`users.casdoor_sub`）| `id` |
| `roles` | 强烈建议 | 角色鉴权 | 见下 |
| `preferred_username` | 可选 | 展示用户名 | `name` → `username` → `sub` |
| `email` | 可选 | 审计 | — |
| `displayName` | 可选 | UI 显示名 | `name` |
| `avatar` | 可选 | 头像 | `picture` |

**角色 claim 名**（你清单里问的 `CASDOOR_ROLES_CLAIM`）：

- CloudCost 已在 v1.1 补上这个环境变量
- 默认值：`roles`
- 读取优先级：**`settings.CASDOOR_ROLES_CLAIM` → `roles` → `role`**（前面空了自动 fallback 到后面，不会漏）
- 你们 Casdoor 的 claim 如果是 `roles` / `role` 其中之一，**CloudCost 不用改任何 env，开箱即用**
- 如果是别的（比如 `user_roles` / `realm_roles`），通知 CloudCost 设置 env：
  ```bash
  az containerapp update --name cloudcost-brank --resource-group CloudCost \
    --container-name api \
    --set-env-vars CASDOOR_ROLES_CLAIM=user_roles
  ```

---

## 3. 四个系统角色

来自 CloudCost Casdoor token 里的 `roles`（或你配置的 claim）：

| 角色 | 权限范围 |
|---|---|
| `cloud_admin` | 全部功能 + 全量数据 |
| `cloud_ops` | dashboard + 触发同步 |
| `cloud_finance` | dashboard + 账单管理 |
| `cloud_viewer` | dashboard 只读 |

M2M 应用（`client_credentials` 拿 token 的）首次调用会自动在 `users` 表插入一条，默认 `roles=[]`。管理员需要手动 SQL 设角色：

```sql
UPDATE users SET roles='["cloud_admin"]'::jsonb 
WHERE casdoor_sub='admin/<your_app_name>';
```

---

## 4. 生产环境地址

| 服务 | URL |
|---|---|
| CloudCost API | `https://cloudcost-brank.yellowground-bf760827.southeastasia.azurecontainerapps.io` |
| Casdoor | `https://casdoor.ashyglacier-8207efd2.eastasia.azurecontainerapps.io` |
| OpenAPI 规范 | `{BASE}/openapi.json` 或 `{BASE}/docs` |

---

## 5. Quick Start

```bash
CASDOOR=https://casdoor.ashyglacier-8207efd2.eastasia.azurecontainerapps.io
BASE=https://cloudcost-brank.yellowground-bf760827.southeastasia.azurecontainerapps.io

# 1. 拿 token (client_credentials)
TOKEN=$(curl -s -X POST "$CASDOOR/api/login/oauth/access_token" \
  -d "grant_type=client_credentials&client_id=<你的 APP_CLIENT_ID>&client_secret=<你的 APP_CLIENT_SECRET>" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. 探身份
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/auth/me"

# 3. 调用任意接口
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/dashboard/bundle?month=2026-04"
```

---

## 6. 最近的 API 契约变更（v1.1）

| 变更 | 说明 |
|---|---|
| **provider 枚举**扩展 | 新增 `taiji`，含义见 `taiji-ingest-api.md` |
| `/api/metering/*` 多选参数 | 新增 `account_ids[]` / `products[]`（重复 query 参数语法），原 `account_id` / `product` 仍兼容 |
| **新增** `POST /api/metering/taiji/ingest` | Taiji 平台专用写入，**不对 AI 开放**，独立 API Key 鉴权 |
| 新增 env `CASDOOR_ROLES_CLAIM` | 角色 claim 名可配置，默认 `roles`，向下兼容 |
| 供应商重名检测 | `POST /api/suppliers/` 对重名返回 409，原静默成功行为废除 |

---

## 7. 联系

问题反馈：CloudCost 后端团队 / 工单系统
