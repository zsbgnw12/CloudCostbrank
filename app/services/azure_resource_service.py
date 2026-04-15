"""Azure resource discovery service.

Lists subscriptions, resource groups, AI Foundry resources, available models,
and existing deployments using the user's ARM token (sent directly from frontend).
"""

import logging
from datetime import datetime, timezone

from azure.core.credentials import AccessToken
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.subscription import SubscriptionClient

logger = logging.getLogger(__name__)


class _UserTokenCredential:
    """Wrap a raw OAuth access_token into an Azure SDK credential."""

    def __init__(self, access_token: str, expires_on: int):
        self._token = AccessToken(access_token, expires_on)

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        return self._token


def _credential(arm_token: str, expires_on: int) -> _UserTokenCredential:
    return _UserTokenCredential(arm_token, expires_on)


# ------------------------------------------------------------------ #
#  Subscriptions
# ------------------------------------------------------------------ #

def list_subscriptions(arm_token: str, expires_on: int) -> list[dict]:
    cred = _credential(arm_token, expires_on)
    client = SubscriptionClient(cred)
    return [
        {
            "subscription_id": sub.subscription_id,
            "display_name": sub.display_name,
            "state": str(sub.state),
        }
        for sub in client.subscriptions.list()
        if str(sub.state) == "Enabled"
    ]


# ------------------------------------------------------------------ #
#  Resource Groups
# ------------------------------------------------------------------ #

def list_resource_groups(arm_token: str, expires_on: int,
                         subscription_id: str) -> list[dict]:
    cred = _credential(arm_token, expires_on)
    client = ResourceManagementClient(cred, subscription_id)
    return [
        {"name": rg.name, "location": rg.location}
        for rg in client.resource_groups.list()
    ]


# ------------------------------------------------------------------ #
#  Create Resource Group
# ------------------------------------------------------------------ #

def create_resource_group(arm_token: str, expires_on: int,
                          subscription_id: str, name: str,
                          location: str) -> dict:
    cred = _credential(arm_token, expires_on)
    client = ResourceManagementClient(cred, subscription_id)
    rg = client.resource_groups.create_or_update(
        name,
        {"location": location},
    )
    return {"name": rg.name, "location": rg.location}


# ------------------------------------------------------------------ #
#  Create AI Foundry Resource (Azure OpenAI)
# ------------------------------------------------------------------ #

def create_ai_resource(arm_token: str, expires_on: int,
                       subscription_id: str, resource_group: str,
                       name: str, location: str,
                       kind: str = "AIServices",
                       sku_name: str = "S0") -> dict:
    cred = _credential(arm_token, expires_on)
    cs_client = CognitiveServicesManagementClient(cred, subscription_id)
    poller = cs_client.accounts.begin_create(
        resource_group,
        name,
        {
            "location": location,
            "kind": kind,
            "sku": {"name": sku_name},
            "properties": {},
        },
    )
    account = poller.result()
    endpoint = None
    if account.properties and account.properties.endpoint:
        endpoint = account.properties.endpoint
    return {
        "name": account.name,
        "location": account.location,
        "resource_group": resource_group,
        "endpoint": endpoint,
        "existing_deployments": 0,
    }


# ------------------------------------------------------------------ #
#  AI Foundry Resources (Cognitive Services accounts of kind "OpenAI")
# ------------------------------------------------------------------ #

def list_ai_resources(arm_token: str, expires_on: int,
                      subscription_id: str, resource_group: str) -> list[dict]:
    cred = _credential(arm_token, expires_on)
    cs_client = CognitiveServicesManagementClient(cred, subscription_id)

    results = []
    for acc in cs_client.accounts.list_by_resource_group(resource_group):
        if acc.kind not in ("OpenAI", "AIServices"):
            continue

        deployment_count = 0
        try:
            deployments = list(cs_client.deployments.list(resource_group, acc.name))
            deployment_count = len(deployments)
        except Exception:
            logger.warning("Failed to count deployments for %s", acc.name)

        endpoint = None
        if acc.properties and acc.properties.endpoint:
            endpoint = acc.properties.endpoint

        results.append({
            "name": acc.name,
            "location": acc.location,
            "resource_group": resource_group,
            "endpoint": endpoint,
            "existing_deployments": deployment_count,
        })
    return results


