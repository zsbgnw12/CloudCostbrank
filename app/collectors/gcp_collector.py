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

        # Aggregate at BQ by the DB unique key so a single SKU's hundreds of
        # intra-day line items collapse into one row with SUM(cost). Without this
        # the UPSERT path in sync_service would have to do the summing itself,
        # and we'd ship 100x more rows across the wire for nothing.
        query = f"""
        SELECT
            DATE(usage_start_time) as billed_date,
            project.id as project_id,
            ANY_VALUE(project.name) as project_name,
            service.description as service,
            sku.description as sku,
            IFNULL(location.region, 'global') as region,
            SUM({cost_field}) as cost,
            SUM(usage.{usage_field}) as usage_quantity,
            ANY_VALUE(usage.pricing_unit) as pricing_unit,
            ANY_VALUE(currency) as currency,
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

        logger.info(f"GCP query: {project_id}.{dataset}.{table} [{start_date} ~ {end_date}]")
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
                "product": row.service or "",
                "usage_type": row.sku or "",
                "region": row.region or "global",
                "cost": float(row.cost or 0),
                "usage_quantity": float(row.usage_quantity or 0),
                "usage_unit": row.pricing_unit or "",
                "currency": row.currency or "USD",
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
