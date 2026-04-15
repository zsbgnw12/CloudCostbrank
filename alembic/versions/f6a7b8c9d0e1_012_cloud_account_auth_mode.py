"""012 cloud_accounts.auth_mode / consent_status — Azure multi-tenant SP consent flow"""

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_accounts",
        sa.Column("auth_mode", sa.String(20), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column("consent_status", sa.String(20), nullable=False, server_default="granted"),
    )


def downgrade() -> None:
    op.drop_column("cloud_accounts", "consent_status")
    op.drop_column("cloud_accounts", "auth_mode")
