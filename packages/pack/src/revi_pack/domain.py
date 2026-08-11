"""Pack-registry domain model (design §5, §9.1, §9.3).

Everything here is a frozen dataclass or enum — typed data, no behavior
beyond validation and lookup. Packs configure *which* concepts, metrics,
playbooks, and policies exist; they never define new arithmetic (the
transform algebra is versioned platform code in the calculation kernel).

Knowledge/definitional content (concept definitions, CARC/RARC/group-code
paraphrases, sources) lives here too: the DEFINITIONAL turn path answers
"what is PR3" from governed pack content with zero probes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Union

from revi_calculation_contracts.contract import MetricContract
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DateBasisRef, ReferentId
from revi_kernel.scope import RelativeRange
from revi_pack.errors import PackIntegrityError

# ---------------------------------------------------------------------------
# term normalization (alias + definitional lookup)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CODE_COMBO = re.compile(r"([a-z]+)_?([0-9]+)")


def normalize_term(text: str) -> str:
    """Normalize analyst language for lookup: lowercase, non-alphanumeric
    runs collapse to a single underscore, leading/trailing stripped.

    ``"Coordination-of-Benefits"`` → ``"coordination_of_benefits"``,
    ``"PR-3"`` and ``"pr 3"`` → ``"pr_3"``.
    """
    return _NON_ALNUM.sub("_", text.lower()).strip("_")


# ---------------------------------------------------------------------------
# knowledge artifacts


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Provenance for governed knowledge (design §9.1: authoritative or
    licensed reference materials, approved policies and SOPs).

    The optional dates carry the source's validity window
    (``effective_from``/``effective_to``) and when it was last checked
    against the publisher (``last_verified_at``).
    """

    id: str
    title: str
    publisher: str
    url: str | None
    authority: str
    effective_from: date | None = None
    effective_to: date | None = None
    last_verified_at: date | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SourceRef.id must be non-empty")
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from > self.effective_to
        ):
            raise ValueError(f"SourceRef {self.id!r}: effective_from is after effective_to")


@dataclass(frozen=True, slots=True)
class Concept:
    """A governed domain concept (COB, denial, clean claim, …).

    ``definition`` is the plain-language governed definition served by the
    DEFINITIONAL turn path; ``aliases`` capture analyst and organization
    terminology (matched after :func:`normalize_term`).
    """

    id: str
    name: str
    description: str
    definition: str
    aliases: tuple[str, ...]
    sources: tuple[SourceRef, ...] = ()
    related: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Concept.id must be non-empty")
        if not self.name:
            raise ValueError(f"Concept {self.id!r}: name must be non-empty")

    @property
    def lookup_terms(self) -> frozenset[str]:
        """All normalized terms that resolve to this concept (id, name, aliases)."""
        terms = {normalize_term(self.id), normalize_term(self.name)}
        terms.update(normalize_term(a) for a in self.aliases)
        terms.discard("")
        return frozenset(terms)


class CodeSystem(StrEnum):
    CARC = "carc"
    RARC = "rarc"
    GROUP_CODE = "group_code"


@dataclass(frozen=True, slots=True)
class CodeDefinition:
    """A governed remittance-code definition for "what is PR3"-style lookups
    (group code PR + CARC 3)."""

    code_system: CodeSystem
    code: str
    title: str
    definition_paraphrase: str
    category: str | None = None
    sources: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("CodeDefinition.code must be non-empty")


class ReviewStatus(StrEnum):
    """Provenance tier for machine-researched knowledge: content enters as
    MACHINE_RESEARCHED and is promoted to HUMAN_APPROVED on review (§9.1)."""

    MACHINE_RESEARCHED = "machine_researched"
    HUMAN_APPROVED = "human_approved"


