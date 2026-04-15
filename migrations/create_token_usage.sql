-- Create token_usage table for LLM token metering
CREATE TABLE IF NOT EXISTS token_usage (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    provider        VARCHAR(10) NOT NULL,
    data_source_id  INTEGER NOT NULL REFERENCES data_sources(id),
    model_id        VARCHAR(200) NOT NULL,
    model_name      VARCHAR(200),
    region          VARCHAR(50),

    request_count       BIGINT DEFAULT 0,
    input_tokens        BIGINT DEFAULT 0,
    output_tokens       BIGINT DEFAULT 0,
    cache_read_tokens   BIGINT DEFAULT 0,
    cache_write_tokens  BIGINT DEFAULT 0,
    total_tokens        BIGINT DEFAULT 0,

    input_cost      DECIMAL(20,6) DEFAULT 0,
    output_cost     DECIMAL(20,6) DEFAULT 0,
    total_cost      DECIMAL(20,6) DEFAULT 0,
    currency        VARCHAR(10) DEFAULT 'USD',

    additional_info JSONB DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT NOW(),

    CONSTRAINT uix_token_usage_dedup
        UNIQUE (date, provider, data_source_id, model_id, region)
);

CREATE INDEX IF NOT EXISTS ix_token_usage_date ON token_usage(date);
CREATE INDEX IF NOT EXISTS ix_token_usage_provider_date ON token_usage(provider, date);
CREATE INDEX IF NOT EXISTS ix_token_usage_model_date ON token_usage(model_id, date);
