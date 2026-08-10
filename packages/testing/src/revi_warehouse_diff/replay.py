"""Corpus replay: recompute every published finding value.

The audit unit is one **published finding value** — the numbers that reach a
human. For each of them the replay:

1. reads the value's name to decide what quantity it claims to be
   (``cash_posted``, ``cash_posted__prior``, ``current_cents``, ``pct_change``…);
2. resolves the cell's own coordinate from the answer's published evidence
   frames, and re-checks that coordinate against the finding's own title so
   the harness never audits a slice the answer did not claim;
3. rebuilds the audit context from the answer's published context header
   (window, basis, scope, cohort, watermark);
4. derives the number again with plain SQL and compares.

Tolerances (stated, not implied)
--------------------------------
* ``money_cents`` and ``count`` — **exact integer equality**. A cent is a
  cent.
* ``ratio`` and ``days`` — absolute ``1e-6``. Published ratios carry six
  decimal places, so this is exactly one unit in the last published place.

Outcomes
--------
``matched`` · ``basis_ambiguous`` (reproduced exactly, but only on an allowed
basis other than the one the answer's context header published — the number is
contract-legal and its provenance is under-disclosed) · ``diverged`` ·
``underivable`` (with a reason code) · ``error``.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from revi_warehouse_diff.archaeology import ARCHAEOLOGY, LIVE, classify
from revi_warehouse_diff.corpus import FrameCell, PublishedFinding, StoredInvestigation
from revi_warehouse_diff.deriver import (
    NO_MUTATION,
    AuditContext,
    Derivation,
    DerivationRun,
    Mutation,
    Predicate,
    Underivable,
)
from revi_warehouse_diff.explain import explain
from revi_warehouse_diff.governed import MetricContract

RATIO_TOLERANCE = Decimal("1e-6")

MATCHED = "matched"
BASIS_AMBIGUOUS = "basis_ambiguous"
#: A delta / percent-change that does not equal the arithmetic over the RAW
#: ratio-of-sums, but does equal it exactly over the finding's own published
#: (6-decimal-rounded) components — and whose components each reproduced.
#: The value is sound; its inputs were rounded before the arithmetic ran.
ROUNDED_INPUTS = "matched_rounded_inputs"
#: A cell the product published as an UPPER BOUND rather than a measurement
#: (small-cell suppression: ``<metric>__is_bound: true``). The audit derives
#: the true value and asserts the bound actually bounds it, and that the
#: published bound population is the real denominator. A bound that does not
#: bound is a ``diverged``, and a serious one.
BOUND_UPHELD = "bound_upheld"
DIVERGED = "diverged"
UNDERIVABLE = "underivable"
ERROR = "error"


@dataclass(frozen=True)
class AuditedValue:
    investigation_id: str
    session_id: str
    question: str | None
    referent: str
    finding_title: str
    value_name: str
    metric_id: str | None
    window: str
    outcome: str
    published: Any = None
    derived: Any = None
    reason: str = ""
    basis: str = ""
    published_basis: str = ""
    coordinate: tuple[str, ...] = ()
    coordinate_confirmed_by: str = ""
    sql: tuple[str, ...] = ()
    #: ``live`` or ``archaeology`` — which disclosure contract was in force
    #: when this answer was published (see :mod:`revi_warehouse_diff.archaeology`).
    era: str = LIVE

    @property
    def is_failure(self) -> bool:
        return self.outcome in (DIVERGED, ERROR)

    @property
    def is_live_failure(self) -> bool:
        """A divergence the engine would produce again today."""
        return self.is_failure and self.era == LIVE


@dataclass
class ReplayReport:
    audited: list[AuditedValue] = field(default_factory=list)
    investigations: int = 0
    findings: int = 0
    values_seen: int = 0
    warehouse_queries: int = 0
    seconds: float = 0.0

    def counts(self) -> Counter[str]:
        return Counter(a.outcome for a in self.audited)

    def reasons(self) -> Counter[str]:
        return Counter(a.reason.split(":")[0] for a in self.audited if a.outcome == UNDERIVABLE)

    @property
    def divergences(self) -> list[AuditedValue]:
        return [a for a in self.audited if a.is_failure]

    @property
    def live_divergences(self) -> list[AuditedValue]:
        """The fix queue. Fossils are reported separately, never dropped."""
        return [a for a in self.audited if a.is_live_failure]

    @property
    def archaeology_divergences(self) -> list[AuditedValue]:
        return [a for a in self.audited if a.is_failure and a.era == ARCHAEOLOGY]


# --------------------------------------------------------------------------
# value-name grammar
# --------------------------------------------------------------------------

#: Names whose quantity is the finding's single metric ref, per window.
_WINDOW_ALIASES = {"current_cents": "current", "prior_cents": "prior"}
#: Names that are arithmetic over the current/prior pair, not a fresh query.
_ARITHMETIC = {"delta_cents", "pct_change"}
#: The period a finding says its own figure was computed over, published as
#: named values because prose is not a contract. A playbook probe may declare
#: its own window (``daily_portfolio``: ``{4, week, full_periods}``), which
#: the planner applies over the investigation window; the finding states the
#: window it was actually read over and publishes it here. The audit DERIVES
#: over this window when it is present — auditing such a cell over the
#: investigation window is auditing a number nobody published.
WINDOW_START_SUFFIX = "__window_start"
WINDOW_END_SUFFIX = "__window_end"
#: …and the range its comparison was taken against, when the probe's own
#: window moved that too. The planner derives a probe's prior twin from the
#: PROBE's window, so this is not the answer's comparison range shifted — it
#: is a different range, and deriving the shift here would be guessing at a
#: rule the product publishes.
PRIOR_WINDOW_START_SUFFIX = "__prior_window_start"
PRIOR_WINDOW_END_SUFFIX = "__prior_window_end"

#: Suffixes that mark a value as metadata about a metric rather than the metric.
_META_SUFFIXES = (
    "__is_bound",
    "__bound",
    "__bound_population",
    WINDOW_START_SUFFIX,
    WINDOW_END_SUFFIX,
    PRIOR_WINDOW_START_SUFFIX,
    PRIOR_WINDOW_END_SUFFIX,
)


@dataclass(frozen=True)
class ValueClaim:
    """What a published value name claims to be."""

    kind: str  # "metric" | "arithmetic" | "unsupported"
    metric_id: str | None = None
    window: str = "current"
    arithmetic: str = ""
    reason: str = ""


def classify_value(name: str, finding: PublishedFinding, contracts: dict[str, MetricContract]) -> ValueClaim:
    if name in _WINDOW_ALIASES:
        if len(finding.metric_ids) != 1:
            return ValueClaim("unsupported", reason=f"window_alias_ambiguous:{len(finding.metric_ids)}")
        return ValueClaim("metric", finding.metric_ids[0], _WINDOW_ALIASES[name])
    if name in _ARITHMETIC:
        if len(finding.metric_ids) != 1:
            return ValueClaim("unsupported", reason=f"window_alias_ambiguous:{len(finding.metric_ids)}")
        metric = finding.metric_ids[0]
        if _finding_value(finding, f"{metric}__is_bound") is True:
            return ValueClaim("unsupported", reason=f"bounded_arithmetic:{name}")
        return ValueClaim("arithmetic", metric, arithmetic=name)
    if name in contracts:
        if _finding_value(finding, f"{name}__is_bound") is True:
            return ValueClaim("bound", name, "current")
        return ValueClaim("metric", name, "current")
    if name.endswith("__prior") and name[: -len("__prior")] in contracts:
        metric = name[: -len("__prior")]
        # A bounded finding publishes a ceiling for BOTH windows, but only the
        # current window carries the `__is_bound` marker; the prior value is a
        # ceiling with no marker of its own. Audit it as the bound it is.
        if _finding_value(finding, f"{metric}__is_bound") is True:
            return ValueClaim("bound", metric, "prior")
        return ValueClaim("metric", metric, "prior")
    if name.endswith("__delta") and name[: -len("__delta")] in contracts:
        metric = name[: -len("__delta")]
        if _finding_value(finding, f"{metric}__is_bound") is True:
            return ValueClaim("unsupported", reason="bounded_arithmetic:delta")
        return ValueClaim("arithmetic", metric, arithmetic="delta")
    for suffix in _META_SUFFIXES:
        if name.endswith(suffix):
            return ValueClaim("unsupported", reason=f"value_shape_unsupported:{suffix.lstrip('_')}")
    return ValueClaim("unsupported", reason=f"value_shape_unsupported:{name}")


# --------------------------------------------------------------------------
# coordinate resolution
# --------------------------------------------------------------------------


def _finding_value(finding: PublishedFinding, name: str) -> Any:
    for candidate, value in finding.values:
        if candidate == name:
            return value
    return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _values_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return str(left) == str(right)


@dataclass(frozen=True)
class ResolvedCoordinate:
    coordinate: tuple[Predicate, ...]
    time_bucket: tuple[str, dt.date] | None
    confirmed_by: str


def resolve_coordinate(
    finding: PublishedFinding,
    metric_id: str,
    window: str,
    published_value: Any,
    cells: tuple[FrameCell, ...],
) -> ResolvedCoordinate:
    """Which published cell is this finding value quoting?

    Coordinates come from the answer's own evidence frames; the choice is then
    confirmed against the finding's published title. Only the group-key
    coordinate is taken from the frame — never a measured value.
    """
    candidates = [c for c in cells if c.metric_id == metric_id and c.window == window]
    if not candidates:
        raise Underivable("no_published_cell", f"{metric_id}/{window}")
    text = f"{finding.title} {finding.statement}"
    by_title = [c for c in candidates if all(label in text for label in c.labels)]
    pool = by_title or candidates
    confirmation = "title" if by_title else "frame_only"

    by_value = [c for c in pool if _values_equal(c.published, published_value)]
    if by_value:
        distinct = {(c.coordinate, c.time_bucket) for c in by_value}
        if len(distinct) == 1:
            cell = by_value[0]
            return ResolvedCoordinate(cell.coordinate, cell.time_bucket, confirmation + "+value")
        raise Underivable("slice_ambiguous", f"{metric_id}/{window}/{len(distinct)}")

    distinct = {(c.coordinate, c.time_bucket) for c in pool}
    if len(distinct) == 1:
        cell = pool[0]
        return ResolvedCoordinate(cell.coordinate, cell.time_bucket, confirmation)
    raise Underivable("slice_unresolved", f"{metric_id}/{window}/{len(distinct)}")


# --------------------------------------------------------------------------
# the replay
# --------------------------------------------------------------------------


#: Reads the distinct values a column actually holds, for §6.6 resolution.
ValueDomain = Any  # Callable[[str, str, str], list[str]]  (schema, view, column)


class CorpusReplay:
    def __init__(
        self,
        run: DerivationRun,
        contracts: dict[str, MetricContract],
        schema_for_watermark: dict[str, str],
        mutation: Mutation = NO_MUTATION,
        newest_data_date: dict[str, dt.date] | None = None,
        explain_divergences: bool = True,
        value_domain: ValueDomain | None = None,
    ) -> None:
        self._run = run
        self._contracts = contracts
        self._schemas = schema_for_watermark
        self._mutation = mutation
        self._newest = newest_data_date or {}
        self._explain = explain_divergences and not mutation.active
        self._value_domain = value_domain
        self._domain_cache: dict[tuple[str, str, str], dict[str, list[str]]] = {}
        #: Resolutions made while building the context for the value being
        #: audited. Reset per value in :meth:`audit_value` and copied onto
        #: the result, so no resolution is applied without being reported.
        self._notes: list[str] = []

    # -- §6.6 step 4b: value resolution ------------------------------------

    def _domain(self, schema: str, view: str, column: str) -> dict[str, list[str]]:
        """``lowercased value -> the canonical value(s)`` for one column."""
        key = (schema, view, column)
        cached = self._domain_cache.get(key)
        if cached is not None:
            return cached
        index: dict[str, list[str]] = {}
        if self._value_domain is not None:
            for value in self._value_domain(schema, view, column):
                if isinstance(value, str):
                    index.setdefault(value.lower(), []).append(value)
        self._domain_cache[key] = index
        return index

    def _resolve_predicates(
        self,
        predicates: tuple[Predicate, ...],
        schema: str,
        entity: str,
        notes: list[str],
    ) -> tuple[Predicate, ...]:
        """Read a published filter value the way §6.6 step 4b reads it.

        The product resolves a filter value against the certified domain
        before it queries — ``'general surgery'`` is queried as
        ``'General Surgery'``, and the answer publishes the corrected value
        with the analyst's original beside it. The stored SPEC keeps the
        analyst's spelling, so an audit that replays the spec literally
        selects an EMPTY population and reports a divergence about a
        population the engine never read.

        The rule is re-implemented here, not imported, and it is the narrow
        one the product's own warning states: *the closest match in this data
        differs only in case or punctuation*. So a value that matches nothing
        exactly and exactly ONE value case-insensitively resolves to that
        value; anything else is left alone and diverges as before. Every
        resolution is recorded on the audited value — a silent one would make
        this a way of not seeing the defect the first run found.
        """
        if self._value_domain is None:
            return predicates
        view = self._run.deriver.catalog.base_view(entity)
        if view is None:
            return predicates
        out: list[Predicate] = []
        for predicate in predicates:
            if predicate.op not in ("eq", "in") or not predicate.values:
                out.append(predicate)
                continue
            column = self._run.deriver.catalog.dimension_column(predicate.dimension, entity)
            if column is None:
                out.append(predicate)
                continue
            try:
                index = self._domain(schema, view, column)
            except Exception:
                out.append(predicate)
                continue
            if not index:
                out.append(predicate)
                continue
            resolved: list[Any] = []
            changed = False
            for value in predicate.values:
                if not isinstance(value, str) or value in index.get(value.lower(), ()):
                    resolved.append(value)
                    continue
                matches = index.get(value.lower(), [])
                if len(matches) == 1:
                    resolved.append(matches[0])
                    changed = True
                    notes.append(
                        f"value_resolved: {predicate.dimension}={value!r} read as "
                        f"{matches[0]!r} (§6.6 step 4b — differs only in case); the stored "
                        "spec keeps the analyst's spelling, the engine queried the "
                        "certified value"
                    )
                else:
                    resolved.append(value)
            out.append(replace(predicate, values=tuple(resolved)) if changed else predicate)
        return tuple(out)

    def _context(
        self,
        investigation: StoredInvestigation,
        window: str,
        coordinate: ResolvedCoordinate,
        finding: PublishedFinding | None = None,
        metric_id: str | None = None,
    ) -> AuditContext:
        schema = self._schemas.get(investigation.watermark_id)
        if schema is None:
            raise Underivable("watermark_unknown", investigation.watermark_id)
        # A finding whose probe declared its own window publishes it. That is
        # the window the number was computed over, so it is the window the
        # audit derives over — deriving over the header's window would be
        # auditing a figure nobody published.
        own = _published_window(finding, metric_id, WINDOW_START_SUFFIX, WINDOW_END_SUFFIX)
        if window == "prior":
            if investigation.comparison is None:
                raise Underivable("no_comparison_window", investigation.id)
            start, end = investigation.comparison
            own_prior = _published_window(
                finding, metric_id, PRIOR_WINDOW_START_SUFFIX, PRIOR_WINDOW_END_SUFFIX
            )
            if own_prior is not None:
                start, end = own_prior
            elif own is not None:
                # The finding declares its own CURRENT window and no prior of
                # its own, which means the two were paired the ordinary way.
                # Refuse rather than shift by the answer's displacement: a
                # full-periods pairing moves by whole calendar periods, not by
                # a day count, and a guess here would report a divergence
                # about a window nobody used.
                raise Underivable("prior_window_undisclosed", f"{metric_id}/{investigation.id}")
        else:
            start, end = own or (investigation.window_start, investigation.window_end)
        if investigation.scope_error:
            raise Underivable("scope_unsupported", investigation.scope_error)
        scope = investigation.scope
        slice_ = coordinate.coordinate
        if metric_id and metric_id in self._contracts:
            entity = self._run.deriver.entity_of(self._contracts[metric_id])
            scope = self._resolve_predicates(scope, schema, entity, self._notes)
            slice_ = self._resolve_predicates(slice_, schema, entity, self._notes)
        return AuditContext(
            schema=schema,
            watermark_id=investigation.watermark_id,
            window_start=start,
            window_end=end,
            published_basis=investigation.basis,
            scope=scope,
            slice=slice_,
            time_bucket=coordinate.time_bucket,
            cohort=investigation.cohort,
        )

    def _finding_coordinate(
        self,
        finding: PublishedFinding,
        metric_id: str,
        window: str,
        published: Any,
        investigation: StoredInvestigation,
    ) -> ResolvedCoordinate:
        """The ONE coordinate a finding is about, used for both windows.

        A period-over-period finding names a single slice; its current and
        prior values are that slice read over two windows. Resolving the two
        independently would let the prior fall back to a different cell
        whenever the prior frame does not carry the slice — which is exactly
        the case worth catching, because it is where a missing prior cell gets
        published as ``prior = 0``.
        """
        anchor = _finding_value(finding, metric_id)
        if anchor is None:
            anchor = _finding_value(finding, "current_cents")
        try:
            return resolve_coordinate(finding, metric_id, "current", anchor, investigation.cells)
        except Underivable:
            return resolve_coordinate(finding, metric_id, window, published, investigation.cells)

    def _anchors(self, investigation: StoredInvestigation) -> tuple[dt.date, ...]:
        anchors = [investigation.window_end]
        if investigation.comparison is not None:
            anchors.append(investigation.comparison[1])
        newest = self._newest.get(investigation.watermark_id)
        if newest is not None:
            anchors.append(newest)
        return tuple(dict.fromkeys(anchors))

    def _derive_with_basis_fallback(
        self, metric_id: str, ctx: AuditContext, published: Any
    ) -> tuple[Derivation, str]:
        """Derive on the policy basis; if that misses, try the alternates.

        A number reproduced only on an alternate basis is reported as
        ``basis_ambiguous``: contract-legal, but the published provenance does
        not say which basis was read.
        """
        primary = self._run.derive(metric_id, ctx, self._mutation)
        contract = self._contracts[metric_id]
        if _matches(primary, published, contract):
            return primary, MATCHED
        if self._mutation.active:
            return primary, DIVERGED
        for alternate in self._run.deriver.bound_bases(contract):
            if alternate == primary.basis:
                continue
            try:
                candidate = self._run.derive(metric_id, replace(ctx, force_basis=alternate))
            except Underivable:
                continue
            if _matches(candidate, published, contract):
                return candidate, BASIS_AMBIGUOUS
        return primary, DIVERGED

    def audit_value(
        self,
        investigation: StoredInvestigation,
        finding: PublishedFinding,
        name: str,
        published: Any,
    ) -> AuditedValue:
        base = AuditedValue(
            investigation_id=investigation.id,
            session_id=investigation.session_id,
            question=investigation.question,
            referent=finding.referent,
            finding_title=finding.title,
            value_name=name,
            metric_id=None,
            window="current",
            outcome=UNDERIVABLE,
            published=published,
            published_basis=investigation.basis,
            era=classify(investigation.created_at),
        )
        self._notes = []
        claim = classify_value(name, finding, self._contracts)
        if claim.kind == "unsupported":
            return replace(base, reason=claim.reason)
        metric_id = claim.metric_id or ""
        base = replace(base, metric_id=metric_id, window=claim.window)

        try:
            if claim.kind == "arithmetic":
                return self._audit_arithmetic(investigation, finding, base, claim, published)
            if claim.kind == "bound":
                return self._audit_bound(investigation, finding, base, claim, published)
            if _as_decimal(published) is None:
                raise Underivable("non_numeric_value", str(published)[:40])
            coordinate = self._finding_coordinate(
                finding, metric_id, claim.window, published, investigation
            )
            ctx = self._context(
                investigation, claim.window, coordinate, finding, metric_id
            )
            derivation, outcome = self._derive_with_basis_fallback(metric_id, ctx, published)
            reason = ""
            if outcome == DIVERGED and self._explain:
                reason = explain(
                    self._run,
                    metric_id,
                    self._contracts[metric_id],
                    ctx,
                    published,
                    self._anchors(investigation),
                )
            return replace(
                base,
                outcome=outcome,
                reason=_joined(self._notes, reason),
                derived=derivation.value,
                basis=derivation.basis,
                coordinate=_coordinate_labels(coordinate.coordinate, coordinate.time_bucket),
                coordinate_confirmed_by=coordinate.confirmed_by,
                sql=derivation.sql_blocks,
            )
        except Underivable as exc:
            return replace(base, outcome=UNDERIVABLE, reason=f"{exc.reason}:{exc.detail}")
        except Exception as exc:
            return replace(base, outcome=ERROR, reason=f"{type(exc).__name__}: {exc}")


    def _audit_bound(
        self,
        investigation: StoredInvestigation,
        finding: PublishedFinding,
        base: AuditedValue,
        claim: ValueClaim,
        published: Any,
    ) -> AuditedValue:
        """A suppressed cell publishes a CEILING. Assert it actually ceilings.

        Two things are checked, both exactly:

        * the true value the audit path derives must be **at or below** the
          published bound — a bound that does not bound is a divergence, and
          the worst kind, because it reads as a measurement;
        * ``<metric>__bound_population``, which the finding publishes as the
          cell's population, must equal the denominator the audit derives.
        """
        metric_id = claim.metric_id or ""
        bound = _as_decimal(published)
        if bound is None:
            raise Underivable("non_numeric_bound", metric_id)
        coordinate = self._finding_coordinate(
            finding, metric_id, claim.window, published, investigation
        )
        ctx = self._context(investigation, claim.window, coordinate, finding, metric_id)
        derivation = self._run.derive(metric_id, ctx, self._mutation)
        problems: list[str] = []
        if derivation.value > bound + RATIO_TOLERANCE:
            problems.append(f"true value {derivation.value} EXCEEDS the published bound {bound}")
        # `__bound_population` describes the CURRENT window's cell only; the
        # prior-window ceiling carries no population of its own.
        population = (
            _as_decimal(_finding_value(finding, f"{metric_id}__bound_population"))
            if claim.window == "current"
            else None
        )
        if (
            population is not None
            and derivation.denominator is not None
            and derivation.denominator.value != population
        ):
            problems.append(
                f"bound_population {population} != derived denominator "
                f"{derivation.denominator.value}"
            )
        return replace(
            base,
            outcome=DIVERGED if problems else BOUND_UPHELD,
            derived=derivation.value,
            basis=derivation.basis,
            reason=_joined(self._notes, "; ".join(problems)),
            coordinate=_coordinate_labels(coordinate.coordinate, coordinate.time_bucket),
            coordinate_confirmed_by=coordinate.confirmed_by,
            sql=derivation.sql_blocks,
        )

    def _audit_arithmetic(
        self,
        investigation: StoredInvestigation,
        finding: PublishedFinding,
        base: AuditedValue,
        claim: ValueClaim,
        published: Any,
    ) -> AuditedValue:
        """delta / pct_change: check the arithmetic over two INDEPENDENT derivations."""
        metric_id = claim.metric_id or ""
        if _as_decimal(published) is None:
            raise Underivable("non_numeric_value", str(published)[:40])
        current_published = _named(finding, ("current_cents", metric_id))
        prior_published = _named(finding, ("prior_cents", f"{metric_id}__prior"))
        if _as_decimal(current_published) is None or _as_decimal(prior_published) is None:
            raise Underivable("non_numeric_operand", metric_id)
        # ONE coordinate, two windows — see _finding_coordinate.
        current_coord = self._finding_coordinate(
            finding, metric_id, "current", current_published, investigation
        )
        prior_coord = current_coord
        current, current_outcome = self._derive_with_basis_fallback(
            metric_id,
            self._context(investigation, "current", current_coord, finding, metric_id),
            current_published,
        )
        prior, prior_outcome = self._derive_with_basis_fallback(
            metric_id,
            self._context(investigation, "prior", prior_coord, finding, metric_id),
            prior_published,
        )
        contract = self._contracts[metric_id]

        def combine(left: Decimal, right: Decimal) -> Decimal:
            if claim.arithmetic in ("delta", "delta_cents"):
                return left - right
            if right == 0:
                raise Underivable("pct_change_zero_baseline", metric_id)
            return (left - right) / right

        derived: Decimal = combine(current.value, prior.value)
        exact = claim.arithmetic in ("delta", "delta_cents") and contract.unit in (
            "money_cents",
            "count",
        )
        target = Decimal(str(published))
        ok = bool(target == derived if exact else abs(target - derived) <= RATIO_TOLERANCE)
        outcome = MATCHED if ok else DIVERGED
        detail = ""
        if not ok and DIVERGED not in (current_outcome, prior_outcome):
            # Does it reconcile exactly over the finding's OWN published,
            # already-rounded components? Then the arithmetic is sound and the
            # only gap is that it ran on rounded inputs.
            try:
                from_published = combine(
                    Decimal(str(current_published)), Decimal(str(prior_published))
                )
            except Exception:
                from_published = None
            if from_published is not None and abs(target - from_published) <= RATIO_TOLERANCE:
                outcome = ROUNDED_INPUTS
                detail = (
                    f"raw ratio-of-sums gives {derived}; published components "
                    f"({current_published}, {prior_published}) give {from_published}; "
                    f"delta {abs(target - derived)}"
                )
        return replace(
            base,
            outcome=outcome,
            reason=_joined(self._notes, detail),
            derived=derived,
            basis=current.basis,
            coordinate=_coordinate_labels(
                current_coord.coordinate, current_coord.time_bucket
            ),
            coordinate_confirmed_by=current_coord.confirmed_by,
            sql=current.sql_blocks + prior.sql_blocks,
        )

    def run(self, corpus: list[StoredInvestigation], report: ReplayReport) -> ReplayReport:
        for investigation in corpus:
            report.investigations += 1
            for finding in investigation.findings:
                report.findings += 1
                for name, value in finding.values:
                    report.values_seen += 1
                    report.audited.append(self.audit_value(investigation, finding, name, value))
        return report


def _joined(notes: list[str], reason: str) -> str:
    """Resolution notes first, then whatever the audit had to say."""
    parts = [*dict.fromkeys(notes), reason]
    return " | ".join(part for part in parts if part)


def _published_window(
    finding: PublishedFinding | None,
    metric_id: str | None,
    start_suffix: str,
    end_suffix: str,
) -> tuple[dt.date, dt.date] | None:
    """A window this finding says one of its own figures was computed over.

    ``None`` when the finding publishes none, which is every finding whose
    probe read the investigation window — the ordinary case, and the only
    one before playbook probe windows were disclosed.
    """
    if finding is None or not metric_id:
        return None
    start = _finding_value(finding, f"{metric_id}{start_suffix}")
    end = _finding_value(finding, f"{metric_id}{end_suffix}")
    if isinstance(start, dt.date) and isinstance(end, dt.date):
        return start, end
    return None


def _named(finding: PublishedFinding, names: tuple[str, ...]) -> Any:
    for wanted in names:
        for name, value in finding.values:
            if name == wanted:
                return value
    raise Underivable("arithmetic_operand_missing", "/".join(names))


def _coordinate_labels(
    coordinate: tuple[Predicate, ...], bucket: tuple[str, dt.date] | None
) -> tuple[str, ...]:
    out = [f"{p.dimension}={'|'.join(str(v) for v in p.values) or p.op}" for p in coordinate]
    if bucket is not None:
        out.append(f"{bucket[0]}={bucket[1].isoformat()}")
    return tuple(out)


def _matches(derivation: Derivation, published: Any, contract: MetricContract) -> bool:
    target = _as_decimal(published)
    if target is None:
        return False
    if contract.unit in ("money_cents", "count"):
        return derivation.value == target
    return abs(derivation.value - target) <= RATIO_TOLERANCE
