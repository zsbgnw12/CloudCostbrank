# Taiji → CloudCost 数据推送接口文档

> 版本 v1.0  ·  最后更新 2026-04-21
> 面向：Taiji 平台研发团队
> 对端：CloudCost 成本管理系统

---

## 1. 概述

CloudCost 需要接收 Taiji 平台的请求级日志（每条 API 调用一条记录），用于成本归集、用量统计、按模型 / 按用户分析。CloudCost 提供一个 **Push 接入端点**，Taiji 侧周期性批量调用即可。

- **通讯方式**：HTTPS POST JSON
- **批量粒度**：单次最多 2000 条
- **推送频率建议**：每 1~5 分钟一次，或攒够 2000 条立即推
- **重试策略**：推送失败或超时请重试 3 次；**重复推送相同 id 的记录不会造成重复计费**（自动去重）

---

## 2. 环境与地址

| 环境 | Base URL |
|---|---|
| 生产 | `https://cloudcost-brank.yellowground-bf760827.southeastasia.azurecontainerapps.io` |

**端点**：`POST {base}/api/metering/taiji/ingest`

---

## 3. 认证

使用 **API Key**，请求头带：

```http
X-API-Key: cck_xxxxxxxxxxxxxxxxxx
```

- API Key 由 CloudCost 管理员在 `/api-keys` 页面创建并发给你
- Key 绑定到一个 CloudAccount（代表你们这套 taiji 实例），推送数据将归属到对应的 DataSource
- Key 可随时撤销；请勿硬编码进代码仓库，从配置中心 / 环境变量读取

**未带 Key 或 Key 无效** → HTTP 401
**Key 配置不符合规范**（例如没绑定到 taiji 类型的 CloudAccount）→ HTTP 403

---

## 4. 请求格式

### 4.1 Headers

| Header | 值 | 说明 |
|---|---|---|
| `X-API-Key` | `cck_...` | **必填**，认证 |
| `Content-Type` | `application/json` | **必填** |

### 4.2 Body Schema

```json
{
  "logs": [
    {
      "id": 40208,
      "user_id": 4,
      "created_at": 1772323212,
      "type": 2,
      "username": "user",
      "token_id": 2,
      "token_name": "user_default",
      "channel_id": 4,
      "channel_name": "bedrock-anthropic",
      "model_name": "claude-sonnet-4-20250514",
      "quota": 3956,
      "prompt_tokens": 1262,
      "completion_tokens": 275,
      "use_time": 7,
      "is_stream": 0,
      "other": {
        "cache_tokens": 50,
        "cache_write_tokens": 0,
        "text_output_tokens": 275,
        "image_output_tokens": 0,
        "note": ""
      }
    }
  ]
}
```

### 4.3 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `integer` | **是** | Taiji 日志表的主键。**用于幂等去重**：重复推送相同 id 会被忽略（不重复计费） |
| `user_id` | `integer` | 否 | Taiji 平台内部用户 ID |
| `created_at` | `integer` | **是** | 请求发生时间，**Unix 秒时间戳（UTC）**。用于按天归集到 `billing_data` |
| `type` | `integer` | 否 | 日志类型。`2` = 消费日志（Taiji 内部约定），其他值也允许但通常只推 `2` |
| `username` | `string(200)` | 否 | Taiji 用户名（会显示在 CloudCost 的服务账号名里，可读友好）|
| `token_id` | `integer` | **是** | Taiji token 表的主键，标识一张 API Key。**CloudCost 按此聚合"服务账号"维度** |
| `token_name` | `string(200)` | 否 | Taiji token 的展示名（会作为服务账号名的一部分显示）|
| `channel_id` | `integer` | 否 | 底层渠道 ID（如 bedrock / openai 官方 等）|
| `channel_name` | `string(200)` | 否 | 底层渠道名，会存到 `billing_data.region` 供筛选 |
| `model_name` | `string(200)` | **是** | 对外模型名（如 `gpt-4o` / `claude-sonnet-4-20250514`）。**CloudCost 按模型聚合成本** |
| `quota` | `integer` | **是** | Taiji 的 quota 值（原始单位）。CloudCost 按 `quota / quota_per_usd` 换算成美元；`quota_per_usd` 由管理员在 DataSource.config 里配置，默认 500000（OneAPI / New-API 惯例） |
| `prompt_tokens` | `integer` | 否 | 输入 token 数，默认 0 |
| `completion_tokens` | `integer` | 否 | 输出 token 数，默认 0。对 Grok 类模型应为"文字输出 + 图片输出"之和 |
| `use_time` | `integer` | 否 | 请求耗时（毫秒或秒，Taiji 自定义） |
| `is_stream` | `integer` | 否 | `1` 流式 / `0` 非流式 |
| `other` | `object \| string` | 否 | 附加 JSON 对象。`cache_tokens` / `cache_write_tokens` / `text_output_tokens` / `image_output_tokens` 等可选子字段会被 CloudCost 解析 |

