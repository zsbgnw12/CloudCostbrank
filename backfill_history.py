"""
Backfill all historical data for GCP sources + 4月 for all.
Runs tasks sequentially (solo worker) and monitors progress.
"""
import json
import time
import urllib.request

BASE = "http://localhost:8001"


def post(url, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(5)
    return None


def wait_task(task_id, label, max_wait=1800):
    start = time.time()
    while time.time() - start < max_wait:
        s = get(f"{BASE}/api/sync/status/{task_id}")
        if s and s["status"] in ("SUCCESS", "FAILURE"):
            elapsed = int(time.time() - start)
            result = s.get("result") or {}
            print(f"  [{elapsed}s] {s['status']} | {result}")
            return s["status"] == "SUCCESS"
        time.sleep(15)
    print(f"  TIMEOUT after {max_wait}s")
    return False


# ============================================================
# Define all sync jobs
# ============================================================
jobs = [
    # --- us_native (ds7): backfill 2025-10 to 2026-02 ---
    {"ds_id": 7, "start_month": "2025-10", "end_month": "2025-10", "label": "us_native 2025-10"},
    {"ds_id": 7, "start_month": "2025-11", "end_month": "2025-11", "label": "us_native 2025-11"},
    {"ds_id": 7, "start_month": "2025-12", "end_month": "2025-12", "label": "us_native 2025-12"},
    {"ds_id": 7, "start_month": "2026-01", "end_month": "2026-01", "label": "us_native 2026-01"},
    {"ds_id": 7, "start_month": "2026-02", "end_month": "2026-02", "label": "us_native 2026-02"},
    # --- testmanger (ds4): backfill 2026-01, 02 ---
    {"ds_id": 4, "start_month": "2026-01", "end_month": "2026-01", "label": "testmanger 2026-01"},
    {"ds_id": 4, "start_month": "2026-02", "end_month": "2026-02", "label": "testmanger 2026-02"},
    # --- April data for active sources ---
    {"ds_id": 3, "start_month": "2026-04", "end_month": "2026-04", "label": "xmind 2026-04"},
    {"ds_id": 4, "start_month": "2026-04", "end_month": "2026-04", "label": "testmanger 2026-04"},
    {"ds_id": 5, "start_month": "2026-04", "end_month": "2026-04", "label": "cb_export 2026-04"},
    {"ds_id": 6, "start_month": "2026-04", "end_month": "2026-04", "label": "px_billing 2026-04"},
    # AWS + Azure april
    {"ds_id": 1, "start_month": "2026-04", "end_month": "2026-04", "label": "AWS 2026-04"},
    {"ds_id": 2, "start_month": "2026-04", "end_month": "2026-04", "label": "Azure 2026-04"},
]

print(f"=== Backfill: {len(jobs)} jobs ===\n")

success_count = 0
fail_count = 0

for i, job in enumerate(jobs, 1):
    print(f"[{i}/{len(jobs)}] {job['label']}  (ds={job['ds_id']})")
    try:
        resp = post(f"{BASE}/api/sync/{job['ds_id']}", {
            "start_month": job["start_month"],
            "end_month": job.get("end_month"),
        })
        task_id = resp["task_id"]
        print(f"  dispatched: {task_id}")
        ok = wait_task(task_id, job["label"])
        if ok:
            success_count += 1
        else:
            fail_count += 1
    except Exception as e:
        print(f"  ERROR dispatching: {e}")
        fail_count += 1
    print()

print(f"=== Done: {success_count} success, {fail_count} failed ===")
