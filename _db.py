"""Shared DB helper with retry on transient Azure PG flaps."""
import psycopg2, time

def connect(readonly=False, retries=5):
    last = None
    for i in range(retries):
        try:
            c = psycopg2.connect(
                host="dataope.postgres.database.azure.com", port=5432,
                user="azuredb", password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W",
                dbname="cloudcost", sslmode="require", connect_timeout=60,
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
            )
            if readonly: c.set_session(readonly=True)
            return c
        except Exception as e:
            last = e
            print(f"  (connect retry {i+1}/{retries}: {type(e).__name__})")
            time.sleep(3 * (i+1))
    raise last

def run_q(q, params=None, readonly=True):
    """Run one query with automatic reconnect, returns rows."""
    for attempt in range(5):
        try:
            c = connect(readonly=readonly)
            cur = c.cursor()
            cur.execute(q, params)
            if cur.description:
                rows = cur.fetchall()
            else:
                rows = None
            if not readonly: c.commit()
            c.close()
            return rows, cur.rowcount
        except psycopg2.OperationalError as e:
            print(f"  (query retry {attempt+1}: {type(e).__name__})")
            time.sleep(3 * (attempt+1))
    raise RuntimeError("Exhausted retries")
