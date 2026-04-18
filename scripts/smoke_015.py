"""One-shot smoke script: verify 015 schema + route registration."""
import asyncio
import os
import json
from sqlalchemy import text
from app.database import engine
from app.main import app


async def main():
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name IN ('project_customer_assignments','project_assignment_logs')
            ORDER BY table_name, ordinal_position
        """))).all()
        for r in rows:
            print("COL", r._mapping.get("column_name"), r._mapping.get("data_type"))

        rows = (await conn.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename='project_customer_assignments'
            ORDER BY indexname
        """))).all()
        for r in rows:
            print("IDX", r._mapping.get("indexname"))

        rows = (await conn.execute(text(
            "SELECT COUNT(*) AS n FROM project_customer_assignments"
        ))).all()
        for r in rows:
            print("ROWS", r._mapping.get("n"))

    paths = sorted([r.path for r in app.routes if hasattr(r, "path") and "service-accounts" in r.path])
    for p in paths:
        print("ROUTE", p)


if __name__ == "__main__":
    asyncio.run(main())
