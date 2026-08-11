"""Narrow application-side seams onto other capabilities' content.

``revi_investigation`` may import the kernel and the *contracts* packages
only (the import-linter independence contract keeps capability
implementations apart). Services still need pack lookups and kernel
transform operators, whose implementations live in ``revi_pack`` and
``revi_calculation`` — so this module names exactly what the application
consumes, as Protocols over kernel/contract types and small neutral
dataclasses. Adapters bridge the real implementations: the API app wires
them for production, ``revi_testing.engine_wiring`` wires them for tests.

The semantic catalog needs no seam: :class:`CatalogSnapshot` itself lives
in ``revi_catalog_contracts`` (a legal dependency), so services consume it
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from revi_calculation_contracts.contract import MetricContract
from revi_kernel.frame import EvidenceFrame
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef
from revi_kernel.scope import RelativeRange

# ---------------------------------------------------------------------------
# pack content, seen through investigation-neutral shapes


@dataclass(frozen=True, slots=True)
class ProbeTemplateSpec:
    """A playbook probe template (mirror of the pack's ProbeTemplate)."""

    id: str
    metric_ids: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    window: RelativeRange | None = None
    basis_override: str | None = None
    top_n: int | None = None
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class TransformStepSpec:
    """One playbook transform request; ``args`` are (name, value) pairs."""

    operator: str
    args: tuple[tuple[str, str], ...] = ()

    def arg(self, name: str) -> str | None:
        for key, value in self.args:
            if key == name:
                return value
        return None


@dataclass(frozen=True, slots=True)
class ScorecardVerdictSpec:
    """The pack's rule for when a scorecard may name one value the leader.

    Carried through the port because the findings stage decides the verdict
    and may not import the pack. ``measures`` empty means every panel
    measure whose contract declares an improvement direction.
    """

    leader_min_measures: int
    measures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlaybookSpec:
    id: str
    description: str
    probes: tuple[ProbeTemplateSpec, ...]
    transforms: tuple[TransformStepSpec, ...] = ()
    conclusion_policies: tuple[str, ...] = ()
    ranking_policy: str | None = None
    #: Present exactly when this playbook answers by ``panel``.
    verdict: ScorecardVerdictSpec | None = None
    #: How the pack author says an analyst asks for this playbook. Carried
    #: through the port so the OFFER-TIME option validator
    #: can recognise a button that routes to a playbook this engine refuses
    #: at plan time, in the pack's vocabulary rather than in one kept beside
    #: it. Empty is legitimate: a playbook reached only by interpretation.
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TermDefinition:
    """One governed definitional match (concept, code, or metric)."""

    term: str
    kind: str  # "concept" | "code:carc" | "code:rarc" | "code:group_code" | "metric"
    title: str
    definition: str
    source: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    """A governed external benchmark **range** for a metric.

    Ranges, never point targets, and never separable from their context:
    ``cohort_label``, ``cautions`` and ``review_status`` travel with the
    numbers because a benchmark quoted without its cohort is a different
    claim from the one the source made. ``review_status`` is
    ``machine_researched`` for everything in KB wave 1 — nothing here is
    certified, and a consumer that hides that is asserting more than the
    pack does.
    """

    id: str
    metric_id: str
    cohort_label: str
    value_low: str
    value_high: str
    unit: str
    period: str
    authority: str
    review_status: str
    cautions: tuple[str, ...] = ()
    source_titles: tuple[str, ...] = ()

    @property
    def range_text(self) -> str:
        span = (
            self.value_low
            if self.value_low == self.value_high
            else f"{self.value_low}-{self.value_high}"
        )
        return f"{span} {self.unit}"

    @property
    def prompt_line(self) -> str:
        """One line for the narrative prompt — range, cohort, provenance."""
        return (
            f"{self.metric_id}: {self.range_text} ({self.cohort_label}; {self.period}; "
            f"{self.authority}; {self.review_status})"
        )


@dataclass(frozen=True, slots=True)
class ConclusionPolicySpec:
    id: str
    required_grade: EvidenceGrade
    estimate_label_required: bool = False


class PackPort(Protocol):
    """What the investigation engine reads from the pinned pack snapshot."""

    @property
    def snapshot_id(self) -> str: ...

    @property
    def pack_id(self) -> str: ...

    @property
    def pack_version(self) -> str: ...

    def metric(self, metric_id: str) -> MetricContract | None: ...

    def metric_summaries(self) -> tuple[tuple[str, str], ...]:
        """(id, description) pairs — prompt vocabulary, never data."""
        ...

    def playbook(self, playbook_id: str) -> PlaybookSpec | None: ...

    def playbook_summaries(self) -> tuple[tuple[str, str], ...]: ...

    def concept_summaries(self) -> tuple[tuple[str, str], ...]:
        """(id, name) pairs for the interpretation vocabulary."""
        ...

    def has_concept(self, concept_id: str) -> bool: ...

    def concept_for_alias(self, text: str) -> str | None:
        """Concept id for analyst language, via normalized alias lookup."""
        ...

    def resolve_term(self, text: str) -> tuple[TermDefinition, ...]:
        """Generic definitional lookup (the DEFINITIONAL turn path)."""
        ...

    def code_title(self, system: str, code: str) -> str | None:
        """Governed title for a remittance code (``"carc"``/``"group_code"``/
        ``"rarc"``), or ``None`` when the pack does not define it.

        Published rows have to name codes the way the domain does — a bare
        ``16`` is not a denial, ``CO / 16`` with its title is. The
        definitional path already returns these titles; this is the same
        content reached from the rendering path without dragging the whole
        ``TermDefinition`` shape through it.
        """
        ...

    def benchmarks_for_metric(self, metric_id: str) -> tuple[BenchmarkSpec, ...]:
        """Governed external benchmark ranges for a metric, or ``()``.

        19 sourced, cohort-labelled, caution-annotated figures were
        authored and reached no user: ``assembly.py`` passed
        ``benchmarks=()`` as a literal, and this port had no way to ask for
        them even if it had wanted to.
        """
        ...

    def conclusion_policy(self, policy_id: str) -> ConclusionPolicySpec | None: ...

    def binding_strength(self, concept_id: str, field_id: str) -> EvidenceGrade | None:
        """Declared evidence strength of ``field_id`` **as evidence for**
        ``concept_id`` (design §5.5), or ``None`` when the pack declares no
        binding between them.

        The same field is not equally good evidence for every concept: a
        CARC is the direct representation of a *denial* and only a proxy for
        *coordination of benefits*, because a reason code is a payer's
        assertion about coverage, not the coverage itself. Grading therefore
        has to be asked per concept — a field-only lookup would either
        downgrade honest denial analysis or launder COB guesswork.
        """
        ...


# ---------------------------------------------------------------------------
# versioned kernel transforms (implemented by revi_calculation)


@dataclass(frozen=True, slots=True)
class ReconcileVerdict:
    """A neutral reconciliation outcome (detail types stay in the kernel)."""

    passed: bool
    summary: str


class TransformPort(Protocol):
    """The closed transform-operator set the engine may apply (design §6.5)."""

    def ratio(
        self,
        frame: EvidenceFrame,
        *,
        numerator: str,
        denominator: str,
        out: str,
        out_ref: MetricRef,
        contract_version: int | None = None,
        unit: str | None = None,
    ) -> EvidenceFrame:
        """Fold a ratio contract's components into its metric column.

        ``unit`` is the metric contract's DECLARED unit. It is optional
        because the implementation resolves the same contract ``out_ref``
        names and may supply it itself; what is not optional is that the
        output column carries the declaration rather than the shape of the
        arithmetic — without it, days in A/R publishes as "15,941.2%".
        """
        ...

    def compare(
        self,
        current: EvidenceFrame,
        prior: EvidenceFrame,
        *,
        join_on: tuple[str, ...] | None = None,
        measures: tuple[str, ...] | None = None,
    ) -> EvidenceFrame: ...

    def share_of_total(
        self, frame: EvidenceFrame, *, measure: str, within: tuple[str, ...] = ()
    ) -> EvidenceFrame: ...

    def top_k(
        self, frame: EvidenceFrame, *, by: str, k: int, per_group: tuple[str, ...] | None = None
    ) -> EvidenceFrame: ...

    def rank(self, frame: EvidenceFrame, *, by: str, descending: bool = True) -> EvidenceFrame: ...

    def pivot(
        self, frame: EvidenceFrame, *, index: tuple[str, ...], column: str, measure: str
    ) -> EvidenceFrame: ...

    def panel(
        self,
        *frames: EvidenceFrame,
        entity: str,
        better_high: tuple[str, ...] = (),
        better_low: tuple[str, ...] = (),
    ) -> EvidenceFrame:
        """One row per entity, one column per governed measure, one
        ordering per measure in the direction its contract declares.

        ``better_high``/``better_low`` are the improvement direction read
        off the metric contracts by the caller. A measure in neither is
        published without an ordering — "neutral" is a real answer, and a
        rank over charges would assert that billing more is better.
        """
        ...

    def decompose(
        self,
        current: EvidenceFrame,
        prior: EvidenceFrame,
        *,
        volume: str,
        value: str,
        cells: tuple[str, ...] | None = None,
    ) -> EvidenceFrame: ...

    def reconcile(
        self,
        parent: EvidenceFrame,
        children: EvidenceFrame,
        *,
        measures: tuple[str, ...],
        suppression_allowance: Decimal = Decimal(0),
    ) -> ReconcileVerdict: ...

    def project_lagged_realization(
        self,
        inventory: EvidenceFrame,
        curves: EvidenceFrame,
        inflow: EvidenceFrame,
        baseline: EvidenceFrame,
        *,
        horizon_weeks: int,
        coverage_min: Decimal = Decimal("0.8"),
    ) -> EvidenceFrame: ...
