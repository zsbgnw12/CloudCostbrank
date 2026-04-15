"""Add missing performance indexes.

Revision ID: 004
Revises: 003
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_status ON projects (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_ext_pid_provider ON projects (external_project_id, provider)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_alert_rules_active ON alert_rules (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_provider ON billing_data (provider)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_ds_id ON billing_data (data_source_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_billing_ds_id")
    op.execute("DROP INDEX IF EXISTS ix_billing_provider")
    op.execute("DROP INDEX IF EXISTS ix_alert_rules_active")
    op.execute("DROP INDEX IF EXISTS ix_projects_ext_pid_provider")
    op.execute("DROP INDEX IF EXISTS ix_projects_status")
