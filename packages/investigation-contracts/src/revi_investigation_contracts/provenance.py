"""Which governed contracts produced this answer's numbers (design §5.2).

The *third* door onto the one :class:`TraceRecord` a turn writes, beside
:mod:`revi_investigation_contracts.debug` (the engine's vocabulary, on
request) and :mod:`revi_investigation_contracts.evidence` (what was read,
always). This one answers the trust question the other two leave implicit:
**whose definition is this number?**

Everything here is read back off the recorded turn — the metric ids the
interpretation resolved, the contract versions the connector stamped on
the executed frames, the playbook that chose the probes, and the pack
version and content snapshot the session was pinned to. Nothing is looked
up in the pack as it stands *now*: a pack promoted since the turn ran
would answer a different question from the one asked.

**What is deliberately absent.** No display name, no numerator or
denominator expression, no date basis, no exclusions, no semantic
fingerprint. A :class:`MetricContract` carries all of those, and none of
them are recorded per turn — publishing them would mean reading today's
pack and captioning last week's answer with it. The badge shows the id,
the version and the pack, because those are the facts the turn actually
recorded, and an id at a version is enough to look the rest up.
"""

from __future__ import annotations

from pydantic import Field

from revi_investigation_contracts.evidence import EvidenceMetricRef
from revi_investigation_contracts.refinements import ClosedModel


class MetricProvenancePayload(ClosedModel):
    """The governed provenance of one answer's numbers.

    ``metrics`` is the honest whole: every governed metric this turn's
    probes named, in recorded order, each with the contract version the
    connector stamped when it ran (``None`` when the probe was planned and
    never executed). A playbook turn legitimately has several, and this
    payload says so rather than electing one of them the headline.

    ``primary`` is set only when ONE governed contract stands behind the
    answer — either the interpretation named it as governing (the engine's
    own ``governing[0]``), or exactly one metric was read all turn. On a
    playbook turn that ran several, it is ``None``: there is no single
    contract to point at, and inventing one would be the exact overclaim
    the badge exists to prevent.

    An empty ``metrics`` with no ``primary`` is a turn that measured
    nothing governed — a definitional answer, a META citation. The pack
    fields still travel: which pack was pinned is a fact about the turn
    even when the turn read no metric.
    """

    #: The one governed metric contract behind this answer, when there is
    #: one. See the class docstring for when this is ``None``.
    primary: EvidenceMetricRef | None = None
    #: Every governed metric this turn's probes named, recorded order.
    metrics: list[EvidenceMetricRef] = Field(default_factory=list)
    #: The governed playbook recorded in this turn's plan context — the
    #: same id ``GET /v1/investigations/{id}/trace`` publishes. Stated as
    #: the investigation's playbook rather than as "what chose the metrics
    #: above", because those come apart: a refinement inherits its
    #: parent's id, and a turn whose spec names its own measures plans
    #: from those instead. It is load-bearing exactly when ``primary`` is
    #: ``None``, which is when it IS what selected them.
    playbook_id: str | None = None
    pack_id: str = ""
    pack_version: str = ""
    #: The content hash of the pack as loaded — the field that separates
    #: "base-rcm 1.0.0" from "base-rcm 1.0.0 with a hot-edited metric".
    pack_snapshot_id: str = ""
