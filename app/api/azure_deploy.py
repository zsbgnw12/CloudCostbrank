"""Azure AI model batch deployment API routes.

Auth: Frontend sends ARM token directly (obtained via MSAL popup login
with scope ``https://management.azure.com/user_impersonation``).
No OBO exchange — the Bearer token IS the ARM token.
"""

import io
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas.azure_deploy import (
    AIResourceItem,
    AzureUserInfo,
    CreateAIResourceRequest,
    CreateResourceGroupRequest,
    ExistingDeploymentItem,
    ExecuteRequest,
    ExecuteResponse,
    ModelItem,
    MsalConfig,
    PlanRequest,
    PlanResponse,
    ProgressResponse,
    ResourceGroupItem,
    RetryResponse,
    SubscriptionItem,
)
from app.services import azure_auth_service, azure_deploy_service, azure_resource_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _extract_arm_token(authorization: str | None) -> tuple[str, int]:
    """Extract the ARM token from the Authorization header and return (token, expires_on)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization Bearer token")
    arm_token = authorization[7:]
    try:
        claims = azure_auth_service.decode_arm_token(arm_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return arm_token, claims["expires_on"]


# ================================================================== #
#  Auth
# ================================================================== #

@router.get("/auth/config", response_model=MsalConfig)
async def auth_config():
    """Return MSAL config for frontend initialization (no auth required)."""
    return azure_auth_service.get_msal_config()


@router.post("/auth/validate", response_model=AzureUserInfo)
async def auth_validate(authorization: str | None = Header(None)):
    """Validate the ARM token and return user info."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization Bearer token")
    try:
        claims = azure_auth_service.decode_arm_token(authorization[7:])
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return {
        "name": claims["name"],
        "email": claims["email"],
        "tenant_id": claims["tenant_id"],
    }


# ================================================================== #
#  Resource Discovery
# ================================================================== #

@router.get("/subscriptions", response_model=list[SubscriptionItem])
async def list_subscriptions(authorization: str | None = Header(None)):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.list_subscriptions(arm_token, expires_on)
    except Exception as e:
        logger.error("Failed to list subscriptions: %s", e)
        raise HTTPException(status_code=502, detail=f"获取订阅列表失败: {e}")


