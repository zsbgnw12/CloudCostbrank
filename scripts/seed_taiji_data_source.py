"""
幂等地创建/更新 Taiji 数据源所需的 4 条记录：Supplier + SupplySource + CloudAccount + DataSource。

使用方法（设置环境变量再跑）：

    export DATABASE_URL=postgresql+asyncpg://...   # 或用 SYNC_DATABASE_URL
    export AES_SECRET_KEY=...                      # 和在线一致（Fernet key）
    export TAIJI_API_BASE=https://api.taijiaicloud.com
    export TAIJI_ACCESS_TOKEN=<admin access token>
    export TAIJI_ADMIN_USER_ID=1                   # 可选
    export TAIJI_SUPPLIER_NAME='Taiji AI 聚合平台'  # 可选，默认此值
    export TAIJI_QUOTA_PER_USD=500000              # 可选，默认 500000

    python -m scripts.seed_taiji_data_source

重复执行是安全的：已存在的 Supplier / SupplySource / CloudAccount / DataSource
会被就地更新（凭据重加密覆盖），不重复插入。
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings  # noqa: F401 — ensure env-loading side effects
from app.services.sync_service import _get_sync_engine
from app.services.crypto_service import encrypt_dict
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.models.cloud_account import CloudAccount
from app.models.data_source import DataSource


def _env(key: str, required: bool = False, default: str | None = None) -> str | None:
    v = os.environ.get(key, default)
    if required and not v:
        print(f"ERROR: 缺少环境变量 {key}", file=sys.stderr)
        sys.exit(2)
    return v


def main():
    api_base = _env("TAIJI_API_BASE", required=True)
    access_token = _env("TAIJI_ACCESS_TOKEN", required=True)
    admin_user_id = _env("TAIJI_ADMIN_USER_ID") or ""
    supplier_name = _env("TAIJI_SUPPLIER_NAME") or "Taiji AI 聚合平台"
    quota_per_usd = int(_env("TAIJI_QUOTA_PER_USD") or "500000")

    engine = _get_sync_engine()
    with Session(engine) as session:
        # 1) Supplier
        sup = session.execute(
            select(Supplier).where(Supplier.name == supplier_name).limit(1)
        ).scalars().first()
        if not sup:
            sup = Supplier(name=supplier_name)
            session.add(sup)
            session.flush()
            print(f"[+] Supplier created: {sup.id} {sup.name}")
        else:
            print(f"[=] Supplier exists:  {sup.id} {sup.name}")

        # 2) SupplySource（provider=taiji）
        ss = session.execute(
            select(SupplySource).where(
                SupplySource.supplier_id == sup.id,
                SupplySource.provider == "taiji",
            ).limit(1)
        ).scalars().first()
        if not ss:
            ss = SupplySource(supplier_id=sup.id, provider="taiji")
            session.add(ss)
            session.flush()
            print(f"[+] SupplySource created: ss.id={ss.id} provider=taiji")
        else:
            print(f"[=] SupplySource exists:  ss.id={ss.id}")

        # 3) CloudAccount — 凭据 Fernet 加密存入 secret_data
        secret_payload = {
            "api_base": api_base.rstrip("/"),
            "access_token": access_token,
            "admin_user_id": admin_user_id,
        }
        encrypted = encrypt_dict(secret_payload)

        ca_name = f"taiji-{sup.name}"
        ca = session.execute(
            select(CloudAccount).where(CloudAccount.name == ca_name).limit(1)
        ).scalars().first()
        if not ca:
            ca = CloudAccount(name=ca_name, provider="taiji", secret_data=encrypted)
            session.add(ca)
            session.flush()
            print(f"[+] CloudAccount created: ca.id={ca.id} {ca_name}")
        else:
            ca.provider = "taiji"
            ca.secret_data = encrypted
            session.flush()
            print(f"[~] CloudAccount updated: ca.id={ca.id} (凭据重加密覆盖)")

        # 4) DataSource
        ds = session.execute(
            select(DataSource).where(DataSource.cloud_account_id == ca.id).limit(1)
        ).scalars().first()
        ds_config = {
            "quota_per_usd": quota_per_usd,
            "page_size": 200,
            "page_start": 1,  # Pull 才用到；默认 1-based
        }
        if not ds:
            ds = DataSource(
                name=f"ds-taiji-{sup.name}",
                cloud_account_id=ca.id,
                config=ds_config,
                # Push 模式下 is_active=False，防止 02:00 定时 Pull 误触发拉取；
                # 人工补历史时手动 UPDATE data_sources SET is_active=true。
                is_active=False,
            )
            session.add(ds)
            session.flush()
            print(f"[+] DataSource created: ds.id={ds.id} (is_active=False, Push 模式)")
        else:
            # 仅更新 config，不动 is_active（尊重运维当前状态）
            ds.config = ds_config
            session.flush()
            print(f"[~] DataSource updated: ds.id={ds.id} config refreshed (is_active 保持现状)")

        session.commit()

        ca_id = ca.id

    print(f"\n✓ Taiji 数据源 seed 完成。")
    print(f"  Push 模式下一步：")
    print(f"    1. 在 cloudcost UI /api/api-keys 创建一个 API Key")
    print(f"       - allowed_modules = ['metering']")
    print(f"       - allowed_cloud_account_ids = [{ca_id}]")
    print(f"    2. 把 X-API-Key 交给 taiji 团队配置 webhook")
    print(f"    3. taiji 推 POST /api/metering/taiji/ingest，body = {{logs: [...]}}")
    print(f"  Pull 后备：UPDATE data_sources SET is_active=true WHERE id=<ds.id>，")
    print(f"    然后 POST /api/sync/<ds_id>?start_month=YYYY-MM 补历史。")


if __name__ == "__main__":
    main()
