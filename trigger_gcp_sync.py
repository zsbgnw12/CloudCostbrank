"""Trigger sync for 5 GCP data sources and poll until done."""
import sys, time, json
sys.path.insert(0, "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost")

import urllib.request

BASE = "http://localhost:8000"

def post(url, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())

# Trigger sync for ds_ids 3-7
ds_ids = [3, 4, 5, 6, 7]
tasks = {}
for ds_id in ds_ids:
    resp = post(f"{BASE}/api/sync/{ds_id}", {"start_month": "2026-03"})
    tasks[ds_id] = resp["task_id"]
    print(f"ds_id={ds_id} dispatched -> {resp['task_id']}")

print("\nPolling task status (Celery is solo, tasks run sequentially)...")
print("This may take 5-10 minutes total.\n")

# Poll until all done
max_wait = 900  # 15 min max
start = time.time()
done = set()
while len(done) < len(tasks) and (time.time() - start) < max_wait:
    for ds_id, task_id in tasks.items():
        if ds_id in done:
            continue
        try:
            s = get(f"{BASE}/api/sync/status/{task_id}")
            status = s["status"]
            if status in ("SUCCESS", "FAILURE"):
                result = s.get("result") or {}
                print(f"[{time.strftime('%H:%M:%S')}] ds_id={ds_id} -> {status} | {result}")
                done.add(ds_id)
        except Exception as e:
            pass
    if len(done) < len(tasks):
        time.sleep(10)

# Final sync log check
print("\n--- Sync logs (GCP) ---")
logs = get(f"{BASE}/api/sync/logs?limit=20")
for log in logs:
    if log["data_source_id"] in ds_ids:
        print(f"ds={log['data_source_id']} status={log['status']} fetched={log['records_fetched']} upserted={log['records_upserted']} err={log.get('error_message','')[:80]}")

print("\n--- billing_data rows by provider ---")
import importlib.util
