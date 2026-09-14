"""Shared predicate for attributing a billing row to a service account.

A billing row carries ``data_source_id``; a ``Project`` records the data source
it was discovered under. Historically the two were joined on the project-id
string alone, with a ``provider`` equality alongside it. That is sufficient
while every deployment owns a distinct project-id namespace, and wrong as soon
as two gateways of the same provider share one: a row from gateway A then also
matches gateway B's same-named project, fanning one row out to several and
multiplying it through any ``SUM``.

``Project.data_source_id`` is nullable and a real minority of live rows leave it
unset, so this predicate stays permissive in that case. Tightening it there
would silently drop those rows' cost from every report, which is a worse
failure than the cross-match it would prevent.
"""

from sqlalchemy import or_

from app.models.project import Project


def same_data_source(billing_model):
    """Restrict a billing-to-project join to one data source.

    Pass the billing model being joined -- ``BillingData`` or
    ``BillingDailySummary``; both carry ``data_source_id``. Combine the result
    with the caller's own project-id equality rather than replacing it, because
    the existing joins differ in how they trim either side and unifying that
    here would change which rows match.
    """
    return or_(
        Project.data_source_id.is_(None),
        billing_model.data_source_id == Project.data_source_id,
    )
