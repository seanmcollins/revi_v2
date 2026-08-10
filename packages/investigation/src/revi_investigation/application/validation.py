"""The planner validation pass (design §6.6), in order, over a typed plan.

1. **Resolution.** Every probe dimension and scope-predicate dimension must
   resolve in the semantic catalog (``UNSUPPORTED_CONCEPT`` otherwise), and
   each node is graded on two independent axes, weakest wins:
   *certification* — any uncertified dimension anywhere in the chain
   downgrades the node to DISCOVERY (design §2.3); and *binding strength* —
   the pack's declared strength of each touched field as evidence for the
   concepts under investigation (§5.5), so certified-but-proxy evidence
   (a CARC standing in for coordination of benefits) cannot launder into a
   certified conclusion. Resolution also covers
   measure *fields*: a contract whose measure fields are not answerable at
   the source — no catalog measure at the probe's entity, no declared
   column, and nothing the repository *advertises* that it computes (see
   "Answerability is negotiated" below) — prunes its probe from the plan
   with a surfaced warning naming the field and the reason: the
   honest-limitation path. The whole plan failing to resolve is an
   ``UNSUPPORTED_CONCEPT`` error. Comparison twins and transform steps that
   consumed pruned probes are pruned with them.
2. **Grain legality.** For every ratio metric, probe dimensions must be a
   subset of the contract's ``scope_dimensions`` (``GRAIN_INCOMPATIBLE``);
   ``time_bucket:*`` pseudo-dimensions are exempt. Additive money/count
   metrics accept any certified dimension. Independently, every group-by
   and scope dimension must be bound at the probe's entity grain — and, for
   a metric whose components live at a *second* entity, at that entity too,
   since each side aggregates the identical keys against its own base view.
3. **Date basis.** The probe's basis (window basis for flow, aging basis
   for snapshots) must be allowed by every referenced contract **and bound
   by the catalog at that contract's entity grain**
   (``DATE_BASIS_INVALID`` either way); a legal non-primary basis yields an
   ``alternate_basis_used`` warning so the header can label it, and when
   the primary was passed over because this warehouse does not bind it,
   the warning says that rather than implying a preference.
4. **Cardinality budget.** The product of catalog cardinality estimates
   over the probe's dimensions must fit the cell budget, or the probe must
   carry a top-N limit (``QUERY_BUDGET_EXCEEDED``); a limited over-budget
   probe warns that results are truncated. A dimension the probe's own
   scope pins to an enumerated value set (a conjunctive ``eq``/``in``)
   counts at that size rather than its catalog estimate — a group-by on
   four dimensions each pinned to one value is one cell, not their
   cross-product, and must not be refused for a budget it cannot spend.
4b. **Predicate values.** Every conjunctive ``eq``/``in`` value is resolved
   against the values that exist — the dimension's declared
   ``value_domain`` when it has one, else the distinct values the source
   holds at this watermark (one grouped read, cached per watermark). A
   case/punctuation variant is corrected and the correction is stated; a
   value that matches nothing raises a clarification naming it, its
   closest matches, and how many values exist. Asynchronous, so it is a
   separate entry point (:meth:`PlanValidationService.resolve_predicate_values`)
   rather than a step inside :meth:`validate`.
5. **Exclusion intersection.** A user scope predicate touching a
   contract's internal exclusions or filtered-numerator dimensions (the
   "denial rate for denied claims" confusion) yields a warning surfaced
   with the answer. In the same step, any contract whose description
   declares a **population caveat** publishes that caveat as a warning on
   every answer that reads the metric — see below.
6. **Suppression plan.** The catalog's small-cell threshold is noted so the
   execution service applies it and the answer can say so.
7. **Capability negotiation.** Cohort semi-joins, server-side top-N,
   HAVING pushdown, and as-of reads are checked against
   ``repository.capabilities()`` (``SOURCE_CAPABILITY_UNSUPPORTED``).
   Probe-time derived measures and cross-entity components are negotiated
   against the same declaration, in step 1 where answerability is decided.
8. **Policy limits.** Simple plan-level budgets (probe count) enforce the
   read-only/row/time posture hooks (``QUERY_BUDGET_EXCEEDED``).

Answerability is negotiated, not assumed
========================================
Deciding answerability from the catalog alone plus a hardcoded list of
probe-time derivations goes stale the moment an adapter grows a new one,
and the consequence is not a warning but a refusal: contracts the source
executes correctly are pruned to an empty plan and answered
``UNSUPPORTED_CONCEPT: no probe in the plan is answerable at the source``
— a sentence about the source that the source disproves.

So this is §6.3's capability negotiation: the repository advertises what
it computes (``derived_measures``, each with its entity and the probe
shapes that can compute it, and ``cross_entity_ratio_of_sums``), and this
pass reads the advertisement. Three properties follow, and each is pinned
by a test:

- **Silence is not permission.** A repository that advertises nothing
  extra gets exactly the old behaviour, refusal text included. Adapters
  that never learned the new tricks degrade honestly rather than being
  assumed capable.
- **Shape verdicts cannot disagree.** The adapter refuses a snapshot-age
  measure inside a flow aggregation; because the same declaration drives
  both, so does this pass — at plan time, with a §6.6 reason, instead of
  as an exception after the click.
- **Cross-entity is aggregation-only.** A component declared at another
  entity compiles to a same-scope block per entity joined on the shared
  group keys, which is why the group-by and scope dimensions must bind at
  *both* entities and the window basis must be bound at both (step 2). A
  snapshot aggregates one entity as-of a date and is never eligible.

Population caveats are structural, not per-metric
=================================================
Several contracts volunteer, in prose, that their population is not the one
a reader would assume — ``denial_rate`` excludes un-adjudicated claims,
``ar_balance`` values A/R at gross billed charges, ``days_in_ar`` is the
aging form rather than MAP FM-1. Authored honesty that never leaves the
pack is not honesty on the wire: without this step the API publishes
``denial_rate`` at 49.94% with a ``warnings`` array carrying only basis and
suppression notes, while the contract's own caveat sits in a description
nothing renders.

So the convention is mechanical: a contract description may carry exactly
one sentence group introduced by ``Population caveat:``, and every answer
that reads that metric emits it as a warning. Prose stays prose (the
semantic fingerprint still excludes ``description``, so writing a caveat
never forces a version bump), but a caveat that exists is a caveat the
reader sees. Authoring one is a pack edit; publishing it is not optional.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from revi_calculation_contracts.contract import (
    Count,
    Filtered,
    MeasureExpr,
    MetricContract,
    MetricKind,
)
from revi_catalog_contracts.model import (
    CatalogSnapshot,
    DimensionDef,
    DimensionKind,
    EntityDef,
    PhiClass,
    normalize_synonym,
)
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.date_basis import basis_bound_at
from revi_investigation.application.planning import (
    ANSWERING_TRANSFORMS,
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
)
from revi_investigation.domain.context import AnalysisSpec
from revi_investigation.domain.turns import ClarificationBinding, ClarificationRequest
from revi_kernel.capabilities import AnalyticalRepository, RepositoryCapabilities
from revi_kernel.errors import (
    DateBasisInvalidError,
    GrainIncompatibleError,
    QueryBudgetExceededError,
    ReviError,
    SourceCapabilityUnsupportedError,
    UnsupportedConceptError,
)
from revi_kernel.filters import (
    EMPTY_SCOPE,
    And,
    FilterExpr,
    Not,
    Or,
    Predicate,
    PredicateOp,
    Scalar,
    iter_cohorts,
    iter_predicates,
)
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.probes import AggregationProbe, ProbeShape, SnapshotProbe, probe_shape
from revi_kernel.refs import SERVICE, DateBasisRef, DimensionRef, EntityGrain, Grain
from revi_kernel.watermark import DataWatermark

_TIME_BUCKET_PREFIX = "time_bucket:"
_PRIOR_SUFFIX = "__prior"

#: The governed marker a metric contract uses to declare that its population
#: is narrower or wider than a reader would assume. Case-insensitive, one per
#: contract, terminated by the next sentence that starts a new topic — in
#: practice by the end of the paragraph the author wrote for it.
_POPULATION_CAVEAT_MARKER = re.compile(r"population caveat:\s*", re.IGNORECASE)
_CAVEAT_TERMINATORS = re.compile(
    r"(?:^|(?<=\s))(?:Primary basis is|Point of clarification:|Benchmark context:|"
    r"Denominator note|Valuation caveat:)",
)


#: Entity grains from coarsest to finest. A candidate metric at a FINER
#: grain than the refused one can still be cut by the requested dimension
#: and still rolls up to the population that was asked about; a coarser one
#: cannot, because the rows it counts span several of the asked-for
#: entities. Testing for EQUAL grain instead filters out metrics that
#: answer the question.
_GRAIN_ORDER: tuple[EntityGrain, ...] = (
    EntityGrain.ENCOUNTER,
    EntityGrain.CLAIM,
    EntityGrain.REMIT,
    EntityGrain.LINE,
    EntityGrain.DENIAL,
    EntityGrain.TRANSACTION,
)

#: The contract unit that answers a "how much revenue" question.
_MONEY_UNIT = "money_cents"


def _grain_at_most(candidate: EntityGrain, refused: EntityGrain) -> bool:
    """Is ``candidate`` the same grain as ``refused``, or finer?"""
    try:
        return _GRAIN_ORDER.index(candidate) >= _GRAIN_ORDER.index(refused)
    except ValueError:  # pragma: no cover - a grain outside the ladder
        return candidate is refused


def population_caveat(description: str) -> str | None:
    """The contract's declared population caveat, or ``None``.

    Pure text, deliberately: the caveat is prose the pack author wrote, and
    lifting it verbatim is the point — a paraphrase generated here would be
    a second, ungoverned statement of the population.
    """
    match = _POPULATION_CAVEAT_MARKER.search(description)
    if match is None:
        return None
    tail = description[match.end() :]
    stop = _CAVEAT_TERMINATORS.search(tail)
    if stop is not None:
        tail = tail[: stop.start()]
    return " ".join(tail.split()).rstrip() or None


@dataclass(frozen=True, slots=True)
class ValidationLimits:
    """Simple pre-execution budgets (§6.6 steps 4 and 8)."""

    max_group_cells: int = 5000
    max_probes: int = 16


DEFAULT_LIMITS = ValidationLimits()


@dataclass(frozen=True, slots=True)
class _FieldVerdict:
    """Whether one measure field is answerable, and where it aggregates.

    ``entity`` is set only when the field lives at a *second* entity — the
    cross-entity case step 2 then re-checks the group keys against.
    """

    resolved: bool
    entity: str | None = None
    reason: str | None = None


_RESOLVED = _FieldVerdict(True)


@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    plan: InvestigationPlan
    grades: tuple[tuple[str, EvidenceGrade], ...]
    warnings: tuple[str, ...]
    #: §6.6 step 4b value resolutions, as ``dimension -> {as typed: as
    #: queried}``. Applying the correction to the PLAN alone leaves the
    #: context header — the one field whose job is to state which population
    #: ran — publishing the user's spelling, contradicting the
    #: ``value_corrected`` caution beside it. Carried out of validation so
    #: the header can state the predicate the engine actually executed.
    value_corrections: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()

    @property
    def corrections_map(self) -> dict[str, dict[str, str]]:
        return {dim: dict(pairs) for dim, pairs in self.value_corrections}

    def grade_of(self, node_id: str) -> EvidenceGrade:
        for name, grade in self.grades:
            if name == node_id:
                return grade
        raise KeyError(f"no grade recorded for node {node_id!r}")


def _measure_fields(expr: MeasureExpr | None) -> tuple[str, ...]:
    if expr is None or isinstance(expr, Count):
        return ()
    if isinstance(expr, Filtered):
        return _measure_fields(expr.inner)
    return (expr.field.id,)  # Sum | CountDistinct


def _internal_filter_dimensions(contract: MetricContract) -> frozenset[str]:
    """Dimensions inside the contract's own definition: exclusions plus any
    Filtered wrappers on numerator/denominator."""
    return frozenset(contract_pinned_values(contract))


def contract_pinned_values(contract: MetricContract) -> dict[str, frozenset[str]]:
    """Dimension values the contract itself pins, by dimension id.

    A contract's own ``exclusions`` and its ``Filtered`` numerator/
    denominator already decide part of the population: ``ar_over_90_pct``
    *is* the 91-120 and 120+ buckets, ``denial_rate`` *is* the adjudicated
    claims. An analyst filter restating one of those adds nothing and, on
    the aging metrics, turns a perfectly good question into a refusal — so
    interpretation drops the restatement (see
    :meth:`InterpretQuestionService._drop_redundant_scope`) and keeps
    anything that genuinely narrows.

    Values are normalized strings so ``"120+"`` matches ``"120+"``
    regardless of how the analyst typed it. A predicate with no enumerated
    values (``is_null``, a range) pins nothing and contributes an empty set:
    the dimension is still *touched*, which is what the exclusion-overlap
    warning is keyed on.
    """
    pinned: dict[str, set[str]] = {}
    sources: list[FilterExpr] = []
    if contract.exclusions is not None:
        sources.append(contract.exclusions)
    for expr in (contract.numerator, contract.denominator):
        if isinstance(expr, Filtered):
            sources.append(expr.where)
    for source in sources:
        for predicate in iter_predicates(source):
            bucket = pinned.setdefault(predicate.dimension.id, set())
            if predicate.op in (PredicateOp.EQ, PredicateOp.IN):
                bucket.update(normalize_synonym(str(v)) for v in predicate.values)
    return {dimension: frozenset(values) for dimension, values in pinned.items()}


def map_predicates(
    expr: FilterExpr, fn: Callable[[Predicate], Predicate]
) -> FilterExpr:
    """Rewrite every predicate in a filter tree, structure preserved.

    Exported: the turn engine applies a clarification's chosen value the
    same way, and two tree-rewriters would be two chances to forget a node
    type.
    """
    if isinstance(expr, Predicate):
        return fn(expr)
    if isinstance(expr, And):
        return And(tuple(map_predicates(clause, fn) for clause in expr.clauses))
    if isinstance(expr, Or):
        return Or(tuple(map_predicates(clause, fn) for clause in expr.clauses))
    if isinstance(expr, Not):
        return Not(map_predicates(expr.clause, fn))
    return expr  # InCohort: its definition is pinned, not re-read here


def _corrected(predicate: Predicate, corrections: dict[str, dict[str, Scalar]]) -> Predicate:
    """Apply resolved canonical values to one predicate."""
    by_value = corrections.get(predicate.dimension.id)
    if not by_value:
        return predicate
    values = tuple(by_value.get(str(value), value) for value in predicate.values)
    return predicate if values == predicate.values else replace(predicate, values=values)


class PlanClarificationNeeded(Exception):
    """A §6.6 finding the analyst can act on, raised as a clarification.

    Two outcomes — a validated plan or a §12 error code — leave the analyst
    at a dead end ("this metric cannot be cut by that dimension", and then
    nothing). Some of what this pass discovers is not a failure of the
    platform but a question for the analyst: a filter value that exists
    nowhere in the data, a breakdown another metric *can* do. Those cross
    the boundary as a
    :class:`ClarificationRequest`, which the engine already treats as a
    successful outcome, rather than as an exception the API renders as an
    error banner.
    """

    def __init__(self, clarification: ClarificationRequest) -> None:
        super().__init__(clarification.question)
        self.clarification = clarification


#: How many near-miss values a clarification names before it stops listing
#: and starts summarizing. Four is the clarification-chip budget; the
#: sentence carries the total so the analyst knows what they are choosing
#: from.
MAX_SUGGESTED_VALUES = 4

#: A domain no larger than this is offered IN FULL rather than sampled. The
#: payer domain here is twelve; offering four of them under the heading
#: "Closest:" — when they are the first four alphabetically — leaves eight
#: values the analyst has no way to reach and no way to know exist. Above
#: this, a list stops being a choice and becomes a wall of text, and the
#: sample is labelled a sample.
MAX_ENUMERATED_VALUES = 16
#: Values below this similarity are not near misses, they are different
#: words; suggesting them teaches the analyst the matcher is guessing.
_VALUE_SIMILARITY_CUTOFF = 0.6


class PlanValidationService:
    """The full §6.6 pass. Stateless; safe to share."""

    def __init__(
        self,
        catalog: CatalogSnapshot,
        pack: PackPort,
        repository: AnalyticalRepository,
        limits: ValidationLimits = DEFAULT_LIMITS,
    ) -> None:
        self._catalog = catalog
        self._pack = pack
        self._repository = repository
        self._limits = limits
        # Observed value sets per (watermark, entity, dimension, window):
        # what a dimension's values ARE cannot change inside a watermark, so
        # one SELECT DISTINCT-shaped read serves every turn of every session
        # that asks about the same cut. Keyed by watermark id so a new load
        # never serves a stale domain.
        self._observed_values: dict[tuple[str, str, str, str], tuple[str, ...]] = {}

    # ------------------------------------------------------------------ api

    def validate(self, plan: InvestigationPlan, spec: AnalysisSpec) -> ValidatedPlan:
        warnings: list[str] = []

        plan = self._prune_unanswerable(plan, warnings)

        grades: list[tuple[str, EvidenceGrade]] = []
        for node in plan.nodes:
            grade = self._resolve_and_grade(node, spec.concepts)  # step 1
            self._check_grain(node)  # step 2
            self._check_basis(node, warnings)  # step 3
            self._check_cardinality(node, warnings)  # step 4
            self._check_exclusion_intersection(node, warnings)  # step 5
            self._publish_population_caveats(node, warnings)  # step 5 (cont.)
            grades.append((node.id, grade))

        self._note_suppression(plan, warnings)  # step 6
        self._check_capabilities(plan)  # step 7
        self._check_limits(plan)  # step 8

        return ValidatedPlan(plan=plan, grades=tuple(grades), warnings=tuple(warnings))

    # ------------------------------------ capability checks on free text

    def unexecutable_cut(
        self, text: str, metric_ids: Sequence[str]
    ) -> tuple[str, str] | None:
        """``(dimension id, metric id)`` this sentence asks for and cannot have.

        Without this check a clarification can offer *"Yes — re-group the
        figure F1 result by denial reason"* and the tap produces ``outcome:
        error``, ``GRAIN_INCOMPATIBLE: denial_category is not a scope
        dimension of denial_rate``, a bracketed internal predicate and a
        correlation id on screen — a breakdown the platform already knew it
        could not run, with the circuit breaker eventually firing on its own
        suggestion.

        The predicate that refuses it is ``MetricContract.allows_dimension``,
        the same one :meth:`_check_grain` applies, so the check the OPTION
        COMPOSER needs is that predicate reached one turn earlier. Free text
        resolves through the catalog's own synonym index — "denial reason"
        is how an analyst says ``denial_category``, and an option composer
        writing in the analyst's vocabulary is the point.

        Deliberately one-sided:

        * ``None`` when the text names no catalog dimension at all (a
          platform recovery chip is not a query, and guessing at one is how
          a good option gets dropped);
        * ``None`` when the text names a cut these metrics DO declare, even
          if it also names one they do not — the option is answerable and
          the composer chose imprecise words;
        * a pair only when every dimension the text names is refused by
          every metric supplied, which is exactly the tap that errors.
        """
        contracts = [c for c in (self._pack.metric(m) for m in metric_ids) if c is not None]
        if not contracts:
            return None
        named = self._dimensions_named(text)
        if not named:
            return None
        refused: tuple[str, str] | None = None
        for dimension_id in named:
            ref = DimensionRef(dimension_id)
            allowing = [c for c in contracts if c.allows_dimension(ref)]
            if allowing:
                return None
            if refused is None:
                refused = (dimension_id, contracts[0].id)
        return refused

    def unanswerable_playbook(self, text: str) -> tuple[str, str] | None:
        """``(playbook id, transform)`` this sentence asks for and cannot have.

        The same defect class as :meth:`unexecutable_cut`, one question to
        the left: "who is my worst payer?" can be answered with the
        clarification option *"Run a full payer scorecard across all
        measures"*, which produces ``PLAYBOOK_TRANSFORM_UNAVAILABLE:
        payer_scorecard answers by 'pivot'`` when taken. The offer-time
        validator dry-runs an option against the plan grammar, which is the
        DIMENSION half of answerability; this is the other half, and the
        knowledge it needs already lives in
        :meth:`_playbook_transform_alternative`.

        ``ANSWERING_TRANSFORMS`` are the transforms a playbook answers WITH
        rather than decorates with — ``pivot`` makes a scorecard a
        scorecard — so a playbook declaring one is a playbook this engine
        refuses at plan time. Offering a button that reaches it is offering
        a button the engine has already decided it cannot press.

        Matching is on the pack's OWN triggers and on the playbook id read
        as words, never on a vocabulary kept here: a trigger phrase is the
        pack author's declaration that this is how an analyst asks for this
        playbook, and it is the same declaration the interpreter routes on.

        One-sided in the same way :meth:`unexecutable_cut` is, and for the
        same reason — dropping a good option costs the analyst a real route.
        An option that names a METRIC this pack holds is a direct query
        whatever playbook words it also contains: ``payer_scorecard``
        declares the trigger "rank payers", and *"Rank payers by denial
        rate"* is a question this engine answers in one probe. So a text
        that names a governed measure is never refused here.
        """
        folded = " ".join(text.casefold().split())
        if not folded or self._names_a_metric(folded):
            return None
        best: tuple[tuple[int, bool], str, str] | None = None
        for playbook_id, _description in self._pack.playbook_summaries():
            playbook = self._pack.playbook(playbook_id)
            if playbook is None:  # pragma: no cover - summaries come from the pack
                continue
            unavailable = next(
                (
                    step.operator
                    for step in playbook.transforms
                    if step.operator in ANSWERING_TRANSFORMS
                ),
                None,
            )
            if unavailable is None:
                continue
            named = playbook_id.replace("_", " ")
            phrases = (
                named,
                *(trigger.casefold().strip() for trigger in playbook.triggers),
            )
            matched = max(
                (len(phrase) for phrase in phrases if phrase and phrase in folded), default=0
            )
            # The LONGEST match wins, and where two playbooks declare the
            # same trigger the one the text NAMES wins: "payer scorecard" is
            # a trigger of both ``payer_scorecard`` and the generic
            # ``dimension_scorecard``, and a refusal that names the generic
            # one tells the reader about a playbook they did not ask for.
            score = (matched, named in folded)
            if matched and (best is None or score > best[0]):
                best = (score, playbook_id, unavailable)
        return None if best is None else (best[1], best[2])

    def _names_a_metric(self, folded: str) -> bool:
        """Does this text name a governed measure, by id or as words?

        Both spellings, because both reach a reader: the pack's own
        ``denial_rate`` (which the composer still leaks) and the "denial
        rate" an analyst writes.
        """
        return any(
            metric_id in folded or metric_id.replace("_", " ") in folded
            for metric_id, _description in self._pack.metric_summaries()
        )

    def _dimensions_named(self, text: str) -> tuple[str, ...]:
        """Catalog dimensions this free text asks to cut by.

        Read off the catalog's synonym index rather than a word list here:
        "denial reason category", "denial bucket" and "root cause category"
        are all ``denial_category`` because ``dimensions.yaml`` says so, and
        a second vocabulary in this file would drift from it by the next
        pack release.
        """
        words = re.findall(r"[a-z0-9_]+", text.casefold())
        found: dict[str, None] = {}
        for size in (4, 3, 2, 1):
            for start in range(len(words) - size + 1):
                phrase = " ".join(words[start : start + size])
                for dimension in self._catalog.dimensions_for_synonym(phrase):
                    if dimension.certified:
                        found.setdefault(dimension.id)
        return tuple(found)

    # -------------------------------------------- refusals with a way out

    def clarification_for(
        self, error: ReviError, spec: AnalysisSpec | None = None
    ) -> ClarificationRequest | None:
        """Turn a §6.6 refusal into a question the analyst can answer.

        ``GRAIN_INCOMPATIBLE`` and ``UNSUPPORTED_CONCEPT`` are honest: this
        metric cannot be cut that way, that dimension is not in the catalog.
        They are also *dead ends* on their own — an error code and no route
        onward, while the pack can often answer the same question with a
        different metric. The near miss is derivable, so it is derived:
        metrics whose
        ``scope_dimensions`` include the dimension that was refused, at the
        same kind and at a grain no coarser than the refused metric's,
        become clarification options. Nothing is invented — an option that
        appears is a metric that exists and a cut it declares.

        ``spec`` is the ask itself, when the caller has it: it carries the
        other dimensions the analyst named, which a generated option must
        not drop.

        Returns ``None`` when the pack offers nothing, in which case the
        refusal stands as it was: a clarification with no way forward is
        worse than an error that says what happened.
        """
        dimension_id = error.details.get("dimension")
        metric_id = error.details.get("metric")
        transform = error.details.get("transform")
        playbook_id = error.details.get("playbook")
        if isinstance(transform, str) and isinstance(playbook_id, str):
            return self._playbook_transform_alternative(playbook_id, transform)
        if isinstance(dimension_id, str) and self._catalog.dimension(dimension_id) is not None:
            return self._grain_alternative(dimension_id, metric_id, spec)
        if isinstance(dimension_id, str):
            return self._near_miss_dimension(dimension_id)
        if isinstance(error, DateBasisInvalidError) and isinstance(metric_id, str):
            return self._basis_alternative(metric_id, error.details.get("basis"))
        if isinstance(metric_id, str) and self._pack.metric(metric_id) is None:
            return self._near_miss_metric(metric_id)
        return None

    def _playbook_transform_alternative(
        self, playbook_id: str, transform: str
    ) -> ClarificationRequest | None:
        """What this pack CAN answer when a playbook's answer is missing.

        ``pivot`` and ``project_lagged_realization`` are the two transforms
        this engine
        does not implement and that a playbook answers WITH, so the plan
        refuses rather than publishing the probes underneath as if they
        were the card or the forecast (see
        ``planning.ANSWERING_TRANSFORMS``). A refusal on its own would be
        honest and useless: the probes it declined to publish name real
        measurements, and each of them is a question this platform answers
        well on the direct path.

        So each probe family becomes one option, bound to the metric ids
        and the cut it declares — content, never invented — and the
        question names the transform that is missing. Nothing here promises
        the scorecard: it says which of its columns can be had one at a
        time.
        """
        playbook = self._pack.playbook(playbook_id)
        if playbook is None:  # pragma: no cover - the planner read it a moment ago
            return None
        options: list[str] = []
        bindings: list[ClarificationBinding] = []
        for probe in playbook.probes:
            metrics = tuple(
                metric_id
                for metric_id in probe.metric_ids
                if self._pack.metric(metric_id) is not None
            )
            if not metrics:
                continue
            dimensions = tuple(
                dimension
                for dimension in probe.dimensions
                if self._catalog.dimension(dimension) is not None
            )
            label = ", ".join(metrics[:3])
            cut = f" by {', '.join(dimensions)}" if dimensions else ""
            option = f"Measure {label}{cut}"
            if option in options:
                continue
            options.append(option)
            bindings.append(
                ClarificationBinding(
                    option=option,
                    kind="metric_cut",
                    metric_ids=metrics,
                    dimension_ids=dimensions,
                )
            )
            if len(options) >= MAX_SUGGESTED_VALUES:
                break
        if not options:
            return None
        return ClarificationRequest(
            question=(
                f"I can't build that: the {playbook_id!r} playbook answers by {transform!r}, "
                "which this engine does not implement — so what it would have produced does "
                "not exist here, and I won't hand you the probes underneath it as if they "
                f"were it. These {len(options)} measurements from the same playbook I can "
                "give you now, one at a time. Which do you want?"
            ),
            options=tuple(options),
            reason=(
                f"PLAYBOOK_TRANSFORM_UNAVAILABLE: {playbook_id} answers by {transform!r}; "
                f"{len(options)} probe famil(ies) offered as direct measurements instead"
            ),
            bindings=tuple(bindings),
        )

    def _basis_alternative(
        self, metric_id: str, refused_basis: object
    ) -> ClarificationRequest | None:
        """Date bases this metric CAN be read on, as recovery options.

        A bare ``DATE_BASIS_INVALID`` is a dead end, and a
        self-contradicting one: static refusal copy reads "Asking on a
        different date basis — service, submission or posting date — will
        answer it" directly above "(allowed: ['service', 'submission'])",
        with no options array behind either sentence. So the alternatives
        are derived from the same two facts the refusal was computed from —
        what the contract allows, and what this warehouse binds at the
        contract's grain — and an option that appears is a basis that will
        actually answer.

        ``None`` when the contract is unknown or nothing survives both
        checks: the refusal then stands, exactly as ``GRAIN_INCOMPATIBLE``
        does, because a clarification with no way forward is worse than an
        error that says what happened.
        """
        contract = self._pack.metric(metric_id)
        if contract is None:
            return None
        available = [
            basis
            for basis in contract.allowed_date_bases
            if basis != refused_basis and basis_bound_at(self._catalog, contract, basis)
        ]
        if not available:
            return None
        entity = contract.entity_grain.value
        asked = (
            f"the {refused_basis!r} date basis"
            if isinstance(refused_basis, str)
            else "that date basis"
        )
        options = tuple(f"Use the {basis.id} date basis" for basis in available)
        return ClarificationRequest(
            question=(
                f"{metric_id!r} cannot be read on {asked} here — the contract and this "
                f"warehouse between them leave "
                f"{', '.join(repr(basis.id) for basis in available)} at the {entity} grain. "
                "Which should I use?"
            ),
            options=options,
            reason=(
                f"DATE_BASIS_INVALID_RECOVERABLE: {metric_id} cannot be read on {asked}; "
                f"{len(available)} bound alternative(s) offered"
            ),
            # Each option carries the basis it stands for, so a reply that
            # names one resumes the question that was interrupted instead of
            # being re-read as a fresh utterance — and so a lone survivor can
            # simply be applied rather than asked about.
            bindings=tuple(
                ClarificationBinding(
                    option=option,
                    kind="date_basis",
                    metric_ids=(metric_id,),
                    basis=basis.id,
                )
                for option, basis in zip(options, available, strict=True)
            ),
        )

    def _grain_alternative(
        self, dimension_id: str, metric_id: object, spec: AnalysisSpec | None = None
    ) -> ClarificationRequest | None:
        """Metrics that declare ``dimension_id`` as a legal cut.

        Three rules, each of which this loop is easy to get wrong on a
        question like "how much revenue did we lose to prior authorization
        denials, broken out by payer?":

        * **The count is not the sample size.** Breaking at
          ``MAX_SUGGESTED_VALUES`` and then interpolating ``len(options)``
          makes a pack where ten metrics declare ``denial_category`` report
          "4 pack metrics declare it". The candidates are counted in full
          first and the offered set is labelled a sample, matching the copy
          ``PREDICATE_VALUE_UNMATCHED`` uses.
        * **Equal-grain is too strict.** ``denied_dollars`` is (flow,
          denial) and ``initial_denial_rate`` is (flow, claim), so an
          equal-grain test filters out the metric that answers the money
          half of the question and returns no options at all — turning a
          recoverable refusal back into a hard ``GRAIN_INCOMPATIBLE`` with
          no way onward. A candidate at a FINER grain can still be cut by
          the dimension and still aggregates to the population that was
          asked about; a coarser one cannot.
        * **Alphabetical order buries the answer.** ``metric_summaries()``
          is alphabetical, so money metrics can sort last and sit
          unreachable behind the cap on a question that asked "how much
          revenue". Money-unit candidates lead, and the other dimensions the
          analyst asked for are preserved in the option text instead of
          being silently dropped from every one.
        """
        refused = self._pack.metric(metric_id) if isinstance(metric_id, str) else None
        dim = self._catalog.dimension(dimension_id)
        assert dim is not None
        candidates: list[MetricContract] = []
        for candidate_id, _ in self._pack.metric_summaries():
            contract = self._pack.metric(candidate_id)
            if contract is None or contract.id == (refused.id if refused else None):
                continue
            if not contract.allows_dimension(DimensionRef(dimension_id)):
                continue
            if refused is not None and contract.kind is not refused.kind:
                continue
            if refused is not None and not _grain_at_most(
                contract.entity_grain, refused.entity_grain
            ):
                continue
            candidates.append(contract)
        if not candidates:
            return None
        # Money first: a "how much" question is answered by a contract that
        # produces dollars, and the ratio the question named has already
        # been refused for this cut. Then the refused metric's own unit (the
        # nearest like-for-like), then everything else, alphabetically
        # inside each tier so the order is stable and explainable.
        refused_unit = str(refused.unit) if refused is not None else None

        def rank(contract: MetricContract) -> tuple[int, str]:
            unit = str(contract.unit)
            if unit == _MONEY_UNIT:
                return (0, contract.id)
            if refused_unit is not None and unit == refused_unit:
                return (1, contract.id)
            return (2, contract.id)

        ranked = sorted(candidates, key=rank)
        offered = ranked[:MAX_SUGGESTED_VALUES]
        # The other cuts the analyst asked for are part of the ask, and
        # dropping them from every option offered a different question back.
        also = [
            ref.id
            for ref in (spec.dimensions if spec is not None else ())
            if ref.id != dimension_id
        ]
        extra = f" and {', '.join(also)}" if also else ""
        options = tuple(
            f"Break {contract.id} down by {dimension_id}{extra}" for contract in offered
        )
        total = len(candidates)
        sample = (
            ""
            if total <= len(offered)
            else f" Showing {len(offered)} of {total} — say the metric you want if it is not here."
        )
        refused_label = f"{refused.id!r}" if refused is not None else "that metric"
        return ClarificationRequest(
            question=(
                f"{refused_label} cannot be cut by {dim.label.lower()} — its contract does not "
                f"declare {dimension_id!r} as a legal scope dimension at the "
                f"{'claim' if refused is None else refused.entity_grain.value} grain. "
                f"{total} pack metric(s) can be, over the same population:{sample}"
            ),
            options=options,
            reason=(
                f"GRAIN_INCOMPATIBLE_RECOVERABLE: {dimension_id} is not a scope dimension of "
                f"{refused_label}; {total} pack metrics declare it, {len(offered)} offered"
            ),
            bindings=tuple(
                ClarificationBinding(
                    option=option,
                    kind="metric_cut",
                    metric_ids=(contract.id,),
                    dimension_ids=(dimension_id, *also),
                )
                for option, contract in zip(options, offered, strict=True)
            ),
        )

    def _near_miss_dimension(self, dimension_id: str) -> ClarificationRequest | None:
        known = [dim.id for dim in self._catalog.dimensions if dim.certified]
        close = difflib.get_close_matches(dimension_id, known, n=MAX_SUGGESTED_VALUES, cutoff=0.6)
        if not close:
            return None
        return ClarificationRequest(
            question=(
                f"I have no dimension called {dimension_id!r} in this catalog. Did you mean "
                "one of these?"
            ),
            options=tuple(close),
            reason=f"UNSUPPORTED_CONCEPT_NEAR_MISS: dimension {dimension_id!r}",
        )

    def _near_miss_metric(self, metric_id: str) -> ClarificationRequest | None:
        known = [mid for mid, _ in self._pack.metric_summaries()]
        close = difflib.get_close_matches(metric_id, known, n=MAX_SUGGESTED_VALUES, cutoff=0.6)
        if not close:
            return None
        return ClarificationRequest(
            question=(
                f"This pack defines no metric called {metric_id!r}. Did you mean one of these?"
            ),
            options=tuple(close),
            reason=f"UNSUPPORTED_CONCEPT_NEAR_MISS: metric {metric_id!r}",
        )

    # ------------------------------------------ step 4b: predicate values

    async def resolve_predicate_values(
        self, validated: ValidatedPlan, *, watermark: DataWatermark
    ) -> ValidatedPlan:
        """Resolve every filter value against the values that exist (§6.6).

        Every id a question produces is checked against governed content —
        metric, playbook, dimension, date basis, concept — and without this
        step the *values* those dimensions are filtered to reach the
        warehouse unexamined. "Denial rate for UnitedHealthcare and Aetna"
        then compiles ``payer in ['UnitedHealthcare', 'Aetna']`` against a
        warehouse holding neither name, executes correctly, matches nothing,
        and publishes an empty answer whose only caveat is about small-cell
        suppression. A wrong-case enum value does the same; so does a
        referent handle that leaks into a filter. Querying the void is not
        an answer, and an empty result is the one shape that cannot tell the
        analyst why.

        Two sources of truth, in order:

        - the dimension's **declared** ``value_domain`` (a closed catalog
          enum: ``payer_type``, ``status``, ``claim_type``); and
        - for an open dimension (``payer``, ``plan``, ``facility``) the
          values the warehouse actually holds at this watermark, read once
          per (watermark, entity, dimension, window) and cached — the
          domain of a dimension cannot change inside a load.

        A value that differs only in case or punctuation ("Medicare
        Advantage" for ``MEDICARE_ADVANTAGE``) is corrected and the
        correction is *stated*, never silent. A value that matches nothing
        raises a clarification naming it, its closest matches, and how many
        values exist — the analyst's question is answerable, they just have
        to say which one they meant.

        Dimensions carrying PHI are never enumerated: a clarification is not
        worth a list of patient-identifying values, so an unmatched value on
        one of those passes through as it always did.
        """
        plan = validated.plan
        warnings = list(validated.warnings)
        nodes: list[ProbeNode] = []
        changed = False
        # One correction map per dimension, shared across probes: the prior
        # twin of a comparison carries the identical predicate and must not
        # be corrected differently from the current side.
        corrections: dict[str, dict[str, Scalar]] = {}
        for node in plan.nodes:
            probe = node.probe
            if not isinstance(probe, (AggregationProbe, SnapshotProbe)):
                nodes.append(node)
                continue
            for predicate in self._top_level_predicates(probe.scope):
                await self._resolve_predicate(node, predicate, watermark, corrections, warnings)
            if corrections:
                scope = map_predicates(probe.scope, lambda p: _corrected(p, corrections))
                if scope != probe.scope:
                    nodes.append(replace(node, probe=replace(probe, scope=scope)))
                    changed = True
                    continue
            nodes.append(node)
        resolved = tuple(
            (dimension, tuple((typed, str(queried)) for typed, queried in sorted(mapping.items())))
            for dimension, mapping in sorted(corrections.items())
        )
        if not changed and len(warnings) == len(validated.warnings) and not resolved:
            return validated
        return ValidatedPlan(
            plan=replace(plan, nodes=tuple(nodes)) if changed else plan,
            grades=validated.grades,
            warnings=tuple(warnings),
            value_corrections=resolved,
        )

    async def _resolve_predicate(
        self,
        node: ProbeNode,
        predicate: Predicate,
        watermark: DataWatermark,
        corrections: dict[str, dict[str, Scalar]],
        warnings: list[str],
    ) -> None:
        if predicate.op not in (PredicateOp.EQ, PredicateOp.IN) or not predicate.values:
            return
        dimension_id = predicate.dimension.id
        if dimension_id.startswith(_TIME_BUCKET_PREFIX):
            return
        dim = self._catalog.dimension(dimension_id)
        if dim is None or dim.phi is not PhiClass.NONE:
            return  # unknown is step 1's error; PHI is never enumerated here
        domain = await self._value_domain(node, dim, watermark)
        if domain is None:
            return  # no declared domain and the source could not enumerate one
        index = {normalize_synonym(str(value)): value for value in domain}
        unmatched: list[Scalar] = []
        for value in predicate.values:
            text = str(value)
            if text in domain:
                continue
            canonical = index.get(normalize_synonym(text))
            if canonical is None:
                unmatched.append(value)
                continue
            corrections.setdefault(dimension_id, {})[text] = canonical
            note = (
                f"value_corrected: read {text!r} as {dim.label.lower()} {canonical!r} — the "
                "closest match in this data differs only in case or punctuation"
            )
            if note not in warnings:
                warnings.append(note)
        if unmatched:
            raise PlanClarificationNeeded(self._value_clarification(dim, unmatched, domain))

    def _value_clarification(
        self,
        dim: DimensionDef,
        unmatched: list[Scalar],
        domain: tuple[str, ...],
    ) -> ClarificationRequest:
        """Name what did not match, what exists, and how to get there.

        Three failure modes this copy has to avoid:

        * agreeing the plural with the count of UNMATCHED values, so a
          domain of twelve payers reads "12 payer value exist here";
        * offering four of twelve under the heading "Closest:" when they are
          simply the first four alphabetically — a false claim of
          similarity, and no route to the other eight;
        * not stating a domain small enough to state in full, which is the
          difference between a dead end and an answerable question.

        So: when the whole domain fits, the whole domain is offered and
        nothing is called "closest". When it does not, the near matches are
        labelled as such, the sample is labelled a sample, and the true
        total is stated.
        """
        label = dim.label.lower()
        named = ", ".join(repr(str(value)) for value in unmatched)
        # Agrees with the number it describes, not with a different one.
        plural = "value" if len(domain) == 1 else "values"
        close: list[str] = []
        for value in unmatched:
            for match in difflib.get_close_matches(
                str(value), domain, n=MAX_SUGGESTED_VALUES, cutoff=_VALUE_SIMILARITY_CUTOFF
            ):
                if match not in close:
                    close.append(match)
        opening = (
            f"There is no {label} named {named} in this data — so I stopped rather than "
            "answer over an empty population."
        )
        if len(domain) <= MAX_ENUMERATED_VALUES:
            options = tuple(sorted(domain))
            question = (
                f"{opening} Here are all {len(domain)} {label} {plural} this watermark "
                f"holds: {', '.join(repr(option) for option in options)}. Which did you mean?"
            )
        else:
            offered = tuple(close[:MAX_SUGGESTED_VALUES]) or tuple(
                sorted(domain)[:MAX_SUGGESTED_VALUES]
            )
            options = offered
            heading = (
                "Closest matches: " if close else f"A sample of the {len(domain)}: "
            )
            question = (
                f"{opening} {heading}"
                + ", ".join(repr(option) for option in offered)
                + f". {len(domain)} {label} {plural} exist here — name the one you mean if "
                "it is not among these."
            )
        return ClarificationRequest(
            question=question,
            options=options,
            reason=(
                f"PREDICATE_VALUE_UNMATCHED: {dim.id} "
                f"{[str(value) for value in unmatched]} not in the {len(domain)} values "
                "this watermark holds"
            ),
            bindings=tuple(
                ClarificationBinding(
                    option=option,
                    kind="predicate_value",
                    scope=((dim.id, (option,)),),
                )
                for option in options
            ),
        )

    async def _value_domain(
        self, node: ProbeNode, dim: DimensionDef, watermark: DataWatermark
    ) -> tuple[str, ...] | None:
        """The values this dimension may take: declared, else observed."""
        if dim.value_domain is not None:
            return dim.value_domain
        if dim.buckets is not None:
            return dim.buckets
        return await self._observed(node, dim, watermark)

    async def _observed(
        self, node: ProbeNode, dim: DimensionDef, watermark: DataWatermark
    ) -> tuple[str, ...] | None:
        """Distinct values at the watermark, via one grouped read.

        Shaped as the probe it validates — same entity, same window, same
        measure — with the scope dropped so the answer is "what exists",
        not "what survives the filter under test". Cached per watermark:
        the second question about payers in the same period costs nothing.

        A source that cannot serve it (an offline adapter, a stub) returns
        ``None`` and the pass declines to judge: refusing to answer because
        a *validation* read failed would turn an unavailable source into a
        wrong-value accusation.
        """
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        window_key = (
            f"{probe.window.range.start.isoformat()}..{probe.window.range.end.isoformat()}"
            if isinstance(probe, AggregationProbe)
            else probe.as_of.isoformat()
        )
        key = (watermark.id, probe.grain.entity.value, dim.id, window_key)
        cached = self._observed_values.get(key)
        if cached is not None:
            return cached
        ref = DimensionRef(dim.id)
        lookup: AggregationProbe | SnapshotProbe
        if isinstance(probe, AggregationProbe):
            lookup = AggregationProbe(
                measures=probe.measures[:1],
                dimensions=(ref,),
                scope=EMPTY_SCOPE,
                window=probe.window,
                grain=Grain(probe.grain.entity),
            )
        else:
            lookup = SnapshotProbe(
                measures=probe.measures[:1],
                dimensions=(ref,),
                scope=EMPTY_SCOPE,
                as_of=probe.as_of,
                grain=Grain(probe.grain.entity),
                aging_basis=probe.aging_basis,
            )
        try:
            frame = await self._repository.execute(lookup, watermark=watermark)
        except ReviError:
            return None
        if dim.id not in frame.schema.names:
            return None
        values = tuple(
            dict.fromkeys(str(value) for value in frame.column(dim.id) if value is not None)
        )
        if not values:
            return None
        self._observed_values[key] = values
        return values

    # ----------------------------------------------------- step 1: resolve

    def _entity_for(self, node: ProbeNode) -> EntityDef:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        entity = self._catalog.entity(probe.grain.entity)
        if entity is None:
            raise UnsupportedConceptError(
                f"no catalog entity is bound to grain {probe.grain.entity.value!r}",
                details={"grain": probe.grain.entity.value, "probe": node.id},
            )
        return entity

    def _contracts_for(self, node: ProbeNode) -> tuple[MetricContract, ...]:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        contracts: list[MetricContract] = []
        for ref in probe.measures:
            contract = self._pack.metric(ref.id)
            if contract is None:
                raise UnsupportedConceptError(
                    f"unknown metric {ref.id!r}", details={"metric": ref.id, "probe": node.id}
                )
            contracts.append(contract)
        return tuple(contracts)

    def _prune_unanswerable(self, plan: InvestigationPlan, warnings: list[str]) -> InvestigationPlan:
        """Drop probes whose measure fields cannot be answered at the source
        (with a surfaced warning); an empty result is UNSUPPORTED_CONCEPT."""
        dropped: set[str] = set()
        reasons: dict[str, list[str]] = {}
        for node in plan.nodes:
            if not isinstance(node.probe, (AggregationProbe, SnapshotProbe)):
                continue
            entity = self._entity_for(node)
            shape = probe_shape(node.probe)
            unresolved: list[str] = []
            for contract in self._contracts_for(node):
                for field_id in (
                    *_measure_fields(contract.numerator),
                    *_measure_fields(contract.denominator),
                ):
                    verdict = self._resolve_field(field_id, entity, shape)
                    if verdict.resolved:
                        continue
                    unresolved.append(f"{contract.id}.{field_id} — {verdict.reason}")
                # A contract-internal filter dimension the catalog does not
                # define is exactly as fatal as an unresolvable measure
                # field — the adapter raises UNSUPPORTED_CONCEPT when it
                # compiles the predicate — but until now nothing checked it
                # here, so the failure would surface as an error dialog
                # after a click rather than as an unanswerable probe before
                # one. This is the ``filtered:`` half of the conformance gap
                # ``packs/base-rcm/NOTES.md`` names.
                for dimension_id in sorted(_internal_filter_dimensions(contract)):
                    if self._catalog.dimension(dimension_id) is None:
                        unresolved.append(
                            f"{contract.id}[{dimension_id}] — the contract filters on a "
                            "dimension the catalog does not define"
                        )
            if unresolved:
                dropped.add(node.id)
                reasons[node.id] = unresolved
        # a pruned base prunes its comparison twin, and vice versa
        for node_id in tuple(dropped):
            twin = (
                node_id.removesuffix(_PRIOR_SUFFIX)
                if node_id.endswith(_PRIOR_SUFFIX)
                else f"{node_id}{_PRIOR_SUFFIX}"
            )
            if any(node.id == twin for node in plan.nodes):
                dropped.add(twin)
        if not dropped:
            return plan
        for node in plan.nodes:
            if node.id in dropped and not node.id.endswith(_PRIOR_SUFFIX):
                why = "; ".join(reasons.get(node.id, ()))
                warnings.append(
                    f"probe '{node.id}' omitted: its measures are not answerable at the "
                    f"source for this catalog and this repository ({why})"
                )
        kept_nodes = tuple(node for node in plan.nodes if node.id not in dropped)
        if not any(not node.id.endswith(_PRIOR_SUFFIX) for node in kept_nodes):
            # The refusal carries the per-field reasons, not only the probe
            # ids: "no probe is answerable" is true and useless on its own.
            # What is missing — a catalog measure, a source that computes it,
            # a probe shape that can — belongs in the error the caller
            # renders.
            raise UnsupportedConceptError(
                "no probe in the plan is answerable at the source",
                details={
                    "dropped": sorted(dropped),
                    "reasons": sorted(
                        reason for items in reasons.values() for reason in items
                    ),
                },
            )
        kept_ids = {node.id for node in kept_nodes}
        steps = []
        for step in plan.transforms.steps:
            if all(inp in kept_ids for inp in step.inputs):
                steps.append(step)
                kept_ids.add(step.id)
        return replace(plan, nodes=kept_nodes, transforms=TransformPlan(steps=tuple(steps)))

    def _capabilities(self) -> RepositoryCapabilities:
        return self._repository.capabilities()

    def _resolve_field(
        self, field_id: str, entity: EntityDef, shape: ProbeShape
    ) -> _FieldVerdict:
        """Can this source answer ``field_id`` on a probe of this shape?

        Four ways a field can be answerable, in the order the adapter
        itself tries them: a catalog measure at the probe's own entity; a
        measure the repository *advertises* that it derives at probe time;
        either of those declared at a **second** entity, when the source
        advertises cross-entity aggregation and the probe is a flow
        aggregation; or a plain declared column of the entity.

        Everything else is unanswerable, and says which of those it
        failed — the warning the analyst reads is the reason, not a
        category.
        """
        caps = self._capabilities()
        measure = self._catalog.measure(field_id)
        if measure is not None:
            if measure.entity == entity.name:
                return _RESOLVED
            return self._resolve_foreign(field_id, measure.entity, shape, caps)

        derived = caps.derived_anywhere(field_id)
        home = caps.derived_at(field_id, entity.name)
        if home is not None:
            if home.computable_in(shape):
                return _RESOLVED
            return _FieldVerdict(
                False,
                None,
                f"the source computes {field_id!r} only in "
                f"{sorted(s.value for s in home.shapes)} probes, not {shape.value!r}",
            )
        if derived:
            # advertised, but at another entity — the cross-entity path,
            # subject to the same shape rule as its home declaration
            elsewhere = derived[0]
            if not elsewhere.computable_in(shape):
                return _FieldVerdict(
                    False,
                    None,
                    f"the source computes {field_id!r} only in "
                    f"{sorted(s.value for s in elsewhere.shapes)} probes, not {shape.value!r}",
                )
            return self._resolve_foreign(field_id, elsewhere.entity, shape, caps)

        if field_id in self._catalog.declared_columns(entity.name):
            return _RESOLVED
        return _FieldVerdict(
            False,
            None,
            f"{field_id!r} is neither a catalog measure at the {entity.name!r} grain, nor a "
            f"measure this source computes, nor a declared column of {entity.name!r}",
        )

    def _resolve_foreign(
        self,
        field_id: str,
        home_entity: str,
        shape: ProbeShape,
        caps: RepositoryCapabilities,
    ) -> _FieldVerdict:
        """A field that lives at another entity than the probe's own.

        Legal exactly where the source says it is: an aggregation probe
        compiles one same-scope block per entity and joins them on the
        shared group keys, so both sides read the identical window, scope
        and cuts. A snapshot has no such construction — it aggregates one
        entity as-of a date — so it is refused here whatever the source
        advertises, which is also what the adapter does.
        """
        if not caps.cross_entity_ratio_of_sums:
            return _FieldVerdict(
                False,
                None,
                f"{field_id!r} is defined at the {home_entity!r} grain and this source cannot "
                f"aggregate components across entity grains in one probe",
            )
        if shape is not ProbeShape.AGGREGATION:
            return _FieldVerdict(
                False,
                None,
                f"{field_id!r} is defined at the {home_entity!r} grain and a "
                f"{shape.value} probe aggregates a single entity",
            )
        if self._catalog.entity_named(home_entity) is None:
            return _FieldVerdict(
                False,
                None,
                f"{field_id!r} names entity {home_entity!r}, which this catalog does not define",
            )
        return _FieldVerdict(True, home_entity, None)

    def _scope_predicates(self, node: ProbeNode) -> tuple[Predicate, ...]:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        return tuple(iter_predicates(probe.scope))

    def _probe_dimensions(self, node: ProbeNode) -> tuple[DimensionRef, ...]:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        return probe.dimensions

    def _resolve_and_grade(self, node: ProbeNode, concepts: tuple[str, ...]) -> EvidenceGrade:
        uncertified = False
        for ref in (
            *self._probe_dimensions(node),
            *(p.dimension for p in self._scope_predicates(node)),
        ):
            if ref.id.startswith(_TIME_BUCKET_PREFIX):
                continue
            dim = self._catalog.dimension(ref.id)
            if dim is None:
                raise UnsupportedConceptError(
                    f"unknown dimension {ref.id!r}",
                    details={"dimension": ref.id, "probe": node.id},
                )
            if not dim.certified:
                uncertified = True
        catalog_grade = EvidenceGrade.DISCOVERY if uncertified else EvidenceGrade.DIRECT
        return min_grade(catalog_grade, *self._binding_grades(node, concepts))

    def _binding_grades(
        self, node: ProbeNode, concepts: tuple[str, ...]
    ) -> tuple[EvidenceGrade, ...]:
        """Declared binding strengths for the concepts under investigation
        (design §5.5), over every field this probe actually touches.

        Certification says a field is trustworthy; a *binding* says how well
        that field stands in for the concept being asked about. A COB probe
        cut by CARC is perfectly certified data and still only proxy
        evidence that a COB problem exists — the code is the payer's
        assertion about coverage, not the coverage. Without this the grade
        law would let proxy evidence carry a certified conclusion, which is
        exactly the laundering §5.5 forbids.

        Fields the pack declares no binding for contribute nothing: silence
        is not a downgrade.
        """
        if not concepts:
            return ()
        fields: set[str] = set()
        for ref in (
            *self._probe_dimensions(node),
            *(p.dimension for p in self._scope_predicates(node)),
        ):
            if not ref.id.startswith(_TIME_BUCKET_PREFIX):
                fields.add(ref.id)
        for contract in self._contracts_for(node):
            fields.add(contract.id)
            fields.update(_measure_fields(contract.numerator))
            fields.update(_measure_fields(contract.denominator))
            fields.update(_internal_filter_dimensions(contract))
        grades: list[EvidenceGrade] = []
        for concept_id in concepts:
            for field_id in sorted(fields):
                strength = self._pack.binding_strength(concept_id, field_id)
                if strength is not None:
                    grades.append(strength)
        return tuple(grades)

    # ------------------------------------------------------- step 2: grain

    def _check_grain(self, node: ProbeNode) -> None:
        probe = node.probe
        entity = self._entity_for(node)
        contracts = self._contracts_for(node)
        expected_kind = MetricKind.SNAPSHOT if isinstance(probe, SnapshotProbe) else MetricKind.FLOW
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        for contract in contracts:
            if contract.kind is not expected_kind:
                raise GrainIncompatibleError(
                    f"metric {contract.id!r} is a {contract.kind.value} metric and cannot run "
                    f"on a {expected_kind.value} probe",
                    details={"metric": contract.id, "probe": node.id},
                )
            if contract.entity_grain is not probe.grain.entity:
                raise GrainIncompatibleError(
                    f"metric {contract.id!r} is defined at the {contract.entity_grain.value!r} "
                    f"grain, but probe '{node.id}' runs at {probe.grain.entity.value!r}",
                    details={"metric": contract.id, "probe": node.id},
                )
            if contract.is_ratio:
                for dim in self._probe_dimensions(node):
                    if dim.id.startswith(_TIME_BUCKET_PREFIX):
                        continue
                    if not contract.allows_dimension(dim):
                        raise GrainIncompatibleError(
                            f"dimension {dim.id!r} is not a legal scope dimension for ratio "
                            f"metric {contract.id!r}",
                            details={"metric": contract.id, "dimension": dim.id, "probe": node.id},
                        )
        # every group-by and scope dimension must be bound at the probe grain
        for ref in (
            *self._probe_dimensions(node),
            *(p.dimension for p in self._scope_predicates(node)),
        ):
            if ref.id.startswith(_TIME_BUCKET_PREFIX):
                continue
            dim_def = self._catalog.dimension(ref.id)
            assert dim_def is not None  # step 1 resolved it
            if dim_def.kind is DimensionKind.DERIVED_BUCKET and isinstance(probe, SnapshotProbe):
                continue
            if dim_def.column_for(entity.name) is None:
                raise GrainIncompatibleError(
                    f"dimension {ref.id!r} is not available at the {entity.name!r} grain",
                    details={"dimension": ref.id, "entity": entity.name, "probe": node.id},
                )
        self._check_cross_entity_grain(node, entity)

    def _check_cross_entity_grain(self, node: ProbeNode, entity: EntityDef) -> None:
        """The same legality, at the *other* entity of a cross-entity metric.

        A metric whose components span two grains compiles to one
        same-scope aggregate per entity, joined on the shared group keys —
        so every group-by and scope dimension has to exist on the second
        base view too, and the window's date basis has to be bound there.
        Checked here rather than discovered at execute time: the second
        side reading a different population (or failing to compile at all)
        is not something an answer can be honest about after the fact.
        """
        probe = node.probe
        if not isinstance(probe, AggregationProbe):
            return  # a snapshot is single-entity by construction (step 1)
        foreign: dict[str, str] = {}  # entity name → the field that put it there
        for contract in self._contracts_for(node):
            for field_id in (
                *_measure_fields(contract.numerator),
                *_measure_fields(contract.denominator),
            ):
                verdict = self._resolve_field(field_id, entity, ProbeShape.AGGREGATION)
                if verdict.resolved and verdict.entity is not None:
                    foreign.setdefault(verdict.entity, field_id)
        for entity_name, field_id in foreign.items():
            other = self._catalog.entity_named(entity_name)
            assert other is not None  # step 1 resolved it
            for ref in (
                *self._probe_dimensions(node),
                *(p.dimension for p in self._scope_predicates(node)),
            ):
                if ref.id.startswith(_TIME_BUCKET_PREFIX):
                    continue
                dim_def = self._catalog.dimension(ref.id)
                assert dim_def is not None  # step 1 resolved it
                if dim_def.column_for(other.name) is None:
                    raise GrainIncompatibleError(
                        f"probe '{node.id}' reads {field_id!r} at the {other.name!r} grain, but "
                        f"dimension {ref.id!r} is not available there — both sides of a "
                        "cross-grain metric must be cut by the same keys",
                        details={"dimension": ref.id, "entity": other.name, "probe": node.id},
                    )
            if other.date_basis_column(probe.window.basis) is None:
                raise DateBasisInvalidError(
                    f"probe '{node.id}' reads {field_id!r} at the {other.name!r} grain, but date "
                    f"basis {probe.window.basis.id!r} is not bound there — both sides of a "
                    "cross-grain metric must read the same window",
                    details={
                        "basis": probe.window.basis.id,
                        "entity": other.name,
                        "probe": node.id,
                    },
                )

    # -------------------------------------------------------- step 3: basis

    def _check_basis(self, node: ProbeNode, warnings: list[str]) -> None:
        """Contract legality *and* warehouse bindability, then the label.

        Legality alone is not enough: ``denial_rate`` declares ``remit``
        primary at the CLAIM grain while this warehouse binds ``remit`` only
        on the remit/transaction/denial views, so a year-over-year
        denial-rate question passes a legality-only check and then dies
        inside the SQL compiler with ``DATE_BASIS_INVALID``. A §12 code
        raised past the pass that exists to raise it is a §6.6 bypass,
        whatever the message says.

        The planner now reduces every basis to one the catalog binds
        (:mod:`revi_investigation.application.date_basis`); this step is the
        independent check that it did, so a hand-built plan, a replayed
        one, or a future planner change cannot slip past.
        """
        probe = node.probe
        if isinstance(probe, SnapshotProbe):
            basis: DateBasisRef = probe.aging_basis if probe.aging_basis is not None else SERVICE
            label = "aging basis"
        elif isinstance(probe, AggregationProbe):
            basis = probe.window.basis
            label = "basis"
        else:  # pragma: no cover - row evidence probes are not planned yet
            return
        for contract in self._contracts_for(node):
            if not contract.allows_date_basis(basis):
                raise DateBasisInvalidError(
                    f"date basis {basis.id!r} is not allowed for metric {contract.id!r} "
                    f"(allowed: {[b.id for b in contract.allowed_date_bases]})",
                    details={"metric": contract.id, "basis": basis.id, "probe": node.id},
                )
            entity = self._catalog.entity(contract.entity_grain)
            if entity is not None and entity.date_basis_column(basis) is None:
                raise DateBasisInvalidError(
                    f"probe '{node.id}' reads {contract.id!r} on the {basis.id!r} {label}, but "
                    f"that basis is not bound at the {entity.name!r} grain in this warehouse "
                    f"(bound here: {[b for b, _ in entity.date_basis_columns]})",
                    details={
                        "metric": contract.id,
                        "basis": basis.id,
                        "entity": entity.name,
                        "probe": node.id,
                    },
                )
            if basis == contract.primary_date_basis:
                continue
            # An alternate basis is permitted and labeled (§5.3). When the
            # primary was passed over because the warehouse cannot read it,
            # the label says so — otherwise "primary is 'remit'" reads as a
            # choice somebody made rather than a binding that does not exist.
            primary_unbound = (
                entity is not None
                and entity.date_basis_column(contract.primary_date_basis) is None
            )
            # Named by METRIC, never by probe. Six probes on one plan can
            # read one contract on one basis, and naming the probe makes the
            # (code, message) dedupe see six facts — six banners differing
            # only by `probe 'main'` / `'premise'` / `'main__window'` and so
            # on. They are one fact spelled six ways, and the probe id is
            # plumbing that belongs on the trace.
            if primary_unbound:
                assert entity is not None
                warnings.append(
                    f"alternate_basis_used: {contract.id!r} is computed on the "
                    f"{basis.id!r} {label} — its primary {contract.primary_date_basis.id!r} "
                    f"basis is not available at the {entity.name!r} grain in this warehouse"
                )
            else:
                warnings.append(
                    f"alternate_basis_used: {contract.id!r} is read on the "
                    f"{basis.id!r} {label} (primary is {contract.primary_date_basis.id!r})"
                )

    # -------------------------------------------------- step 4: cardinality

    def _check_cardinality(self, node: ProbeNode, warnings: list[str]) -> None:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        pinned = self._pinned_cardinalities(node)
        cells = 1
        for dim_ref in self._probe_dimensions(node):
            if dim_ref.id.startswith(_TIME_BUCKET_PREFIX):
                continue
            dim = self._catalog.dimension(dim_ref.id)
            assert dim is not None
            estimate = max(1, dim.cardinality_estimate)
            # A dimension the scope pins to an enumerated value set can only
            # produce that many groups, whatever the catalog estimate says.
            # Without this, grouping by four dimensions that are each pinned
            # to ONE value is scored at their full cross-product and refused
            # for a budget it cannot possibly spend — which is exactly the
            # shape of a drill into one detected cell.
            narrowed = pinned.get(dim_ref.id)
            if narrowed is not None:
                estimate = min(estimate, narrowed)
            cells = cells * estimate
            if cells > self._limits.max_group_cells * 1000:
                break  # avoid pointless overflow-scale products
        if cells <= self._limits.max_group_cells:
            return
        limit = probe.limit if isinstance(probe, AggregationProbe) else None
        if limit is None:
            raise QueryBudgetExceededError(
                f"probe '{node.id}' groups an estimated {cells} cells, over the "
                f"{self._limits.max_group_cells}-cell budget; a top-N limit is required",
                details={"probe": node.id, "cells": cells, "budget": self._limits.max_group_cells},
            )
        warnings.append(
            f"probe '{node.id}' truncated to the top {limit} of an estimated {cells} cells"
        )

    def _pinned_cardinalities(self, node: ProbeNode) -> dict[str, int]:
        """Per-dimension group counts the probe's own scope already forces.

        Only *conjunctive* equality/membership narrows a group-by: an
        ``eq``/``in`` predicate caps the distinct values at what it
        enumerates. Anything under an ``Or``/``Not`` widens or inverts and
        is ignored, so the estimate stays an upper bound (``iter_predicates``
        walks every clause, so the conjunctive test is explicit below).
        """
        capped: dict[str, int] = {}
        for predicate in self._top_level_predicates(node.probe.scope):
            if predicate.op not in (PredicateOp.EQ, PredicateOp.IN) or not predicate.values:
                continue
            distinct = len(set(predicate.values))
            existing = capped.get(predicate.dimension.id)
            capped[predicate.dimension.id] = (
                distinct if existing is None else min(existing, distinct)
            )
        return capped

    @staticmethod
    def _top_level_predicates(expr: FilterExpr) -> tuple[Predicate, ...]:
        """Predicates that hold unconditionally — the top-level AND chain."""
        if isinstance(expr, Predicate):
            return (expr,)
        if isinstance(expr, And):
            return tuple(
                p for clause in expr.clauses for p in PlanValidationService._top_level_predicates(clause)
            )
        return ()

    # ------------------------------------------- step 5: exclusion overlap

    def _check_exclusion_intersection(self, node: ProbeNode, warnings: list[str]) -> None:
        scope_dims = {p.dimension.id for p in self._scope_predicates(node)}
        if not scope_dims:
            return
        for contract in self._contracts_for(node):
            overlap = scope_dims & _internal_filter_dimensions(contract)
            for dim in sorted(overlap):
                warnings.append(
                    f"scope on '{dim}' interacts with metric '{contract.id}' — the contract "
                    "already constrains that dimension internally (exclusions or numerator "
                    "filter); the result reflects both conditions"
                )

    def _publish_population_caveats(self, node: ProbeNode, warnings: list[str]) -> None:
        """Every governed population caveat, on every answer that reads the
        metric (see the module docstring)."""
        for contract in self._contracts_for(node):
            caveat = population_caveat(contract.description)
            if caveat is None:
                continue
            warning = f"population_caveat: {contract.id} — {caveat}"
            if warning not in warnings:  # one probe per comparison side
                warnings.append(warning)

    # ------------------------------------------------- step 6: suppression

    def _note_suppression(self, plan: InvestigationPlan, warnings: list[str]) -> None:
        """State the §15 policy as it actually is.

        "Cells counting fewer than 11 entities are suppressed" is false over
        a frame containing a 10-entity cell, because the thing counted is a
        ratio's numerator and not the population the cell describes. Two
        rules, so two clauses: a small POPULATION is withheld entirely, and
        a small numerator over a publishable population is shown as an upper
        bound rather than dropped — dropping it removes the best-performing
        cells from a ranking and says nothing about it.
        """
        if any(self._probe_dimensions(node) for node in plan.nodes):
            threshold = self._catalog.suppression.threshold
            warnings.append(
                f"suppression: cells counting fewer than {threshold} entities in their "
                "POPULATION are withheld entirely before results leave the engine; a cell whose "
                f"population is larger but whose numerator is under {threshold} keeps its place "
                "and is published as an upper bound, never dropped"
            )

    # ------------------------------------------------ step 7: capabilities

    def _check_capabilities(self, plan: InvestigationPlan) -> None:
        caps = self._repository.capabilities()
        for node in plan.nodes:
            probe = node.probe
            if not isinstance(probe, (AggregationProbe, SnapshotProbe)):
                continue
            if any(iter_cohorts(probe.scope)) and not caps.cohort_semijoin:
                raise SourceCapabilityUnsupportedError(
                    f"probe '{node.id}' needs a cohort semi-join the source does not support",
                    details={"probe": node.id, "capability": "cohort_semijoin"},
                )
            if isinstance(probe, AggregationProbe):
                if probe.limit is not None and not caps.server_side_top_n:
                    raise SourceCapabilityUnsupportedError(
                        f"probe '{node.id}' needs server-side top-N the source does not support",
                        details={"probe": node.id, "capability": "server_side_top_n"},
                    )
                if probe.having and not caps.having_pushdown:
                    raise SourceCapabilityUnsupportedError(
                        f"probe '{node.id}' needs HAVING pushdown the source does not support",
                        details={"probe": node.id, "capability": "having_pushdown"},
                    )
            if isinstance(probe, SnapshotProbe) and not caps.as_of_reads:
                raise SourceCapabilityUnsupportedError(
                    f"probe '{node.id}' needs as-of reads the source does not support",
                    details={"probe": node.id, "capability": "as_of_reads"},
                )

    # ------------------------------------------------------ step 8: limits

    def _check_limits(self, plan: InvestigationPlan) -> None:
        if len(plan.nodes) > self._limits.max_probes:
            raise QueryBudgetExceededError(
                f"plan holds {len(plan.nodes)} probes, over the {self._limits.max_probes} "
                "probe budget",
                details={"probes": len(plan.nodes), "budget": self._limits.max_probes},
            )
        # read-only posture and row/time budgets are enforced at the
        # repository boundary (design §15); this plan-level hook exists so
        # tenants can tighten limits without touching adapters.
