"""007 suppliers + supply_sources; projects 单一来源 supply_source_id

Revision ID: a1b2c3d4e5f6
Revises: 872adaccf0b2
Create Date: 2026-04-12

- 供应商 suppliers、货源 supply_sources（supplier_id + provider 唯一）
- projects：新增 supply_source_id，删除 provider/group_label（云类型与分组仅从货源/供应商来）
- 删除 service_account_groups（由 suppliers + supply_sources 替代）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "872adaccf0b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    # 避免与 SQLAlchemy metadata.create_all 已创建的表冲突
    if not insp.has_table("suppliers"):
        op.create_table(
            "suppliers",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not insp.has_table("supply_sources"):
        op.create_table(
            "supply_sources",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=10), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("supplier_id", "provider", name="uq_supply_src_supplier_provider"),
        )
        op.create_index("ix_supply_sources_supplier_id", "supply_sources", ["supplier_id"], unique=False)

    proj_cols = {c["name"] for c in insp.get_columns("projects")} if insp.has_table("projects") else set()
    if "supply_source_id" not in proj_cols:
        op.add_column("projects", sa.Column("supply_source_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_projects_supply_source_id",
            "projects",
            "supply_sources",
            ["supply_source_id"],
            ["id"],
        )

    # ── 数据回填 ─────────────────────────────────────────────
    conn = op.get_bind()

    conn.execute(sa.text("""
        INSERT INTO suppliers (name)
        SELECT DISTINCT x.n FROM (
            SELECT COALESCE(NULLIF(TRIM(group_label), ''), '未分组') AS n FROM projects
            UNION
            SELECT label AS n FROM service_account_groups
            UNION
            SELECT '其他货源' AS n
        ) AS x
        WHERE x.n IS NOT NULL
    """))

    conn.execute(sa.text("""
        INSERT INTO supply_sources (supplier_id, provider)
        SELECT s.id, pairs.p
        FROM (
            SELECT DISTINCT COALESCE(NULLIF(TRIM(group_label), ''), '未分组') AS lbl, provider AS p FROM projects
            UNION
            SELECT DISTINCT label AS lbl, provider AS p FROM service_account_groups
        ) AS pairs
        INNER JOIN suppliers s ON s.name = pairs.lbl
        WHERE NOT EXISTS (
            SELECT 1 FROM supply_sources ss
            WHERE ss.supplier_id = s.id AND ss.provider = pairs.p
        )
    """))

    # 自动发现 GCP 默认落在「其他货源」+ gcp，历史上可能无此组合行
    conn.execute(sa.text("""
        INSERT INTO supply_sources (supplier_id, provider)
        SELECT s.id, 'gcp'
        FROM suppliers s
        WHERE s.name = '其他货源'
          AND NOT EXISTS (
            SELECT 1 FROM supply_sources ss
            WHERE ss.supplier_id = s.id AND ss.provider = 'gcp'
          )
    """))

    conn.execute(sa.text("""
        UPDATE projects AS pr
        SET supply_source_id = ss.id
        FROM supply_sources ss
        INNER JOIN suppliers su ON su.id = ss.supplier_id
        WHERE su.name = COALESCE(NULLIF(TRIM(pr.group_label), ''), '未分组')
          AND ss.provider = pr.provider
    """))

    bad = conn.execute(sa.text("SELECT COUNT(*) FROM projects WHERE supply_source_id IS NULL")).scalar()
    if bad and bad > 0:
        raise RuntimeError(f"Migration: {bad} projects still have NULL supply_source_id")

    op.alter_column("projects", "supply_source_id", nullable=False)

    op.drop_constraint("uq_project_provider_ext_id", "projects", type_="unique")
    op.drop_index("ix_projects_ext_pid_provider", table_name="projects", if_exists=True)

    op.create_unique_constraint(
        "uq_project_supply_src_ext_id",
        "projects",
        ["supply_source_id", "external_project_id"],
    )
    op.create_index("ix_projects_supply_source_id", "projects", ["supply_source_id"], unique=False)

    op.drop_column("projects", "provider")
    op.drop_column("projects", "group_label")

    op.drop_table("service_account_groups")


def downgrade() -> None:
    op.add_column("projects", sa.Column("provider", sa.String(length=10), nullable=True))
    op.add_column("projects", sa.Column("group_label", sa.String(length=100), nullable=True))

    op.drop_constraint("uq_project_supply_src_ext_id", "projects", type_="unique")
    op.drop_index("ix_projects_supply_source_id", table_name="projects", if_exists=True)

    op.create_unique_constraint(
        "uq_project_provider_ext_id",
        "projects",
        ["provider", "external_project_id"],
    )
    op.create_index(
        "ix_projects_ext_pid_provider",
        "projects",
        ["external_project_id", "provider"],
        unique=False,
    )

    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE projects AS pr
        SET provider = ss.provider,
            group_label = su.name
        FROM supply_sources ss
        INNER JOIN suppliers su ON su.id = ss.supplier_id
        WHERE pr.supply_source_id = ss.id
    """))

    op.alter_column("projects", "provider", nullable=False)

    op.drop_constraint("fk_projects_supply_source_id", "projects", type_="foreignkey")
    op.drop_column("projects", "supply_source_id")

    op.create_table(
        "service_account_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "label", name="uq_group_provider_label"),
    )

    op.drop_index("ix_supply_sources_supplier_id", table_name="supply_sources")
    op.drop_table("supply_sources")
    op.drop_table("suppliers")
