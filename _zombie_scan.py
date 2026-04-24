"""Full zombie data scan — read-only.
Find ALL data that references records that no longer exist or are inconsistent."""
import sys; sys.path.insert(0, ".")
from _db import run_q

def sep(t): print("\n" + "=" * 78 + "\n  " + t + "\n" + "=" * 78)

# ============================================================================
sep("1. projects with hash-style external_project_id (known zombies)")
# ============================================================================
rows, _ = run_q("""
SELECT p.id, p.external_project_id, p.name, p.status, p.supply_source_id,
       s.name supplier,
       (SELECT COUNT(*) FROM billing_data bd
        WHERE bd.provider='gcp' AND bd.project_id = p.external_project_id) bd_count
FROM projects p
LEFT JOIN supply_sources ss ON p.supply_source_id = ss.id
LEFT JOIN suppliers s ON ss.supplier_id = s.id
WHERE p.external_project_id ~ '^project-[0-9a-f]{12}$'
ORDER BY p.id
""")
print(f"  total: {len(rows)}")
for r in rows: print(f"  id={r[0]} ext={r[1]!r:<30} name={r[2]!r:<25} status={r[3]} supplier={r[5]!r} billing_rows={r[6]}")

# ============================================================================
sep("2. projects with NO billing_data at all (any provider, any ds)")
# ============================================================================
rows, _ = run_q("""
SELECT p.id, p.external_project_id, p.name, p.status, ss.provider,
       s.name supplier, p.created_at
FROM projects p
LEFT JOIN supply_sources ss ON p.supply_source_id = ss.id
LEFT JOIN suppliers s ON ss.supplier_id = s.id
WHERE NOT EXISTS (
    SELECT 1 FROM billing_data bd WHERE bd.project_id = p.external_project_id
)
ORDER BY p.id
""")
print(f"  total: {len(rows)}")
for r in rows:
    print(f"  id={r[0]} ext={r[1]!r:<35} name={r[2]!r:<25} status={r[3]:<10} provider={r[4]!r} supplier={r[5]!r} created={r[6]}")

# ============================================================================
sep("3. duplicate external_project_id across different supply_sources (true dups)")
# ============================================================================
rows, _ = run_q("""
SELECT external_project_id, COUNT(DISTINCT supply_source_id) n_ss, COUNT(*) n_rows,
       array_agg(id ORDER BY id) ids,
       array_agg(supply_source_id ORDER BY id) ss_ids
FROM projects
GROUP BY external_project_id HAVING COUNT(*) > 1
ORDER BY external_project_id
""")
if not rows: print("  (none)")
for r in rows: print(f"  ext={r[0]!r}  ids={r[3]}  ss={r[4]}")

# ============================================================================
sep("4. billing_daily_summary rows with project_id not in billing_data")
# ============================================================================
rows, _ = run_q("""
SELECT bs.project_id, COUNT(*) n, ROUND(SUM(bs.total_cost)::numeric,2) c,
       MIN(bs.date), MAX(bs.date)
FROM billing_daily_summary bs
WHERE NOT EXISTS (
    SELECT 1 FROM billing_data bd
    WHERE bd.date = bs.date AND bd.provider = bs.provider
      AND bd.data_source_id = bs.data_source_id
      AND COALESCE(bd.project_id,'') = COALESCE(bs.project_id,'')
      AND COALESCE(bd.product,'') = COALESCE(bs.product,'')
)
GROUP BY bs.project_id
""")
if not rows: print("  (none — summary is consistent)")
for r in rows: print(f"  {r}")

# ============================================================================
sep("5. billing_daily_summary rows that reference a data_source_id not in data_sources")
# ============================================================================
rows, _ = run_q("""
SELECT bs.data_source_id, COUNT(*) n FROM billing_daily_summary bs
WHERE NOT EXISTS (SELECT 1 FROM data_sources ds WHERE ds.id = bs.data_source_id)
GROUP BY bs.data_source_id
""")
if not rows: print("  (none)")
for r in rows: print(f"  orphan ds={r[0]}  n={r[1]}")

