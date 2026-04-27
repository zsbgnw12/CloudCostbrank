"""GCP BigQuery billing collector — adapted from export_all_5_sources.py."""

import logging

from google.cloud import bigquery
from google.oauth2 import service_account

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class GCPCollector(BaseCollector):

    _ALLOWED_BQ_ID = __import__("re").compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,127}$")
    _ALLOWED_FIELD = __import__("re").compile(r"^[a-zA-Z_][a-zA-Z0-9_.]{0,63}$")

    def collect_billing(self, secret_data: dict, config: dict, start_date: str, end_date: str) -> list[dict]:
        """
        Query BigQuery billing table for a single data source.

        secret_data: {"service_account_json": {...}}
        config: {
            "project_id": "share-service-nonprod",
            "dataset": "xmind",
            "table": "billing_report",
            "cost_field": "cost_at_list",
            "usage_field": "amount_in_pricing_unit",
            "billing_account_id": "01DE67-975828-40894C",
            "is_native": false
        }
        """
        from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter

        client = self._create_client(secret_data)

        project_id = config["project_id"]
        dataset = config["dataset"]
        table = config["table"]
        cost_field = config.get("cost_field", "cost")
        usage_field = config.get("usage_field", "amount_in_pricing_units")

        for name, val in [("project_id", project_id), ("dataset", dataset), ("table", table)]:
            if not self._ALLOWED_BQ_ID.match(val):
                raise ValueError(f"Invalid BigQuery identifier for {name}: {val!r}")
        for name, val in [("cost_field", cost_field), ("usage_field", usage_field)]:
            if not self._ALLOWED_FIELD.match(val):
                raise ValueError(f"Invalid BigQuery field name for {name}: {val!r}")

        # Schema introspection: VIEW 类源（ds#3/4 share-service-nonprod.*.billing_report）
        # 没有 cost / credits 列，cb-export / px-billing / native 三类都有。
        # 服务/SKU id、cost_at_list、resource、cost_type 五处都有。
        fqt = f"{project_id}.{dataset}.{table}"
        try:
            schema_cols = {f.name for f in client.get_table(fqt).schema}
        except Exception as e:
            logger.warning("GCP collect_billing: get_table %s failed: %s; assume minimal schema", fqt, e)
            schema_cols = {"cost_at_list", "service", "sku", "project", "location", "usage", "currency", "labels", "usage_start_time"}

        has_credits = "credits" in schema_cols
        has_cost_col = "cost" in schema_cols  # ds#3/4 VIEW 没有
        has_cost_at_list = "cost_at_list" in schema_cols
        has_resource = "resource" in schema_cols
        has_cost_type = "cost_type" in schema_cols
        has_billing_account = "billing_account_id" in schema_cols
        has_invoice = "invoice" in schema_cols
        has_transaction_type = "transaction_type" in schema_cols
        has_seller = "seller_name" in schema_cols
        has_currency_rate = "currency_conversion_rate" in schema_cols
        has_consumption_model = "consumption_model" in schema_cols
        has_system_labels = "system_labels" in schema_cols

        # 标价合计：所有 GCP 源都有 cost_at_list；没有就 NULL
        cost_at_list_select = "SUM(cost_at_list) as cost_at_list," if has_cost_at_list else "CAST(NULL AS NUMERIC) as cost_at_list,"

        # credits：保留两种字段
        #   credits_total       —— 所有 type 合计 × -1（正数表示节省金额）
        #   credits_breakdown   —— 按 type 分组的 JSON map: {"PROMOTION": 1.50, "DISCOUNT": 0.05, ...}
        # 不再拆 committed / other 两列（reseller 数据里 committed 永远 0）
        if has_credits:
            credits_select = (
                "SUM(IFNULL(("
                "SELECT SUM(c.amount) FROM UNNEST(credits) c"
                "), 0)) * -1 as credits_total,"
                # credits_breakdown: 收集所有 (type, amount) 对，给 Python 端聚合成 JSON
                # ARRAY_AGG 在每个 GROUP 里把 credits 数组逐条 cross-join 后压平
                " ARRAY_CONCAT_AGG(("
                "SELECT ARRAY_AGG(STRUCT(c.type AS type, c.amount AS amount))"
                " FROM UNNEST(credits) c WHERE c.type IS NOT NULL"
                ")) as credits_array,"
            )
        else:
            credits_select = (
                "CAST(NULL AS NUMERIC) as credits_total,"
                " CAST(NULL AS ARRAY<STRUCT<type STRING, amount FLOAT64>>) as credits_array,"
            )

        # resource：取 cost 最高那行的 resource.name 作代表（聚合后只能保留一个）
        if has_resource:
            sort_field = cost_field
            resource_select = f"(ARRAY_AGG(IFNULL(resource.name, '') ORDER BY {sort_field} DESC NULLS LAST LIMIT 1))[OFFSET(0)] as resource_name,"
        else:
            resource_select = "CAST(NULL AS STRING) as resource_name,"

        # cost_type 加进 GROUP BY，让 regular / tax / adjustment 各自独立成行
        cost_type_select = "IFNULL(cost_type, 'regular') as cost_type," if has_cost_type else "CAST('regular' AS STRING) as cost_type,"

        # 新加字段（按 schema 条件分支）
        billing_account_select = "ANY_VALUE(billing_account_id) as billing_account_id," if has_billing_account else "CAST(NULL AS STRING) as billing_account_id,"
        invoice_select = "ANY_VALUE(invoice.month) as invoice_month," if has_invoice else "CAST(NULL AS STRING) as invoice_month,"
        transaction_type_select = "ANY_VALUE(transaction_type) as transaction_type," if has_transaction_type else "CAST(NULL AS STRING) as transaction_type,"
        seller_select = "ANY_VALUE(seller_name) as seller_name," if has_seller else "CAST(NULL AS STRING) as seller_name,"
        currency_rate_select = "ANY_VALUE(currency_conversion_rate) as currency_conversion_rate," if has_currency_rate else "CAST(NULL AS NUMERIC) as currency_conversion_rate,"
        if has_consumption_model:
            consumption_select = (
                "ANY_VALUE(consumption_model.id) as consumption_model_id,"
                " ANY_VALUE(consumption_model.description) as consumption_model_description,"
            )
        else:
            consumption_select = (
                "CAST(NULL AS STRING) as consumption_model_id,"
                " CAST(NULL AS STRING) as consumption_model_description,"
            )
        system_labels_select = "TO_JSON_STRING(ANY_VALUE(system_labels)) as system_labels," if has_system_labels else "CAST(NULL AS STRING) as system_labels,"

        # GROUP BY 现在按 unique key 完整对齐：(date, project_id, service, sku, region, cost_type)
        # 把 cost_type 加进 GROUP BY 以后 regular/tax 不再被混算成一行，对应 DB 唯一键 019 后的形态。
        query = f"""
        SELECT
            DATE(usage_start_time) as billed_date,
            project.id as project_id,
            ANY_VALUE(project.name) as project_name,
            ANY_VALUE(service.id) as service_id,
            service.description as service,
            ANY_VALUE(sku.id) as sku_id,
            sku.description as sku,
            IFNULL(location.region, 'global') as region,
            {cost_type_select}
            SUM({cost_field}) as cost,
            {cost_at_list_select}
            {credits_select}
            SUM(usage.{usage_field}) as usage_quantity,
            ANY_VALUE(usage.pricing_unit) as pricing_unit,
            ANY_VALUE(currency) as currency,
            {currency_rate_select}
            {resource_select}
            {billing_account_select}
            {invoice_select}
            {transaction_type_select}
            {seller_select}
            {consumption_select}
            {system_labels_select}
            TO_JSON_STRING(ANY_VALUE(labels)) as labels
        FROM `{project_id}.{dataset}.{table}`
        WHERE usage_start_time >= TIMESTAMP(@start_date)
          AND usage_start_time < TIMESTAMP(@end_date) + INTERVAL 1 DAY
        GROUP BY billed_date, project_id, service, sku, region, cost_type
        ORDER BY billed_date, project_id, service, sku
        """

        job_config = QueryJobConfig(query_parameters=[
            ScalarQueryParameter("start_date", "STRING", start_date),
            ScalarQueryParameter("end_date", "STRING", end_date),
        ])

        logger.info(f"GCP query: {fqt} [{start_date} ~ {end_date}]")
        job = client.query(query, job_config=job_config)
        results = list(job.result())
        logger.info(f"GCP returned {len(results)} rows")

        rows = []
        import json as _json
        from collections import defaultdict
        for row in results:
            # labels: BQ 返回 list[{key, value}] 或 JSON 字符串。统一成 dict。
            raw_labels = row.labels
            if isinstance(raw_labels, str):
                try:
                    raw_labels = _json.loads(raw_labels)
                except Exception:
                    raw_labels = []
            if isinstance(raw_labels, list):
                tags = {kv.get("key"): kv.get("value") for kv in raw_labels if isinstance(kv, dict) and kv.get("key")}
            elif isinstance(raw_labels, dict):
                tags = raw_labels
            else:
                tags = {}

            # system_labels 同样处理
            raw_sys = getattr(row, "system_labels", None)
            if isinstance(raw_sys, str):
                try:
                    raw_sys = _json.loads(raw_sys)
                except Exception:
                    raw_sys = []
            if isinstance(raw_sys, list):
                sys_labels = {kv.get("key"): kv.get("value") for kv in raw_sys if isinstance(kv, dict) and kv.get("key")}
            elif isinstance(raw_sys, dict):
                sys_labels = raw_sys
            else:
                sys_labels = None

            # credits_breakdown：BQ 返回 list[{type, amount}]，按 type group + sum
            credits_breakdown = None
            credits_array = getattr(row, "credits_array", None)
            if credits_array:
                bd = defaultdict(float)
                for c in credits_array:
                    t = c.get("type") if isinstance(c, dict) else getattr(c, "type", None)
                    a = c.get("amount") if isinstance(c, dict) else getattr(c, "amount", None)
                    if t is not None and a is not None:
                        bd[t] += float(a)
                # 入库存原始 BQ 金额（负数）；导出层若需要正数（节省金额）自己 × -1
                credits_breakdown = {k: round(v, 6) for k, v in bd.items()} if bd else None

            rows.append({
                "date": row.billed_date.isoformat() if row.billed_date else start_date,
                "project_id": row.project_id or "",
                "project_name": row.project_name or "",
                "service_id": row.service_id or None,
                "sku_id": row.sku_id or None,
                "product": row.service or "",
                "usage_type": row.sku or "",
                "region": row.region or "global",
                "cost_type": row.cost_type or "regular",
                "cost": float(row.cost or 0),
                "cost_at_list": float(row.cost_at_list) if row.cost_at_list is not None else None,
                "credits_total": float(row.credits_total) if row.credits_total is not None else None,
                "credits_breakdown": credits_breakdown,
                "usage_quantity": float(row.usage_quantity or 0),
                "usage_unit": row.pricing_unit or "",
                "currency": row.currency or "USD",
                "currency_conversion_rate": float(row.currency_conversion_rate) if row.currency_conversion_rate is not None else None,
                "resource_name": (row.resource_name or None) if row.resource_name else None,
                "billing_account_id": row.billing_account_id or None,
                "invoice_month": row.invoice_month or None,
                "transaction_type": row.transaction_type or None,
                "seller_name": row.seller_name or None,
                "consumption_model_id": row.consumption_model_id or None,
                "consumption_model_description": row.consumption_model_description or None,
                "tags": tags,
                "system_labels": sys_labels,
                "additional_info": {},
            })
        return rows

    def collect_resources(self, secret_data: dict, config: dict) -> list[dict]:
        return []

    @staticmethod
    def _create_client(secret_data: dict) -> bigquery.Client:
        sa_json = secret_data.get("service_account_json", {})
        credentials = service_account.Credentials.from_service_account_info(
            sa_json,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return bigquery.Client(credentials=credentials, project=credentials.project_id)
