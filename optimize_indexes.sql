-- ============================================================
-- CloudCost 性能优化 SQL 脚本
-- 包含: 索引优化 + 表分区准备
-- 执行前请备份数据!
-- ============================================================

-- ==================== 1. 索引优化 ====================

-- 删除冗余索引 (被 ix_billing_ds_date 和分区键覆盖)
DROP INDEX IF EXISTS ix_billing_date;

-- 升级为 covering index (包含 cost，避免聚合查询回表)
DROP INDEX IF EXISTS ix_billing_provider_date;
CREATE INDEX ix_billing_provider_date_cost ON billing_data (provider, date, cost);

DROP INDEX IF EXISTS ix_billing_project_date;
CREATE INDEX ix_billing_project_date_cost ON billing_data (project_id, date, cost);

-- daily_summary 表索引已在 ORM 模型中定义，create_tables.py 会自动创建

-- ==================== 2. 表分区 (可选，数据量大时启用) ====================
-- 以下是将 billing_data 转换为按月分区表的步骤
-- 注意: 需要停机维护窗口执行

-- 步骤 1: 重命名原表
-- ALTER TABLE billing_data RENAME TO billing_data_old;

-- 步骤 2: 创建分区主表
-- CREATE TABLE billing_data (
--     id SERIAL,
--     date DATE NOT NULL,
--     provider VARCHAR(10) NOT NULL,
--     data_source_id INTEGER NOT NULL REFERENCES data_sources(id),
--     project_id VARCHAR(200),
--     project_name VARCHAR(200),
--     product VARCHAR(200),
--     usage_type VARCHAR(300),
--     region VARCHAR(50),
--     cost DECIMAL(20,6) NOT NULL,
--     usage_quantity DECIMAL(20,6) DEFAULT 0,
--     usage_unit VARCHAR(50),
--     currency VARCHAR(10) DEFAULT 'USD',
--     tags JSONB DEFAULT '{}',
--     additional_info JSONB DEFAULT '{}',
--     created_at TIMESTAMP DEFAULT now(),
--     CONSTRAINT uix_billing_dedup UNIQUE (date, data_source_id, project_id, product, usage_type, region)
-- ) PARTITION BY RANGE (date);

-- 步骤 3: 创建月分区 (示例: 2025-01 到 2026-12)
-- DO $$
-- DECLARE
--     y INT;
--     m INT;
--     start_date DATE;
--     end_date DATE;
--     partition_name TEXT;
-- BEGIN
--     FOR y IN 2025..2026 LOOP
--         FOR m IN 1..12 LOOP
--             start_date := make_date(y, m, 1);
--             IF m = 12 THEN
--                 end_date := make_date(y + 1, 1, 1);
--             ELSE
--                 end_date := make_date(y, m + 1, 1);
--             END IF;
--             partition_name := format('billing_data_%s_%s', y, lpad(m::text, 2, '0'));
--             EXECUTE format(
--                 'CREATE TABLE %I PARTITION OF billing_data FOR VALUES FROM (%L) TO (%L)',
--                 partition_name, start_date, end_date
--             );
--         END LOOP;
--     END LOOP;
-- END $$;

-- 步骤 4: 在分区表上重建索引
-- CREATE INDEX ix_billing_ds_date ON billing_data (data_source_id, date);
-- CREATE INDEX ix_billing_provider_date_cost ON billing_data (provider, date, cost);
-- CREATE INDEX ix_billing_project_date_cost ON billing_data (project_id, date, cost);

-- 步骤 5: 迁移数据
-- INSERT INTO billing_data SELECT * FROM billing_data_old;

-- 步骤 6: 验证数据完整性
-- SELECT count(*) FROM billing_data;
-- SELECT count(*) FROM billing_data_old;

-- 步骤 7: 删除旧表
-- DROP TABLE billing_data_old;

-- ==================== 3. 自动分区创建函数 ====================
-- 生产环境建议使用 pg_partman 扩展自动管理分区
-- 或用以下函数定期创建未来分区:

-- CREATE OR REPLACE FUNCTION create_billing_partition(target_date DATE)
-- RETURNS VOID AS $$
-- DECLARE
--     start_date DATE;
--     end_date DATE;
--     partition_name TEXT;
-- BEGIN
--     start_date := date_trunc('month', target_date)::date;
--     end_date := (start_date + interval '1 month')::date;
--     partition_name := format('billing_data_%s_%s',
--         extract(year from start_date)::int,
--         lpad(extract(month from start_date)::int::text, 2, '0'));
--     
--     IF NOT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = partition_name) THEN
--         EXECUTE format(
--             'CREATE TABLE %I PARTITION OF billing_data FOR VALUES FROM (%L) TO (%L)',
--             partition_name, start_date, end_date
--         );
--         RAISE NOTICE 'Created partition: %', partition_name;
--     END IF;
-- END;
-- $$ LANGUAGE plpgsql;
