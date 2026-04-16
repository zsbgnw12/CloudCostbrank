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
from app.auth.dependencies import require_module, require_roles

logger = logging.getLogger(__name__)

_DB_UNAVAILABLE = {
    "detail": (
        "数据库连接失败。请检查 DATABASE_URL 与网络；若为 Azure PostgreSQL，请在防火墙中允许当前公网 IP，"
        "并确认 .env 中 DATABASE_SSL=true。仅本地无 TLS 的 Postgres 使用 DATABASE_SSL=false。"
    )
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create missing tables on startup (safe: CREATE IF NOT EXISTS)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified / created.")
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


# ---------- Business routers (gated by ApiModulePermission) ----------
def _m(module: str):
    """Attach module switch gate as router-level dependency."""
    return [Depends(require_module(module))]


app.include_router(dashboard.router,        prefix="/api/dashboard",        tags=["Dashboard"],         dependencies=_m("dashboard"))
app.include_router(categories.router,       prefix="/api/categories",       tags=["Categories"],        dependencies=_m("categories"))
app.include_router(cloud_accounts.router,   prefix="/api/cloud-accounts",   tags=["Cloud Accounts"],    dependencies=_m("cloud_accounts"))
app.include_router(data_sources.router,     prefix="/api/data-sources",     tags=["Data Sources"],      dependencies=_m("data_sources"))
app.include_router(projects.router,         prefix="/api/projects",         tags=["Projects"],          dependencies=_m("projects"))
app.include_router(billing.router,          prefix="/api/billing",          tags=["Billing"],           dependencies=_m("billing"))
app.include_router(sync.router,             prefix="/api/sync",             tags=["Sync"],              dependencies=_m("sync"))
app.include_router(resources.router,        prefix="/api/resources",        tags=["Resources"],         dependencies=_m("resources"))
app.include_router(alerts.router,           prefix="/api/alerts",           tags=["Alerts"],            dependencies=_m("alerts"))
app.include_router(bills.router,            prefix="/api/bills",            tags=["Monthly Bills"],     dependencies=_m("bills"))
app.include_router(exchange_rates.router,   prefix="/api/exchange-rates",   tags=["Exchange Rates"],    dependencies=_m("exchange_rates"))
app.include_router(suppliers.router,        prefix="/api/suppliers",        tags=["Suppliers"],         dependencies=_m("suppliers"))
# Highly sensitive routers additionally require cloud_admin:
app.include_router(service_accounts.router, prefix="/api/service-accounts", tags=["Service Accounts"],  dependencies=_m("service_accounts") + [Depends(require_roles("cloud_admin"))])
app.include_router(azure_deploy.router,     prefix="/api/azure-deploy",     tags=["Azure Deploy"],      dependencies=_m("azure_deploy") + [Depends(require_roles("cloud_admin"))])
app.include_router(azure_consent.router,    prefix="/api/azure-consent",    tags=["Azure Consent"],     dependencies=_m("azure_consent") + [Depends(require_roles("cloud_admin"))])
# Consent callback — public (no auth), customer browser lands here after Microsoft redirect
app.include_router(azure_consent.callback_router, prefix="/api/azure-consent", tags=["Azure Consent Callback"])
app.include_router(metering.router,         prefix="/api/metering",         tags=["Metering"],          dependencies=_m("metering"))


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
