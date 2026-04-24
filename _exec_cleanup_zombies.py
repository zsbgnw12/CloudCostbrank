"""Execute zombie cleanup in a single transaction with verification.
Deletes:
  1. 16 project_assignment_logs rows (refs to hash projects 89-102)
  2. 14 hash projects (ids 89-102, external_project_id ~ ^project-[0-9a-f]{12}$)
  3. 1 orphan supplier (id=12 光影科技, 0 supply_sources)

Does NOT delete (deliberately):
  - 3 empty supply_sources (ss#2 aws, ss#9 taiji, ss#16 azure) — catchall buckets for future auto-create
  - 6 Azure standby projects (112-117) — newly registered, awaiting first sync
  - 109 failed sync_logs — audit trail
  - 8 taiji NULL region rows — needs code fix first
"""
import sys; sys.path.insert(0, ".")
from _db import connect

EXPECTED_LOGS = 16
EXPECTED_PROJECTS = 14
EXPECTED_SUPPLIER = 1

print("=" * 70)
print("ZOMBIE CLEANUP — single transaction, abort on any mismatch")
print("=" * 70)

c = connect(readonly=False); c.autocommit = False
cur = c.cursor()

try:
    # -------- pre-check --------
    cur.execute("SELECT COUNT(*) FROM project_assignment_logs WHERE project_id BETWEEN 89 AND 102")
    pre_logs = cur.fetchone()[0]
    print(f"\n  pre-check: project_assignment_logs referencing 89-102 = {pre_logs}")
    assert pre_logs == EXPECTED_LOGS, f"expected {EXPECTED_LOGS}"

    cur.execute("SELECT COUNT(*) FROM projects WHERE id BETWEEN 89 AND 102 AND external_project_id ~ '^project-[0-9a-f]{12}$'")
    pre_projects = cur.fetchone()[0]
    print(f"  pre-check: hash projects (89-102) = {pre_projects}")
    assert pre_projects == EXPECTED_PROJECTS, f"expected {EXPECTED_PROJECTS}"

    cur.execute("SELECT COUNT(*) FROM suppliers WHERE id=12")
    pre_suppliers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM supply_sources WHERE supplier_id=12")
    ss_refs = cur.fetchone()[0]
    print(f"  pre-check: supplier id=12 = {pre_suppliers}, ss refs = {ss_refs}")
    assert pre_suppliers == EXPECTED_SUPPLIER and ss_refs == 0

    # -------- A. Delete assignment logs --------
    cur.execute("DELETE FROM project_assignment_logs WHERE project_id BETWEEN 89 AND 102")
    d1 = cur.rowcount
    print(f"\n  A. DELETE project_assignment_logs rowcount = {d1}")
    assert d1 == EXPECTED_LOGS

    # -------- B. Delete 14 hash projects --------
    cur.execute("DELETE FROM projects WHERE id BETWEEN 89 AND 102 AND external_project_id ~ '^project-[0-9a-f]{12}$'")
    d2 = cur.rowcount
    print(f"  B. DELETE projects rowcount = {d2}")
    assert d2 == EXPECTED_PROJECTS

    # -------- C. Delete orphan supplier --------
    cur.execute("DELETE FROM suppliers WHERE id=12")
    d3 = cur.rowcount
    print(f"  C. DELETE supplier id=12 rowcount = {d3}")
    assert d3 == EXPECTED_SUPPLIER

    # -------- post-check --------
    cur.execute("SELECT COUNT(*) FROM projects WHERE external_project_id ~ '^project-[0-9a-f]{12}$'")
    remaining_hash = cur.fetchone()[0]
    print(f"\n  post-check: remaining hash projects = {remaining_hash}")
    assert remaining_hash == 0

    cur.execute("SELECT COUNT(*) FROM project_assignment_logs WHERE project_id NOT IN (SELECT id FROM projects)")
    orphan_logs = cur.fetchone()[0]
    print(f"  post-check: orphan assignment_logs = {orphan_logs}")
    assert orphan_logs == 0

    cur.execute("SELECT COUNT(*) FROM suppliers WHERE id NOT IN (SELECT supplier_id FROM supply_sources WHERE supplier_id IS NOT NULL)")
    empty_suppliers = cur.fetchone()[0]
    print(f"  post-check: remaining suppliers with 0 supply_sources = {empty_suppliers}")

    # -------- commit --------
    c.commit()
    print("\n  COMMIT done.")

except Exception as e:
    c.rollback()
    print(f"\n  ROLLBACK: {e}")
    raise
finally:
    c.close()
