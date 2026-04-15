"""Azure Cost Management billing collector — adapted from export_azure_cost.py."""

import calendar
import csv
import json
import logging
import re
import tempfile
import time
from datetime import datetime
from io import StringIO
from typing import Any

import requests
from azure.core.exceptions import HttpResponseError
from azure.identity import ClientSecretCredential
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    CostDetailsTimePeriod,
    GenerateCostDetailsReportRequestDefinition,
)

from app.collectors.base import BaseCollector
from app.config import settings

logger = logging.getLogger(__name__)

REGION_MAP = {
    "global": "Global", "unknown": "Unknown", "unassigned": "Unassigned",
    "all regions": "All Regions", "intercontinental": "Intercontinental",
    "ap east": "eastasia", "ca central": "canadacentral", "ca east": "canadaeast",
    "east us": "eastus", "east us2": "eastus2", "eu west": "westeurope",
    "kr central": "koreacentral", "kr south": "koreasouth",
    "ja east": "japaneast", "ja west": "japanwest",
    "us east": "eastus", "us east 2": "eastus2", "us west": "westus",
    "us west 2": "westus2stage", "us central": "centralus",
    "us north central": "northcentralus", "ap southeast": "southeastasia",
    "za north": "southafricanorth", "uk south": "uksouth",
    "br south": "brazilsouth", "in west": "westindia", "in central": "centralindia",
    "de west central": "germanywestcentral", "us south central": "southcentralus",
}

SCOPE_MAP = {
    "subscription": "subscriptions/{subscription_id}",
    "billing_account": "providers/Microsoft.Billing/billingAccounts/{billing_account_id}",
}


