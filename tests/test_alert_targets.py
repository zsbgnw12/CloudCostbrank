"""Alert-rule targets must not merge cost across data sources.

Two sites of one provider can each own a project with the same external id. A
rule that names the project row (``pid:<Project.id>``) must count only its own
site's billing rows; a legacy rule naming the bare external id keeps its
original meaning of matching every row with that id.
"""

import asyncio
import datetime as dt
import os
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/x")

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api import alerts as alerts_api
from app.models.alert import AlertRule
from app.models.project import Project
from app.models.supply_source import SupplySource
from app.services import alert_service
from app.services.alert_targets import parse_alert_targets

TODAY = dt.date.today()
YESTERDAY = TODAY - dt.timedelta(days=1)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SupplySource.__table__.create(engine)
    Project.__table__.create(engine)
    AlertRule.__table__.create(engine)
    with engine.begin() as conn:
        # The partial unique indexes compile to plain ones on SQLite, which
        # would forbid exactly the cross-site duplicate under test.
        conn.execute(text("DROP INDEX uq_project_ss_ext_id_no_ds"))
        conn.execute(text("DROP INDEX uq_project_ss_ds_ext_id"))
        # billing_summary carries a JSONB column SQLite cannot create; the
        # queries under test only touch these columns.
        conn.execute(text(
            "CREATE TABLE billing_summary (id INTEGER PRIMARY KEY, date DATE, provider VARCHAR,"
            " data_source_id INTEGER, project_id VARCHAR, cost NUMERIC, cost_usd NUMERIC)"
        ))
    with Session(engine) as s:
        yield s


def _seed(session):
    session.execute(text("INSERT INTO supply_sources (id, supplier_id, provider) VALUES (1, 1, 'taiji')"))
    session.execute(Project.__table__.insert(), [
        # Same external id on two sites, plus one project without a data source.
        {"id": 11, "name": "site-a", "external_project_id": "alice:key", "supply_source_id": 1, "data_source_id": 1},
        {"id": 12, "name": "site-b", "external_project_id": "alice:key", "supply_source_id": 1, "data_source_id": 2},
        {"id": 13, "name": "legacy", "external_project_id": "bob:key", "supply_source_id": 1, "data_source_id": None},
    ])
    session.execute(text(
        "INSERT INTO billing_summary (date, provider, data_source_id, project_id, cost, cost_usd)"
        " VALUES (:date, :provider, :data_source_id, :project_id, :cost, :cost_usd)"
    ), [
        {"date": YESTERDAY, "provider": "taiji", "data_source_id": 1, "project_id": "alice:key", "cost": 10, "cost_usd": 10},
        {"date": YESTERDAY, "provider": "taiji", "data_source_id": 2, "project_id": "alice:key", "cost": 100, "cost_usd": 100},
        {"date": YESTERDAY, "provider": "taiji", "data_source_id": 1, "project_id": "bob:key", "cost": 1, "cost_usd": 1},
        {"date": YESTERDAY, "provider": "taiji", "data_source_id": 2, "project_id": "bob:key", "cost": 2, "cost_usd": 2},
    ])
    session.commit()


def _rule(**kw):
    base = dict(id=1, name="r", target_type="project_group", threshold_value=Decimal("1"), is_active=True)
    base.update(kw)
    return AlertRule(**base)


@pytest.fixture
def triggered(monkeypatch):
    fired = []
    monkeypatch.setattr(
        alert_service, "_trigger_alert",
        lambda session, rule, actual, custom_message=None: fired.append((actual, custom_message)),
    )
    return fired


@pytest.mark.parametrize("target_id, expected", [
    ("", ([], [])),
    ("alice:key", ([], ["alice:key"])),
    (" pid:11 , alice:key,pid:011,pid:12 ", ([11, 12], ["alice:key"])),
    # AWS account ids are plain digits and must stay external ids.
    ("123456789012", ([], ["123456789012"])),
])
def test_parse_alert_targets(target_id, expected):
    targets = parse_alert_targets(target_id)
    assert (targets.project_row_ids, targets.external_ids) == expected


@pytest.mark.parametrize("target_id", ["pid:", "pid:abc", "pid:-1"])
def test_parse_rejects_malformed_project_reference(target_id):
    with pytest.raises(ValueError):
        parse_alert_targets(target_id)


@pytest.mark.parametrize("target_id, expected", [
    ("pid:11", Decimal("10")),                 # site A only
    ("pid:12", Decimal("100")),                # site B only
    ("pid:11,pid:12", Decimal("110")),         # both, each counted once
    ("pid:13", Decimal("3")),                  # no data source: permissive, as elsewhere
    ("alice:key", Decimal("110")),             # legacy: unchanged, all sites
    ("pid:11,bob:key", Decimal("13")),         # mixed
])
def test_multi_budget_sums_by_target(session, triggered, target_id, expected):
    _seed(session)
    alert_service._check_monthly_budget_multi(session, _rule(target_id=target_id, threshold_type="monthly_budget_multi"), TODAY)
    assert len(triggered) == 1
    assert Decimal(str(triggered[0][0])) == expected


def test_multi_budget_message_names_external_ids(session, triggered):
    _seed(session)
    alert_service._check_monthly_budget_multi(session, _rule(target_id="pid:12", threshold_type="monthly_budget_multi"), TODAY)
    assert "alice:key" in triggered[0][1]
    assert "pid:" not in triggered[0][1]


@pytest.mark.parametrize("target_id, expected", [
    ("pid:12", Decimal("100")),
    ("alice:key", Decimal("110")),
])
def test_single_project_rules(session, triggered, target_id, expected):
    _seed(session)
    rule = _rule(target_type="project", target_id=target_id, threshold_type="account_lifetime_quota")
    alert_service._check_account_lifetime_quota(session, rule)
    assert Decimal(str(triggered[0][0])) == expected
    assert alert_service._get_daily_cost(session, rule, YESTERDAY) == expected


class _AsyncAdapter:
    """Just enough of AsyncSession for the rule-status endpoint."""

    def __init__(self, session):
        self._session = session

    async def execute(self, stmt):
        return self._session.execute(stmt)


def test_rule_status_scopes_project_references(session):
    _seed(session)
    session.add_all([
        _rule(id=1, target_id="pid:11", threshold_type="monthly_budget_multi", threshold_value=Decimal("1000")),
        _rule(id=2, target_id="alice:key", threshold_type="monthly_budget_multi", threshold_value=Decimal("1000")),
        _rule(id=3, target_type="project", target_id="pid:12", threshold_type="monthly_budget", threshold_value=Decimal("1000")),
    ])
    session.commit()
    principal = SimpleNamespace(roles=["cloud_admin"])
    items = asyncio.run(alerts_api.rule_status(month=YESTERDAY.strftime("%Y-%m"), db=_AsyncAdapter(session), principal=principal))
    by_rule = {i.rule_id: i for i in items}
    assert by_rule[1].actual == 10
    assert by_rule[2].actual == 110
    assert by_rule[3].actual == 100
    assert by_rule[3].account_name == "site-b"
    assert by_rule[3].external_project_id == "alice:key"