# ============================================================================
sep("6. billing_data rows with data_source_id not in data_sources")
# ============================================================================
rows, _ = run_q("""
SELECT bd.data_source_id, COUNT(*) n FROM billing_data bd
WHERE NOT EXISTS (SELECT 1 FROM data_sources ds WHERE ds.id = bd.data_source_id)
GROUP BY bd.data_source_id
""")
if not rows: print("  (none)")
for r in rows: print(f"  orphan ds={r[0]}  n={r[1]}")

# ============================================================================
sep("7. MonthlyBill records that reference non-existent ds/category")
# ============================================================================
rows, _ = run_q("""
SELECT mb.id, mb.month, mb.category_id, mb.provider,
       mb.original_cost, mb.final_cost, mb.status
FROM monthly_bills mb
WHERE mb.category_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM categories c WHERE c.id = mb.category_id)
ORDER BY mb.month DESC LIMIT 20
""")
if not rows: print("  (none)")
for r in rows: print(f"  mb={r}")

# ============================================================================
sep("8. suppliers with 0 supply_sources")
# ============================================================================
rows, _ = run_q("""
SELECT s.id, s.name FROM suppliers s
WHERE NOT EXISTS (SELECT 1 FROM supply_sources WHERE supplier_id = s.id)
""")
if not rows: print("  (none)")
for r in rows: print(f"  orphan supplier id={r[0]} name={r[1]!r}")

# ============================================================================
sep("9. supply_sources with 0 projects")
# ============================================================================
rows, _ = run_q("""
SELECT ss.id, ss.provider, s.name, ss.created_at
FROM supply_sources ss LEFT JOIN suppliers s ON ss.supplier_id = s.id
WHERE NOT EXISTS (SELECT 1 FROM projects WHERE supply_source_id = ss.id)
""")
if not rows: print("  (none)")
for r in rows: print(f"  empty ss id={r[0]} provider={r[1]} supplier={r[2]!r}  created={r[3]}")

# ============================================================================
sep("10. data_sources with 0 billing_data rows (unused sources)")
# ============================================================================
rows, _ = run_q("""
SELECT ds.id, ds.name, ds.cloud_account_id, ds.is_active, ds.sync_status
FROM data_sources ds
WHERE NOT EXISTS (SELECT 1 FROM billing_data bd WHERE bd.data_source_id = ds.id)
ORDER BY ds.id
""")
if not rows: print("  (none)")
for r in rows: print(f"  ds={r[0]} name={r[1]!r} ca={r[2]} active={r[3]} status={r[4]!r}")

# ============================================================================
sep("11. Taiji region=NULL rows (known collector issue)")
# ============================================================================
rows, _ = run_q("""
SELECT data_source_id, COUNT(*), ROUND(SUM(cost)::numeric,4)
FROM billing_data WHERE provider='taiji' AND region IS NULL
GROUP BY data_source_id
""")
if not rows: print("  (none)")
for r in rows: print(f"  ds={r[0]} rows={r[1]} cost=${r[2]}")

# ============================================================================
sep("12. sync_logs — failed entries >7 days old (safe to prune, but historical)")
# ============================================================================
rows, _ = run_q("""
SELECT COUNT(*), MIN(start_time), MAX(start_time)
FROM sync_logs WHERE status='failed'
""")
for r in rows: print(f"  total failed logs = {r[0]}  range {r[1]} ~ {r[2]}")

# ============================================================================
sep("13. user_cloud_account_grants — referencing deleted cloud_accounts/users")
# ============================================================================
try:
    rows, _ = run_q("""
    SELECT COUNT(*) FROM user_cloud_account_grants g
    WHERE NOT EXISTS (SELECT 1 FROM cloud_accounts ca WHERE ca.id = g.cloud_account_id)
       OR NOT EXISTS (SELECT 1 FROM users u WHERE u.id = g.user_id)
    """)
    for r in rows: print(f"  orphan grants: {r[0]}")
except Exception as e:
    print(f"  skip: {e}")
