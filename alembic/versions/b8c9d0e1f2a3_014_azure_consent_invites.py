"""014: create azure_consent_invites table

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "azure_consent_invites",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("account_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("cloud_account_id", sa.BigInteger(), sa.ForeignKey("cloud_accounts.id"), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("error_reason", sa.String(256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_azure_consent_invites_state", "azure_consent_invites", ["state"], unique=True)
    op.create_index("ix_azure_consent_invites_status", "azure_consent_invites", ["status"])
    op.create_index("ix_azure_consent_invites_expires_at", "azure_consent_invites", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_azure_consent_invites_expires_at", table_name="azure_consent_invites")
    op.drop_index("ix_azure_consent_invites_status", table_name="azure_consent_invites")
    op.drop_index("ix_azure_consent_invites_state", table_name="azure_consent_invites")
    op.drop_table("azure_consent_invites")
