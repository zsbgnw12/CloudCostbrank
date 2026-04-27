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

        # 标价合计：所有 GCP 源都有 cost_at_list；没有就 NULL
        cost_at_list_select = "SUM(cost_at_list) as cost_at_list," if has_cost_at_list else "CAST(NULL AS NUMERIC) as cost_at_list,"
        # credits 折扣合计：BQ 里 credits 是 ARRAY<RECORD<amount, type, ...>>，
        # amount 是负值（折扣是负的）；× -1 后存正数表示"节省金额"。
        # 拆分对应 Excel 导出列：
        #   credits_committed = type='COMMITTED_USAGE_DISCOUNT' (节省计划/CUD)
        #   credits_other     = 其他 type (SUSTAINED_USAGE_DISCOUNT / PROMOTION / FREE_TIER ...)
        #   credits_total     = committed + other（冗余但便于对账）
        if has_credits:
            credits_select = (
                "SUM(IFNULL(("
                "SELECT SUM(c.amount) FROM UNNEST(credits) c"
                "), 0)) * -1 as credits_total,"
                " SUM(IFNULL(("
                "SELECT SUM(c.amount) FROM UNNEST(credits) c WHERE c.type='COMMITTED_USAGE_DISCOUNT'"
                "), 0)) * -1 as credits_committed,"
                " SUM(IFNULL(("
                "SELECT SUM(c.amount) FROM UNNEST(credits) c WHERE c.type IS NULL OR c.type!='COMMITTED_USAGE_DISCOUNT'"
                "), 0)) * -1 as credits_other,"
            )
        else:
            credits_select = (
                "CAST(NULL AS NUMERIC) as credits_total,"
                " CAST(NULL AS NUMERIC) as credits_committed,"
                " CAST(NULL AS NUMERIC) as credits_other,"
            )
        # resource：取 cost 最高那行的 resource.name 作代表（聚合后只能保留一个）
        if has_resource:
            # 注意：要按 cost 排，但有些 VIEW 没 cost 字段，用 cost_at_list 排
            sort_field = cost_field
            resource_select = f"(ARRAY_AGG(IFNULL(resource.name, '') ORDER BY {sort_field} DESC NULLS LAST LIMIT 1))[OFFSET(0)] as resource_name,"
        else:
            resource_select = "CAST(NULL AS STRING) as resource_name,"
        cost_type_select = "ANY_VALUE(IFNULL(cost_type, 'regular')) as cost_type," if has_cost_type else "CAST(NULL AS STRING) as cost_type,"

        # Aggregate at BQ by the DB unique key so a single SKU's hundreds of
        # intra-day line items collapse into one row with SUM(cost). Without this
        # the UPSERT path in sync_service would have to do the summing itself,
        # and we'd ship 100x more rows across the wire for nothing.
        # GROUP BY 仍按 (date, project, service-desc, sku-desc, region) —— service.id/sku.id
        # 在同一描述下唯一，加进 GROUP BY 也不增行；不加而用 ANY_VALUE 也行，这里选后者
        # 保持和原有维度一致。
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
            SUM({cost_field}) as cost,
            {cost_at_list_select}
            {credits_select}
            SUM(usage.{usage_field}) as usage_quantity,
            ANY_VALUE(usage.pricing_unit) as pricing_unit,
            ANY_VALUE(currency) as currency,
            {resource_select}
            {cost_type_select}
            TO_JSON_STRING(ANY_VALUE(labels)) as labels
        FROM `{project_id}.{dataset}.{table}`
        WHERE usage_start_time >= TIMESTAMP(@start_date)
          AND usage_start_time < TIMESTAMP(@end_date) + INTERVAL 1 DAY
        GROUP BY billed_date, project_id, service, sku, region
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
        for row in results:
            import json
            # labels can be a JSON string (billing_report schema) or already
            # a Python list/dict (native export schema). Handle both.
            raw_labels = row.labels
            if isinstance(raw_labels, (list, dict)):
                tags = raw_labels
            elif raw_labels:
                try:
                    tags = json.loads(raw_labels)
                except Exception:
                    tags = {}
            else:
                tags = {}

            rows.append({
                "date": row.billed_date.isoformat() if row.billed_date else start_date,
                "project_id": row.project_id or "",
                "project_name": row.project_name or "",
                "service_id": row.service_id or None,
                "sku_id": row.sku_id or None,
                "product": row.service or "",
                "usage_type": row.sku or "",
                "region": row.region or "global",
                "cost": float(row.cost or 0),
                "cost_at_list": float(row.cost_at_list) if row.cost_at_list is not None else None,
                "credits_total": float(row.credits_total) if row.credits_total is not None else None,
                "credits_committed": float(row.credits_committed) if row.credits_committed is not None else None,
                "credits_other": float(row.credits_other) if row.credits_other is not None else None,
                "usage_quantity": float(row.usage_quantity or 0),
                "usage_unit": row.pricing_unit or "",
                "currency": row.currency or "USD",
                "resource_name": (row.resource_name or None) if row.resource_name else None,
                "cost_type": row.cost_type or None,
                "tags": tags,
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
