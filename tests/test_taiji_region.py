"""Taiji rows must carry region '' (never NULL).

billing_summary and token_usage dedupe on unique keys that include region.
PostgreSQL treats NULL <> NULL, so a NULL region defeats ON CONFLICT and lets
re-syncs insert duplicates; taiji-ingest-day already stores ''. The collector /
push paths feed these aggregation outputs straight into the upserts, so they
must produce '' as well.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/x")

from app.collectors.taiji_collector import _aggregate_logs, _parse_blob_day

_CREATED_AT = 1778112000  # 2026-05-07 00:00:00 UTC


def _log(**overrides):
    log = {
        "created_at": _CREATED_AT,
        "username": "alice",
        "token_name": "prod",
        "token_id": 1,
        "model_name": "gpt-4o",
        "channel_name": None,
        "quota": 500_000,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "other": None,
    }
    log.update(overrides)
    return log


def test_aggregated_rows_without_channel_use_empty_region():
    rows = _aggregate_logs([_log(), _log()], quota_per_usd=500_000)

    assert len(rows) == 1
    assert rows[0]["region"] == ""
    assert rows[0]["_token_usage"]["region"] == ""


def test_aggregated_rows_keep_channel_as_region():
    rows = _aggregate_logs([_log(channel_name="azure-east")], quota_per_usd=500_000)

    assert rows[0]["region"] == "azure-east"
    # token_usage is not split by channel.
    assert rows[0]["_token_usage"]["region"] == ""


def test_blob_rows_use_empty_region():
    payload = {
        "date_range": {"start_at": "2026-05-07T00:00:00Z"},
        "taiji": {"alice": {"prod": {"details": {"gpt-4o": {"cost": 1.5, "count": 2}}}}},
    }

    rows = _parse_blob_day(payload, default_date="2026-05-07")

    assert len(rows) == 1
    assert rows[0]["region"] == ""
