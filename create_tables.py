"""
直接连接 Azure PostgreSQL 创建所有数据表。
用法: python create_tables.py
"""
import sys
import os

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, text

# 同步连接串 —— 从环境变量读取,禁止硬编码凭据。优先 SYNC_DATABASE_URL,回退 app 配置(.env)。
DB_URL = os.environ.get("SYNC_DATABASE_URL")
if not DB_URL:
    from app.config import settings
    DB_URL = settings.SYNC_DATABASE_URL
if not DB_URL or "user:password@localhost" in DB_URL:
    raise SystemExit("请设置环境变量 SYNC_DATABASE_URL(禁止硬编码数据库凭据)。")


def main():
    print(f"连接数据库: {DB_URL.split('@')[1]}")
    engine = create_engine(DB_URL, echo=False, connect_args={"connect_timeout": 30})

    # 测试连接
    with engine.connect() as conn:
        ver = conn.execute(text("SELECT version()")).scalar()
        print(f"连接成功: {ver[:60]}...")

    # 导入所有模型（确保 metadata 被填充）
    from app.database import Base
    import app.models  # noqa: F401

    print(f"\n准备创建 {len(Base.metadata.tables)} 张表:")
    for name in sorted(Base.metadata.tables.keys()):
        print(f"  - {name}")

    # 创建表
    Base.metadata.create_all(engine)
    print("\n所有表创建完成!")

    # 验证
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        ))
        tables = [row[0] for row in result]
        print(f"\n数据库中已有的表 ({len(tables)}):")
        for t in tables:
            print(f"  ✓ {t}")

    engine.dispose()


if __name__ == "__main__":
    main()
