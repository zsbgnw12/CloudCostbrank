"""Add notify_email to alert_rules, create notifications table

Revision ID: 002
Revises: 001
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # alert_rules: widen threshold_type and add notify_email
    op.alter_column("alert_rules", "threshold_type", type_=sa.String(30), existing_type=sa.String(20))
    op.add_column("alert_rules", sa.Column("notify_email", sa.String(500), nullable=True))

    # notifications table
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("type", sa.String(20), server_default="warning"),
        sa.Column("is_read", sa.Boolean, server_default=sa.text("false")),
        sa.Column("alert_history_id", sa.Integer, sa.ForeignKey("alert_history.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_column("alert_rules", "notify_email")
    op.alter_column("alert_rules", "threshold_type", type_=sa.String(20), existing_type=sa.String(30))
