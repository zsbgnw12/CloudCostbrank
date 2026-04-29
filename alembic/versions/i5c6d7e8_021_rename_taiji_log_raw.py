"""021 把 taiji_log_raw rename 为 billing_raw_taiji

按 v3-final §5 规格：用 to_regclass 运行时检测：
  - 表存在 → ALTER TABLE RENAME（连同索引、PK 约束）
  - 表不存在 → CREATE TABLE billing_raw_taiji（生产可能从未 boot 应用，没靠 create_all 建出旧表）

字段定义参考 app/models/taiji_log_raw.py 现有定义。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "i5c6d7e8"
down_revision = "h4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PL/pgSQL 块在 PG 端原子判断 + 执行
    op.execute(text("""
        DO $$
        BEGIN
            IF to_regclass('public.taiji_log_raw') IS NOT NULL THEN
                -- 表存在：原子 RENAME 路径
                ALTER TABLE taiji_log_raw RENAME TO billing_raw_taiji;
                ALTER TABLE billing_raw_taiji
                    RENAME CONSTRAINT pk_taiji_log_raw TO pk_billing_raw_taiji;
                ALTER INDEX ix_taiji_log_raw_ds_date
                    RENAME TO ix_billing_raw_taiji_ds_date;
                ALTER INDEX ix_taiji_log_raw_ds_ingested
                    RENAME TO ix_billing_raw_taiji_ds_ingested;
            ELSE
                -- 表不存在：直接建新表（字段对齐 app/models/taiji_log_raw.py）
                CREATE TABLE billing_raw_taiji (
                    id BIGINT NOT NULL,
                    data_source_id INTEGER NOT NULL
                        REFERENCES data_sources(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    created_at BIGINT NOT NULL,
                    type SMALLINT,
                    user_id INTEGER,
                    username VARCHAR(200),
                    token_id INTEGER NOT NULL,
                    token_name VARCHAR(200),
                    channel_id INTEGER,
                    channel_name VARCHAR(200),
                    model_name VARCHAR(200) NOT NULL,
                    quota BIGINT NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    use_time INTEGER,
                    is_stream SMALLINT,
                    other JSONB,
                    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                    CONSTRAINT pk_billing_raw_taiji PRIMARY KEY (data_source_id, id)
                );
                CREATE INDEX ix_billing_raw_taiji_ds_date
                    ON billing_raw_taiji (data_source_id, date);
                CREATE INDEX ix_billing_raw_taiji_ds_ingested
                    ON billing_raw_taiji (data_source_id, ingested_at);
            END IF;
        END
        $$
    """))


def downgrade() -> None:
    # 镜像：把 billing_raw_taiji 改回 taiji_log_raw（如果存在）
    op.execute(text("""
        DO $$
        BEGIN
            IF to_regclass('public.billing_raw_taiji') IS NOT NULL THEN
                ALTER TABLE billing_raw_taiji RENAME TO taiji_log_raw;
                ALTER TABLE taiji_log_raw
                    RENAME CONSTRAINT pk_billing_raw_taiji TO pk_taiji_log_raw;
                ALTER INDEX ix_billing_raw_taiji_ds_date
                    RENAME TO ix_taiji_log_raw_ds_date;
                ALTER INDEX ix_billing_raw_taiji_ds_ingested
                    RENAME TO ix_taiji_log_raw_ds_ingested;
            END IF;
        END
        $$
    """))
