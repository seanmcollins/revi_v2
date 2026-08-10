"""Governed display names for metric ids that overclaim.

Loads ``packs/base-rcm/metric_display.yaml`` — pack-adjacent governed
content, content-hashed for the trace, exactly like
:mod:`revi_api.actionability`.

``timely_filing_at_risk_dollars`` is the motivating case: it sums billed
charges on open, never-submitted claims and applies no deadline predicate
whatsoever, so its id claims far more than it measures. The id is a
reference anchor across the pack and the answer key, so it is not renamed;
instead every surface that shows the id also shows what the number is and
the qualification that makes it honest.

Two doors, deliberately:

* the **contract's own** ``Population caveat:`` sentence, which the §6.6
  validation pass already publishes as a warning on every answer that
  reads the metric — mandatory, governed, and not this module's to
  decide;
* this file, which carries the same correction to surfaces that have no
  answer to hang a warning on: a portfolio card, a chip, a picker.

The two must agree in substance; a pack test pins that they do.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from revi_investigation_contracts.api import MetricDisplayPayload


@dataclass(frozen=True, slots=True)
class MetricDisplay:
    metric_id: str
    display_name: str
    caveat: str | None = None
    rationale: str | None = None

    def payload(self) -> MetricDisplayPayload:
        return MetricDisplayPayload(
            metric_id=self.metric_id,
            display_name=self.display_name,
            caveat=self.caveat,
            rationale=self.rationale,
        )


@dataclass(frozen=True, slots=True)
class MetricDisplayRules:
    by_metric: Mapping[str, MetricDisplay]
    content_hash: str

    def name_for(self, metric_id: str) -> str | None:
        entry = self.by_metric.get(metric_id)
        return entry.display_name if entry is not None else None

    @property
    def names(self) -> dict[str, str]:
        """``{metric id: display name}`` — the substitution map itself.

        One accessor, because every surface that renders a metric id to a
        human needs the same map. Building it inline let surfaces drift:
        the finding card substituted display names, the referent chip
        beside it did not, and the two disagreed about the same title.
        """
        return {mid: entry.display_name for mid, entry in self.by_metric.items()}

    def payloads_for(self, metric_ids: Iterable[str]) -> list[MetricDisplayPayload]:
        """Entries for the metrics named, in the order named, deduplicated.

        Only the ids that HAVE a correction come back. An empty list is
        the honest and common case: most metric ids say what they measure.
        """
        seen: dict[str, MetricDisplayPayload] = {}
        for metric_id in metric_ids:
            entry = self.by_metric.get(metric_id)
            if entry is not None and metric_id not in seen:
                seen[metric_id] = entry.payload()
        return list(seen.values())

    def all_payloads(self) -> list[MetricDisplayPayload]:
        return [entry.payload() for entry in self.by_metric.values()]


def _text(node: Mapping[str, Any], key: str) -> str | None:
    raw = node.get(key)
    if raw is None:
        return None
    collapsed = " ".join(str(raw).split())
    return collapsed or None


def load_metric_display(path: str | Path) -> MetricDisplayRules:
    """Read the governed display names, or an empty ruleset when absent.

    A missing file is not an error: a pack that needs no corrections
    should not have to ship an empty one. A malformed file IS an error —
    a display name the deployment silently failed to load is a metric
    still wearing the name this file exists to correct.
    """
    file = Path(path)
    if not file.is_file():
        return MetricDisplayRules(by_metric={}, content_hash="")
    raw = file.read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a mapping document")
    metrics = document.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError(f"{path}: 'metrics' must be a mapping of metric id → entry")
    by_metric: dict[str, MetricDisplay] = {}
    for metric_id, node in metrics.items():
        if not isinstance(node, dict):
            raise ValueError(f"{path}: metrics[{metric_id!r}] must be a mapping")
        display_name = _text(node, "display_name")
        if not display_name:
            raise ValueError(f"{path}: metrics[{metric_id!r}] needs a display_name")
        by_metric[str(metric_id)] = MetricDisplay(
            metric_id=str(metric_id),
            display_name=display_name,
            caveat=_text(node, "caveat"),
            rationale=_text(node, "rationale"),
        )
    return MetricDisplayRules(
        by_metric=by_metric,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