# ------------------------------------------------------------------ #
#  Available Models (region-level, kept as fallback)
# ------------------------------------------------------------------ #

def list_models(arm_token: str, expires_on: int,
                subscription_id: str, region: str) -> list[dict]:
    cred = _credential(arm_token, expires_on)
    cs_client = CognitiveServicesManagementClient(cred, subscription_id)

    results = []
    for model in cs_client.models.list(location=region):
        if not model.model:
            continue
        m = model.model
        skus = []
        max_cap = None
        raw_skus = getattr(model, "skus", None) or getattr(m, "skus", None) or []
        for sku in raw_skus:
            sku_name = getattr(sku, "name", None)
            if sku_name:
                skus.append(sku_name)
            cap_obj = getattr(sku, "capacity", None)
            if cap_obj:
                cap_max = getattr(cap_obj, "maximum", None)
                if cap_max is not None:
                    if max_cap is None or cap_max > max_cap:
                        max_cap = cap_max

        capabilities = []
        raw_caps = getattr(m, "capabilities", None)
        if raw_caps:
            try:
                capabilities = [
                    k for k, v in raw_caps.items()
                    if v and str(v).lower() == "true"
                ]
            except Exception:
                pass

        model_format = getattr(m, "format", "") or ""
        results.append({
            "model_name": m.name,
            "model_version": m.version or "",
            "model_format": model_format,
            "capabilities": capabilities,
            "available_skus": skus,
            "max_capacity": max_cap,
        })
    return results


# ------------------------------------------------------------------ #
#  Available Models (account-level — same as Azure portal "Deploy base model")
# ------------------------------------------------------------------ #

def list_account_models(arm_token: str, expires_on: int,
                        subscription_id: str, resource_group: str,
                        account_name: str) -> list[dict]:
    """Return all models deployable to a specific AI Foundry account.

    Uses ``accounts.list_models()`` which mirrors the Azure portal's
    "Deploy base model" dialog and returns 100+ models.
    """
    cred = _credential(arm_token, expires_on)
    cs_client = CognitiveServicesManagementClient(cred, subscription_id)

    results = []
    for m in cs_client.accounts.list_models(resource_group, account_name):
        model_name = getattr(m, "name", None)
        if not model_name:
            continue

        model_format = getattr(m, "format", "") or ""
        model_version = getattr(m, "version", "") or ""

        skus = []
        max_cap = None
        raw_skus = getattr(m, "skus", None) or []
        for sku in raw_skus:
            sku_name = getattr(sku, "name", None)
            if sku_name:
                skus.append(sku_name)
            cap_obj = getattr(sku, "capacity", None)
            if cap_obj:
                cap_max = getattr(cap_obj, "maximum", None)
                if cap_max is not None:
                    if max_cap is None or cap_max > max_cap:
                        max_cap = cap_max

        capabilities = []
        raw_caps = getattr(m, "capabilities", None)
        if raw_caps:
            try:
                capabilities = [
                    k for k, v in raw_caps.items()
                    if v and str(v).lower() == "true"
                ]
            except Exception:
                pass

        lifecycle = getattr(m, "lifecycle_status", "") or ""
        is_deprecated = lifecycle in ("Deprecated",)

        results.append({
            "model_name": model_name,
            "model_version": model_version,
            "model_format": model_format,
            "capabilities": capabilities,
            "available_skus": skus,
            "max_capacity": max_cap,
            "lifecycle_status": lifecycle,
            "is_deprecated": is_deprecated,
        })
    return results


# ------------------------------------------------------------------ #
#  Existing Deployments
# ------------------------------------------------------------------ #