@dataclass(frozen=True, slots=True)
class KnowledgeCard:
    """A governed narrative knowledge card.

    Where a :class:`Concept` carries the one-paragraph governed definition, a
    card *elaborates*: key points, cautions, and dated provenance in
    ``authored_by``. A card may share aliases with a concept — a card
    elaborates a concept — but never with another card (snapshot integrity).
    """

    id: str
    title: str
    domains: tuple[str, ...]
    aliases: tuple[str, ...]
    summary: str
    key_points: tuple[str, ...]
    cautions: tuple[str, ...]
    authored_by: str
    review_status: ReviewStatus
    sources: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("KnowledgeCard.id must be non-empty")
        if not self.title:
            raise ValueError(f"KnowledgeCard {self.id!r}: title must be non-empty")
        if not self.summary:
            raise ValueError(f"KnowledgeCard {self.id!r}: summary must be non-empty")
        if not self.authored_by:
            raise ValueError(f"KnowledgeCard {self.id!r}: authored_by must be non-empty")

    @property
    def lookup_terms(self) -> frozenset[str]:
        """All normalized terms that resolve to this card (id, title, aliases)."""
        terms = {normalize_term(self.id), normalize_term(self.title)}
        terms.update(normalize_term(a) for a in self.aliases)
        terms.discard("")
        return frozenset(terms)


@dataclass(frozen=True, slots=True)
class BenchmarkFigure:
    """A governed external benchmark *range* for a pack metric — ranges, not
    point targets. ``metric_id`` must resolve to a pack metric (snapshot
    integrity); consumers must surface ``cohort_label`` and ``cautions``
    alongside the figures."""

    id: str
    metric_id: str
    cohort_label: str
    value_low: str
    value_high: str
    unit: str
    period: str
    authority: str
    review_status: ReviewStatus
    sources: tuple[SourceRef, ...] = ()
    cautions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("BenchmarkFigure.id must be non-empty")
        if not self.metric_id:
            raise ValueError(f"BenchmarkFigure {self.id!r}: metric_id must be non-empty")
        if not self.cohort_label:
            raise ValueError(f"BenchmarkFigure {self.id!r}: cohort_label must be non-empty")
        if not self.value_low or not self.value_high:
            raise ValueError(
                f"BenchmarkFigure {self.id!r}: value_low and value_high must both be non-empty "
                "(benchmarks are ranges, not point targets)"
            )


# ---------------------------------------------------------------------------
# bindings (design §5.5)


class BindingState(StrEnum):
    PROPOSED = "proposed"
    OBSERVED = "observed"
    VALIDATED = "validated"
    CERTIFIED = "certified"
    DEPRECATED = "deprecated"


@dataclass(frozen=True, slots=True)
class BindingCandidate:
    """Maps a domain concept to available data, with declared evidence
    strength; the grade law propagates the strength downstream."""

    concept_id: str
    dimension_or_measure_id: str
    state: BindingState
    strength: EvidenceGrade
    rationale: str

    def __post_init__(self) -> None:
        if not self.concept_id:
            raise ValueError("BindingCandidate.concept_id must be non-empty")
        if not self.dimension_or_measure_id:
            raise ValueError("BindingCandidate.dimension_or_measure_id must be non-empty")


# ---------------------------------------------------------------------------
# policies


@dataclass(frozen=True, slots=True)
class FilingRule:
    """A payer/plan timely-filing rule. ``requires_confirmation`` marks rules
    that must be confirmed against the payer contract before being asserted."""

    id: str
    payer_pattern: str
    plan_pattern: str | None
    filing_limit_days: int
    date_basis: DateBasisRef
    authority: str
    requires_confirmation: bool

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("FilingRule.id must be non-empty")
        if self.filing_limit_days <= 0:
            raise ValueError(f"FilingRule {self.id!r}: filing_limit_days must be positive")


