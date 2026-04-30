"""AWS Cost Explorer billing collector — adapted from export_aws_cost.py."""

import logging

import boto3

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# Service name normalization mapping
SERVICE_NAME_MAP = {
    "EC2 - Other": "AmazonEC2",
    "Amazon Elastic Compute Cloud - Compute": "AmazonEC2",
    "Amazon Elastic Container Service for Kubernetes": "AmazonEKS",
}


class AWSCollector(BaseCollector):

    def collect_billing(self, secret_data: dict, config: dict, start_date: str, end_date: str) -> list[dict]:
        """
        Query AWS Cost Explorer for billing data.

        secret_data: {"aws_access_key_id": "...", "aws_secret_access_key": "...", "role_arn": null, "external_id": null}
        config: {"account_id": null, "end_date": null}
        """
        session = self._create_session(secret_data)
        account_id = config.get("account_id") or self._get_account_id(session)

        # CE end_date is exclusive
        from datetime import datetime, timedelta
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        ce_end = end_dt.strftime("%Y-%m-%d")

        ce = session.client("ce")
        query = {
            "TimePeriod": {"Start": start_date, "End": ce_end},
            "Granularity": "DAILY",
            "Metrics": ["UnblendedCost", "UsageQuantity"],
            "Filter": {
                "Dimensions": {
                    "Key": "LINKED_ACCOUNT",
                    "Values": [account_id],
                }
            },
            "GroupBy": [
                {"Type": "DIMENSION", "Key": "SERVICE"},
                {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
            ],
        }

        logger.info(f"AWS CE query: account={account_id} [{start_date} ~ {ce_end})")
        raw_rows = self._get_all_pages(ce, query)

        rows = []
        for raw in raw_rows:
            row = self._convert_row(raw, account_id)
            rows.append(row)

        logger.info(f"AWS returned {len(rows)} rows")
        return rows

    def collect_resources(self, secret_data: dict, config: dict) -> list[dict]:
        return []

    @staticmethod
    def _create_session(secret_data: dict) -> boto3.Session:
        session = boto3.Session(
            aws_access_key_id=secret_data["aws_access_key_id"],
            aws_secret_access_key=secret_data["aws_secret_access_key"],
            region_name="us-east-1",
        )
        # Validate credentials
        session.client("sts").get_caller_identity()

        role_arn = secret_data.get("role_arn")
        if not role_arn:
            return session

        sts = session.client("sts")
        assume_kwargs = {
            "RoleArn": role_arn,
            "RoleSessionName": "CloudCostSync",
        }
        if secret_data.get("external_id"):
            assume_kwargs["ExternalId"] = secret_data["external_id"]

        creds = sts.assume_role(**assume_kwargs)["Credentials"]
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name="us-east-1",
        )

    @staticmethod
    def _get_account_id(session: boto3.Session) -> str:
        return session.client("sts").get_caller_identity()["Account"]

    @staticmethod
    def _get_all_pages(ce_client, query: dict) -> list:
        all_rows = []
        next_token = None

        while True:
            if next_token:
                query["NextPageToken"] = next_token

            response = ce_client.get_cost_and_usage(**query)
            for time_result in response.get("ResultsByTime", []):
                time_period = time_result.get("TimePeriod", {})
                for group in time_result.get("Groups", []):
                    group["TimePeriod"] = time_period
                    all_rows.append(group)

            next_token = response.get("NextPageToken")
            if not next_token:
                break

        return all_rows

    @staticmethod
    def _convert_row(raw_group: dict, account_id: str) -> dict:
        keys = raw_group.get("Keys", [])
        service_raw = keys[0] if len(keys) > 0 else ""
        usage_type = keys[1] if len(keys) > 1 else ""
        product = SERVICE_NAME_MAP.get(service_raw, service_raw)

        metrics = raw_group.get("Metrics", {})
        cost_info = metrics.get("UnblendedCost", {})
        cost = float(cost_info.get("Amount", 0) or 0)

        usage_info = metrics.get("UsageQuantity", {})
        usage_quantity = float(usage_info.get("Amount", 0) or 0)
        usage_unit = usage_info.get("Unit", "")

        billed_date = raw_group.get("TimePeriod", {}).get("Start", "")

        # Extract region from usage_type prefix (e.g., USW2-BoxUsage:m5.large → us-west-2)
        region = ""
        if "-" in usage_type:
            prefix = usage_type.split("-")[0]
            region = prefix.lower()

        # invoice_month: 从 billed_date 推 YYYYMM(AWS 单日数据天然属于该日所在月发票)
        invoice_month = billed_date.replace("-", "")[:6] if billed_date else None

        return {
            "date": billed_date,
            "project_id": account_id,
            "project_name": account_id,
            "product": product,
            "usage_type": usage_type,
            "region": region,
            "cost": cost,
            "usage_quantity": usage_quantity,
            "usage_unit": usage_unit,
            "currency": "USD",
            # cost_type: AWS Cost Explorer 已合并 tax/adjustment 到 cost,无独立维度,统一 'regular'
            "cost_type": "regular",
            # billing_account_id: AWS 12 位账号 ID 就是财务账户主键
            "billing_account_id": account_id,
            "invoice_month": invoice_month,
            # AWS Cost Explorer 默认走 USD,无非 USD 计费场景;给 1.0 让聚合 SQL 用 cost_usd 时安全
            "currency_conversion_rate": 1.0,
            "tags": {},
            "additional_info": {"service_raw": service_raw},
        }
