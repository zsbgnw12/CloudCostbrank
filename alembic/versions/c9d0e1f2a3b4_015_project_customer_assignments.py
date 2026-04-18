"""015: project_customer_assignments + project_assignment_logs.customer_code

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the app's lifespan uses Base.metadata.create_all which may have
    # already created this table on first boot of the new code. Use raw SQL with
    # IF NOT EXISTS so this migration is safe to re-run / run after create_all.
    op.execute("""
        CREATE TABLE IF NOT EXISTS project_customer_assignments (
            id BIGSERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            customer_code VARCHAR(64) NOT NULL,
            assigned_by VARCHAR(50),
            notes TEXT,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_pca_project_customer UNIQUE (project_id, customer_code)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pca_customer_code "
        "ON project_customer_assignments (customer_code)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pca_project_id "
        "ON project_customer_assignments (project_id)"
    )

    # Extend ProjectAssignmentLog so customer bind/unbind events can be
    # rendered in the existing service account history timeline.
    op.execute(
        "ALTER TABLE project_assignment_logs "
        "ADD COLUMN IF NOT EXISTS customer_code VARCHAR(64)"
    )


def downgrade() -> None:
    op.drop_column("project_assignment_logs", "customer_code")
    op.drop_index("ix_pca_project_id", table_name="project_customer_assignments")
    op.drop_index("ix_pca_customer_code", table_name="project_customer_assignments")
    op.drop_table("project_customer_assignments")