@dataclass(frozen=True, slots=True)
class DetectorPolicy:
    """An anomaly/comparison detector. Overlays may tune ``threshold`` only
    within the declared ``[threshold_min, threshold_max]`` range (§5.4)."""

    id: str
    description: str
    threshold: Decimal
    threshold_min: Decimal
    threshold_max: Decimal

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DetectorPolicy.id must be non-empty")
        if self.threshold_min > self.threshold_max:
            raise ValueError(f"DetectorPolicy {self.id!r}: threshold_min exceeds threshold_max")
        if not (self.threshold_min <= self.threshold <= self.threshold_max):
            raise ValueError(
                f"DetectorPolicy {self.id!r}: threshold {self.threshold} outside "
                f"[{self.threshold_min}, {self.threshold_max}]"
            )


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    """How findings are prioritized. ``weights`` is an ordered tuple of
    ``(criterion_id, weight)`` pairs — mapping-free, hashable, canonical."""

    id: str
    weights: tuple[tuple[str, Decimal], ...]
    description: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("RankingPolicy.id must be non-empty")
        keys = [k for k, _ in self.weights]
        if len(keys) != len(set(keys)):
            raise ValueError(f"RankingPolicy {self.id!r}: duplicate weight criteria")


@dataclass(frozen=True, slots=True)
class ConclusionPolicy:
    """Evidence required before a specific claim may be made (design §5.1);
    ``estimate_label_required`` forces estimates to be labeled as estimates."""

    id: str
    claim: str
    required_grade: EvidenceGrade
    required_evidence: tuple[str, ...]
    estimate_label_required: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ConclusionPolicy.id must be non-empty")


@dataclass(frozen=True, slots=True)
class PresentationRecipe:
    """Chart/explanation preference for a metric or finding family."""

    id: str
    applies_to: str
    chart_type: str
    notes: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("PresentationRecipe.id must be non-empty")


# ---------------------------------------------------------------------------
# playbooks — typed data; execution comes later


@dataclass(frozen=True, slots=True)
class ProbeTemplate:
    """A parameterized probe within a playbook. Windows stay relative here;
    they resolve to concrete dates exactly once, at plan time."""

    id: str
    metric_ids: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    window: RelativeRange | None = None
    basis_override: str | None = None
    top_n: int | None = None
    scope_note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ProbeTemplate.id must be non-empty")
        if not self.metric_ids:
            raise ValueError(f"ProbeTemplate {self.id!r}: metric_ids must be non-empty")
        if self.top_n is not None and self.top_n <= 0:
            raise ValueError(f"ProbeTemplate {self.id!r}: top_n must be positive when set")


@dataclass(frozen=True, slots=True)
class TransformStep:
    """One transform-operator application; ``args`` is an ordered tuple of
    ``(name, value)`` pairs interpreted by the calculation kernel."""

    operator: str
    args: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.operator:
            raise ValueError("TransformStep.operator must be non-empty")


@dataclass(frozen=True, slots=True)
class ScorecardVerdict:
    """When a scorecard is allowed to name one entity the best.

    A scorecard measures N governed things. There is no overall score,
    because averaging a denial rate against posted cash requires weights
    nobody in the pack has authored and the reader cannot inspect — an
    invented composite is exactly the number this platform refuses to
    publish. What CAN be said honestly is an arithmetic fact about the
    orderings themselves: *this payer is first on M of the N measures we
    could rank*.

    ``leader_min_measures`` is the M that makes that fact a verdict. It is
    pack content because it is a judgement about the domain, not about the
    engine: a contracting conversation rests on a different majority than a
    facility review does. Below it the honest answer is that no one leads,
    stated with the per-measure leaders — a first-class answer, not a
    failure to reach one.
    """

    #: How many of the rankable measures one entity must lead before the
    #: scorecard will call it the leader. At least one, or the "verdict"
    #: is a coin toss dressed as a finding.
    leader_min_measures: int
    #: The measures the verdict is taken over, when the pack wants a
    #: subset. Empty means every panel measure whose contract declares an
    #: improvement direction.
    measures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.leader_min_measures < 1:
            raise ValueError("ScorecardVerdict.leader_min_measures must be at least 1")


