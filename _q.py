import os, json, psycopg2
url = os.environ.get("SYNC_DATABASE_URL") or os.environ["DATABASE_URL"]
url = url.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
c = psycopg2.connect(url)
cur = c.cursor()
cur.execute("SELECT id, name, provider, is_active FROM cloud_accounts ORDER BY id")
print("=== cloud_accounts ===")
for r in cur.fetchall():
    print(r)
cur.execute("SELECT id, cloud_account_id, name, config, is_active FROM data_sources ORDER BY id")
print("=== data_sources ===")
for r in cur.fetchall():
    print(r)
