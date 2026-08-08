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
class PlaybookSpec:
    id: str
    description: str
    probes: tuple[ProbeTemplateSpec, ...]
    transforms: tuple[TransformStepSpec, ...] = ()
    conclusion_policies: tuple[str, ...] = ()
    ranking_policy: str | None = None


@dataclass(frozen=True, slots=True)
class TermDefinition:
    """One governed definitional match (concept, code, or metric)."""

    term: str
    kind: str  # "concept" | "code:carc" | "code:rarc" | "code:group_code" | "metric"
    title: str
    definition: str
    source: str | None = None


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

    def conclusion_policy(self, policy_id: str) -> ConclusionPolicySpec | None: ...


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
    ) -> EvidenceFrame: ...

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