### 4.4 `other` 子字段约定（Taiji 如果能提供则推荐带上）

| 子字段 | 用途 |
|---|---|
| `cache_tokens` | 缓存命中的 token 数，会进 `token_usage.cache_read_tokens` |
| `cache_write_tokens` | 缓存写入的 token 数，会进 `token_usage.cache_write_tokens` |
| `text_output_tokens` | Grok 的文字输出 token（保留审计用）|
| `image_output_tokens` | Grok 的图片输出 token |
| `note` | 请求备注 |
| `admin_info` | 透传，原样保留 |
| 其他任意字段 | 全部原样落库到 `taiji_log_raw.other`，不丢失 |

### 4.5 字段增加不破坏兼容

Taiji 将来新增任何字段，按 JSON 规范加到 log 对象里 / `other` 子对象里都可以。CloudCost 采用 `extra="ignore"` 策略，**不识别的字段自动忽略，不报错**。已有字段改名/删除需提前知会。

---

## 5. 响应格式

### 5.1 成功 200

```json
{
  "received": 2000,
  "stored_new": 1850,
  "deduped": 150,
  "dates_reaggregated": ["2026-03-20", "2026-03-21"],
  "billing_rows_upserted": 24,
  "token_usage_rows_upserted": 8,
  "projects_created": 2
}
```

| 字段 | 含义 |
|---|---|
| `received` | 本次请求收到的 log 条数 |
| `stored_new` | 原始表新写入的条数（去重后）|
| `deduped` | 重复 id 被跳过的条数 |
| `dates_reaggregated` | 本次涉及的日期集合（会重算这些日期的聚合账单） |
| `billing_rows_upserted` | 生成的成本账单行数（按天×token×模型聚合）|
| `token_usage_rows_upserted` | 生成的 AI 用量行数（按天×模型聚合） |
| `projects_created` | 自动发现并登记的新 token 服务账号数 |

### 5.2 错误码

| 状态码 | 场景 | 处理 |
|---|---|---|
| `400` | body 格式错 / logs 超过 2000 / 必填字段缺失 | 修正请求 |
| `401` | Key 缺失或无效 | 检查 `X-API-Key` header |
| `403` | Key 未绑定 CloudAccount / 绑定的不是 taiji 类型 / 鉴权方式错误 | 联系 CloudCost 管理员 |
| `409` | 绑定的 CloudAccount 下没有或有多个 DataSource（配置异常） | 联系 CloudCost 管理员 |
| `422` | 单条 log 字段类型错误（例 `id` 不是整数）| 修正数据 |
| `5xx` | CloudCost 内部异常 | 按退避重试（见 §6）|

### 5.3 错误响应体

```json
{"detail": "taiji ingest 必须使用 X-API-Key 鉴权"}
```

---

## 6. 幂等性与重试

### 6.1 幂等保证

**以 `id` 为自然主键去重**：
- 相同 `id` 多次推送 → 原始表只保留一份，`stored_new` 只加一次，`deduped` 累加
- 即使乱序推送也不会出错（日期聚合层会按 `dates_reaggregated` 里涉及的日期**全量重算**）

**结论**：推送失败/超时/网络抖动造成的重试**完全安全**，不会重复计费。

### 6.2 推荐的推送节奏

| 场景 | 推荐做法 |
|---|---|
| 日常运行 | 每 1~5 分钟，推送上一窗口的增量日志 |
| 攒满 2000 条 | 立即推送一次 |
| 首次接入历史数据 | 分批推（每批 2000 条），按时间顺序 |
| 推送失败 | 指数退避重试 3 次（间隔 1s / 2s / 4s），仍失败则报警 |
| CloudCost 返回 5xx | 视为可重试 |

### 6.3 推送延迟

CloudCost 内部处理耗时：单次 2000 条大约 300~800 ms（含原始层幂等写入 + 聚合重算 + 缓存刷新）。建议客户端 HTTP 超时设置为 **30 秒**。

---

## 7. 完整示例

### 7.1 单条推送（调试用）

