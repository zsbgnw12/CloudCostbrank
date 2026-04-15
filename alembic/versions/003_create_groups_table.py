"""Create service_account_groups table

Revision ID: 003
Revises: 002
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_account_groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "label", name="uq_group_provider_label"),
    )

    # Backfill: insert existing (provider, group_label) combinations from projects
    op.execute("""
        INSERT INTO service_account_groups (provider, label)
        SELECT DISTINCT provider, group_label
        FROM projects
        WHERE group_label IS NOT NULL
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("service_account_groups")
