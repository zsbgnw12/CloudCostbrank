"""008 supply_sources.label + 唯一约束 (supplier_id, provider, label)

- 标准云 aws/gcp/azure 的 label 为空串
- provider=other 时 label 为用户自定义名称，同一供应商下可有多条「其他」
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supply_sources",
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
    )
    op.drop_constraint("uq_supply_src_supplier_provider", "supply_sources", type_="unique")
    op.create_unique_constraint(
        "uq_supply_src_supplier_provider_label",
        "supply_sources",
        ["supplier_id", "provider", "label"],
    )
    op.alter_column("supply_sources", "label", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_supply_src_supplier_provider_label", "supply_sources", type_="unique")
    op.create_unique_constraint(
        "uq_supply_src_supplier_provider",
        "supply_sources",
        ["supplier_id", "provider"],
    )
    op.drop_column("supply_sources", "label")
