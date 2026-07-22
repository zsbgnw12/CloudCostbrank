"""Create cloudcost database and tables, clean up gongdan."""
import os
import sys

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# 连接串从环境变量读取,禁止硬编码凭据。CLOUDCOST 用 SYNC_DATABASE_URL(去掉方言后缀),
# GONGDAN 由同实例换库名派生。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_raw = os.environ.get("SYNC_DATABASE_URL")
if not _raw:
    from app.config import settings
    _raw = settings.SYNC_DATABASE_URL
if not _raw or "user:password@localhost" in _raw:
    raise SystemExit("请设置环境变量 SYNC_DATABASE_URL(禁止硬编码数据库凭据)。")
CLOUDCOST_URL = _raw.replace("postgresql+psycopg2://", "postgresql://")
GONGDAN_URL = CLOUDCOST_URL.rsplit("/", 1)[0] + "/gongdan"

# Step 1: Create cloudcost database
print("=== Step 1: Create cloudcost database ===")
conn = psycopg2.connect(GONGDAN_URL)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname = 'cloudcost'")
if cur.fetchone():
    print("  cloudcost database already exists")
else:
    cur.execute("CREATE DATABASE cloudcost")
    print("  cloudcost database created!")
cur.close()
conn.close()

# Step 2: Drop mistakenly created tables from gongdan
print("\n=== Step 2: Clean up gongdan ===")
CLOUDCOST_TABLES = [
    "alert_history", "alert_rules", "billing_data", "categories",
    "cloud_accounts", "data_sources", "exchange_rates",
    "monthly_bills", "operation_logs", "project_assignment_logs",
    "projects", "resource_inventory", "sync_logs",
]
conn = psycopg2.connect(GONGDAN_URL)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
for table in CLOUDCOST_TABLES:
    cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    print(f"  dropped {table}")
cur.close()
conn.close()
print("  gongdan cleanup done!")

# Step 3: Create tables in cloudcost
print("\n=== Step 3: Create tables in cloudcost ===")
from sqlalchemy import create_engine, text
from app.database import Base
import app.models  # noqa

engine = create_engine(_raw)
Base.metadata.create_all(engine)

with engine.connect() as c:
    result = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))
    tables = [row[0] for row in result]
    print(f"  Created {len(tables)} tables in cloudcost:")
    for t in tables:
        print(f"    ✓ {t}")

engine.dispose()
print("\nDone!")
