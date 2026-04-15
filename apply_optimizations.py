"""Create daily_summary table and optimize indexes."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, text
from app.config import settings
from app.database import Base
import app.models  # noqa

engine = create_engine(settings.SYNC_DATABASE_URL, echo=False, connect_args={"connect_timeout": 30})

# Create only the new table
with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'billing_daily_summary')"
    ))
    exists = result.scalar()
    print(f"billing_daily_summary exists: {exists}")

if not exists:
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["billing_daily_summary"]])
    print("Created billing_daily_summary table")
else:
    print("Table already exists, skipping")

# Run index optimization
with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'billing_data' ORDER BY indexname"
    ))
    indexes = [r[0] for r in result.all()]
    print(f"Current indexes: {indexes}")

    if "ix_billing_date" in indexes:
        conn.execute(text("DROP INDEX ix_billing_date"))
        print("Dropped ix_billing_date (redundant)")

    if "ix_billing_provider_date" in indexes:
        conn.execute(text("DROP INDEX ix_billing_provider_date"))
        conn.execute(text("CREATE INDEX ix_billing_provider_date_cost ON billing_data (provider, date, cost)"))
        print("Upgraded ix_billing_provider_date -> ix_billing_provider_date_cost (covering)")

    if "ix_billing_project_date" in indexes:
        conn.execute(text("DROP INDEX ix_billing_project_date"))
        conn.execute(text("CREATE INDEX ix_billing_project_date_cost ON billing_data (project_id, date, cost)"))
        print("Upgraded ix_billing_project_date -> ix_billing_project_date_cost (covering)")

    conn.commit()

# Verify
with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'billing_data' ORDER BY indexname"
    ))
    print(f"Final billing_data indexes: {[r[0] for r in result.all()]}")

    result = conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'billing_daily_summary' ORDER BY indexname"
    ))
    print(f"Final daily_summary indexes: {[r[0] for r in result.all()]}")

print("All optimizations applied!")
