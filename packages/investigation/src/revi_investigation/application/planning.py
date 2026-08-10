"""AnalysisSpec → typed InvestigationPlan (design §8.1 step 8) and plan diffs.

Two planning modes:

**Direct metric query** — the spec's measures compile into probes grouped by
(metric kind, entity grain, effective date basis): FLOW groups become one
:class:`AggregationProbe` each; SNAPSHOT groups become a
:class:`SnapshotProbe` as-of the watermark's newest data date. A direct
query cut by dimensions with *no* comparison also gets a ``rank`` step, so
a ranked-population question answers instead of executing correctly and
returning nothing (see ``_rank_uncompared``).

**Playbook mode** — the pack playbook's probe templates expand through the
same grouping. Template semantics:

- ``window=None`` inherits the spec window. A template's own window applies
  only when the analyst gave no explicit window (``window_explicit=False``);
  an explicit analyst window governs every probe — the user's stated scope
  always wins over playbook defaults.
- ``basis_override`` wins over the spec basis; otherwise the spec basis
  applies when the contract allows it, else the contract's primary basis.
  Whatever that choice is, it is then reduced to a basis this warehouse
  actually binds at the metric's grain
  (:mod:`revi_investigation.application.date_basis`) — a plan may not name
  a basis no probe can read.
- ``top_n`` becomes the probe ``limit`` plus a server-side ordering by the
  group's first additive measure (descending) when one exists. At
  ``evidence_depth=deep`` that pack-authored cutoff is scaled by
  :data:`DEEP_TOP_N_MULTIPLIER` — a wider sweep of the same probe, never a
  different or weaker check.
- ``$dimension`` placeholders bind to the spec's interpreted dimensions;
  templates needing a dimension parameter are skipped (with a surfaced
  note) when the spec names none.

**Comparison pairing** — when the context carries a comparison, every flow
probe gets a ``<id>__prior`` twin over the deterministically derived prior
range, and the playbook's ``compare`` transform step consumes the pairs.
Snapshot probes are a point in time and are never paired.

**Transform steps** are emitted only when they can be fully typed against
the planned probes; anything else becomes an honest plan note (surfaced as
a warning), never a silent drop. Playbook arg conventions handled here:
``by: impact_cents`` resolves to the paired money measure's ``__delta``
column on compare outputs, ranked ascending so the most negative movement
(the biggest decline of a higher-is-good measure) comes first.

``plan_hash`` is a SHA-256 over the sorted probe hashes — stable across
runs, sensitive to any probe change (the evidence-cache and plan-diff key).

**Content-stable probe hashing.** ``ProbeNode.hash`` (the cache/diff/replay
identity) hashes a *normalized* projection of the probe: predicate
``origin_turn`` tags are stripped and ``InCohort`` cohort refs are reduced
to their *definition* (volatile identity — cohort id, size, pinned
materialization handle — removed, nested predicates normalized). What a
probe retrieves at a given (watermark, pack) depends only on its logical
content, never on which turn asked or which uuid a re-materialization drew;
identical drill-downs therefore share cache entries and replays reproduce
identical plan hashes (§7.9, §18.1-15). Repository execution still receives
the full probe with its pinned cohort handle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from revi_calculation_contracts.contract import MetricContract, MetricKind, MetricUnit
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.anchoring import window_anchor
from revi_investigation.application.capability_ports import (
    PackPort,
    PlaybookSpec,
    ProbeTemplateSpec,
    TransformStepSpec,
)
from revi_investigation.application.date_basis import resolve_answerable_basis
from revi_investigation.domain.context import (
    AnalysisSpec,
    AskedDirection,
    AskedMagnitude,
    descending_for_order,
    wanted_delta_sign,
)
from revi_investigation_contracts.settings import EvidenceDepth
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.errors import UnsupportedConceptError
from revi_kernel.filters import (
    And,
    FilterExpr,
    InCohort,
    Not,
    Or,
    Predicate,
    and_merge,
)
from revi_kernel.probes import (
    AggregationProbe,
    EvidenceProbe,
    Ordering,
    SnapshotProbe,
    probe_hash,
)
from revi_kernel.refs import (
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
)
from revi_kernel.scope import (
    AbsoluteRange,
    Comparison,
    ComparisonKind,
    TimeWindow,
    derive_comparison,
    resolve_window,
)

_DIMENSION_PARAM = "$dimension"
_IMPACT_ARG = "impact_cents"
_PRIOR_SUFFIX = "__prior"

#: Playbook transforms that ARE the answer, as opposed to transforms that
#: enrich one. A pack may declare a transform this milestone's engine does
#: not implement; for ``share_of_total`` or ``decompose`` that costs the
#: reader a column and the plan says so in a note. For these two it costs
#: them the question:
#:
#: * ``pivot`` is what turns the payer scorecard's six probes into one row
#:   per payer — without it there is no card, only six unrelated frames;
#: * ``project_lagged_realization`` is the cash outlook's forecast —
#:   without it there is no next month, only a total for a window the
#:   question did not name.
#:
#: Named here rather than in the pack because it is a fact about THIS
#: engine's operator set, not about the content: the day either is
#: implemented, it comes off this list and the playbooks answer unchanged.
ANSWERING_TRANSFORMS: frozenset[str] = frozenset({"pivot", "project_lagged_realization"})

#: What ``evidence_depth=deep`` multiplies a *pack-authored* ``top_n`` by.
#:
#: A playbook's ``top_n`` is the platform's own cutoff — the pack author's
#: judgement about how many payers are worth looking at by default, not a
#: number the analyst asked for. Deep mode widens that judgement so fewer
#: probes come back truncated, which is a real change in how much evidence
#: the answer rests on: more rows retrieved, a different probe hash, a
#: different cache entry, and the §6.6 truncation warning either narrowed
#: or gone. An analyst's OWN limit (``spec.limit``, set by an ``Expand``
#: gesture) is never rescaled — "show me the top 5" means five.
DEEP_TOP_N_MULTIPLIER = 4

#: How many cells a comparison's PRIOR side may be read whole at, before
#: the plan keeps its top-N and lets the honesty guard handle the rest.
#:
#: Every cut in this catalog is far under it — the widest is
#: ``rendering_provider`` at ~150 — so in practice the prior side of every
#: comparison is retrieved complete and a key missing from it is a real
#: absence rather than a retrieval decision. The cap exists so that a
#: future high-cardinality dimension cannot silently plan an unbounded
#: probe: past it the limit stands, the frame comes back ``truncated``, and
#: :func:`revi_investigation.application.calculation_glue` publishes the
#: unmatched cells as UNKNOWN instead of zero.
UNTRUNCATED_PRIOR_CELL_CAP = 2_000

_NORMALIZED_ORIGIN = ReferentId(value="__cohort__", kind=ReferentKind.COHORT)


def _scaled_top_n(top_n: int | None, depth: EvidenceDepth) -> int | None:
    """A pack-authored cutoff at the requested evidence depth.

    ``None`` (no cutoff) stays ``None``: there is nothing to widen, and
    inventing a limit where the pack asked for none would *narrow* the
    evidence in the name of deepening it.
    """
    if top_n is None or depth is EvidenceDepth.STANDARD:
        return top_n
    return top_n * DEEP_TOP_N_MULTIPLIER


def _normalize_scope(expr: FilterExpr) -> FilterExpr:
    """Strip turn provenance and volatile cohort identity for hashing."""
    if isinstance(expr, Predicate):
        return replace(expr, origin_turn=None)
    if isinstance(expr, InCohort):
        definition = expr.cohort.definition
        normalized = CohortDefinition(
            entity=definition.entity,
            scope=_normalize_scope(definition.scope),
            window=definition.window,
        )
        return InCohort(
            cohort=CohortRef(
                id="__cohort__",
                definition=normalized,
                origin=_NORMALIZED_ORIGIN,
                size=0,
                pinned=None,
            ),
            origin_turn=None,
        )
    if isinstance(expr, And):
        return And(tuple(_normalize_scope(clause) for clause in expr.clauses))
    if isinstance(expr, Or):
        return Or(tuple(_normalize_scope(clause) for clause in expr.clauses))
    return Not(_normalize_scope(expr.clause))


def content_probe_hash(probe: EvidenceProbe) -> str:
    """The content-stable probe identity (see module docstring)."""
    normalized: EvidenceProbe = replace(probe, scope=_normalize_scope(probe.scope))
    return probe_hash(normalized)


@dataclass(frozen=True, slots=True)
class ProbeNode:
    id: str
    probe: EvidenceProbe
    purpose: str
    consumes_cohorts: tuple[str, ...] = ()

    @property
    def hash(self) -> str:
        return content_probe_hash(self.probe)


@dataclass(frozen=True, slots=True)
class TransformPlanStep:
    """A fully typed transform application: named inputs, string args."""

    id: str
    operator: str
    inputs: tuple[str, ...]
    args: tuple[tuple[str, str], ...] = ()

    def arg(self, name: str) -> str | None:
        for key, value in self.args:
            if key == name:
                return value
        return None


@dataclass(frozen=True, slots=True)
class TransformPlan:
    steps: tuple[TransformPlanStep, ...] = ()


@dataclass(frozen=True, slots=True)
class InvestigationPlan:
    nodes: tuple[ProbeNode, ...]
    transforms: TransformPlan
    playbook_id: str | None = None
    notes: tuple[str, ...] = ()
    #: The movement the question asked about, carried from the spec so the
    #: layer that SELECTS rows can honor it (design §8.1 step 8). Ranking a
    #: compare frame by delta is direction-blind by construction: ascending
    #: answers "biggest decrease", descending "biggest increase", and the
    #: plan is the only place that knows which was asked. Rank/top_k steps
    #: additionally carry it as a ``direction`` arg so a transform operator
    #: reads it without reaching back to the plan.
    direction: AskedDirection | None = None
    #: The extremity phrased over that direction ("biggest"/"smallest").
    magnitude: AskedMagnitude | None = None
    #: ``(dimension id, declared bucket order)`` for every ordinal bucket
    #: dimension this plan cuts by, in the catalog's own declared order —
    #: which for a runway dimension IS urgency order (``expired``, ``0-30``,
    #: ``31-60``, …).
    #:
    #: Without it, findings are ordered by SIZE and nothing downstream knows
    #: the buckets have a direction, so the narrative sequences the ``90+``
    #: band ahead of ``61-90`` — work the least urgent first and let the
    #: 61-90 band age into expired. The catalog declares the order
    #: (``dimensions.yaml``: ``buckets: ["expired", "0-30", "31-60",
    #: "61-90", "90+", "filed"]``); the plan carries it to the layer that
    #: orders sentences.
    bucket_orders: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def bucket_order(self, dimension_id: str) -> tuple[str, ...] | None:
        for name, order in self.bucket_orders:
            if name == dimension_id:
                return order
        return None

    def node(self, node_id: str) -> ProbeNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(f"no probe node {node_id!r} in plan")

    @property
    def plan_hash(self) -> str:
        blob = "\n".join(sorted(node.hash for node in self.nodes))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanDiff:
    added: tuple[ProbeNode, ...]
    removed: tuple[ProbeNode, ...]
    unchanged: tuple[ProbeNode, ...]


#: How deep a transform chain may be walked back to the probe that fed it.
#: A plan is a DAG a few steps deep; this exists so a malformed plan cannot
#: spin rather than to express a real limit.
_WINDOW_WALK_LIMIT = 32


def frame_window(plan: InvestigationPlan, frame_id: str) -> TimeWindow | None:
    """The window the probe behind ``frame_id`` actually read.

    A playbook probe template may declare its OWN window — ``daily_portfolio``
    measures denial rate over ``{quantity: 4, unit: week, mode: full_periods}``
    — which the planner resolves and applies instead of the investigation
    window whenever the analyst named no window of their own
    (:meth:`PlanBuilder._build_playbook`). That resolution is correct and
    invisible unless a finding can ask about it: titling every cell with
    ``spec.context.window`` publishes "denial rate: 14.3%
    (2026-07-01..2026-07-31)" over a figure computed across
    2026-07-06..2026-08-02.

    The probe knows its window. This is how a finding gets to ask.

    ``None`` means "no window applies" — a snapshot probe reads a balance at
    the watermark and applies no ``start..end`` predicate at all, which the
    findings layer already renders as "as of". Transform outputs are walked
    back to their first input, because every operator in this engine
    preserves the window of the frame it was given; a ``compare`` output is
    keyed to its CURRENT side, which is the first input by construction.
    """
    seen: set[str] = set()
    current = frame_id
    steps = {step.id: step for step in plan.transforms.steps}
    for _ in range(_WINDOW_WALK_LIMIT):
        if current in seen:
            return None  # pragma: no cover - a cyclic plan cannot be built
        seen.add(current)
        for node in plan.nodes:
            if node.id == current:
                return getattr(node.probe, "window", None)
        step = steps.get(current)
        if step is None or not step.inputs:
            return None
        current = step.inputs[0]
    return None  # pragma: no cover - defensive


def declared_probe_windows(plan: InvestigationPlan) -> tuple[tuple[str, TimeWindow], ...]:
    """``(node id, window)`` for every probe whose window is its own.

    "Its own" is decided against the other probes on the plan rather than
    against the spec, because the plan is what this module owns; the caller
    compares against the investigation window. Ordered by node id so the
    disclosure a header renders is stable across runs.
    """
    out: list[tuple[str, TimeWindow]] = []
    for node in plan.nodes:
        window = getattr(node.probe, "window", None)
        if window is not None:
            out.append((node.id, window))
    return tuple(out)


def resolved_orderings(plan: InvestigationPlan) -> tuple[tuple[str, str, bool], ...]:
    """``(frame id, column, descending)`` for every frame this plan ordered.

    The ordering a ranked question resolves exists in three places inside
    the plan — an :class:`~revi_kernel.probes.Ordering` on the probe,
    ``by``/``descending`` args on a rank step, and the ``{by}__rank`` column
    the rank operator appends — and none of them reaches the renderer on its
    own. Without this the findings obey "best to worst" while the chart
    directly beneath them is drawn alphabetically, off the same rows.

    Both sources are read, transform steps last so they win: a rank step is
    a later and more specific decision than the probe's own ``ORDER BY``,
    and it is the one the findings layer reads. The step's INPUT frame is
    keyed as well as its output, because the rank operator appends a rank
    column rather than reordering rows — the frame that gets charted is the
    input, and the output frame is skipped by the chart builder.

    Nothing is inferred: a plan that resolved no ordering yields no entry,
    and the chart then publishes ``sort: null`` rather than a guess a
    renderer would sort by.
    """
    out: dict[str, tuple[str, bool]] = {}
    for node in plan.nodes:
        order_by = getattr(node.probe, "order_by", ())
        for ordering in order_by:
            out[node.id] = (ordering.by.id, ordering.descending)
            break
    for step in plan.transforms.steps:
        if step.operator not in ("rank", "top_k"):
            continue
        by = step.arg("by")
        if not by:
            continue
        # ``top_k`` is descending-only by construction (see the operator).
        descending = step.operator == "top_k" or step.arg("descending") != "false"
        out[step.id] = (by, descending)
        for source in step.inputs:
            out[source] = (by, descending)
    return tuple((frame_id, by, desc) for frame_id, (by, desc) in out.items())


# ---------------------------------------------------------------------------
# probe grouping


@dataclass(frozen=True, slots=True)
class _MetricGroup:
    kind: MetricKind
    grain_entity: str  # EntityGrain value
    basis: DateBasisRef
    contracts: tuple[MetricContract, ...]


class BuildInvestigationPlanService:
    """Compile an AnalysisSpec (plus optional playbook) into a typed plan."""

    def __init__(self, pack: PackPort, catalog: CatalogSnapshot) -> None:
        self._pack = pack
        # The planner needs the catalog for one decision only: which of a
        # contract's declared date bases this warehouse actually binds at
        # the metric's grain (§5.3). Without it a plan can name a basis
        # the source cannot read, and the refusal surfaces as a SQL
        # compile error past every governed checkpoint.
        self._catalog = catalog

    # ------------------------------------------------------------------ api

    def build(
        self,
        spec: AnalysisSpec,
        *,
        playbook_id: str | None = None,
        window_explicit: bool = True,
        evidence_depth: EvidenceDepth = EvidenceDepth.STANDARD,
    ) -> InvestigationPlan:
        # Explicit measures win: a spec that names metrics (first-turn
        # interpretation or a Pivot refinement) plans a direct query even
        # when a playbook context is inherited from the parent turn.
        if not spec.measures and playbook_id is not None:
            playbook = self._pack.playbook(playbook_id)
            if playbook is None:
                raise UnsupportedConceptError(
                    f"unknown playbook {playbook_id!r}", details={"playbook": playbook_id}
                )
            return self._build_playbook(
                spec, playbook, window_explicit=window_explicit, evidence_depth=evidence_depth
            )
        if not spec.measures and playbook_id is None:
            raise UnsupportedConceptError(
                "the question resolved to no governed measures or playbook",
                details={"reason": "empty measures"},
            )
        return self._build_direct(spec)

    # --------------------------------------------------------------- direct

    def _build_direct(self, spec: AnalysisSpec) -> InvestigationPlan:
        if not spec.measures:
            raise UnsupportedConceptError(
                "the question resolved to no governed measures or playbook",
                details={"reason": "empty measures"},
            )
        notes: list[str] = []
        nodes: list[ProbeNode] = []
        groups = self._group_metrics(
            tuple(ref.id for ref in spec.measures), spec, basis_override=None
        )
        dimensions = spec.dimensions
        for index, group in enumerate(groups):
            node_id = "main" if index == 0 else f"main_{index + 1}"
            nodes.append(
                self._node_for_group(
                    node_id,
                    group,
                    spec,
                    dimensions=dimensions,
                    window=spec.context.window,
                    limit=spec.limit,
                    rank_by=spec.rank_by,
                    rank_descending=spec.rank_descending,
                    purpose="direct metric query",
                )
            )
        nodes.extend(self._premise_nodes(spec, nodes))
        steps = self._pair_comparisons(nodes, spec)
        if not steps and dimensions:
            steps.extend(self._rank_uncompared(nodes, spec))
        notes.extend(self._dropped_grain_notes(spec, nodes))
        return InvestigationPlan(
            nodes=tuple(nodes),
            transforms=TransformPlan(steps=tuple(steps)),
            playbook_id=None,
            notes=tuple(notes),
            direction=spec.direction,
            magnitude=spec.magnitude,
            bucket_orders=self._bucket_orders(nodes),
        )

    def _bucket_orders(
        self, nodes: list[ProbeNode]
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """The declared order of every ordinal bucket dimension this plan cuts by.

        Read off the catalog rather than inferred from the values, because
        the values do not carry it: sorted lexically, ``expired`` follows
        ``90+``, and sorted by size the most urgent band leads only by
        accident.
        """
        out: list[tuple[str, tuple[str, ...]]] = []
        seen: set[str] = set()
        for node in nodes:
            probe = node.probe
            if not isinstance(probe, (AggregationProbe, SnapshotProbe)):
                continue
            for ref in probe.dimensions:
                if ref.id in seen:
                    continue
                seen.add(ref.id)
                declared = self._catalog.dimension(ref.id)
                if declared is not None and declared.buckets:
                    out.append((ref.id, tuple(declared.buckets)))
        return tuple(out)

    def _named_cut_nodes(
        self,
        spec: AnalysisSpec,
        nodes: list[ProbeNode],
        node_contracts: dict[str, tuple[MetricContract, ...]],
        notes: list[str],
        *,
        window: TimeWindow,
        limit: int | None,
    ) -> list[ProbeNode]:
        """A probe for a governed dimension the utterance named and no template cut by.

        A governed dimension the analyst names is the primary cut, not a
        suggestion. Without this probe, "on service dates, break my unbilled
        inventory down by filing runway bucket and tell me how much is
        already expired" comes back cut by plan and facility, with prose
        instructing the reader to run the cut themselves ("Before anyone
        works this list, cut F1 by filing_runway_bucket…") — a correct
        number reachable only by typing the internal identifier.
        """
        if not spec.dimensions:
            return []
        already = {
            ref.id
            for node in nodes
            if isinstance(node.probe, (AggregationProbe, SnapshotProbe))
            for ref in node.probe.dimensions
        }
        wanted = tuple(ref for ref in spec.dimensions if ref.id not in already)
        if not wanted:
            return []
        contracts = [
            contract
            for group in node_contracts.values()
            for contract in group
            if all(contract.allows_dimension(ref) for ref in wanted)
        ]
        if not contracts:
            return []
        groups = self._group_metrics(
            tuple(dict.fromkeys(contract.id for contract in contracts)),
            spec,
            basis_override=None,
        )
        if not groups:
            return []
        group = groups[0]
        node = self._node_for_group(
            "named_cut",
            group,
            spec,
            dimensions=wanted,
            window=window,
            limit=limit,
            rank_by=None,
            rank_descending=True,
            purpose=(
                "the breakdown the question named: "
                + ", ".join(ref.id for ref in wanted)
            ),
        )
        node_contracts[node.id] = group.contracts
        notes.append(
            "named_cut_applied: the question named "
            + ", ".join(repr(ref.id) for ref in wanted)
            + " and no playbook probe cuts by it, so this answer leads with that breakdown "
            "rather than describing it."
        )
        return [node]

    @staticmethod
    def _dropped_grain_notes(spec: AnalysisSpec, nodes: list[ProbeNode]) -> list[str]:
        """Breakdowns the question asked for that no probe actually cuts by.

        A dropped grain is the quietest wrong answer this engine can give:
        the analyst asks "by payer", every probe aggregates over payer, and
        the totals that come back are *averages* presented as the answer to
        a split. Nothing else in the pipeline notices, because a plan that
        ignores a dimension is a perfectly valid plan. So the plan says it
        — surfaced as a turn warning like every other note — and the answer
        is caveated rather than silently flattened.
        """
        planned: set[str] = set()
        for node in nodes:
            probe = node.probe
            if isinstance(probe, (AggregationProbe, SnapshotProbe)):
                planned.update(ref.id for ref in probe.dimensions)
        return [
            f"dropped_grain: the question asked for a breakdown by {ref.id!r} and no probe in "
            "this plan is cut by it — the numbers below are aggregated over "
            f"{ref.id!r}, not split by it"
            for ref in spec.dimensions
            if ref.id not in planned
        ]

    #: Node-id prefix for the premise-verification probe. Distinct from any
    #: playbook template id (those never start with an underscore) so the
    #: findings layer can recognize it structurally.
    PREMISE_PREFIX = "premise"

    def _premise_nodes(self, spec: AnalysisSpec, nodes: list[ProbeNode]) -> list[ProbeNode]:
        """The aggregate probe a stated movement has to be checked against.

        "Why did denials at Federal Medicare double in July?" asserts a
        movement. Answered as a query it returns the cells that rose — three
        CARC cells totalling $3,204 of increases, presented as the
        explanation, inside a move from $58,983.54 to $10,915.24. Every
        number is right and the answer is false, because nothing has
        computed the thing the question took for granted.

        So an asserted direction plans ONE extra probe, cloned from the
        probe whose breakdown the findings layer will publish from: the same
        measures, scope, window and basis, **ungrouped** and unlimited.
        Paired with its prior twin by :meth:`_pair_comparisons` it yields the
        aggregate movement, and the findings layer compares that movement's
        sign against what was asserted before anything is offered as a
        cause.

        Cloned rather than re-derived so the aggregate is the same
        measurement as the cells it contextualizes — a premise checked on a
        different basis, window or population would be its own kind of wrong
        answer. Direct queries and playbooks go through the same clone,
        which is why both are covered by one rule. Only turns that assert
        something get it; every other plan is byte-identical.
        """
        if not spec.direction_asserted:
            return []
        for node in nodes:
            probe = node.probe
            if not isinstance(probe, AggregationProbe) or not probe.dimensions:
                # With no breakdown the probe IS the aggregate: there is
                # nothing to verify against that the turn does not compute.
                continue
            return [
                ProbeNode(
                    id=self.PREMISE_PREFIX,
                    probe=replace(probe, dimensions=(), limit=None, order_by=()),
                    purpose="premise check: the aggregate movement the question asserts",
                )
            ]
        return []

    def _rank_uncompared(self, nodes: list[ProbeNode], spec: AnalysisSpec) -> list[TransformPlanStep]:
        """Rank a grouped query that has nothing to compare.

        A direct query cut by dimensions with no comparison window is a
        *ranked population* question — "which cells hold the most?" —
        which is exactly the shape the concentration finding path reads.
        Without this step such a plan executes perfectly and then answers
        **nothing**: correct evidence, no findings. The playbook path already
        avoids that; a typed first turn (a portfolio card drill, a chart
        click from a fresh session) makes direct queries hit it too, so the
        same generic step is emitted here rather than a second finding shape
        being invented.

        Ranked by the group's first measure, descending — biggest first,
        whatever the unit — unless the analyst asked for an order, in which
        case "best" and "worst" are resolved against the metric contract's
        own sign convention. "Rank payers best to worst" on a higher-is-bad
        rate sorts ASCENDING, and rank #1 is then the payer the question
        called best; ranking it descending and narrating row one as "ranks
        first" is how the worst payer got published as the best.
        ``impact_cents`` still only appears when that measure is money (the
        findings layer owns that rule).
        """
        steps: list[TransformPlanStep] = []
        for node in nodes:
            probe = node.probe
            # Row-evidence probes retrieve masked sample rows, not measures;
            # there is nothing to rank and nothing to conclude from them.
            if not isinstance(probe, (AggregationProbe, SnapshotProbe)) or not probe.measures:
                continue
            measure_id = probe.measures[0].id
            asked = descending_for_order(spec.order, self._contract(measure_id).sign)
            descending = (
                asked
                if asked is not None
                else spec.magnitude is not AskedMagnitude.SMALLEST
            )
            args: tuple[tuple[str, str], ...] = (
                ("by", measure_id),
                ("descending", "true" if descending else "false"),
            )
            if spec.direction is not None:
                args = (*args, ("direction", spec.direction.value))
            steps.append(
                TransformPlanStep(
                    id=f"{node.id}__rank", operator="rank", inputs=(node.id,), args=args
                )
            )
        return steps

    # ------------------------------------------------------------- playbook

    def _build_playbook(
        self,
        spec: AnalysisSpec,
        playbook: PlaybookSpec,
        *,
        window_explicit: bool,
        evidence_depth: EvidenceDepth = EvidenceDepth.STANDARD,
    ) -> InvestigationPlan:
        notes: list[str] = []
        nodes: list[ProbeNode] = []
        node_contracts: dict[str, tuple[MetricContract, ...]] = {}
        watermark = spec.context.watermark

        for template in playbook.probes:
            dimensions = self._template_dimensions(template, spec)
            if dimensions is None:
                notes.append(
                    f"probe template '{template.id}' skipped: it parameterizes a dimension "
                    "and the question names none"
                )
                continue
            window = spec.context.window
            if template.window is not None and not window_explicit:
                # The same anchor rule interpretation resolved the analyst's
                # own window against, so a playbook default and an analyst
                # window never sit on two different "now"s.
                window = resolve_window(
                    template.window,
                    window_anchor(watermark, template.window.mode),
                    basis=spec.context.window.basis,
                    calendar=spec.context.window.calendar,
                )
            groups = self._group_metrics(
                template.metric_ids, spec, basis_override=template.basis_override
            )
            for index, group in enumerate(groups):
                node_id = template.id if index == 0 else f"{template.id}_{index + 1}"
                nodes.append(
                    self._node_for_group(
                        node_id,
                        group,
                        spec,
                        dimensions=dimensions,
                        window=window,
                        limit=_scaled_top_n(template.top_n, evidence_depth),
                        rank_by=None,
                        rank_descending=True,
                        purpose=template.purpose or f"playbook probe {template.id}",
                    )
                )
                node_contracts[node_id] = group.contracts

        named = self._named_cut_nodes(
            spec,
            nodes,
            node_contracts,
            notes,
            window=spec.context.window,
            limit=_scaled_top_n(spec.limit, evidence_depth),
        )
        # Prepended, so the cut the question NAMED is the frame the
        # findings path reads first.
        nodes[:0] = named
        nodes.extend(self._premise_nodes(spec, nodes))
        prior_steps = self._pair_comparisons(nodes, spec, node_contracts)
        steps, transform_notes = self._playbook_transforms(
            playbook, nodes, node_contracts, prior_steps, spec
        )
        if named:
            steps[:0] = self._rank_uncompared(named, spec)
        notes.extend(transform_notes)
        notes.extend(self._dropped_grain_notes(spec, nodes))
        return InvestigationPlan(
            nodes=tuple(nodes),
            transforms=TransformPlan(steps=tuple(steps)),
            playbook_id=playbook.id,
            notes=tuple(notes),
            direction=spec.direction,
            magnitude=spec.magnitude,
            bucket_orders=self._bucket_orders(nodes),
        )

    # ------------------------------------------------------------- helpers

    def _contract(self, metric_id: str) -> MetricContract:
        contract = self._pack.metric(metric_id)
        if contract is None:
            raise UnsupportedConceptError(
                f"unknown metric {metric_id!r}", details={"metric": metric_id}
            )
        return contract

    def _group_metrics(
        self, metric_ids: tuple[str, ...], spec: AnalysisSpec, *, basis_override: str | None
    ) -> tuple[_MetricGroup, ...]:
        """Group metrics by (kind, entity grain, effective basis) — one probe
        per group; a probe can only aggregate one entity at one basis."""
        spec_basis = spec.context.window.basis
        grouped: dict[tuple[MetricKind, str, str], list[MetricContract]] = {}
        order: list[tuple[MetricKind, str, str]] = []
        for metric_id in metric_ids:
            contract = self._contract(metric_id)
            if basis_override is not None:
                requested = DateBasisRef(basis_override.lower())
            elif contract.allows_date_basis(spec_basis):
                requested = spec_basis
            else:
                requested = contract.primary_date_basis
            # …and then the basis this warehouse can actually read it on.
            basis = resolve_answerable_basis(contract, requested, self._catalog).basis
            key = (contract.kind, contract.entity_grain.value, basis.id)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(contract)
        return tuple(
            _MetricGroup(
                kind=key[0],
                grain_entity=key[1],
                basis=DateBasisRef(key[2]),
                contracts=tuple(grouped[key]),
            )
            for key in order
        )

    def _with_companions(
        self, dimensions: tuple[DimensionRef, ...], contracts: tuple[MetricContract, ...]
    ) -> tuple[DimensionRef, ...]:
        """Add the dimensions a requested cut is only meaningful alongside.

        The catalog declares companionship (``companion_dimensions``); this
        applies it. A breakdown "by CARC" that cuts by ``carc`` alone puts
        CO-50 — a contractual write-off nobody can appeal — in the same row
        as PI-50, which is disputable money: $21,234 and $5,752 merged under
        one label, with the rendering layer left to disclose "(all
        adjustment groups)" over a number that has already lost the
        distinction. Every pack playbook that cuts by ``carc`` conjoins
        ``group_code`` by hand; applied here, a free-form question gets the
        same treatment instead of the merged version.

        A companion is added only when the governing contracts actually
        allow that cut — a metric that cannot be sliced by ``group_code``
        would turn an answerable question into ``GRAIN_INCOMPATIBLE``, and
        the disclosure the renderer already makes is the honest fallback.
        The companion is placed BEFORE the dimension it completes, so a row
        reads "CO / 50" in the order the domain says it.
        """
        resolved: list[DimensionRef] = []
        for ref in dimensions:
            declared = self._catalog.dimension(ref.id)
            for companion_id in declared.companion_dimensions if declared else ():
                companion = DimensionRef(companion_id)
                if companion in resolved or companion in dimensions:
                    continue
                if self._catalog.dimension(companion_id) is None:
                    continue
                if any(not c.allows_dimension(companion) for c in contracts):
                    continue
                resolved.append(companion)
            if ref not in resolved:
                resolved.append(ref)
        return tuple(resolved)

    def _template_dimensions(
        self, template: ProbeTemplateSpec, spec: AnalysisSpec
    ) -> tuple[DimensionRef, ...] | None:
        """Resolve template dimensions; ``None`` means the template cannot
        bind (a ``$dimension`` parameter with nothing to bind to)."""
        resolved: list[DimensionRef] = []
        for dim in template.dimensions:
            if dim == _DIMENSION_PARAM:
                if not spec.dimensions:
                    return None
                resolved.extend(d for d in spec.dimensions if d not in resolved)
            else:
                ref = DimensionRef(dim)
                if ref not in resolved:
                    resolved.append(ref)
        return tuple(resolved)

    def _node_for_group(
        self,
        node_id: str,
        group: _MetricGroup,
        spec: AnalysisSpec,
        *,
        dimensions: tuple[DimensionRef, ...],
        window: TimeWindow,
        limit: int | None,
        rank_by: MetricRef | None,
        rank_descending: bool,
        purpose: str,
    ) -> ProbeNode:
        measures = tuple(MetricRef(contract.id) for contract in group.contracts)
        # …plus any dimension the catalog says these cuts are only
        # meaningful alongside (``carc`` without ``group_code`` merges a
        # contractual write-off with a disputable reduction).
        dimensions = self._with_companions(dimensions, group.contracts)
        scope = spec.context.effective_scope()
        if spec.context.cohort is not None:
            # the active cohort is part of every probe's population (§7.5)
            scope = and_merge(scope, InCohort(cohort=spec.context.cohort))
        grain_entity = EntityGrain(group.grain_entity)
        window = replace(window, basis=group.basis) if window.basis != group.basis else window

        if group.kind is MetricKind.SNAPSHOT:
            aging = self._aging_basis(group, spec)
            probe: EvidenceProbe = SnapshotProbe(
                measures=measures,
                dimensions=dimensions,
                scope=scope,
                as_of=spec.context.watermark.newest_data_date,
                grain=Grain(grain_entity),
                aging_basis=aging,
            )
            return ProbeNode(id=node_id, probe=probe, purpose=purpose)

        order_by = self._ordering(group, limit, rank_by, rank_descending)
        probe = AggregationProbe(
            measures=measures,
            dimensions=dimensions,
            scope=scope,
            window=window,
            grain=Grain(grain_entity, spec.context.grain.time_bucket),
            order_by=order_by,
            limit=limit,
        )
        return ProbeNode(id=node_id, probe=probe, purpose=purpose)

    def _aging_basis(self, group: _MetricGroup, spec: AnalysisSpec) -> DateBasisRef:
        """Snapshot aging basis: the spec basis when every snapshot contract
        allows it, else the group's primary basis — and in either case one
        this warehouse binds at the entity (§5.3)."""
        spec_basis = spec.context.window.basis
        requested = (
            spec_basis
            if all(contract.allows_date_basis(spec_basis) for contract in group.contracts)
            else group.contracts[0].primary_date_basis
        )
        return resolve_answerable_basis(group.contracts[0], requested, self._catalog).basis

    def _ordering(
        self,
        group: _MetricGroup,
        limit: int | None,
        rank_by: MetricRef | None,
        rank_descending: bool,
    ) -> tuple[Ordering, ...]:
        if limit is None:
            return ()
        additive = {c.id for c in group.contracts if not c.is_ratio}
        if rank_by is not None and rank_by.id in additive:
            return (Ordering(by=rank_by, descending=rank_descending),)
        for contract in group.contracts:
            if contract.id in additive:
                return (Ordering(by=MetricRef(contract.id), descending=True),)
        return ()  # ratio-only probe: limit still caps rows; kernel ranks later

    # ---------------------------------------------------------- comparisons

    def _pair_comparisons(
        self,
        nodes: list[ProbeNode],
        spec: AnalysisSpec,
        node_contracts: dict[str, tuple[MetricContract, ...]] | None = None,
    ) -> list[TransformPlanStep]:
        """Give every flow probe a prior-window twin and a compare step.

        Two invariants this pairing exists to hold.

        **The join may not key on a time bucket.** A comparison is movement
        between two WINDOWS. A monthly-bucketed probe over 2026-04..06
        joined against its prior twin over 2026-01..03 shares *no* key at
        all: every current cell reads as "absent from prior", and for an
        additive unit absent fills as zero. That publishes "CO / 16 — denied
        dollars moved from $0.00 to $41,918.23" at direct/high with an impact
        figure over a cell that had actually FALLEN $15,780, and the $41,918
        is one month's figure published as the quarter's — three times over,
        once per bucket, each claiming a different rank over one window. So
        a bucketed probe is compared through a de-bucketed
        twin — the aggregate over the stated window, which is the thing the
        question asked about — and the bucketed frame keeps its trend.

        **The prior side may not be top-N truncated.** A key that survives
        the current top-N but falls outside the prior top-N is not a zero;
        it is a value this plan chose not to retrieve. The prior twin
        therefore drops the ``limit``/``ORDER BY`` its current side carries
        whenever the cut is small enough to read whole
        (:data:`UNTRUNCATED_PRIOR_CELL_CAP`), which for every cut in this
        catalog it is. When it is not, the limit stands and
        :mod:`calculation_glue` marks the unmatched cells UNKNOWN rather
        than zero — never silently either way.
        """
        comparison = spec.context.comparison
        if comparison is None:
            return []
        steps: list[TransformPlanStep] = []
        for node in list(nodes):
            probe = node.probe
            if not isinstance(probe, AggregationProbe):
                continue
            current_id, current_probe = node.id, probe
            if probe.grain.time_bucket is not None:
                current_id = f"{node.id}__window"
                current_probe = replace(
                    probe, grain=replace(probe.grain, time_bucket=None)
                )
                nodes.append(
                    ProbeNode(
                        id=current_id,
                        probe=current_probe,
                        purpose="comparison current window (aggregated over the time bucket)",
                    )
                )
                if node_contracts is not None and node.id in node_contracts:
                    # the twin measures exactly what its source did, so a
                    # playbook rank on ``impact_cents`` still binds to it
                    node_contracts[current_id] = node_contracts[node.id]
            prior_range = self._prior_range(
                current_probe.window, spec.context.window, comparison
            )
            prior_probe = replace(
                current_probe,
                window=TimeWindow(
                    basis=current_probe.window.basis,
                    range=prior_range,
                    requested=None,
                    calendar=current_probe.window.calendar,
                ),
            )
            if current_probe.limit is not None and self._readable_whole(current_probe):
                prior_probe = replace(prior_probe, limit=None, order_by=())
            prior_id = f"{current_id}{_PRIOR_SUFFIX}"
            nodes.append(ProbeNode(id=prior_id, probe=prior_probe, purpose="comparison baseline"))
            steps.append(
                TransformPlanStep(
                    id=f"{current_id}__compare",
                    operator="compare",
                    inputs=(current_id, prior_id),
                    args=(("kind", comparison.kind.value),),
                )
            )
        return steps

    def _readable_whole(self, probe: AggregationProbe) -> bool:
        """Can this cut be retrieved un-truncated without an unbounded probe?

        The catalog declares a ``cardinality_estimate`` per dimension; the
        product of the cut's estimates is how many cells the un-limited
        probe would return. An undeclared dimension is treated as
        unbounded, because a guess in the permissive direction is how an
        unbounded probe gets planned.
        """
        cells = 1
        for ref in probe.dimensions:
            declared = self._catalog.dimension(ref.id)
            if declared is None:
                return False
            cells *= max(1, declared.cardinality_estimate)
            if cells > UNTRUNCATED_PRIOR_CELL_CAP:
                return False
        return True

    @staticmethod
    def _prior_range(
        probe_window: TimeWindow, spec_window: TimeWindow, comparison: Comparison
    ) -> AbsoluteRange:
        if probe_window.range == spec_window.range or comparison.kind is ComparisonKind.CUSTOM:
            return comparison.window.range
        return derive_comparison(probe_window, comparison.kind).window.range

    # ----------------------------------------------------------- transforms

    def _playbook_transforms(
        self,
        playbook: PlaybookSpec,
        nodes: list[ProbeNode],
        node_contracts: dict[str, tuple[MetricContract, ...]],
        compare_steps: list[TransformPlanStep],
        spec: AnalysisSpec,
    ) -> tuple[list[TransformPlanStep], list[str]]:
        steps: list[TransformPlanStep] = list(compare_steps)
        notes: list[str] = []
        # latest logical frame per base node (updated as steps derive frames)
        latest: dict[str, str] = {
            node.id: node.id for node in nodes if not node.id.endswith(_PRIOR_SUFFIX)
        }
        compared: set[str] = set()
        for step in compare_steps:
            latest[step.inputs[0]] = step.id
            compared.add(step.inputs[0])

        def money_measure(node_id: str) -> str | None:
            for contract in node_contracts.get(node_id, ()):
                if contract.unit is MetricUnit.MONEY_CENTS:
                    return contract.id
            return None

        for requested in playbook.transforms:
            operator = requested.operator
            if operator == "compare":
                if not compare_steps:
                    notes.append(
                        "transform 'compare' skipped: the question carries no comparison window"
                    )
                continue
            if operator == "share_of_total":
                measure = requested.arg("measure")
                # `within` scopes the denominator to a group (share of THAT
                # payer's claims, not of the whole population). It is passed
                # through rather than dropped: a silently global share would
                # be a different, wrong number under the same column name.
                within = requested.arg("within")
                share_args: tuple[tuple[str, str], ...] = (("measure", measure or ""),)
                if within:
                    share_args = (*share_args, ("within", within))
                emitted = False
                for node_id, contracts in node_contracts.items():
                    if measure is not None and any(c.id == measure for c in contracts):
                        step_id = f"{latest[node_id]}__share"
                        steps.append(
                            TransformPlanStep(
                                id=step_id,
                                operator="share_of_total",
                                inputs=(latest[node_id],),
                                args=share_args,
                            )
                        )
                        latest[node_id] = step_id
                        emitted = True
                if not emitted:
                    notes.append(
                        f"transform 'share_of_total' skipped: measure {measure!r} is not "
                        "produced by any planned probe"
                    )
                continue
            if operator in ("rank", "top_k"):
                by = requested.arg("by")
                emitted = False
                for node_id in latest:
                    column, descending = self._rank_binding(
                        by, requested, node_id, node_contracts, node_id in compared, spec
                    )
                    if column is None:
                        continue
                    step_id = f"{latest[node_id]}__{operator}"
                    args: list[tuple[str, str]] = [
                        ("by", column),
                        ("descending", "true" if descending else "false"),
                    ]
                    if spec.direction is not None:
                        args.append(("direction", spec.direction.value))
                    if operator == "top_k":
                        args.append(("k", requested.arg("k") or "10"))
                    steps.append(
                        TransformPlanStep(
                            id=step_id,
                            operator=operator,
                            inputs=(latest[node_id],),
                            args=tuple(args),
                        )
                    )
                    latest[node_id] = step_id
                    emitted = True
                if not emitted:
                    notes.append(
                        f"transform {operator!r} skipped: ranking column {by!r} does not "
                        "resolve on any planned frame"
                    )
                continue
            if operator == "decompose":
                # needs volume and value measures in one frame; surfaced when
                # the playbook's measures live at different grains
                candidate = None
                for node_id, contracts in node_contracts.items():
                    units = {c.unit for c in contracts}
                    if MetricUnit.MONEY_CENTS in units and MetricUnit.COUNT in units:
                        candidate = node_id
                        break
                if candidate is None:
                    notes.append(
                        "transform 'decompose' skipped: volume and value measures are "
                        "retrieved by separate probes at different grains in this plan"
                    )
                continue
            if operator in ANSWERING_TRANSFORMS:
                # …and the ones whose absence changes WHAT the answer is.
                # Recording them as skipped enrichments at INFO produces
                # answers to different questions:
                #
                # * "Build me a payer scorecard for Pinnacle" runs six
                #   probes, gets direct-grade rows from every one, records
                #   `transform 'pivot' is not executable` and publishes ZERO
                #   findings beside four one-row charts. The pivot is what
                #   makes a scorecard a scorecard;
                # * "Will my cash increase next month?" records
                #   `project_lagged_realization is not executable` at INFO
                #   and hands over $6,355,211.10 of cash posted over the
                #   playbook's own trailing window, with the words
                #   "forecast" and "cannot" nowhere in the response.
                #
                # A skipped enrichment is a caveat; a skipped ANSWER is a
                # refusal, and it is raised here so no probe runs, no chart
                # is drawn and no figure for a different question is handed
                # to a reader who will quote it. The recovery path turns it
                # into the alternatives this pack really can answer.
                raise UnsupportedConceptError(
                    f"the {playbook.id!r} playbook answers by {operator!r}, and this engine "
                    f"cannot execute that transform — so there is no {playbook.id!r} answer "
                    "to give. The probes underneath it measure real things and none of them "
                    "is the shape you asked for.",
                    details={
                        "playbook": playbook.id,
                        "transform": operator,
                        "metric": None,
                    },
                )
            notes.append(
                f"transform {operator!r} is not executable on this milestone's engine; "
                "recorded and skipped"
            )
        return steps, notes

    def _rank_binding(
        self,
        by: str | None,
        requested: TransformStepSpec,
        node_id: str,
        node_contracts: dict[str, tuple[MetricContract, ...]],
        has_compare: bool,
        spec: AnalysisSpec,
    ) -> tuple[str | None, bool]:
        """Resolve a playbook ranking arg to a concrete frame column.

        ``impact_cents`` is the governed alias for the money measure's
        ``__delta`` on compare outputs. Its default order is **ascending**,
        so the most negative movement of a higher-is-good measure surfaces
        first — that default is the pack's judgement about an unprompted
        sweep, and it is exactly wrong for a question that named the other
        direction. "Which payers had the biggest increase in denials" ranked
        ascending returns the three biggest *decreases*, narrated as
        improvements. So an asserted direction wins over the default: a
        wanted delta sign
        of ``+1`` ranks descending, ``-1`` ascending, and ``smallest``
        flips whichever of those applies.
        """
        if by is None:
            return None, True
        descending = (requested.arg("descending") or "true").lower() != "false"
        if by == _IMPACT_ARG:
            money = next(
                (
                    contract
                    for contract in node_contracts.get(node_id, ())
                    if contract.unit is MetricUnit.MONEY_CENTS
                ),
                None,
            )
            if money is None:
                return None, True
            asked_order = descending_for_order(spec.order, money.sign)
            if not has_compare:
                # "Rank by impact" with nothing to compare against means
                # rank by SIZE — the dollars standing there, not a movement.
                # Returning None instead drops the rank step entirely, and
                # with it every finding the frame could produce: the daily
                # portfolio plans nine probes, retrieves ~100 ranked rows of
                # payers and plans, emits no rank step because its
                # ``compare`` has no comparison window to work from, and
                # publishes two ungrouped scalars as the whole answer to
                # "what should I work first today".
                return money.id, asked_order if asked_order is not None else True
            wanted = wanted_delta_sign(spec.direction, money.sign)
            if wanted is None:
                return f"{money.id}__delta", False
            biggest_first = spec.magnitude is not AskedMagnitude.SMALLEST
            return f"{money.id}__delta", (wanted > 0) == biggest_first
        ranked = next((c for c in node_contracts.get(node_id, ()) if c.id == by), None)
        if ranked is not None:
            asked_order = descending_for_order(spec.order, ranked.sign)
            return by, descending if asked_order is None else asked_order
        return None, True


class DiffPlanService:
    """Probe-hash diff between two plans (the refinement path re-executes
    only ``added``; ``unchanged`` is served from the evidence cache)."""

    def diff(self, old_plan: InvestigationPlan, new_plan: InvestigationPlan) -> PlanDiff:
        old_by_hash = {node.hash: node for node in old_plan.nodes}
        new_by_hash = {node.hash: node for node in new_plan.nodes}
        added = tuple(node for digest, node in new_by_hash.items() if digest not in old_by_hash)
        removed = tuple(node for digest, node in old_by_hash.items() if digest not in new_by_hash)
        unchanged = tuple(node for digest, node in new_by_hash.items() if digest in old_by_hash)
        return PlanDiff(added=added, removed=removed, unchanged=unchanged)
