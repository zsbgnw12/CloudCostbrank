"""Remove Customer entity and all customer_id references.

Drops the customers table, removes customer_id columns from projects,
monthly_bills, and project_assignment_logs, and drops related indexes/constraints.

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    # Drop indexes that reference customer_id (may or may not exist depending on
    # whether 004 was run before or after the column was removed)
    op.execute("DROP INDEX IF EXISTS ix_projects_customer_id")
    op.execute("DROP INDEX IF EXISTS ix_monthly_bills_customer_month")

    # Drop customer_id columns — CASCADE handles dependent constraints (e.g. old
    # uq_monthly_bill that still includes customer_id, or FK constraints pointing
    # to customers table).  IF EXISTS guards against already-clean schemas.
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS customer_id CASCADE")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS assigned_at")
    op.execute("ALTER TABLE monthly_bills DROP COLUMN IF EXISTS customer_id CASCADE")
    op.execute("ALTER TABLE project_assignment_logs DROP COLUMN IF EXISTS from_customer_id CASCADE")
    op.execute("ALTER TABLE project_assignment_logs DROP COLUMN IF EXISTS to_customer_id CASCADE")

    # Ensure the new unique constraint exists (old one was dropped by CASCADE above;
    # new schemas already have this from model definition, so IF NOT EXISTS).
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "  SELECT 1 FROM pg_constraint WHERE conname = 'uq_monthly_bill'"
        ") THEN "
        "  ALTER TABLE monthly_bills "
        "    ADD CONSTRAINT uq_monthly_bill UNIQUE (month, category_id, provider); "
        "END IF; "
        "END $$"
    )

    # Drop the customers table
    op.execute("DROP TABLE IF EXISTS customers CASCADE")


def downgrade():
    # Recreate customers table
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("contact_person", sa.String(50)),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(100)),
        sa.Column("billing_type", sa.String(10), server_default="postpaid"),
        sa.Column("credit_limit", sa.Numeric(14, 2), server_default="0"),
        sa.Column("balance", sa.Numeric(14, 2), server_default="0"),
        sa.Column("status", sa.String(15), server_default="active"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # Re-add customer_id columns
    op.add_column("projects", sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id")))
    op.add_column("projects", sa.Column("assigned_at", sa.DateTime))
    op.add_column("monthly_bills", sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id")))
    op.add_column("project_assignment_logs", sa.Column("from_customer_id", sa.Integer, sa.ForeignKey("customers.id")))
    op.add_column("project_assignment_logs", sa.Column("to_customer_id", sa.Integer, sa.ForeignKey("customers.id")))

    # Re-create indexes
    op.create_index("ix_projects_customer_id", "projects", ["customer_id"])
    op.create_index("ix_monthly_bills_customer_month", "monthly_bills", ["customer_id", "month"])