@dataclass(frozen=True, slots=True)
class Playbook:
    """A parameterized investigation DAG template (design §5.1)."""

    id: str
    description: str
    triggers: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    probes: tuple[ProbeTemplate, ...] = ()
    transforms: tuple[TransformStep, ...] = ()
    conclusion_policies: tuple[str, ...] = ()
    ranking_policy: str | None = None
    #: The decision rule for a scorecard's verdict sentence. Required by
    #: the ``panel`` transform and meaningless without it.
    verdict: ScorecardVerdict | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Playbook.id must be non-empty")
        answers_by_panel = any(step.operator == "panel" for step in self.transforms)
        if answers_by_panel and self.verdict is None:
            raise ValueError(
                f"Playbook {self.id!r}: a playbook that answers by 'panel' must author a "
                "'verdict' policy — how many measures one value has to lead before this "
                "scorecard names a leader is a judgement about the domain, and there is no "
                "engine default that would not be inventing one"
            )


# ---------------------------------------------------------------------------
# layers


class PackLayerKind(StrEnum):
    BASE = "base"
    ORGANIZATION = "organization"
    TENANT = "tenant"


@dataclass(frozen=True, slots=True)
class PackLayerRef:
    """Identity of one composed layer: kind, name, and content hash."""

    kind: PackLayerKind
    name: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class PackVersion:
    pack_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.pack_id:
            raise ValueError("PackVersion.pack_id must be non-empty")
        if not self.version:
            raise ValueError("PackVersion.version must be non-empty")


@dataclass(frozen=True, slots=True)
class AliasOverride:
    """An overlay's alias patch on an existing concept — the only concept
    mutation an overlay may perform (design §5.4)."""

    concept_id: str
    add_aliases: tuple[str, ...] = ()
    replace_aliases: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.concept_id:
            raise ValueError("AliasOverride.concept_id must be non-empty")
        if not self.add_aliases and self.replace_aliases is None:
            raise ValueError(
                f"AliasOverride {self.concept_id!r}: provide add_aliases and/or replace_aliases"
            )


@dataclass(frozen=True, slots=True)
class DetectorOverride:
    """An overlay's threshold tune on an existing detector policy; legality
    against the declared [min, max] range is checked at merge time."""

    id: str
    threshold: Decimal

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DetectorOverride.id must be non-empty")


