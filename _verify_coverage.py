"""Verify all GCP billing data has a matching non-recycled Project.
Also show current state of ss#4 (未分组货源) and any orphaned billing rows."""
import sys; sys.path.insert(0, ".")
from _db import run_q

def sep(t): print("\n" + "=" * 78 + "\n  " + t + "\n" + "=" * 78)

# 1. supply_source #4 现状（未分组货源）
sep("1. supply_source #4 (未分组货源) 现状")
rows, _ = run_q("""
SELECT ss.id, s.name supplier, ss.provider,
       (SELECT COUNT(*) FROM projects WHERE supply_source_id=ss.id) total_projects,
       (SELECT COUNT(*) FROM projects WHERE supply_source_id=ss.id AND recycled_at IS NULL) visible_projects
FROM supply_sources ss LEFT JOIN suppliers s ON ss.supplier_id=s.id
WHERE ss.id=4
""")
for r in rows: print(f"  ss_id=4 supplier={r[1]!r} provider={r[2]} total_projects={r[3]} visible={r[4]}")

# ss#4 下如果还有 projects 列出来
rows, _ = run_q("""SELECT id, external_project_id, name, status, recycled_at
                   FROM projects WHERE supply_source_id=4 ORDER BY id""")
if rows:
    for r in rows: print(f"  id={r[0]} ext={r[1]!r} name={r[2]!r} status={r[3]} recycled={r[4]}")
else:
    print("  (ss#4 下已完全无 project)")

# 2. 所有 GCP billing_data 的 project_id 是否都在 projects 表里 (非 recycled)
sep("2. GCP billing_data project_id → 是否都能 JOIN 到可见 Project")
rows, _ = run_q("""
SELECT bd.project_id, ROUND(SUM(bd.cost)::numeric,2) total_cost, COUNT(*) n,
       MIN(bd.date) first, MAX(bd.date) last
FROM billing_data bd
WHERE bd.provider='gcp' AND bd.project_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM projects p
      JOIN supply_sources ss ON p.supply_source_id=ss.id
      WHERE ss.provider='gcp'
        AND p.external_project_id = bd.project_id
        AND p.recycled_at IS NULL
  )
GROUP BY bd.project_id ORDER BY total_cost DESC NULLS LAST
""")
if not rows:
    print("  [OK] 所有 GCP billing_data 的 project_id 都能找到对应 project 档案")
else:
    print(f"  !! {len(rows)} 个 project_id 在 billing_data 有但 projects 表没有（或已软删）:")
    for r in rows: print(f"    {r[0]!r:<35} cost=${float(r[1] or 0):>12,.2f} rows={r[2]} dates={r[3]}~{r[4]}")

# 3. 反向：projects 表里有 (非软删) 但 billing_data 没数据的 —— 允许存在（standby 或新注册）
sep("3. 可见 projects 但 0 billing 的（不是问题，是 standby/新注册）")
rows, _ = run_q("""
SELECT p.id, p.external_project_id, p.name, p.status, ss.provider, s.name supplier, p.created_at
FROM projects p
LEFT JOIN supply_sources ss ON p.supply_source_id=ss.id
LEFT JOIN suppliers s ON ss.supplier_id=s.id
WHERE p.recycled_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM billing_data bd WHERE bd.project_id = p.external_project_id)
ORDER BY p.id
""")
for r in rows: print(f"  id={r[0]} ext={r[1]!r:<45} name={r[2]!r:<25} status={r[3]} provider={r[4]} supplier={r[5]!r}")

# 4. 前端列表模拟：list_accounts 会看到多少个 GCP projects
sep("4. 前端 list_accounts 现在会看到多少 project (各 provider)")
rows, _ = run_q("""
SELECT ss.provider, p.status, COUNT(*) n
FROM projects p JOIN supply_sources ss ON p.supply_source_id=ss.id
WHERE p.recycled_at IS NULL
GROUP BY ss.provider, p.status
ORDER BY ss.provider, p.status
""")
for r in rows: print(f"  {r[0]:<8} status={r[1]:<10} count={r[2]}")

# 5. GCP projects 按 supply_source 分布
sep("5. GCP projects 按 supply_source (supplier) 分布")
rows, _ = run_q("""
SELECT ss.id, s.name supplier, COUNT(p.id) n
FROM supply_sources ss
LEFT JOIN suppliers s ON ss.supplier_id=s.id
LEFT JOIN projects p ON p.supply_source_id=ss.id AND p.recycled_at IS NULL
WHERE ss.provider='gcp'
GROUP BY ss.id, s.name
ORDER BY ss.id
""")
for r in rows: print(f"  ss#{r[0]} {r[1]!r:<20} projects={r[2]}")

# 6. GCP 按 supplier 聚合的费用 (4月)
sep("6. GCP April 2026 费用按 supplier 聚合（前端按 supplier 看的总账）")
rows, _ = run_q("""
SELECT s.name supplier, COUNT(DISTINCT bd.project_id) n_proj,
       ROUND(SUM(bd.cost)::numeric, 2) total
FROM billing_data bd
JOIN projects p ON p.external_project_id = bd.project_id AND p.recycled_at IS NULL
JOIN supply_sources ss ON p.supply_source_id=ss.id AND ss.provider=bd.provider
LEFT JOIN suppliers s ON ss.supplier_id=s.id
WHERE bd.provider='gcp' AND bd.date BETWEEN '2026-04-01' AND '2026-04-22'
GROUP BY s.name ORDER BY total DESC NULLS LAST
""")
total_coverage = 0
for r in rows:
    print(f"  {r[0]!r:<20} projects={r[1]:<4} cost=${float(r[2] or 0):>12,.2f}")
    total_coverage += float(r[2] or 0)

rows, _ = run_q("""SELECT ROUND(SUM(cost)::numeric,2) FROM billing_data
                   WHERE provider='gcp' AND date BETWEEN '2026-04-01' AND '2026-04-22'""")
total_all = float(rows[0][0] or 0)
uncovered = total_all - total_coverage
print(f"\n  April total GCP: ${total_all:,.2f}")
print(f"  Visible via supplier join: ${total_coverage:,.2f}")
print(f"  Difference (cost tied to recycled/missing project): ${uncovered:,.2f}")
