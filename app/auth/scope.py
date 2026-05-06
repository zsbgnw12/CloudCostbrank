"""Data-scope helpers — 角色驱动 + provider 命名约定。

数据范围只看 token 里的 roles(Casdoor 是单一权威源):
  - cloud_admin / cloud_ops             → 全量,无 provider 限制
  - cloud_<provider>(如 cloud_aws)     → 按命名约定提取 provider,限定为该 provider 的全部 cloud_account
  - 多角色叠加                          → provider 取并集
  - 都没有                              → 空(看不到任何数据)

加新云(阿里 / 甲骨文 / 火山等)的步骤:
  1. Casdoor 后台建 cloud_<新provider> 角色
  2. cloud_accounts 表加该 provider 的账号
  3. 加 collector 文件
  本文件 + 所有消费层代码完全不用改 — 通过命名约定自动识别。

API key 的 restricted_cloud_account_ids 仍然是叠加在 role 计算结果上的硬限制
(给三方对接窄化用,跟角色无关)。

`UserCloudAccountGrant` 表保留(schema 不删),但代码不再读取 — 数据范围完全
由 Casdoor 角色决定。如果未来真有"按云账号细分而非按 provider"的诉求,
可以重新启用 grants 作为额外限定层。
"""

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.models.cloud_account import CloudAccount


# 命名约定:cloud_<provider> 形式的 Casdoor 角色 → 数据范围限定为该 provider。
# cloud_admin / cloud_ops 不是 provider 角色(全量数据)。
_PROVIDER_ROLE_RE = re.compile(r"^cloud_([a-z0-9_]+)$")
_NON_PROVIDER_ROLES = {"cloud_admin", "cloud_ops"}
_FULL_ACCESS_ROLES = {"cloud_admin", "cloud_ops"}


def extract_providers_from_roles(roles) -> list[str]:
    """从 token roles 提取 provider 限定。

    >>> extract_providers_from_roles({"cloud_aws", "cloud_gcp", "engineer-l3"})
    ['aws', 'gcp']
    >>> extract_providers_from_roles({"cloud_admin"})
    []
    >>> extract_providers_from_roles({"cloud_taiji"})    # 加新云零代码改
    ['taiji']
    """
    out: set[str] = set()
    for r in (roles or []):
        if r in _NON_PROVIDER_ROLES:
            continue
        m = _PROVIDER_ROLE_RE.match(r)
        if m:
            out.add(m.group(1))
    return sorted(out)


def has_full_access(principal: Principal) -> bool:
    """Admin / ops 看全部数据,不受 provider 限制。"""
    return bool(set(principal.roles or []) & _FULL_ACCESS_ROLES)


def visible_providers(principal: Principal) -> list[str] | None:
    """返回该 principal 可见的 provider 列表。

    None  → 全量(admin / ops)
    list  → 限定到这些 provider(可能是空 = 没任何云管角色)
    """
    if has_full_access(principal):
        return None
    return extract_providers_from_roles(principal.roles)


async def visible_cloud_account_ids(
    db: AsyncSession,
    principal: Principal,
) -> list[int] | None:
    """Returns the visible cloud account ids for this principal.

    Rules:
      - admin / ops → None (全量)
      - cloud_<provider> 角色 → 该 provider 下所有 active cloud_account 的 id
      - 多 provider 角色 → 并集
      - API key 显式 restricted_cloud_account_ids → 跟角色结果取交集
    """
    restricted = (
        principal.restricted_cloud_account_ids
        if principal.method.value == "api_key"
        else None
    )

    if has_full_access(principal):
        if restricted is None:
            return None
        return sorted(set(restricted))

    providers = extract_providers_from_roles(principal.roles)
    if not providers:
        return []

    stmt = select(CloudAccount.id).where(CloudAccount.provider.in_(providers))
    base: set[int] = set((await db.execute(stmt)).scalars().all())
    if restricted is not None:
        base &= set(restricted)
    return sorted(base)


async def visible_data_source_ids(
    db: AsyncSession,
    principal: Principal,
) -> list[int] | None:
    """把可见 cloud_accounts 翻译成 data_source 列表。

    Returns None for full-access (admin/ops); empty list means "no visibility".
    """
    from app.models.data_source import DataSource  # local import to avoid cycles

    account_ids = await visible_cloud_account_ids(db, principal)
    if account_ids is None:
        return None
    if not account_ids:
        return []
    rows = (
        await db.execute(
            select(DataSource.id).where(DataSource.cloud_account_id.in_(account_ids))
        )
    ).scalars().all()
    return list(rows)


async def ensure_cloud_account_visible(
    db: AsyncSession,
    principal: Principal,
    cloud_account_id: int,
) -> None:
    """写操作前校验:目标 cloud_account 必须在用户范围内。"""
    visible = await visible_cloud_account_ids(db, principal)
    if visible is None:
        return
    if cloud_account_id not in visible:
        raise HTTPException(status_code=403, detail="Cloud account out of scope")


async def ensure_data_source_visible(
    db: AsyncSession,
    principal: Principal,
    data_source_id: int,
) -> None:
    """写操作前校验:目标 data_source 必须在用户范围内。"""
    visible = await visible_data_source_ids(db, principal)
    if visible is None:
        return
    if data_source_id not in visible:
        raise HTTPException(status_code=403, detail="Data source out of scope")


def ensure_provider_visible(principal: Principal, provider: str) -> None:
    """写操作前校验:目标 provider 必须在用户范围内。

    给那些"知道 provider 但不知道具体 cloud_account_id"的写操作用
    (比如 bills 按 provider 划分;或新建 cloud_account 时 body 带 provider)。
    """
    if has_full_access(principal):
        return
    if provider in extract_providers_from_roles(principal.roles):
        return
    raise HTTPException(status_code=403, detail=f"Provider '{provider}' out of scope")
