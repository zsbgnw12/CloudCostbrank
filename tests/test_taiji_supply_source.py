"""Which supply source a newly discovered taiji token is filed under.

A site (data source) can name its supply source explicitly; otherwise the
choice is inferred, and the inference must be deterministic so that tokens
of one site do not drift between supply sources.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/x")

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.services.sync_service import _resolve_supply_source_for_taiji

DS = 7


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Supplier.__table__.create(engine)
    SupplySource.__table__.create(engine)
    Project.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX uq_project_ss_ext_id_no_ds"))
        conn.execute(text("DROP INDEX uq_project_ss_ds_ext_id"))
        # data_sources.config is JSONB, which SQLite cannot create.
        conn.execute(text("CREATE TABLE data_sources (id INTEGER PRIMARY KEY, config JSON)"))
        conn.execute(text(
            "INSERT INTO suppliers (id, name) VALUES (1, 'Taiji AI 聚合平台'), (2, '另一家'), (3, '未分配资源组'), (4, 'AWS 供应商')"
        ))
        conn.execute(text(
            "INSERT INTO supply_sources (id, supplier_id, provider) VALUES (11, 1, 'taiji'), (12, 2, 'taiji'), (13, 3, 'taiji'), (14, 4, 'aws')"
        ))
    with Session(engine) as s:
        yield s


def _data_source(session, config):
    session.execute(text("INSERT INTO data_sources (id, config) VALUES (:id, :c)"), {"id": DS, "c": json.dumps(config)})


def _projects(session, *supply_source_ids):
    session.execute(Project.__table__.insert(), [
        {"name": f"t{i}", "external_project_id": f"u:t{i}", "supply_source_id": ss, "data_source_id": DS}
        for i, ss in enumerate(supply_source_ids)
    ])


def test_configured_supply_source_wins_over_existing_projects(session):
    _data_source(session, {"supply_source_id": 11})
    _projects(session, 13, 13, 13)  # already stuck in 未分配资源组
    assert _resolve_supply_source_for_taiji(session, DS)[0] == 11


@pytest.mark.parametrize("configured", [14, 999, "abc"])  # not taiji / missing / malformed
def test_invalid_configured_supply_source_falls_back_to_inference(session, configured):
    _data_source(session, {"supply_source_id": configured})
    _projects(session, 12)
    assert _resolve_supply_source_for_taiji(session, DS)[0] == 12


def test_inference_follows_the_majority_of_the_sites_projects(session):
    _data_source(session, {})
    _projects(session, 12, 11, 11)
    assert _resolve_supply_source_for_taiji(session, DS)[0] == 11


def test_inference_breaks_ties_by_lowest_id(session):
    _data_source(session, {})
    _projects(session, 12, 11)
    assert _resolve_supply_source_for_taiji(session, DS)[0] == 11


def test_new_site_without_config_falls_back_to_unassigned_when_ambiguous(session):
    # Two user-level taiji supply sources exist, so the choice is ambiguous.
    _data_source(session, {})
    assert _resolve_supply_source_for_taiji(session, DS)[0] == 13