def list_existing_deployments(arm_token: str, expires_on: int,
                              subscription_id: str, resource_group: str,
                              account_name: str) -> list[dict]:
    cred = _credential(arm_token, expires_on)
    cs_client = CognitiveServicesManagementClient(cred, subscription_id)

    results = []
    for dep in cs_client.deployments.list(resource_group, account_name):
        model_name = ""
        model_version = ""
        if dep.properties and dep.properties.model:
            model_name = dep.properties.model.name or ""
            model_version = dep.properties.model.version or ""

        sku_name = dep.sku.name if dep.sku else None
        sku_capacity = dep.sku.capacity if dep.sku else None
        prov_state = dep.properties.provisioning_state if dep.properties else None

        results.append({
            "deployment_name": dep.name,
            "model_name": model_name,
            "model_version": model_version,
            "sku_name": sku_name,
            "sku_capacity": sku_capacity,
            "provisioning_state": prov_state,
        })
    return results


# ------------------------------------------------------------------ #
#  Deploy export (endpoint, keys, RPM/TPM for Excel)
# ------------------------------------------------------------------ #

def _per_minute_throttle_count(rule) -> float | None:
    """Interpret ThrottlingRule.count as a per-minute limit when possible."""
    if rule is None or rule.count is None:
        return None
    c = float(rule.count)
    rp = rule.renewal_period
    if rp is None or rp <= 0:
        return c
    # renewal_period is in seconds; scale to a 60s window
    if abs(rp - 60.0) < 1.0:
        return c
    return c * (60.0 / rp)


def _rpm_tpm_from_deployment(dep) -> tuple[int | None, int | None]:
    """Read RPM/TPM from deployment rateLimits; fallback to SKU capacity as TPM (×1000)."""
    rpm: int | None = None
    tpm: int | None = None
    props = dep.properties if dep else None
    if props and props.rate_limits:
        for rule in props.rate_limits:
            k = (rule.key or "").lower()
            val = _per_minute_throttle_count(rule)
            if val is None:
                continue
            iv = int(round(val))
            if "token" in k:
                tpm = iv
            elif "request" in k:
                rpm = iv
    if tpm is None and dep and dep.sku and dep.sku.capacity is not None:
        try:
            tpm = int(dep.sku.capacity) * 1000
        except (TypeError, ValueError):
            pass
    return rpm, tpm


def fetch_deploy_export_rows(
    arm_token: str,
    expires_on: int,
    subscription_id: str,
    task_items: list[dict],
) -> list[dict[str, str | int | None]]:
    """One row per task item: deployment endpoint URL, key, RPM, TPM (for Excel export)."""
    cred = _credential(arm_token, expires_on)
    cs_client = CognitiveServicesManagementClient(cred, subscription_id)

    account_cache: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    rows: list[dict[str, str | int | None]] = []

    for item in task_items:
        rg = item["resource_group"]
        account = item["account_name"]
        dep_name = item["deployment_name"]
        cache_key = (rg, account)

        if cache_key not in account_cache:
            acc = cs_client.accounts.get(rg, account)
            ep = None
            if acc.properties and acc.properties.endpoint:
                ep = acc.properties.endpoint
            keys = cs_client.accounts.list_keys(rg, account)
            key = keys.key1 if keys and keys.key1 else None
            account_cache[cache_key] = (ep, key)
        base_endpoint, api_key = account_cache[cache_key]

        deployment_url = ""
        if base_endpoint:
            base = base_endpoint.rstrip("/")
            deployment_url = f"{base}/openai/deployments/{dep_name}"

        rpm: int | None = None
        tpm: int | None = None
        try:
            dep = cs_client.deployments.get(rg, account, dep_name)
            rpm, tpm = _rpm_tpm_from_deployment(dep)
        except Exception as e:
            logger.warning("Export: could not get deployment %s/%s: %s", account, dep_name, e)

        if tpm is None:
            cap = item.get("sku_capacity")
            try:
                tpm = int(cap) * 1000 if cap is not None else None
            except (TypeError, ValueError):
                tpm = None

        rows.append({
            "deployment_name": dep_name,
            "endpoint": deployment_url or (base_endpoint or ""),
            "api_key": api_key or "",
            "rpm": rpm,
            "tpm": tpm,
        })

    return rows
