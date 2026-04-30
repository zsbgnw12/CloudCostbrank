# AWS 服务账号接入指南

> 给客户的对接说明:在 AWS 这一侧需要做的准备,让 CloudCost 后端能够采集到该 AWS 账号的费用与用量数据。

---

## 一、最小权限原则

CloudCost 当前**只读**调用以下 AWS API:
- `Cost Explorer`(成本明细 + 维度)— 主要数据来源
- `STS`(确认账号身份)— 仅用 `GetCallerIdentity`

**不需要**:`IAMFullAccess` / `AdministratorAccess` / 任何写权限 / S3 读权限。

---

## 二、操作步骤

### Step 1 — 启用 Cost Explorer(必须,最容易漏)

AWS Cost Explorer **首次使用必须在控制台手工启用一次**,否则 API 永远返回空数据。

1. AWS 控制台 → **Billing and Cost Management** → **Cost Explorer**
2. 看到欢迎页 → 点击 "Launch Cost Explorer" 一次即可
3. 启用后 24 小时内才能看到完整历史数据(AWS 自身处理时延)

### Step 2 — 创建 IAM Policy

在 IAM 控制台创建一个 Customer Managed Policy,命名建议 `CloudCostReadOnly`,JSON 内容:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CostExplorerRead",
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetCostAndUsageWithResources",
        "ce:GetDimensionValues",
        "ce:GetTags",
        "ce:GetCostForecast"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WhoAmI",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    }
  ]
}
```

> Cost Explorer API 不支持 resource-level 限制,`Resource` 只能是 `*`,这是 AWS 的设计限制不是过度授权。

### Step 3 — 创建专用 IAM User(推荐方式 A)

1. IAM → **Users** → **Create user**
2. 用户名建议:`cloudcost-readonly`
3. **不勾**"Provide user access to the AWS Management Console"(不需要登录控制台)
4. 下一步 → **Attach policies directly** → 勾选刚才创建的 `CloudCostReadOnly`
5. 创建完成 → 进入用户详情 → **Security credentials** → **Create access key**
6. Use case 选 "Application running outside AWS"
7. **保存 Access key ID + Secret access key**(secret 只显示一次)
8. 把这两个值发给 CloudCost 管理员录入

### Step 3b — 跨账号 IAM Role(方式 B,更安全,生产推荐)

如果客户不想在外部存储长效 access key,可以让 CloudCost 用 STS AssumeRole:

1. IAM → **Roles** → **Create role**
2. Trusted entity:**Another AWS account** → 输入 CloudCost 的 AWS Account ID(向我们的运维索取)
3. External ID:让 CloudCost 给你一个 UUID 填进去(防混淆代理人攻击)
4. Permissions:挂上 `CloudCostReadOnly` policy
5. Role name:`CloudCostCrossAccountRead`
6. 把 Role ARN(形如 `arn:aws:iam::123456789012:role/CloudCostCrossAccountRead`)和 External ID 发给 CloudCost 管理员

> 当前后端 collector 已支持 `role_arn` + `external_id`(看 cloud_accounts.secret_data 结构),但前端"添加 AWS 服务账号"对话框默认只露 access_key 字段。需要 Role 模式的可联系管理员手工录入凭据。

### Step 4(可选)— 启用 Cost Allocation Tags

如果你们想按业务标签(如 `Environment=prod` / `Team=ml`)分账,需要:

1. AWS Billing → **Cost allocation tags**
2. 找到要启用的 user-defined tag → 勾 "Activate"
3. 激活后 ~24 小时,Cost Explorer API 才能按该 tag 维度返回数据

未激活的 tag 在 BQ/CE 里都查不到聚合数据(虽然在 EC2 实例上能看到原始 tag)。

---

## 三、对接信息汇总表

把下面这张表填好发给 CloudCost 管理员:

| 字段 | 你的值 | 说明 |
|---|---|---|
| AWS Account ID | `123456789012` | 12 位数字,IAM 用户控制台右上角能看到 |
| Account 别名 | `prod-main` | 自定义,在 CloudCost 里展示用 |
| 接入方式 | A: AccessKey / B: AssumeRole | |
| **A.** Access Key ID | `AKIA...` | 仅方式 A |
| **A.** Secret Access Key | `...` | 仅方式 A,**首次创建时一定要保存** |
| **B.** Role ARN | `arn:aws:iam::...:role/CloudCostCrossAccountRead` | 仅方式 B |
| **B.** External ID | `<我们提供的 UUID>` | 仅方式 B |
| 是否启用 Cost Explorer | ✅ / ❌ | 必须 ✅ |

---

## 四、常见问题

**Q: 我已经把 Policy 挂上了,为什么 CloudCost 拉到的数据全是空?**
- 99% 是没启用 Cost Explorer(Step 1)。控制台访问一次 Cost Explorer 页面就行。
- 1% 是 access key 失效 / 复制时多了空格。

**Q: Cost Explorer 收费吗?**
- API 调用 $0.01 / 次。CloudCost 每天每账号约 1-3 次调用,月成本 < $1。

**Q: 能不能限制只能查某些服务的费用?**
- AWS Cost Explorer API 不支持 resource-level IAM 限制,只能限制"能不能调 API"。如果担心数据外泄,用方式 B(AssumeRole + External ID)。

**Q: 数据有多新?**
- AWS Cost Explorer 数据**滞后约 24 小时**。今天看不到今天的数据,明天 02:00 同步任务才能拿到。

---

## 五、给运维:手工录入示例(若客户用 Role 模式)

```sql
-- 后端 cloud_accounts.secret_data 是 Fernet(AES) 加密的 JSON
-- 解密后结构(方式 B):
{
  "role_arn": "arn:aws:iam::123456789012:role/CloudCostCrossAccountRead",
  "external_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "aws_access_key_id": null,
  "aws_secret_access_key": null
}
-- 走 STS AssumeRole 拿临时凭证,1 小时自动续期。
```

如果走方式 A,把 `aws_access_key_id` / `aws_secret_access_key` 填上,`role_arn` / `external_id` 留 null 即可。