@router.get("/resource-groups", response_model=list[ResourceGroupItem])
async def list_resource_groups(
    subscription_id: str = Query(...),
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.list_resource_groups(arm_token, expires_on, subscription_id)
    except Exception as e:
        logger.error("Failed to list resource groups: %s", e)
        raise HTTPException(status_code=502, detail=f"获取资源组列表失败: {e}")


@router.post("/resource-groups", response_model=ResourceGroupItem)
async def create_resource_group(
    body: CreateResourceGroupRequest,
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.create_resource_group(
            arm_token, expires_on, body.subscription_id, body.name, body.location,
        )
    except Exception as e:
        logger.error("Failed to create resource group: %s", e)
        raise HTTPException(status_code=502, detail=f"创建资源组失败: {e}")


@router.post("/ai-resources", response_model=AIResourceItem)
async def create_ai_resource(
    body: CreateAIResourceRequest,
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.create_ai_resource(
            arm_token, expires_on, body.subscription_id,
            body.resource_group, body.name, body.location,
            body.kind, body.sku_name,
        )
    except Exception as e:
        logger.error("Failed to create AI resource: %s", e)
        raise HTTPException(status_code=502, detail=f"创建 AI 资源失败: {e}")


@router.get("/ai-resources", response_model=list[AIResourceItem])
async def list_ai_resources(
    subscription_id: str = Query(...),
    resource_group: str = Query(...),
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.list_ai_resources(
            arm_token, expires_on, subscription_id, resource_group,
        )
    except Exception as e:
        logger.error("Failed to list AI resources: %s", e)
        raise HTTPException(status_code=502, detail=f"获取 AI Foundry 资源列表失败: {e}")


@router.get("/models", response_model=list[ModelItem])
async def list_models(
    subscription_id: str = Query(...),
    region: str = Query(...),
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.list_models(arm_token, expires_on, subscription_id, region)
    except Exception as e:
        logger.error("Failed to list models: %s", e)
        raise HTTPException(status_code=502, detail=f"获取模型列表失败: {e}")


@router.get("/account-models", response_model=list[ModelItem])
async def list_account_models(
    subscription_id: str = Query(...),
    resource_group: str = Query(...),
    account_name: str = Query(...),
    authorization: str | None = Header(None),
):
    """List all models deployable to a specific AI Foundry account
    (same as Azure portal 'Deploy base model')."""
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.list_account_models(
            arm_token, expires_on, subscription_id, resource_group, account_name
        )
    except Exception as e:
        logger.error("Failed to list account models: %s", e)
        raise HTTPException(status_code=502, detail=f"获取账户模型列表失败: {e}")


@router.get("/existing-deployments", response_model=list[ExistingDeploymentItem])
async def list_existing_deployments(
    subscription_id: str = Query(...),
    resource_group: str = Query(...),
    account_name: str = Query(...),
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        return azure_resource_service.list_existing_deployments(
            arm_token, expires_on, subscription_id, resource_group, account_name,
        )
    except Exception as e:
        logger.error("Failed to list existing deployments: %s", e)
        raise HTTPException(status_code=502, detail=f"获取已有部署列表失败: {e}")


# ================================================================== #
#  Plan (pre-check)
# ================================================================== #

@router.post("/plan", response_model=PlanResponse)
async def plan_deployment(
    body: PlanRequest,
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        result = await azure_deploy_service.plan(
            arm_token, expires_on,
            body.subscription_id,
            [item.model_dump() for item in body.items],
        )
        return result
    except Exception as e:
        logger.error("Plan failed: %s", e)
        raise HTTPException(status_code=502, detail=f"预检失败: {e}")


# ================================================================== #
#  Execute
# ================================================================== #

@router.post("/execute", response_model=ExecuteResponse)
async def execute_deployment(
    body: ExecuteRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)

    items = [item.model_dump() for item in body.items]
    task_id = await azure_deploy_service.start_deployment(
        arm_token, expires_on, body.subscription_id, items,
    )

    background_tasks.add_task(azure_deploy_service.execute, task_id)

    return ExecuteResponse(
        task_id=task_id,
        total=len(items),
        message="部署任务已启动",
    )


# ================================================================== #
#  Progress
# ================================================================== #

@router.get("/progress/{task_id}", response_model=ProgressResponse)
async def get_progress(
    task_id: str,
    authorization: str | None = Header(None),
):
    """与导出等接口一致，需有效 ARM Bearer，避免仅凭 task_id 枚举任务详情。"""
    _extract_arm_token(authorization)
    result = await azure_deploy_service.get_progress(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return result


@router.get("/export/{task_id}")
async def export_deploy_excel(
    task_id: str,
    authorization: str | None = Header(None),
):
    """Download Excel with 部署名 / 终结点 / 密钥 / RPM / TPM for each row in the task."""
    arm_token, expires_on = _extract_arm_token(authorization)
    try:
        data, fname = await azure_deploy_service.build_export_xlsx_bytes(
            arm_token, expires_on, task_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Export deploy Excel failed")
        raise HTTPException(status_code=502, detail=f"导出失败: {e}") from e

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ================================================================== #
#  Retry
# ================================================================== #

@router.post("/retry/{task_id}", response_model=RetryResponse)
async def retry_failed(
    task_id: str,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
):
    arm_token, expires_on = _extract_arm_token(authorization)

    try:
        retrying = await azure_deploy_service.retry_failed(task_id, arm_token, expires_on)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    background_tasks.add_task(azure_deploy_service.execute, task_id)

    return RetryResponse(
        task_id=task_id,
        retrying=retrying,
        message=f"正在重试 {retrying} 个失败项",
    )