```bash
curl -X POST \
  "https://cloudcost-brank.yellowground-bf760827.southeastasia.azurecontainerapps.io/api/metering/taiji/ingest" \
  -H "X-API-Key: cck_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "logs": [
      {
        "id": 40208,
        "user_id": 4,
        "created_at": 1772323212,
        "type": 2,
        "username": "user",
        "token_id": 2,
        "token_name": "user_default",
        "channel_id": 4,
        "channel_name": "bedrock",
        "model_name": "claude-sonnet-4-20250514",
        "quota": 3956,
        "prompt_tokens": 1262,
        "completion_tokens": 275,
        "use_time": 7,
        "is_stream": 0,
        "other": {"cache_tokens": 50}
      }
    ]
  }'
```

期望响应：

```json
{
  "received": 1,
  "stored_new": 1,
  "deduped": 0,
  "dates_reaggregated": ["2026-03-01"],
  "billing_rows_upserted": 1,
  "token_usage_rows_upserted": 1,
  "projects_created": 1
}
```

### 7.2 批量推送（生产做法）

伪代码：

```python
import httpx, time

ENDPOINT = "https://cloudcost-brank.yellowground-bf760827.southeastasia.azurecontainerapps.io/api/metering/taiji/ingest"
API_KEY = os.environ["CLOUDCOST_API_KEY"]

def push_logs(logs: list[dict]) -> dict:
    """批量推送，自动重试。logs 长度不超过 2000。"""
    for attempt in range(3):
        try:
            r = httpx.post(
                ENDPOINT,
                headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
                json={"logs": logs},
                timeout=30.0,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 401, 403, 409, 422):
                # 客户端错误，不重试
                raise RuntimeError(f"permanent error {r.status_code}: {r.text}")
            # 5xx，按退避重试
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError("push failed after 3 retries")


# 定时任务：每 5 分钟推一次上 5 分钟的日志
while True:
    batch = query_recent_logs_from_taiji_db(limit=2000)  # 自己实现
    if batch:
        result = push_logs(batch)
        print(f"pushed: {result['received']}, stored: {result['stored_new']}")
    time.sleep(300)
```

---

## 8. 字段填写指南（给 Taiji 研发的快速对照）

假设 Taiji 日志表叫 `logs`，字段名沿用 new-api 约定。直接 `SELECT * FROM logs WHERE created_at >= :last_pushed AND type = 2` 然后转换：

```python
taiji_log = {
    "id":                row["id"],
    "user_id":           row["user_id"],
    "created_at":        row["created_at"],        # 已经是 unix 秒
    "type":              row["type"],
    "username":          row["username"],
    "token_id":          row["token_id"],
    "token_name":        row["token_name"],
    "channel_id":        row["channel_id"],
    "channel_name":      row["channel_name"],     # 可 null
    "model_name":        row["model_name"],
    "quota":             row["quota"],
    "prompt_tokens":     row["prompt_tokens"],
    "completion_tokens": row["completion_tokens"],
    "use_time":          row["use_time"],
    "is_stream":         row["is_stream"],
    "other":             row["other"],             # JSON 字符串或对象都可
}
```

完成上面这个映射即可。**不需要任何数据换算**（美元换算由 CloudCost 按 `quota_per_usd` 自己做）。

---

## 9. 测试与验收

### 9.1 拿到 API Key 后的自测步骤

1. **用 §7.1 的 curl 发一条**，检查返回 `stored_new=1`
2. **再发同一条**，检查 `deduped=1 / stored_new=0`（幂等）
3. **改 id 发新一条**，检查 `stored_new=1`
4. 登录 CloudCost 前端 `/metering` 页，选对应供应商/货源，能看到数字

### 9.2 联调问题排查清单

| 现象 | 可能原因 |
|---|---|
| 401 Unauthorized | `X-API-Key` 没带或写错 |
| 403 `must use X-API-Key` | 用了其他认证方式（Bearer / Cookie）|
| 403 `must limit to 1 cloud_account_id` | 你拿到的 Key 没绑 CloudAccount，联系 CloudCost 管理员重发 |
| 403 `provider is not taiji` | Key 绑到了非 taiji 的 CloudAccount（配置错）|
| 409 `no DataSource` | CloudCost 侧 seed 没跑或 DataSource 被误删 |
| 422 `id field required` | 批次里某条缺 id 字段 |
| 200 但 `stored_new=0 / deduped=N` | 这批 id 已经推过，正常 |
| 前端看不到数据 | 多半是日期范围不包含推送的日期，或登录用户没有对应 CloudAccount 的可见权限 |

---

## 10. 版本变更

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-04-21 | 首发：ingest 端点 + 幂等 + 自动发现 token |

---

## 11. 联系

- 接口负责人：CloudCost 后端团队
- 问题反馈：在 CloudCost 前端 `/alerts` 或 issue 系统提单
- 紧急故障：直接联系云管平台值班运维