@dataclass(frozen=True, slots=True)
class PackLayer:
    """One loaded pack layer (base, organization, or tenant), pre-merge.

    Overlay-only patch artifacts (``alias_overrides``, ``detector_overrides``)
    must be empty on BASE layers; the loader and merge both enforce this.
    """

    kind: PackLayerKind
    name: str
    version: str
    description: str = ""
    concepts: tuple[Concept, ...] = ()
    alias_overrides: tuple[AliasOverride, ...] = ()
    code_definitions: tuple[CodeDefinition, ...] = ()
    knowledge_cards: tuple[KnowledgeCard, ...] = ()
    benchmarks: tuple[BenchmarkFigure, ...] = ()
    metric_contracts: tuple[MetricContract, ...] = ()
    bindings: tuple[BindingCandidate, ...] = ()
    playbooks: tuple[Playbook, ...] = ()
    conclusion_policies: tuple[ConclusionPolicy, ...] = ()
    ranking_policies: tuple[RankingPolicy, ...] = ()
    detector_policies: tuple[DetectorPolicy, ...] = ()
    detector_overrides: tuple[DetectorOverride, ...] = ()
    presentation_recipes: tuple[PresentationRecipe, ...] = ()
    filing_rules: tuple[FilingRule, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("PackLayer.name must be non-empty")
        if not self.version:
            raise ValueError(f"PackLayer {self.name!r}: version must be non-empty")


# ---------------------------------------------------------------------------
# snapshot


TermMatch = Union[Concept, KnowledgeCard, CodeDefinition, MetricContract]  # noqa: UP007 - union spelled out for clarity


@dataclass(frozen=True)
class PackSnapshot:
    """The immutable composed pack (design §5). ``id`` is a content hash over
    the composed layers (:mod:`revi_pack.snapshot` computes it).

    Frozen *without* ``slots`` on purpose: lookup indexes are built once in
    ``__post_init__`` (via ``object.__setattr__``) into non-init,
    non-compare fields, so equality and hashing stay content-based while
    lookups are O(1). Integrity invariants — unique ids, no alias owned by
    two concepts, resolvable playbook references — are enforced here, so an
    invalid snapshot is unrepresentable no matter who constructs it.
    """

    id: str
    version: PackVersion
    layers: tuple[PackLayerRef, ...]
    concepts: tuple[Concept, ...] = ()
    code_definitions: tuple[CodeDefinition, ...] = ()
    metric_contracts: tuple[MetricContract, ...] = ()
    bindings: tuple[BindingCandidate, ...] = ()
    playbooks: tuple[Playbook, ...] = ()
    conclusion_policies: tuple[ConclusionPolicy, ...] = ()
    ranking_policies: tuple[RankingPolicy, ...] = ()
    detector_policies: tuple[DetectorPolicy, ...] = ()
    presentation_recipes: tuple[PresentationRecipe, ...] = ()
    filing_rules: tuple[FilingRule, ...] = ()
    knowledge_cards: tuple[KnowledgeCard, ...] = ()
    benchmarks: tuple[BenchmarkFigure, ...] = ()

    _concepts_by_id: dict[str, Concept] = field(init=False, repr=False, compare=False)
    _metrics_by_id: dict[str, MetricContract] = field(init=False, repr=False, compare=False)
    _codes_by_key: dict[tuple[CodeSystem, str], CodeDefinition] = field(
        init=False, repr=False, compare=False
    )
    _alias_index: dict[str, Concept] = field(init=False, repr=False, compare=False)
    _code_term_index: dict[str, tuple[CodeDefinition, ...]] = field(
        init=False, repr=False, compare=False
    )
    _metric_term_index: dict[str, MetricContract] = field(init=False, repr=False, compare=False)
    _cards_by_id: dict[str, KnowledgeCard] = field(init=False, repr=False, compare=False)
    _card_alias_index: dict[str, KnowledgeCard] = field(init=False, repr=False, compare=False)
    _benchmarks_by_metric: dict[str, tuple[BenchmarkFigure, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("PackSnapshot.id must be non-empty")
        object.__setattr__(self, "_concepts_by_id", self._build_concept_index())
        object.__setattr__(self, "_metrics_by_id", self._build_metric_index())
        object.__setattr__(self, "_codes_by_key", self._build_code_index())
        object.__setattr__(self, "_alias_index", self._build_alias_index())
        object.__setattr__(self, "_code_term_index", self._build_code_term_index())
        object.__setattr__(
            self, "_metric_term_index", {normalize_term(m.id): m for m in self.metric_contracts}
        )
        object.__setattr__(self, "_cards_by_id", self._build_card_index())
        object.__setattr__(self, "_card_alias_index", self._build_card_alias_index())
        object.__setattr__(self, "_benchmarks_by_metric", self._build_benchmark_index())
        self._check_unique_ids()
        self._check_playbook_references()
        self._check_benchmark_references()

    # -- index construction ------------------------------------------------

    def _build_concept_index(self) -> dict[str, Concept]:
        index: dict[str, Concept] = {}
        for concept in self.concepts:
            if concept.id in index:
                raise PackIntegrityError(f"duplicate concept id {concept.id!r} in snapshot")
            index[concept.id] = concept
        return index

    def _build_metric_index(self) -> dict[str, MetricContract]:
        index: dict[str, MetricContract] = {}
        for contract in self.metric_contracts:
            if contract.id in index:
                raise PackIntegrityError(f"duplicate metric id {contract.id!r} in snapshot")
            index[contract.id] = contract
        return index

    def _build_code_index(self) -> dict[tuple[CodeSystem, str], CodeDefinition]:
        index: dict[tuple[CodeSystem, str], CodeDefinition] = {}
        for code_def in self.code_definitions:
            key = (code_def.code_system, normalize_term(code_def.code))
            if key in index:
                raise PackIntegrityError(
                    f"duplicate code {code_def.code!r} in system {code_def.code_system.value!r}"
                )
            index[key] = code_def
        return index

    def _build_alias_index(self) -> dict[str, Concept]:
        index: dict[str, Concept] = {}
        for concept in self.concepts:
            for term in sorted(concept.lookup_terms):
                owner = index.get(term)
                if owner is not None and owner.id != concept.id:
                    raise PackIntegrityError(
                        f"alias {term!r} is owned by two concepts: {owner.id!r} and {concept.id!r}"
                    )
                index[term] = concept
        return index

    def _build_card_index(self) -> dict[str, KnowledgeCard]:
        index: dict[str, KnowledgeCard] = {}
        for card in self.knowledge_cards:
            if card.id in index:
                raise PackIntegrityError(f"duplicate knowledge card id {card.id!r} in snapshot")
            index[card.id] = card
        return index

    def _build_card_alias_index(self) -> dict[str, KnowledgeCard]:
        # Alias uniqueness holds among cards only: a card may share an alias
        # with a concept (a card elaborates a concept), never with another card.
        index: dict[str, KnowledgeCard] = {}
        for card in self.knowledge_cards:
            for term in sorted(card.lookup_terms):
                owner = index.get(term)
                if owner is not None and owner.id != card.id:
                    raise PackIntegrityError(
                        f"alias {term!r} is owned by two knowledge cards: "
                        f"{owner.id!r} and {card.id!r}"
                    )
                index[term] = card
        return index

    def _build_benchmark_index(self) -> dict[str, tuple[BenchmarkFigure, ...]]:
        index: dict[str, tuple[BenchmarkFigure, ...]] = {}
        for benchmark in self.benchmarks:
            index[benchmark.metric_id] = (*index.get(benchmark.metric_id, ()), benchmark)
        return index

    def _build_code_term_index(self) -> dict[str, tuple[CodeDefinition, ...]]:
        index: dict[str, tuple[CodeDefinition, ...]] = {}
        for code_def in self.code_definitions:
            terms = {
                normalize_term(code_def.code),
                normalize_term(f"{code_def.code_system.value} {code_def.code}"),
            }
            terms.discard("")
            for term in sorted(terms):
                existing = index.get(term, ())
                if code_def not in existing:
                    index[term] = (*existing, code_def)
        return index

    # -- integrity checks --------------------------------------------------

    def _check_unique_ids(self) -> None:
        for label, ids in (
            ("playbook", [p.id for p in self.playbooks]),
            ("conclusion policy", [p.id for p in self.conclusion_policies]),
            ("ranking policy", [p.id for p in self.ranking_policies]),
            ("detector policy", [p.id for p in self.detector_policies]),
            ("presentation recipe", [p.id for p in self.presentation_recipes]),
            ("filing rule", [p.id for p in self.filing_rules]),
            ("benchmark", [b.id for b in self.benchmarks]),
        ):
            seen: set[str] = set()
            for artifact_id in ids:
                if artifact_id in seen:
                    raise PackIntegrityError(f"duplicate {label} id {artifact_id!r} in snapshot")
                seen.add(artifact_id)

    def _check_playbook_references(self) -> None:
        conclusion_ids = {p.id for p in self.conclusion_policies}
        ranking_ids = {p.id for p in self.ranking_policies}
        for playbook in self.playbooks:
            for probe in playbook.probes:
                for metric_id in probe.metric_ids:
                    if metric_id not in self._metrics_by_id:
                        raise PackIntegrityError(
                            f"playbook {playbook.id!r} probe {probe.id!r} references "
                            f"unknown metric {metric_id!r}"
                        )
            for policy_id in playbook.conclusion_policies:
                if policy_id not in conclusion_ids:
                    raise PackIntegrityError(
                        f"playbook {playbook.id!r} references unknown conclusion policy {policy_id!r}"
                    )
            if playbook.ranking_policy is not None and playbook.ranking_policy not in ranking_ids:
                raise PackIntegrityError(
                    f"playbook {playbook.id!r} references unknown ranking policy "
                    f"{playbook.ranking_policy!r}"
                )

    def _check_benchmark_references(self) -> None:
        for benchmark in self.benchmarks:
            if benchmark.metric_id not in self._metrics_by_id:
                raise PackIntegrityError(
                    f"benchmark {benchmark.id!r} references unknown metric "
                    f"{benchmark.metric_id!r}"
                )

    # -- lookups -----------------------------------------------------------

    def metric(self, metric_id: str) -> MetricContract | None:
        return self._metrics_by_id.get(metric_id)

    def concept(self, concept_id: str) -> Concept | None:
        return self._concepts_by_id.get(concept_id)

    def card(self, card_id: str) -> KnowledgeCard | None:
        return self._cards_by_id.get(card_id)

    def benchmarks_for_metric(self, metric_id: str) -> tuple[BenchmarkFigure, ...]:
        return self._benchmarks_by_metric.get(metric_id, ())

    def concept_for_alias(self, text: str) -> Concept | None:
        """Resolve analyst language to a concept via normalized alias lookup."""
        return self._alias_index.get(normalize_term(text))

    def code(self, system: CodeSystem, code: str) -> CodeDefinition | None:
        return self._codes_by_key.get((system, normalize_term(code)))

    def resolve_term(self, text: str) -> tuple[TermMatch, ...]:
        """Generic definitional lookup for the DEFINITIONAL turn path.

        Tries, in order: concept aliases, knowledge-card aliases (a card
        elaborating the same term follows its concept), code systems (exact
        codes, then a GROUP_CODE + CARC combination — "pr3"/"PR-3"/"pr 3"
        returns both the PR group code and CARC 3), and metric ids. Returns
        every match, deduplicated, in that order.
        """
        norm = normalize_term(text)
        if not norm:
            return ()
        matches: list[TermMatch] = []
        concept = self._alias_index.get(norm)
        if concept is not None:
            matches.append(concept)
        card = self._card_alias_index.get(norm)
        if card is not None:
            matches.append(card)
        matches.extend(self._code_term_index.get(norm, ()))
        combo = _CODE_COMBO.fullmatch(norm)
        if combo is not None:
            group = self.code(CodeSystem.GROUP_CODE, combo.group(1))
            carc = self.code(CodeSystem.CARC, combo.group(2))
            if group is not None and carc is not None:
                matches.extend((group, carc))
        metric = self._metric_term_index.get(norm)
        if metric is not None:
            matches.append(metric)
        deduped: list[TermMatch] = []
        for match in matches:
            if not any(match is kept or match == kept for kept in deduped):
                deduped.append(match)
        return tuple(deduped)


# ---------------------------------------------------------------------------
# atomic pack proposals (design §9.3) — typed now, no runtime producers


class ArtifactType(StrEnum):
    CONCEPT = "concept"
    ALIAS = "alias"
    CODE_DEFINITION = "code_definition"
    KNOWLEDGE_CARD = "knowledge_card"
    BENCHMARK_FIGURE = "benchmark_figure"
    BINDING = "binding"
    METRIC_CONTRACT = "metric_contract"
    PLAYBOOK = "playbook"
    CONCLUSION_POLICY = "conclusion_policy"
    RANKING_POLICY = "ranking_policy"
    DETECTOR_POLICY = "detector_policy"
    PRESENTATION_RECIPE = "presentation_recipe"
    FILING_RULE = "filing_rule"


class DeltaOperation(StrEnum):
    ADD = "add"
    REVISE = "revise"  # meaning-changing revisions create a new version (§5.2)
    DEPRECATE = "deprecate"


class RiskClass(StrEnum):
    """Promotion tiers per design §9.5."""

    AUTOMATED = "automated"  # e.g. additive synonyms, doc links
    SHADOWED_REVIEW = "shadowed_review"  # e.g. playbook branches, proxy mappings
    EXPLICIT_APPROVAL = "explicit_approval"  # e.g. formulas, denominators, join paths


@dataclass(frozen=True, slots=True)
class ConceptArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.CONCEPT
    concept: Concept


@dataclass(frozen=True, slots=True)
class AliasArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.ALIAS
    override: AliasOverride


@dataclass(frozen=True, slots=True)
class CodeArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.CODE_DEFINITION
    code: CodeDefinition


@dataclass(frozen=True, slots=True)
class KnowledgeCardArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.KNOWLEDGE_CARD
    card: KnowledgeCard


@dataclass(frozen=True, slots=True)
class BenchmarkFigureArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.BENCHMARK_FIGURE
    benchmark: BenchmarkFigure


@dataclass(frozen=True, slots=True)
class BindingArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.BINDING
    binding: BindingCandidate


@dataclass(frozen=True, slots=True)
class MetricContractArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.METRIC_CONTRACT
    contract: MetricContract


@dataclass(frozen=True, slots=True)
class PlaybookArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.PLAYBOOK
    playbook: Playbook


@dataclass(frozen=True, slots=True)
class ConclusionPolicyArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.CONCLUSION_POLICY
    policy: ConclusionPolicy


@dataclass(frozen=True, slots=True)
class RankingPolicyArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.RANKING_POLICY
    policy: RankingPolicy


@dataclass(frozen=True, slots=True)
class DetectorPolicyArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.DETECTOR_POLICY
    policy: DetectorPolicy


@dataclass(frozen=True, slots=True)
class PresentationRecipeArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.PRESENTATION_RECIPE
    recipe: PresentationRecipe


@dataclass(frozen=True, slots=True)
class FilingRuleArtifact:
    artifact_type: ClassVar[ArtifactType] = ArtifactType.FILING_RULE
    rule: FilingRule


ArtifactDefinition = Union[  # noqa: UP007 - discriminated union spelled out for clarity
    ConceptArtifact,
    AliasArtifact,
    CodeArtifact,
    KnowledgeCardArtifact,
    BenchmarkFigureArtifact,
    BindingArtifact,
    MetricContractArtifact,
    PlaybookArtifact,
    ConclusionPolicyArtifact,
    RankingPolicyArtifact,
    DetectorPolicyArtifact,
    PresentationRecipeArtifact,
    FilingRuleArtifact,
]


@dataclass(frozen=True, slots=True)
class ExpectedBehavior:
    """A concrete before/after expectation attached to a proposal."""

    given: str
    expect: str


@dataclass(frozen=True, slots=True)
class ProposedTestCase:
    """A test the proposal ships with. An LLM may generate tests, but it
    cannot be the only test oracle (§9.4)."""

    id: str
    description: str


@dataclass(frozen=True, slots=True)
class PackDelta:
    """A small, atomic pack change (design §9.3). Deliberately typed rather
    than stringly typed: this is the object that mutates governed meaning."""

    artifact_type: ArtifactType
    operation: DeltaOperation
    definition: ArtifactDefinition
    evidence_refs: tuple[str, ...]
    dependencies: tuple[str, ...]
    risk_class: RiskClass
    expected_behavior: tuple[ExpectedBehavior, ...]
    proposed_tests: tuple[ProposedTestCase, ...]

    def __post_init__(self) -> None:
        if self.definition.artifact_type is not self.artifact_type:
            raise ValueError(
                f"PackDelta.artifact_type {self.artifact_type.value!r} does not match "
                f"definition type {self.definition.artifact_type.value!r}"
            )


# ---------------------------------------------------------------------------
# analyst corrections (design §9.1) — typed schema, not mined from free text


class CorrectionTarget(StrEnum):
    BINDING = "binding"
    CALCULATION = "calculation"
    THRESHOLD = "threshold"
    SCOPE = "scope"
    RANKING = "ranking"
    NARRATIVE_CLAIM = "narrative_claim"


@dataclass(frozen=True, slots=True)
class AnalystCorrection:
    target: CorrectionTarget
    referent: ReferentId | None
    detail: str
    session_id: str
    investigation_id: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError("AnalystCorrection.detail must be non-empty")
