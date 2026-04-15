"""Pydantic schemas for Azure AI model batch deployment."""

from pydantic import BaseModel, Field


# ---- Auth ----

class MsalConfig(BaseModel):
    client_id: str
    authority: str
    redirect_uri: str
    scopes: list[str]


class AzureUserInfo(BaseModel):
    name: str
    email: str
    tenant_id: str


# ---- Resource Discovery ----

class SubscriptionItem(BaseModel):
    subscription_id: str
    display_name: str
    state: str


class ResourceGroupItem(BaseModel):
    name: str
    location: str


class AIResourceItem(BaseModel):
    name: str
    location: str
    resource_group: str
    endpoint: str | None = None
    existing_deployments: int = 0


class ModelItem(BaseModel):
    model_name: str
    model_version: str
    model_format: str = ""
    capabilities: list[str] = []
    available_skus: list[str] = []
    max_capacity: int | None = None
    lifecycle_status: str = ""
    is_deprecated: bool = False


class ExistingDeploymentItem(BaseModel):
    deployment_name: str
    model_name: str
    model_version: str
    sku_name: str | None = None
    sku_capacity: int | None = None
    provisioning_state: str | None = None


# ---- Resource Creation ----

class CreateResourceGroupRequest(BaseModel):
    subscription_id: str
    name: str = Field(..., min_length=1, max_length=90)
    location: str


class CreateAIResourceRequest(BaseModel):
    subscription_id: str
    resource_group: str
    name: str = Field(..., min_length=2, max_length=64)
    location: str
    kind: str = "AIServices"
    sku_name: str = "S0"


# ---- Plan (Pre-check) ----

class DeployItem(BaseModel):
    resource_group: str
    account_name: str
    region: str
    model_name: str
    model_version: str
    model_format: str = "OpenAI"
    deployment_name: str
    sku_name: str = "GlobalStandard"
    sku_capacity: int = 10


class PlanRequest(BaseModel):
    subscription_id: str
    items: list[DeployItem]


class PlanResultItem(BaseModel):
    index: int
    action: str  # create / skip / conflict / unavailable / quota_risk
    message: str | None = None


class PlanResponse(BaseModel):
    total: int
    can_create: int = 0
    will_skip: int = 0
    has_conflict: int = 0
    unavailable: int = 0
    quota_risk: int = 0
    items: list[PlanResultItem]


# ---- Execute ----

class ExecuteItem(BaseModel):
    resource_group: str
    account_name: str
    region: str
    model_name: str
    model_version: str
    model_format: str = "OpenAI"
    deployment_name: str
    sku_name: str = "GlobalStandard"
    sku_capacity: int = 10
    action: str = "create"


class ExecuteRequest(BaseModel):
    subscription_id: str
    items: list[ExecuteItem]


class ExecuteResponse(BaseModel):
    task_id: str
    total: int
    message: str = "部署任务已启动"


# ---- Progress ----

class ProgressItem(BaseModel):
    index: int
    model_name: str
    region: str
    account_name: str
    deployment_name: str
    status: str  # pending / deploying / succeeded / failed
    error: str | None = None


class ProgressResponse(BaseModel):
    task_id: str
    status: str  # pending / running / completed
    total: int
    succeeded: int = 0
    failed: int = 0
    deploying: int = 0
    pending: int = 0
    items: list[ProgressItem]


# ---- Retry ----

class RetryResponse(BaseModel):
    task_id: str
    retrying: int
    message: str
