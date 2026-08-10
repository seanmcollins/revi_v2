"""The governed inputs the audit path is allowed to read: metric contract
YAML and the semantic catalog YAML. Plain ``yaml.safe_load`` into plain
dataclasses — no product model classes, on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_METRICS_DIR = REPO_ROOT / "packs" / "base-rcm" / "metrics"
DEFAULT_CATALOG_DIR = REPO_ROOT / "warehouse" / "catalog"
DEFAULT_WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
DEFAULT_ANSWER_KEY = REPO_ROOT / "data" / "answer_key.json"


@dataclass(frozen=True)
class MetricContract:
    """One ``packs/base-rcm/metrics/*.yaml`` file, verbatim."""

    id: str
    version: int
    kind: str
    entity_grain: str
    numerator: dict[str, Any]
    denominator: dict[str, Any] | None
    exclusions: dict[str, Any] | None
    primary_date_basis: str
    allowed_date_bases: tuple[str, ...]
    scope_dimensions: tuple[str, ...]
    unit: str
    sign: str
    source: str

    @property
    def is_ratio(self) -> bool:
        return self.denominator is not None

    def allows_basis(self, basis: str) -> bool:
        return basis.lower() in self.allowed_date_bases


def load_contracts(metrics_dir: Path | None = None) -> dict[str, MetricContract]:
    """Read every metric contract in the pack."""
    directory = metrics_dir or DEFAULT_METRICS_DIR
    contracts: dict[str, MetricContract] = {}
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        contract = MetricContract(
            id=str(raw["id"]),
            version=int(raw["version"]),
            kind=str(raw["kind"]),
            entity_grain=str(raw["entity_grain"]),
            numerator=dict(raw["numerator"]),
            denominator=dict(raw["denominator"]) if raw.get("denominator") else None,
            exclusions=dict(raw["exclusions"]) if raw.get("exclusions") else None,
            primary_date_basis=str(raw["primary_date_basis"]).lower(),
            allowed_date_bases=tuple(str(b).lower() for b in raw.get("allowed_date_bases", [])),
            scope_dimensions=tuple(str(d) for d in raw.get("scope_dimensions", [])),
            unit=str(raw.get("unit", "")),
            sign=str(raw.get("sign", "")),
            source=str(path.relative_to(REPO_ROOT)),
        )
        contracts[contract.id] = contract
    return contracts


@dataclass(frozen=True)
class Catalog:
    """``warehouse/catalog/*.yaml`` — entities, dimensions, measures, bases."""

    entities: dict[str, dict[str, Any]]
    dimensions: dict[str, dict[str, Any]]
    measures: dict[str, dict[str, Any]]
    date_bases: dict[str, dict[str, Any]]
    join_paths: tuple[dict[str, Any], ...] = ()

    def join_column(self, source: str, target: str) -> str | None:
        """The certified join column from ``source`` grain to ``target`` grain.

        ``on:`` is a YAML 1.1 boolean, so PyYAML parses the catalog's join
        key as ``True`` rather than the string ``"on"``. Both spellings are
        accepted so the audit path reads the file the catalog actually ships.
        """
        for path in self.join_paths:
            if path.get("from") == source and path.get("to") == target:
                column = path.get("on", path.get(True))
                return None if column is None else str(column)
        return None

    def base_view(self, entity: str) -> str | None:
        spec = self.entities.get(entity)
        return None if spec is None else str(spec["base_view"])

    def primary_key(self, entity: str) -> str | None:
        spec = self.entities.get(entity)
        return None if spec is None else str(spec["primary_key"])

    def dimension_column(self, dimension: str, entity: str) -> str | None:
        spec = self.dimensions.get(dimension)
        if spec is None:
            return None
        column = spec.get("entities", {}).get(entity)
        return None if column is None else str(column)

    def dimension_kind(self, dimension: str) -> str | None:
        spec = self.dimensions.get(dimension)
        return None if spec is None else spec.get("kind")

    def basis_column(self, basis: str, entity: str) -> str | None:
        spec = self.date_bases.get(basis.upper())
        if spec is None:
            return None
        column = spec.get("columns", {}).get(entity)
        return None if column is None else str(column)

    def declared_columns(self, entity: str) -> tuple[str, ...]:
        spec = self.entities.get(entity) or {}
        return tuple(str(c) for c in spec.get("declared_columns", ()))


def load_catalog(catalog_dir: Path | None = None) -> Catalog:
    directory = catalog_dir or DEFAULT_CATALOG_DIR
    entities_doc = yaml.safe_load((directory / "entities.yaml").read_text())
    dimensions = yaml.safe_load((directory / "dimensions.yaml").read_text())["dimensions"]
    measures = yaml.safe_load((directory / "measures.yaml").read_text())["measures"]
    date_bases = yaml.safe_load((directory / "date_bases.yaml").read_text())["date_bases"]
    return Catalog(
        entities=dict(entities_doc["entities"]),
        dimensions=dict(dimensions),
        measures=dict(measures),
        date_bases=dict(date_bases),
        join_paths=tuple(entities_doc.get("join_paths", ())),
    )


@dataclass(frozen=True)
class DerivedMeasure:
    """A probe-time derived measure, as the base pack's NOTES.md declares it.

    These are not stored columns and they are not in ``measures.yaml``; the
    pack declares each one's entity and formula in the
    "Derived measure registry (probe-time computations)" table of
    ``packs/base-rcm/NOTES.md``. The audit path re-implements each formula
    from that declaration — it never reads the compiler's version.

    ``shape`` records which probe shape the declaration says the measure is
    valid for. v1 derives the ``aggregation`` ones; the ``snapshot`` ones
    need the snapshot builder's as-of open-inventory population, which is an
    adapter convention rather than governed contract content, so they are
    refused and counted.
    """

    id: str
    entity: str
    shape: str
    note: str


#: Verbatim from packs/base-rcm/NOTES.md, "Derived measure registry".
DERIVED_MEASURES: dict[str, DerivedMeasure] = {
    m.id: m
    for m in (
        DerivedMeasure(
            "payment_lag_days",
            "transaction",
            "aggregation",
            "for PAYMENT transactions: post_date - submission_date (days); NULL otherwise",
        ),
        DerivedMeasure(
            "submission_lag_days",
            "claim",
            "aggregation",
            "for submitted claims: submission_date - service_date (days); NULL otherwise",
        ),
        DerivedMeasure(
            "charge_entry_lag_days",
            "claim_line",
            "aggregation",
            "charge_entry_date - service_date (days)",
        ),
        DerivedMeasure(
            "late_charge_cents",
            "claim_line",
            "aggregation",
            "billed_amount_cents when charge_entry_date > service_date + 3 days, else 0",
        ),
        DerivedMeasure(
            "underpayment_cents",
            "claim",
            "aggregation",
            "adjudicated claims: max(0, expected_amount_cents - SUM(visible line "
            "allowed_amount_cents)); never netted across claims",
        ),
        DerivedMeasure(
            "ar_age_days_billed_cents",
            "claim",
            "snapshot",
            "unresolved claims: billed_amount_cents x (newest_data_date - aging basis date)",
        ),
        DerivedMeasure(
            "credit_balance_cents",
            "claim",
            "snapshot",
            "max(0, posted PAYMENT + PATIENT_PAYMENT - expected) - REFUND already posted",
        ),
        DerivedMeasure(
            "days_to_filing_deadline",
            "claim",
            "snapshot",
            "unsubmitted claims: (service_date + plan timely_filing_days) - as_of, in days",
        ),
    )
}

#: The late-charge threshold the pack's registry states in words.
LATE_CHARGE_THRESHOLD_DAYS = 3
