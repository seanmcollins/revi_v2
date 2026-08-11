"""Governed content for deep research, loaded beside the definitions library.

Two files, both content rather than code, both content-hashed so a report
can be traced to the exact rules it ran under:

* ``packs/base-rcm/deep_research.yaml`` — the rate floor, the band edges,
  the maturity windows, the words for every population and every angle.
* ``packs/base-rcm/filing_rules.yaml`` — which plans' filing limits are
  stated without a confirmation caveat. That distinction changes what
  crossing a deadline means, so it is read from content and handed to the
  estimator as an input rather than guessed from the data.

Both follow the pattern the anomaly-actionability rules already use: a
loader here, a frozen shape, and nothing interpreted at read time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from revi_investigation.application.deep_research.policy import (
    AngleCopy,
    BandSpec,
    DeepResearchSettings,
)

DEEP_RESEARCH_FILENAME = "deep_research.yaml"
FILING_RULES_FILENAME = "filing_rules.yaml"


def _bands(raw: Any, where: str) -> tuple[BandSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{where}: expected a list of bands")
    bands: list[BandSpec] = []
    for node in raw:
        if not isinstance(node, dict) or "label" not in node or "lower" not in node:
            raise ValueError(f"{where}: every band needs a label and a lower edge")
        upper = node.get("upper")
        bands.append(
            BandSpec(
                label=str(node["label"]),
                lower=int(node["lower"]),
                upper=None if upper is None else int(upper),
            )
        )
    return tuple(bands)


def _text(node: Any) -> str:
    return " ".join(str(node or "").split())


def load_deep_research_settings(path: str | Path) -> DeepResearchSettings:
    raw = Path(path).read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a mapping document")

    estimation = document.get("estimation") or {}
    if not isinstance(estimation, dict):
        raise ValueError(f"{path}: 'estimation' must be a mapping")
    population = document.get("population") or {}
    if not isinstance(population, dict):
        raise ValueError(f"{path}: 'population' must be a mapping")

    maturity_raw = estimation.get("maturity_days") or {}
    if not isinstance(maturity_raw, dict):
        raise ValueError(f"{path}: 'maturity_days' must be a mapping")

    angles_raw = document.get("angles") or {}
    if not isinstance(angles_raw, dict):
        raise ValueError(f"{path}: 'angles' must be a mapping")
    angle_copy = {
        str(name): AngleCopy(
            title=_text(node.get("title")),
            progress=_text(node.get("progress")),
            purpose=_text(node.get("purpose")),
        )
        for name, node in angles_raw.items()
        if isinstance(node, dict)
    }

    value_labels_raw = document.get("value_labels") or {}
    value_labels: dict[str, Mapping[str, str]] = {}
    if isinstance(value_labels_raw, dict):
        for stratifier, node in value_labels_raw.items():
            if isinstance(node, dict):
                value_labels[str(stratifier)] = {
                    str(key): _text(value) for key, value in node.items()
                }

    earliest = population.get("earliest_service_date")
    if isinstance(earliest, date):
        earliest_date = earliest
    elif earliest:
        earliest_date = date.fromisoformat(str(earliest))
    else:
        earliest_date = date(2025, 1, 1)

    return DeepResearchSettings(
        min_cohort=int(estimation.get("min_cohort", 30)),
        min_cohort_label=_text(estimation.get("min_cohort_label")),
        min_cohort_recommender=_text(estimation.get("min_cohort_recommender")),
        confidence=Decimal(str(estimation.get("confidence", "0.95"))),
        delay_bands=_bands(estimation.get("delay_bands"), f"{path}: delay bands"),
        dollar_bands=_bands(estimation.get("dollar_bands"), f"{path}: dollar bands"),
        age_bands=_bands(estimation.get("age_bands"), f"{path}: age bands"),
        maturity_days={str(name): int(days) for name, days in maturity_raw.items()},
        earliest_service_date=earliest_date,
        exclude_appealed=bool(population.get("exclude_appealed", True)),
        population_description=_text(population.get("description")),
        disclosure_floor=int(estimation.get("disclosure_floor", 11)),
        max_rows=int(estimation.get("max_rows", 250_000)),
        stratifier_labels={
            str(key): _text(value)
            for key, value in (document.get("stratifier_labels") or {}).items()
        },
        value_labels=value_labels,
        class_context={
            str(key): _text(value)
            for key, value in (document.get("class_context") or {}).items()
        },
        angle_copy=angle_copy,
        copy={
            str(key): _text(value) for key, value in (document.get("copy") or {}).items()
        },
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class FilingRule:
    id: str
    payer_pattern: str
    plan_pattern: str
    requires_confirmation: bool


@dataclass(frozen=True, slots=True)
class FilingRuleLadder:
    """First match wins, most specific first — the file's own rule.

    ``confirmed`` answers one question and only one: does this plan's filing
    limit stand without a confirmation caveat? An analysis that treats every
    configured limit as settled over-predicts the deadline cliff on every
    plan whose limit is really a planning default.
    """

    rules: tuple[FilingRule, ...]
    content_hash: str = ""

    def confirmed(self, payer_name: str, plan_name: str) -> bool:
        for rule in self.rules:
            if not fnmatchcase(payer_name, rule.payer_pattern):
                continue
            if rule.plan_pattern and not fnmatchcase(plan_name, rule.plan_pattern):
                continue
            return not rule.requires_confirmation
        return False


def load_filing_rule_ladder(path: str | Path) -> FilingRuleLadder:
    raw = Path(path).read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a mapping document")
    entries = document.get("filing_rules")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: 'filing_rules' must be a list")
    rules = tuple(
        FilingRule(
            id=str(node.get("id", "")),
            payer_pattern=str(node.get("payer_pattern", "*")),
            plan_pattern=str(node.get("plan_pattern", "")),
            requires_confirmation=bool(node.get("requires_confirmation", True)),
        )
        for node in entries
        if isinstance(node, dict)
    )
    return FilingRuleLadder(
        rules=rules, content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest()
    )
