"""Activate 13 standby GCP projects that have billing data but are hidden from frontend."""
import sys; sys.path.insert(0, ".")
from _db import connect

TARGET_IDS = [84,  # xhxg-20260212
              75, 76, 77, 78, 79, 80, 81, 82, 83,  # wecut-001..010
              74,  # vertex-core-tt5
              86,  # xmtyal
              87]  # ysgemini-20260324

print(f"Activating {len(TARGET_IDS)} standby projects: {TARGET_IDS}\n")

c = connect(readonly=False); c.autocommit = False
cur = c.cursor()
try:
    # Pre-check: show current state
    cur.execute("""SELECT id, external_project_id, name, status
                   FROM projects WHERE id = ANY(%s) ORDER BY id""", (TARGET_IDS,))
    print("  BEFORE:")
    for r in cur.fetchall():
        print(f"    id={r[0]:<4} ext={r[1]!r:<25} name={r[2]!r:<25} status={r[3]}")

    # UPDATE
    cur.execute("""UPDATE projects SET status='active', updated_at=NOW()
                   WHERE id = ANY(%s) AND status='standby'""", (TARGET_IDS,))
    updated = cur.rowcount
    print(f"\n  UPDATE rowcount: {updated}")

    # Post-check
    cur.execute("""SELECT id, external_project_id, name, status
                   FROM projects WHERE id = ANY(%s) ORDER BY id""", (TARGET_IDS,))
    print("  AFTER:")
    all_active = True
    for r in cur.fetchall():
        print(f"    id={r[0]:<4} ext={r[1]!r:<25} name={r[2]!r:<25} status={r[3]}")
        if r[3] != 'active': all_active = False

    assert all_active, "Not all became active"
    c.commit()
    print("\n  COMMIT done.")

    # Also write an assignment_log entry for audit trail
    cur = c.cursor()
    cur.execute("""INSERT INTO project_assignment_logs
                   (project_id, action, from_status, to_status, operator, notes, created_at)
                   SELECT id, 'activated', 'standby', 'active',
                          'claude-cleanup', 'frontend visibility fix (had billing but hidden)', NOW()
                   FROM projects WHERE id = ANY(%s)""", (TARGET_IDS,))
    audit_count = cur.rowcount
    c.commit()
    print(f"  audit log rows inserted: {audit_count}")

except Exception as e:
    c.rollback()
    print(f"\n  ROLLBACK: {e}")
    raise
finally:
    c.close()
