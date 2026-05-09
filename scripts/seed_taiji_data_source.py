"""
幂等地创建/更新 Taiji 数据源所需的 4 条记录:Supplier + SupplySource + CloudAccount + DataSource。

Pull 路径(主):每日由 Celery `sync_recent_days` 拉取 Azure Blob 上的预聚合 JSON
              文件 `{YYYY-MM-DD}_UTC+0.json`,详见 collectors/taiji_collector.py。

Push 路径(可选):taiji 主动 POST `/api/metering/taiji/ingest` 推送原始日志到
              billing_raw_taiji,实时重算 billing_summary;Push 与 Pull 互斥,
              开 Pull 时 Push 不应再供数(否则 project_id 主键约定不一致)。

使用方法(环境变量配置后执行):

    # 必填
    export DATABASE_URL=postgresql+asyncpg://...
    export AES_SECRET_KEY=...                  # 与在线一致(Fernet key)
    export TAIJI_BLOB_SAS_URL='https://<acc>.blob.core.windows.net/<container>?sp=r&sv=...&sr=c&sig=...'

    # 可选
    export TAIJI_TIMEZONE_TAG=UTC+0            # blob 文件名时区后缀,默认 UTC+0
    export TAIJI_SUPPLIER_NAME='Taiji AI 聚合平台'   # 默认此值
    export TAIJI_DS_ACTIVE=true                # 是否启用日常 Pull,默认 true

    python -m scripts.seed_taiji_data_source

重复执行安全:已存在的记录就地更新(凭据重加密覆盖),不重复插入。
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings  # noqa: F401 — env load 副作用
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


def _parse_bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "y")


def main():
    blob_sas_url = _env("TAIJI_BLOB_SAS_URL", required=True)
    timezone_tag = _env("TAIJI_TIMEZONE_TAG") or "UTC+0"
    supplier_name = _env("TAIJI_SUPPLIER_NAME") or "Taiji AI 聚合平台"
    ds_active = _parse_bool(_env("TAIJI_DS_ACTIVE"), default=True)

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

        # 2) SupplySource(provider=taiji)
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

        # 3) CloudAccount — Fernet 加密 secret_data
        secret_payload = {"blob_sas_url": blob_sas_url}
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
            print(f"[~] CloudAccount updated: ca.id={ca.id} (SAS 重加密覆盖)")

        # 4) DataSource — 配置精简,timezone_tag 之外都用默认
        ds_config = {"timezone_tag": timezone_tag}
        ds = session.execute(
            select(DataSource).where(DataSource.cloud_account_id == ca.id).limit(1)
        ).scalars().first()
        if not ds:
            ds = DataSource(
                name=f"ds-taiji-{sup.name}",
                cloud_account_id=ca.id,
                config=ds_config,
                is_active=ds_active,
            )
            session.add(ds)
            session.flush()
            print(f"[+] DataSource created: ds.id={ds.id} (is_active={ds_active})")
        else:
            ds.config = ds_config
            ds.is_active = ds_active
            session.flush()
            print(f"[~] DataSource updated: ds.id={ds.id} (config 刷新, is_active={ds_active})")

        session.commit()

    print("\n✓ Taiji 数据源 seed 完成。")
    print("  日常调度:Celery beat sync_recent_days 会每天滚动拉 [今天-6, 今天]。")
    print("  人工补历史:POST /api/sync/<ds_id>?start_month=YYYY-MM 或调 sync_data_source_by_dates。")


if __name__ == "__main__":
    main()
