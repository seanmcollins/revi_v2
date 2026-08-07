"""Fixture metric contracts for the analytical contract suite.

Small, catalog-aligned contracts standing in for the pinned pack until the
pack registry wires real metric content. Column/measure names follow
``warehouse/catalog/measures.yaml`` exactly:

- ``cash_posted``      — additive: posted payer payments (transaction/POST).
- ``claim_count``      — additive: distinct claims (claim/SERVICE).
- ``denial_count``     — additive: distinct denials (denial/REMIT).
- ``denied_amount``    — additive: denied dollars (denial/REMIT).
- ``denial_rate``      — ratio: distinct claims with a CARC-197 denial over
  distinct denied claims, by denial date (denial/REMIT). The adapter returns
  ``denial_rate__num`` / ``denial_rate__den``; the kernel divides.
- ``ar_balance``       — snapshot: open balance (billed minus money applied
  as-of) over open claim inventory (claim, aged by SERVICE).
- ``open_claim_count`` — snapshot: distinct open claims.
"""

from __future__ import annotations

from revi_calculation_contracts.contract import (
    CountDistinct,
    Filtered,
    MetricContract,
    MetricKind,
    MetricUnit,
    SignConvention,
    Sum,
)
from revi_kernel.filters import Predicate, PredicateOp
from revi_kernel.refs import POST, REMIT, SERVICE, SUBMISSION, DimensionRef, EntityGrain, FieldRef

_COMMON_DIM_IDS = (
    "payer",
    "payer_type",
    "financial_class",
    "plan",
    "product_type",
    "facility",
    "region",
    "service_line",
    "claim_type",
)
_SCOPE_DIMS_COMMON = tuple(DimensionRef(d) for d in _COMMON_DIM_IDS)
_SCOPE_DIMS_DENIAL = (
    *_SCOPE_DIMS_COMMON,
    DimensionRef("carc"),
    DimensionRef("group_code"),
    DimensionRef("denial_category"),
    DimensionRef("appeal_status"),
)

CASH_POSTED = MetricContract(
    id="cash_posted",
    version=1,
    kind=MetricKind.FLOW,
    entity_grain=EntityGrain.TRANSACTION,
    numerator=Sum(FieldRef("payment_cents")),
    denominator=None,
    primary_date_basis=POST,
    allowed_date_bases=(POST, SERVICE, SUBMISSION),
    scope_dimensions=_SCOPE_DIMS_COMMON,
    sign=SignConvention.HIGHER_IS_GOOD,
    unit=MetricUnit.MONEY_CENTS,
    description="Posted payer payments (cash), POST date basis.",
)

CLAIM_COUNT = MetricContract(
    id="claim_count",
    version=1,
    kind=MetricKind.FLOW,
    entity_grain=EntityGrain.CLAIM,
    numerator=CountDistinct(FieldRef("claim_id")),
    denominator=None,
    primary_date_basis=SERVICE,
    allowed_date_bases=(SERVICE, SUBMISSION),
    scope_dimensions=_SCOPE_DIMS_COMMON,
    sign=SignConvention.NEUTRAL,
    unit=MetricUnit.COUNT,
    description="Distinct claims in scope.",
)

DENIAL_COUNT = MetricContract(
    id="denial_count",
    version=1,
    kind=MetricKind.FLOW,
    entity_grain=EntityGrain.DENIAL,
    numerator=CountDistinct(FieldRef("denial_id")),
    denominator=None,
    primary_date_basis=REMIT,
    allowed_date_bases=(REMIT, SERVICE, SUBMISSION),
    scope_dimensions=_SCOPE_DIMS_DENIAL,
    sign=SignConvention.HIGHER_IS_BAD,
    unit=MetricUnit.COUNT,
    description="Distinct denial records, dated by their remit.",
)

DENIED_AMOUNT = MetricContract(
    id="denied_amount",
    version=1,
    kind=MetricKind.FLOW,
    entity_grain=EntityGrain.DENIAL,
    numerator=Sum(FieldRef("denied_amount_cents")),
    denominator=None,
    primary_date_basis=REMIT,
    allowed_date_bases=(REMIT, SERVICE, SUBMISSION),
    scope_dimensions=_SCOPE_DIMS_DENIAL,
    sign=SignConvention.HIGHER_IS_BAD,
    unit=MetricUnit.MONEY_CENTS,
    description="Denied dollars, dated by their remit.",
)

DENIAL_RATE = MetricContract(
    id="denial_rate",
    version=1,
    kind=MetricKind.FLOW,
    entity_grain=EntityGrain.DENIAL,
    numerator=Filtered(
        inner=CountDistinct(FieldRef("claim_id")),
        where=Predicate(DimensionRef("carc"), PredicateOp.EQ, (197,)),
    ),
    denominator=CountDistinct(FieldRef("claim_id")),
    primary_date_basis=REMIT,
    allowed_date_bases=(REMIT, SERVICE, SUBMISSION),
    scope_dimensions=_SCOPE_DIMS_DENIAL,
    sign=SignConvention.HIGHER_IS_BAD,
    unit=MetricUnit.RATIO,
    description=(
        "Share of denied claims carrying a CARC-197 (prior auth) denial, by denial date. "
        "Component sums only; the kernel computes the ratio."
    ),
)

AR_BALANCE = MetricContract(
    id="ar_balance",
    version=1,
    kind=MetricKind.SNAPSHOT,
    entity_grain=EntityGrain.CLAIM,
    numerator=Sum(FieldRef("open_balance_cents")),
    denominator=None,
    primary_date_basis=SERVICE,
    allowed_date_bases=(SERVICE, SUBMISSION),
    scope_dimensions=(*_SCOPE_DIMS_COMMON, DimensionRef("ar_age_bucket")),
    sign=SignConvention.HIGHER_IS_BAD,
    unit=MetricUnit.MONEY_CENTS,
    description="Open AR balance as-of: billed minus money applied on/before the as-of date.",
)

OPEN_CLAIM_COUNT = MetricContract(
    id="open_claim_count",
    version=1,
    kind=MetricKind.SNAPSHOT,
    entity_grain=EntityGrain.CLAIM,
    numerator=CountDistinct(FieldRef("claim_id")),
    denominator=None,
    primary_date_basis=SERVICE,
    allowed_date_bases=(SERVICE, SUBMISSION),
    scope_dimensions=(*_SCOPE_DIMS_COMMON, DimensionRef("ar_age_bucket")),
    sign=SignConvention.HIGHER_IS_BAD,
    unit=MetricUnit.COUNT,
    description="Distinct claims in open inventory as-of.",
)

FIXTURE_METRICS: dict[str, MetricContract] = {
    contract.id: contract
    for contract in (
        CASH_POSTED,
        CLAIM_COUNT,
        DENIAL_COUNT,
        DENIED_AMOUNT,
        DENIAL_RATE,
        AR_BALANCE,
        OPEN_CLAIM_COUNT,
    )
}


def fixture_metrics(metric_id: str) -> MetricContract | None:
    """Metric resolver in the shape the DuckDB repository constructor expects."""
    return FIXTURE_METRICS.get(metric_id)
