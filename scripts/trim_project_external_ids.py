"""One-off / maintenance: TRIM projects.external_project_id. Run from cloudcost/."""
from pathlib import Path

import psycopg2


def load_sync_url() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("SYNC_DATABASE_URL="):
            u = line.split("=", 1)[1].strip().strip('"')
            for p in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
                if u.startswith(p):
                    u = "postgresql://" + u[len(p) :]
                    break
            return u
    raise SystemExit("SYNC_DATABASE_URL not in .env")


if __name__ == "__main__":
    import os

    os.chdir(Path(__file__).resolve().parent.parent)
    conn = psycopg2.connect(load_sync_url())
    cur = conn.cursor()
    cur.execute(
        "UPDATE projects SET external_project_id = trim(external_project_id) "
        "WHERE external_project_id <> trim(external_project_id)"
    )
    print("trimmed rows:", cur.rowcount)
    conn.commit()
    cur.close()
    conn.close()