class AzureCollector(BaseCollector):

    def collect_billing(self, secret_data: dict, config: dict, start_date: str, end_date: str) -> list[dict]:
        """
        Query Azure Cost Management API via LRO.

        secret_data: {"tenant_id": "...", "client_id": "...", "client_secret": "..."}
        config: {"subscription_id": "...", "collect_mode": "subscription", "cost_metric": "ActualCost"}
        """
        client = self._create_client(secret_data, config)
        scope = self._build_scope(secret_data, config)
        metric = config.get("cost_metric", "ActualCost")

        # Build monthly time periods
        periods = self._make_time_periods(start_date, end_date)

        all_rows = []
        for period in periods:
            blobs = self._generate_report(client, scope, period["start"], period["end"], metric)
            for blob in blobs:
                blob_link = getattr(blob, "blob_link", None) or (blob if isinstance(blob, str) else "")
                if not blob_link:
                    continue
                raw_records = self._download_and_parse(blob_link)
                for raw in raw_records:
                    mapped = self._map_record(raw)
                    if mapped:
                        all_rows.append(mapped)

        logger.info(f"Azure returned {len(all_rows)} rows")
        return all_rows

    def collect_resources(self, secret_data: dict, config: dict) -> list[dict]:
        return []

    @staticmethod
    def _resolve_credentials(secret_data: dict) -> tuple[str, str, str]:
        """Return (tenant_id, client_id, client_secret) based on auth_mode.

        - legacy: customer deployed their own app; all three live in secret_data.
        - multi_tenant: our global app (from settings); customer's tenant_id in secret_data.
        """
        mode = secret_data.get("auth_mode", "legacy")
        tenant_id = secret_data["tenant_id"]
        if mode == "multi_tenant":
            client_id = settings.AZURE_APP_CLIENT_ID
            client_secret = settings.AZURE_APP_CLIENT_SECRET
            if not client_id or not client_secret:
                raise RuntimeError(
                    "AZURE_APP_CLIENT_ID / AZURE_APP_CLIENT_SECRET not configured; "
                    "cannot use multi_tenant auth_mode."
                )
            return tenant_id, client_id, client_secret
        return tenant_id, secret_data["client_id"], secret_data["client_secret"]

    @classmethod
    def _create_client(cls, secret_data: dict, config: dict) -> CostManagementClient:
        tenant_id, client_id, client_secret = cls._resolve_credentials(secret_data)
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        subscription_id = config.get("subscription_id", secret_data.get("subscription_id", ""))
        return CostManagementClient(credential=credential, subscription_id=subscription_id)

    @staticmethod
    def _build_scope(secret_data: dict, config: dict) -> str:
        mode = config.get("collect_mode", "subscription")
        if mode == "subscription":
            sub_id = config.get("subscription_id") or secret_data.get("subscription_id")
            return f"subscriptions/{sub_id}"
        elif mode == "billing_account":
            billing_id = config.get("billing_account_id") or secret_data.get("billing_account_id")
            return f"providers/Microsoft.Billing/billingAccounts/{billing_id}"
        raise ValueError(f"Unknown collect_mode: {mode}")

    @staticmethod
    def _make_time_periods(start_date: str, end_date: str) -> list[dict]:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        today = datetime.utcnow()
        if end > today:
            end = today

        periods = []
        cur_y, cur_m = start.year, start.month
        end_y, end_m = end.year, end.month

        while (cur_y, cur_m) <= (end_y, end_m):
            p_start = datetime(cur_y, cur_m, 1)
            p_last = calendar.monthrange(cur_y, cur_m)[1]
            p_end = datetime(cur_y, cur_m, p_last, 23, 59, 59)
            if p_end > end:
                p_end = end
            periods.append({"start": p_start, "end": p_end})
            cur_m += 1
            if cur_m > 12:
                cur_m = 1
                cur_y += 1

        return periods

    @staticmethod
    def _generate_report(client, scope, start_date, end_date, metric="ActualCost", retry_count=3):
        parameters = GenerateCostDetailsReportRequestDefinition(
            metric=metric,
            time_period=CostDetailsTimePeriod(
                start=start_date.strftime("%Y-%m-%dT%H:%M:%S"),
                end=end_date.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        for attempt in range(1, retry_count + 1):
            try:
                poller = client.generate_cost_details_report.begin_create_operation(
                    scope=scope, parameters=parameters,
                )
                result = poller.result()
                return getattr(result, "blobs", None) or []
            except HttpResponseError as e:
                if e.status_code == 429 and attempt < retry_count:
                    wait = 60
                    matches = re.findall(r"\d+", str(e))
                    if len(matches) > 1:
                        wait = int(matches[1])
                    time.sleep(wait)
                    continue
                elif e.status_code in (404, 412):
                    return []
                raise
        return []

    @staticmethod
    def _download_and_parse(blob_link: str) -> list[dict]:
        with tempfile.TemporaryFile() as tmp:
            with requests.get(blob_link, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        tmp.write(chunk)
            tmp.seek(0)
            content = tmp.read()

        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(StringIO(text))
        records = []
        for row in reader:
            normalized = {k: (None if v in ("", "nan", "None") else v) for k, v in row.items()}
            records.append(normalized)
        return records

    @staticmethod
    def _map_record(raw: dict) -> dict | None:
        row = {k.lower(): v for k, v in raw.items()}
        billed_date = AzureCollector._parse_date(row.get("date"))
        if not billed_date:
            return None

        cost = AzureCollector._to_float(row.get("costinbillingcurrency", 0))
        usage_quantity = AzureCollector._to_float(row.get("quantity", 0))
        usage_type = str(row.get("metername", "") or "")
        usage_unit = str(row.get("unitofmeasure", "") or "")
        region = (row.get("resourcelocation", "") or "").strip().lower()
        region = REGION_MAP.get(region, region)

        # Product logic from original script
        charge_type = row.get("chargetype", "")
        pricing_model = row.get("pricingmodel", "")
        if charge_type in ("Purchase", "Refund") and pricing_model == "OnDemand":
            product = str(row.get("productname", "") or "")
        else:
            product = str(row.get("metercategory", "") or "")

        subscription_id = str(row.get("subscriptionid", "") or "")

        tags = {}
        tags_str = row.get("tags")
        if tags_str and isinstance(tags_str, str):
            try:
                s = tags_str.strip()
                if not s.startswith("{"):
                    s = "{" + s + "}"
                tags = {k: v for k, v in json.loads(s).items() if "." not in str(k)}
            except Exception:
                pass

        additional_info = {
            "Subscription Id": subscription_id,
            "Subscription Name": str(row.get("subscriptionname", "") or ""),
            "Charge Type": str(row.get("chargetype", "") or ""),
            "Pricing Model": str(row.get("pricingmodel", "") or ""),
        }

        return {
            "date": billed_date,
            "project_id": subscription_id or "Shared",
            "project_name": str(row.get("subscriptionname", "") or subscription_id),
            "product": product,
            "usage_type": usage_type,
            "region": region,
            "cost": cost,
            "usage_quantity": usage_quantity,
            "usage_unit": usage_unit,
            "currency": str(row.get("billingcurrency", "USD") or "USD"),
            "tags": tags,
            "additional_info": additional_info,
        }

    @staticmethod
    def _parse_date(val) -> str:
        try:
            if isinstance(val, int):
                return datetime.strptime(str(val), "%Y%m%d").strftime("%Y-%m-%d")
            elif isinstance(val, datetime):
                return val.strftime("%Y-%m-%d")
            elif isinstance(val, str):
                if len(val.split("/")) == 3:
                    return datetime.strptime(val, "%m/%d/%Y").strftime("%Y-%m-%d")
                return val[:10]
        except Exception:
            pass
        return str(val)[:10] if val else ""

    @staticmethod
    def _to_float(val) -> float:
        try:
            return 0.0 if val is None else float(str(val))
        except (ValueError, TypeError):
            return 0.0
