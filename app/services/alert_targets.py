"""Parsing and billing attribution for ``AlertRule.target_id``.

``target_id`` historically holds one ``Project.external_project_id`` or a
comma-separated list of them. An external id is not unique across data sources:
two gateways of the same provider can each own a project with the same id, and
a rule written that way sums both sites' cost into one number.

A reference may therefore also name the project row itself as ``pid:<Project.id>``.
Such a reference is attributed only to billing rows of that project's data
source (see ``billing_scope.same_data_source``). The prefix is required because
some providers' external ids are plain digits (AWS account ids), so a bare
number cannot be told apart from a legacy external id.

Legacy references keep their original meaning -- matching every billing row
with that project id -- so existing rules evaluate exactly as before.
"""

from dataclasses import dataclass, field

from sqlalchemy import exists, false, func, or_, select

from app.models.billing import BillingData
from app.models.project import Project
from app.services.billing_scope import same_data_source

PROJECT_REF_PREFIX = "pid:"


@dataclass(frozen=True)
class AlertTargets:
    """References parsed from one ``target_id``, in their written order."""

    refs: list[str] = field(default_factory=list)
    project_row_ids: list[int] = field(default_factory=list)
    external_ids: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.refs)


def parse_alert_targets(target_id: str | None) -> AlertTargets:
    """Split ``target_id`` into project-row references and legacy external ids.

    A ``pid:`` entry whose remainder is not an integer is rejected rather than
    treated as an external id, so a malformed reference can never silently
    widen into a cross-site match.
    """
    refs: list[str] = []
    project_row_ids: list[int] = []
    external_ids: list[str] = []
    for raw in (target_id or "").split(","):
        ref = raw.strip()
        if not ref or ref in refs:
            continue
        if ref.startswith(PROJECT_REF_PREFIX):
            number = ref[len(PROJECT_REF_PREFIX):].strip()
            if not number.isdigit():
                raise ValueError(f"invalid project reference: {ref!r}")
            ref = f"{PROJECT_REF_PREFIX}{int(number)}"
            if ref in refs:
                continue
            project_row_ids.append(int(number))
        else:
            external_ids.append(ref)
        refs.append(ref)
    return AlertTargets(refs, project_row_ids, external_ids)


def billing_matches_project_row():
    """Join condition from ``BillingData`` to the ``Project`` it belongs to."""
    return (BillingData.project_id == func.trim(Project.external_project_id)) & same_data_source(
        BillingData
    )


def billing_target_filter(targets: AlertTargets):
    """WHERE clause selecting the billing rows attributed to ``targets``.

    Usable from both the sync alert checker and the async API because it is a
    plain expression: project-row references become a correlated EXISTS rather
    than a pre-loaded id list.
    """
    clauses = []
    if targets.external_ids:
        clauses.append(BillingData.project_id.in_(targets.external_ids))
    if targets.project_row_ids:
        clauses.append(
            exists(
                select(Project.id).where(
                    Project.id.in_(targets.project_row_ids),
                    billing_matches_project_row(),
                )
            )
        )
    if not clauses:
        return false()
    return or_(*clauses)
