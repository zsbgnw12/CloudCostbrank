"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config import settings
from app.database import engine
import app.models  # noqa: F401  — ensure ALL models are registered with Base
from app.database import Base
from app.api import (
    categories,
    cloud_accounts,
    data_sources,
    projects,
    billing,
    sync,
    resources,
    alerts,
    bills,
    exchange_rates,
    dashboard,
    service_accounts,
    suppliers,  # noqa: F401 — used in include_router below
    azure_deploy,
    azure_consent,
    metering,
    admin_users,
    api_permissions,
    api_keys,
)
from app.auth import router as auth_router_module
from app.auth.middleware import AuthMiddleware
from app.auth.dependencies import require_module, require_roles, require_cloud_role

logger = logging.getLogger(__name__)

_DB_UNAVAILABLE = {
    "detail": (
        "数据库连接失败。请检查 DATABASE_URL 与网络；若为 Azure PostgreSQL，请在防火墙中允许当前公网 IP，"
        "并确认 .env 中 DATABASE_SSL=true。仅本地无 TLS 的 Postgres 使用 DATABASE_SSL=false。"
    )
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — table creation is owned by alembic; do not create_all here.

    Phase 1 migration removed the previous `Base.metadata.create_all` call because
    `billing_summary` is a partition table and `create_all` would emit plain-table
    DDL that desyncs ORM metadata from DB structure. All schema changes go through
    alembic upgrade.
    """
    logger.info("Application started; schema is managed by alembic.")
    yield


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

_cors_raw = (settings.CORS_ORIGINS or "").strip()
if _cors_raw:
    _cors_list = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    _cors_allow_credentials = bool(_cors_list)
    _cors_origins = _cors_list or ["*"]
else:
    _cors_allow_credentials = False
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# AuthMiddleware: parse credentials & attach Principal to request.state.
# NOTE: Starlette adds middleware outer-first, so AuthMiddleware actually runs
# INSIDE CORSMiddleware here — which is what we want (CORS preflight remains
# anonymous-friendly, auth runs on real requests).
app.add_middleware(AuthMiddleware)


@app.exception_handler(OperationalError)
async def operational_error_handler(request: Request, exc: OperationalError):
    """DB 不可达 / 连接被重置时返回 503，避免笼统 500。"""
    logger.exception("Database operational error: %s", exc)
    return JSONResponse(status_code=503, content=_DB_UNAVAILABLE)


@app.exception_handler(ConnectionResetError)
async def connection_reset_error_handler(request: Request, exc: ConnectionResetError):
    """asyncpg 等在 SSL/握手阶段被远端 RST 时常抛出，未必包装为 OperationalError。"""
    logger.exception("Database connection reset: %s", exc)
    return JSONResponse(status_code=503, content=_DB_UNAVAILABLE)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    """Convert unique-constraint / FK violations to user-friendly 409 responses."""
    orig = exc.orig
    detail = str(orig) if orig else str(exc)
    pgc = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if pgc == "23505" or "unique" in detail.lower() or "duplicate" in detail.lower():
        return JSONResponse(status_code=409, content={"detail": "Record already exists (unique constraint violation)"})
    if pgc == "23503" or "foreign key" in detail.lower():
        return JSONResponse(status_code=409, content={"detail": "Referenced record conflict (foreign key violation)"})
    logger.error("Unhandled IntegrityError: %s", detail)
    return JSONResponse(status_code=500, content={"detail": "Database integrity error"})


# ---------- Auth + admin routers (no module gating) ----------
app.include_router(auth_router_module.router, prefix="/api/auth", tags=["Auth"])
app.include_router(admin_users.router, prefix="/api/admin/users", tags=["Admin - Users"])
app.include_router(api_permissions.router, prefix="/api/api-permissions", tags=["Admin - Module Switches"])
app.include_router(api_keys.router, prefix="/api/api-keys", tags=["API Keys"])


# ---------- Business routers (gated by ApiModulePermission + cloud role) ----------
def _m(module: str):
    """Attach module switch gate as router-level dependency."""
    return [Depends(require_module(module))]


def _cloud(module: str):
    """Module gate + 任何"云管角色"(cloud_admin / cloud_ops / cloud_<provider>)。

    数据范围限定由各 endpoint 内部用 visible_cloud_account_ids /
    ensure_provider_visible 实现。这一层只做"是不是云管成员"的粗筛。
    """
    return _m(module) + [Depends(require_cloud_role())]


def _admin(module: str):
    """Module gate + 仅 cloud_admin 可调(系统管理 / 全局元数据)。"""
    return _m(module) + [Depends(require_roles("cloud_admin"))]


# ── 任何云管角色都能进的业务路由(数据范围由 endpoint 内部限定到自己的 provider)
app.include_router(dashboard.router,        prefix="/api/dashboard",        tags=["Dashboard"],         dependencies=_cloud("dashboard"))
app.include_router(cloud_accounts.router,   prefix="/api/cloud-accounts",   tags=["Cloud Accounts"],    dependencies=_cloud("cloud_accounts"))
app.include_router(data_sources.router,     prefix="/api/data-sources",     tags=["Data Sources"],      dependencies=_cloud("data_sources"))
app.include_router(projects.router,         prefix="/api/projects",         tags=["Projects"],          dependencies=_cloud("projects"))
app.include_router(billing.router,          prefix="/api/billing",          tags=["Billing"],           dependencies=_cloud("billing"))
app.include_router(sync.router,             prefix="/api/sync",             tags=["Sync"],              dependencies=_cloud("sync"))
app.include_router(resources.router,        prefix="/api/resources",        tags=["Resources"],         dependencies=_cloud("resources"))
app.include_router(alerts.router,           prefix="/api/alerts",           tags=["Alerts"],            dependencies=_cloud("alerts"))
app.include_router(bills.router,            prefix="/api/bills",            tags=["Monthly Bills"],     dependencies=_cloud("bills"))
app.include_router(service_accounts.router, prefix="/api/service-accounts", tags=["Service Accounts"],  dependencies=_cloud("service_accounts"))
app.include_router(metering.router,         prefix="/api/metering",         tags=["Metering"],          dependencies=_cloud("metering"))

# ── 全局元数据 / 跨云配置 — router 级开放给所有云管角色(读),
# 写操作(POST/PUT/PATCH/DELETE)在各 router 内部单独 require_roles("cloud_admin")。
# 之所以读不锁 admin:前端"添加云账号"对话框需要拉货源/渠道下拉选项,云角色用户也需要看。
app.include_router(categories.router,       prefix="/api/categories",       tags=["Categories"],        dependencies=_cloud("categories"))
app.include_router(suppliers.router,        prefix="/api/suppliers",        tags=["Suppliers"],         dependencies=_cloud("suppliers"))
app.include_router(exchange_rates.router,   prefix="/api/exchange-rates",   tags=["Exchange Rates"],    dependencies=_cloud("exchange_rates"))

# ── Azure 部署 / 跨租户授权 — admin + ops + cloud_azure(Azure 专属功能,该云运维理应能用)
# cloud_aws / cloud_gcp / cloud_taiji 故意不放(Azure 跟他们无关)
app.include_router(azure_deploy.router,     prefix="/api/azure-deploy",     tags=["Azure Deploy"],      dependencies=_m("azure_deploy") + [Depends(require_roles("cloud_admin", "cloud_ops", "cloud_azure"))])
app.include_router(azure_consent.router,    prefix="/api/azure-consent",    tags=["Azure Consent"],     dependencies=_m("azure_consent") + [Depends(require_roles("cloud_admin", "cloud_ops", "cloud_azure"))])
# Consent callback — public (no auth), customer browser lands here after Microsoft redirect
app.include_router(azure_consent.callback_router, prefix="/api/azure-consent", tags=["Azure Consent Callback"])


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
