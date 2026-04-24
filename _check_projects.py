"""Check GCP 服务账号 (projects) completeness, status, and assignment.
User reports 'xh' project is missing from frontend.
Read-only."""
import sys; sys.path.insert(0, ".")
from _db import run_q

def sep(t): print("\n" + "=" * 78 + "\n  " + t + "\n" + "=" * 78)

# 1. All GCP projects in DB (projects table)
sep("1. All GCP projects in DB (projects table)")
rows, _ = run_q("""
SELECT p.id, p.external_project_id, p.name, p.status, p.data_source_id,
       p.supply_source_id, s.name AS supplier_name, p.category_id, p.created_at
FROM projects p
LEFT JOIN supply_sources ss ON p.supply_source_id = ss.id
LEFT JOIN suppliers s ON ss.supplier_id = s.id
WHERE ss.provider = 'gcp'
ORDER BY p.external_project_id
""")
print(f"  {'id':<4} {'external_project_id':<35} {'name':<30} {'status':<9} {'ds':<3} {'ss':<3} {'supplier':<15} {'cat':<5}")
for r in rows:
    print(f"  {r[0]:<4} {r[1]!r:<35} {r[2]!r:<30} {r[3]!r:<9} {str(r[4] or ''):<3} {str(r[5] or ''):<3} {r[6]!r:<15} {str(r[7] or ''):<5}")
print(f"  total: {len(rows)} GCP projects registered")

# 2. Projects whose external_project_id contains 'xh'
sep("2. Search 'xh' in GCP projects (user reported missing)")
rows, _ = run_q("""
SELECT p.id, p.external_project_id, p.name, p.status, p.data_source_id, p.supply_source_id,
       s.name supplier
FROM projects p
LEFT JOIN supply_sources ss ON p.supply_source_id = ss.id
LEFT JOIN suppliers s ON ss.supplier_id = s.id
WHERE (p.external_project_id ILIKE '%xh%' OR p.name ILIKE '%xh%')
ORDER BY p.external_project_id
""")
if rows:
    for r in rows: print(f"  {r}")
else:
    print("  !! projects 表中没有 'xh' 关键字匹配")

# 3. Does 'xh' appear in billing_data at all?
sep("3. Search 'xh' in billing_data")
rows, _ = run_q("""
SELECT project_id, project_name, COUNT(*) n, ROUND(SUM(cost)::numeric,2) cost,
       MIN(date) first_date, MAX(date) last_date,
       array_agg(DISTINCT data_source_id) ds_ids
FROM billing_data
WHERE provider='gcp' AND (project_id ILIKE '%xh%' OR project_name ILIKE '%xh%')
GROUP BY project_id, project_name ORDER BY cost DESC
""")
if rows:
    for r in rows: print(f"  pid={r[0]!r:<30} pname={r[1]!r:<25} n={r[2]:>5,}  cost=${float(r[3] or 0):>10,.2f}  {r[4]}~{r[5]}  in ds{r[6]}")
else:
    print("  (no match)")

# 4. All distinct GCP project_ids in billing_data vs projects table
sep("4. billing_data GCP project_ids vs projects table registration status")
rows, _ = run_q("""
SELECT bd.project_id,
       ROUND(SUM(bd.cost)::numeric,2) total_cost,
       COUNT(*) n_rows,
       MIN(bd.date) first, MAX(bd.date) last,
       array_agg(DISTINCT bd.data_source_id ORDER BY bd.data_source_id) ds_ids,
       (SELECT MAX(p.id) FROM projects p
        JOIN supply_sources ss ON p.supply_source_id = ss.id
        WHERE ss.provider = 'gcp' AND p.external_project_id = bd.project_id) registered_project_id
FROM billing_data bd
WHERE bd.provider='gcp' AND bd.project_id IS NOT NULL
GROUP BY bd.project_id ORDER BY total_cost DESC NULLS LAST
""")
print(f"  {'project_id':<38} {'cost':>13} {'rows':>7} {'dates':<25} {'ds':<15} {'reg_id':<7}")
for r in rows:
    reg = str(r[6]) if r[6] else "MISSING"
    marker = "" if r[6] else "  <-- not in projects"
    print(f"  {r[0]!r:<38} ${float(r[1] or 0):>12,.2f} {r[2]:>7,} {str(r[3])}~{str(r[4]):<11} {str(r[5]):<15} {reg:<7}{marker}")

# 5. Projects table orphans (registered but no billing)
sep("5. Projects table rows with NO billing_data (orphan registrations)")
rows, _ = run_q("""
SELECT p.id, p.external_project_id, p.name, p.status, p.created_at,
       s.name supplier
FROM projects p
LEFT JOIN supply_sources ss ON p.supply_source_id = ss.id
LEFT JOIN suppliers s ON ss.supplier_id = s.id
WHERE ss.provider = 'gcp'
  AND NOT EXISTS (SELECT 1 FROM billing_data bd
                  WHERE bd.provider='gcp' AND bd.project_id = p.external_project_id)
ORDER BY p.external_project_id
""")
if rows:
    for r in rows: print(f"  id={r[0]}  ext={r[1]!r:<30}  name={r[2]!r:<25}  status={r[3]}  supplier={r[5]!r}  created={r[4]}")
else:
    print("  (none)")

# 6. supply_sources + suppliers for gcp
sep("6. GCP supply_sources / suppliers structure")
rows, _ = run_q("""
SELECT ss.id, s.name supplier, ss.provider,
       (SELECT COUNT(*) FROM projects WHERE supply_source_id = ss.id) n_projects
FROM supply_sources ss LEFT JOIN suppliers s ON ss.supplier_id = s.id
WHERE ss.provider = 'gcp'
ORDER BY ss.id
""")
for r in rows: print(f"  ss_id={r[0]}  supplier={r[1]!r}  provider={r[2]}  #projects={r[3]}")

# 7. Cloud accounts for gcp
sep("7. GCP cloud_accounts")
rows, _ = run_q("""
SELECT id, name, provider, is_active, created_at
FROM cloud_accounts WHERE provider='gcp' ORDER BY id
""")
for r in rows: print(f"  id={r[0]}  name={r[1]!r}  active={r[3]}  created={r[4]}")
