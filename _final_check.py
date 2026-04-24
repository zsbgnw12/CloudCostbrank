import sys; sys.path.insert(0, ".")
from _db import run_q

print("=== FINAL STATE ===")
rows, _ = run_q("SELECT COUNT(*) FROM projects")
print(f"  projects total: {rows[0][0]}")

rows, _ = run_q("SELECT COUNT(*) FROM projects WHERE external_project_id ~ '^project-[0-9a-f]{12}$'")
print(f"  hash projects remaining: {rows[0][0]}")

rows, _ = run_q("""SELECT ss.provider, COUNT(*) FROM projects p
                    LEFT JOIN supply_sources ss ON p.supply_source_id=ss.id
                    GROUP BY ss.provider ORDER BY ss.provider""")
for r in rows: print(f"  {r[0]}: {r[1]} projects")

print()
print("=== suppliers / supply_sources ===")
rows, _ = run_q("SELECT COUNT(*) FROM suppliers")
print(f"  suppliers: {rows[0][0]}")
rows, _ = run_q("SELECT COUNT(*) FROM supply_sources")
print(f"  supply_sources: {rows[0][0]}")
rows, _ = run_q("""SELECT s.id, s.name FROM suppliers s
                   WHERE NOT EXISTS (SELECT 1 FROM supply_sources WHERE supplier_id=s.id)""")
print(f"  orphan suppliers (0 supply_sources): {len(rows)}")

print()
print("=== GCP project status distribution ===")
rows, _ = run_q("""SELECT p.status, COUNT(*) FROM projects p
                    JOIN supply_sources ss ON p.supply_source_id=ss.id
                    WHERE ss.provider='gcp' GROUP BY p.status ORDER BY p.status""")
for r in rows: print(f"  {r[0]}: {r[1]}")

print()
print("=== orphan FK references ===")
rows, _ = run_q("SELECT COUNT(*) FROM project_assignment_logs WHERE project_id NOT IN (SELECT id FROM projects)")
print(f"  orphan project_assignment_logs: {rows[0][0]}")
rows, _ = run_q("SELECT COUNT(*) FROM supply_sources WHERE supplier_id IS NOT NULL AND supplier_id NOT IN (SELECT id FROM suppliers)")
print(f"  orphan supply_sources: {rows[0][0]}")

print()
print("=== billing_data totals (unchanged by this cleanup) ===")
rows, _ = run_q("SELECT ROUND(SUM(cost)::numeric,2) FROM billing_data WHERE provider='gcp'")
print(f"  Total GCP cost: ${rows[0][0]}")
rows, _ = run_q("SELECT COUNT(*) FROM billing_data WHERE provider='gcp'")
print(f"  Total GCP rows: {rows[0][0]}")
