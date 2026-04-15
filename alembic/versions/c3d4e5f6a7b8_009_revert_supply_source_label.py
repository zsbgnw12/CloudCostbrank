"""009 撤销 008：恢复 (supplier_id, provider) 唯一约束，删除 label 列

删除 provider=other 的货源；若其下仍有服务账号，会先清理关联计费/日志等再删项目。
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 找出「其他」货源下的 data_source / project
    ds_rows = conn.execute(
        sa.text("""
            SELECT DISTINCT p.data_source_id
            FROM projects p
            INNER JOIN supply_sources ss ON p.supply_source_id = ss.id
            WHERE ss.provider = 'other' AND p.data_source_id IS NOT NULL
        """)
    ).fetchall()
    ds_ids = [r[0] for r in ds_rows if r[0] is not None]

    if ds_ids:
        placeholders = ", ".join(str(int(i)) for i in ds_ids)
        conn.execute(sa.text(f"DELETE FROM billing_daily_summary WHERE data_source_id IN ({placeholders})"))
        conn.execute(sa.text(f"DELETE FROM billing_data WHERE data_source_id IN ({placeholders})"))
        conn.execute(sa.text(f"DELETE FROM token_usage WHERE data_source_id IN ({placeholders})"))
        conn.execute(sa.text(f"DELETE FROM resource_inventory WHERE data_source_id IN ({placeholders})"))
        conn.execute(sa.text(f"DELETE FROM sync_logs WHERE data_source_id IN ({placeholders})"))

    conn.execute(
        sa.text("""
            DELETE FROM project_assignment_logs WHERE project_id IN (
                SELECT p.id FROM projects p
                INNER JOIN supply_sources ss ON p.supply_source_id = ss.id
                WHERE ss.provider = 'other'
            )
        """)
    )

    conn.execute(
        sa.text("""
            DELETE FROM projects WHERE supply_source_id IN (
                SELECT id FROM supply_sources WHERE provider = 'other'
            )
        """)
    )

    # 孤儿 data_sources（曾挂「其他」项目）
    if ds_ids:
        placeholders = ", ".join(str(int(i)) for i in ds_ids)
        ca_rows = conn.execute(
            sa.text(f"SELECT DISTINCT cloud_account_id FROM data_sources WHERE id IN ({placeholders})")
        ).fetchall()
        ca_ids = [r[0] for r in ca_rows if r[0] is not None]

        conn.execute(sa.text(f"DELETE FROM data_sources WHERE id IN ({placeholders})"))

        for caid in ca_ids:
            n = conn.execute(
                sa.text("SELECT COUNT(*) FROM data_sources WHERE cloud_account_id = :caid"),
                {"caid": caid},
            ).scalar_one()
            if n == 0:
                conn.execute(sa.text("DELETE FROM cloud_accounts WHERE id = :caid"), {"caid": caid})

    # 无项目的 other 货源
    conn.execute(sa.text("DELETE FROM supply_sources WHERE provider = 'other'"))

    op.drop_constraint("uq_supply_src_supplier_provider_label", "supply_sources", type_="unique")
    op.drop_column("supply_sources", "label")
    op.create_unique_constraint(
        "uq_supply_src_supplier_provider",
        "supply_sources",
        ["supplier_id", "provider"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_supply_src_supplier_provider", "supply_sources", type_="unique")
    op.add_column(
        "supply_sources",
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
    )
    op.create_unique_constraint(
        "uq_supply_src_supplier_provider_label",
        "supply_sources",
        ["supplier_id", "provider", "label"],
    )
    op.alter_column("supply_sources", "label", server_default=None)
