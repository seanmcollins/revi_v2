"""Probe coverage: can an ad-hoc question actually be cut by anything?

The catalog promises 24 certified dimensions and a set of additive measures. A
warehouse that declares them but leaves them single-valued or empty turns every
exploratory probe into a degenerate answer. These tests hold the data to the
catalog's promise at the newest watermark: every certified dimension has at
least two populated values to group by, and every additive measure is non-zero
over a trailing 90-day window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"
SCHEMA = "snap_003"
WATERMARK = "2026-08-02"
TRAILING_START = "2026-05-05"  # 90 days back from the watermark, inclusive

# The date column a trailing window rides on at each grain (date_bases.yaml:
# each entity's primary basis).
TRAILING_BASIS = {
    "claim": "service_date",
    "claim_line": "service_date",
    "transaction": "post_date",
    "remit": "remit_date",
    "denial": "denial_date",
}


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CATALOG_DIR / name).read_text())


@pytest.fixture(scope="module")
def con(small_result: Any) -> Any:
    connection = duckdb.connect(str(small_result.db_path), read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def base_views() -> dict[str, str]:
    return {name: spec["base_view"] for name, spec in _load("entities.yaml")["entities"].items()}


def _certified_bindings(base_views: dict[str, str]) -> list[tuple[str, str, str]]:
    """(dimension, base view, column) for every certified, stored binding."""
    out: list[tuple[str, str, str]] = []
    for name, spec in _load("dimensions.yaml")["dimensions"].items():
        if not spec["certified"] or spec.get("kind") == "derived_bucket":
            continue
        for entity, column in spec.get("entities", {}).items():
            out.append((name, base_views[entity], column))
    return out


def test_every_certified_dimension_has_something_to_group_by(
    con: Any, base_views: dict[str, str]
) -> None:
    """At least two populated values per certified binding at the newest watermark."""
    thin: list[str] = []
    for name, view, column in _certified_bindings(base_views):
        (n,) = con.execute(
            f"SELECT count(DISTINCT {column}) FROM {SCHEMA}.{view} WHERE {column} IS NOT NULL"
        ).fetchone()
        if int(n) < 2:
            thin.append(f"{name} on {view}.{column}: {n} value(s)")
    assert not thin, thin


def test_certified_dimensions_stay_cuttable_inside_a_trailing_window(
    con: Any, base_views: dict[str, str]
) -> None:
    """A 90-day probe must not collapse a dimension to a single value either."""
    entity_of_view = {v: k for k, v in base_views.items()}
    thin: list[str] = []
    for name, view, column in _certified_bindings(base_views):
        basis = TRAILING_BASIS[entity_of_view[view]]
        (n,) = con.execute(
            f"SELECT count(DISTINCT {column}) FROM {SCHEMA}.{view} "
            f"WHERE {column} IS NOT NULL "
            f"AND {basis} BETWEEN DATE '{TRAILING_START}' AND DATE '{WATERMARK}'"
        ).fetchone()
        if int(n) < 2:
            thin.append(f"{name} on {view}.{column}: {n} value(s) in the trailing window")
    assert not thin, thin


def test_declared_value_domains_are_fully_populated(con: Any, base_views: dict[str, str]) -> None:
    """Where the catalog enumerates a domain, the data must actually contain it.

    An enumerated value with no rows behind it is a filter that silently returns
    nothing — the worst possible outcome for an exploratory probe.
    """
    missing: list[str] = []
    for name, spec in _load("dimensions.yaml")["dimensions"].items():
        domain = spec.get("value_domain")
        if not spec["certified"] or not domain:
            continue
        entity, column = next(iter(spec["entities"].items()))
        present = {
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT {column} FROM {SCHEMA}.{base_views[entity]}"
            ).fetchall()
        }
        absent = set(domain) - present
        if absent:
            missing.append(f"{name}: {sorted(absent)}")
    assert not missing, missing


def test_derived_ar_age_buckets_are_all_reachable(con: Any) -> None:
    """ar_age_bucket is computed at probe time; every declared bucket needs rows."""
    buckets = _load("dimensions.yaml")["dimensions"]["ar_age_bucket"]["buckets"]
    rows = con.execute(
        f"""
        SELECT CASE
                 WHEN age <= 30 THEN '0-30' WHEN age <= 60 THEN '31-60'
                 WHEN age <= 90 THEN '61-90' WHEN age <= 120 THEN '91-120'
                 ELSE '120+' END AS bucket,
               count(*)
        FROM (SELECT DATE '{WATERMARK}' - service_date AS age FROM {SCHEMA}.fact_claim
              WHERE status IN ('OPEN', 'DENIED'))
        GROUP BY 1
        """
    ).fetchall()
    populated = {b for b, n in rows if n > 0}
    assert set(buckets) <= populated, sorted(set(buckets) - populated)


def _measure_sql(spec: dict[str, Any]) -> str:
    column = spec["column"]
    inner = (
        f"count(DISTINCT {column})" if spec["aggregation"] == "count_distinct" else f"SUM({column})"
    )
    where = spec.get("filter")
    return f"{inner} FILTER (WHERE {where})" if where else inner


def test_every_additive_measure_is_non_degenerate_in_a_trailing_window(con: Any) -> None:
    """Each additive measure returns something to talk about over 90 days."""
    base_views = {n: s["base_view"] for n, s in _load("entities.yaml")["entities"].items()}
    empty: list[str] = []
    for name, spec in _load("measures.yaml")["measures"].items():
        if not spec["additive"]:
            continue
        entity = spec["entity"]
        basis = TRAILING_BASIS[entity]
        (value,) = con.execute(
            f"SELECT COALESCE({_measure_sql(spec)}, 0) FROM {SCHEMA}.{base_views[entity]} "
            f"WHERE {basis} BETWEEN DATE '{TRAILING_START}' AND DATE '{WATERMARK}'"
        ).fetchone()
        if not value or int(value) <= 0:
            empty.append(f"{name}: {value}")
    assert not empty, empty


#: Certified dimensions deliberately single-valued inside the 2024 backfill,
#: with the invariant that makes them so. The backfill is closed business by
#: construction — verify.py guards "every backfill claim is billed" directly —
#: so a two-valued billed_flag in 2024 would mean the backfill had sprung a
#: leak, not that the dimension had become more useful.
_SINGLE_VALUED_IN_2024 = {"billed_flag": "the 2024 backfill is fully billed (verify.py guards it)"}


def test_certified_boolean_flags_carry_both_values(con: Any, base_views: dict[str, str]) -> None:
    """A flag dimension exists to be filtered on. One that is all-true (or
    all-false) at the newest watermark silently answers every predicate the
    same way, so both values must have rows behind them — and the derived
    view flags must agree with the source columns they restate."""
    for name in ("first_pass_paid", "billed_flag", "discharged_flag"):
        spec = _load("dimensions.yaml")["dimensions"][name]
        assert spec["certified"], name
        column = spec["entities"]["claim"]
        values = {
            r[0]: int(r[1])
            for r in con.execute(
                f"SELECT {column}, count(*) FROM {SCHEMA}.v_claim GROUP BY 1"
            ).fetchall()
        }
        assert set(values) == {True, False}, f"{name}: {values}"
        assert all(n > 0 for n in values.values()), f"{name}: {values}"
    (mismatched,) = con.execute(
        f"""
        SELECT count(*) FROM {SCHEMA}.v_claim
        WHERE billed_flag <> (submission_date IS NOT NULL)
           OR discharged_flag <> (discharge_date IS NOT NULL)
        """
    ).fetchone()
    assert int(mismatched) == 0


def test_the_backfill_year_is_probeable_on_the_same_axes(con: Any) -> None:
    """A 2024 window must be as cuttable as a 2026 one — that is the point of it."""
    base_views = {n: s["base_view"] for n, s in _load("entities.yaml")["entities"].items()}
    thin: list[str] = []
    for name, view, column in _certified_bindings(base_views):
        entity = {v: k for k, v in base_views.items()}[view]
        basis = TRAILING_BASIS[entity]
        (n,) = con.execute(
            f"SELECT count(DISTINCT {column}) FROM {SCHEMA}.{view} "
            f"WHERE {column} IS NOT NULL "
            f"AND {basis} BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'"
        ).fetchone()
        if name in _SINGLE_VALUED_IN_2024:
            # Asserted, not skipped: the exemption is itself an invariant.
            assert int(n) == 1, f"{name} is two-valued in 2024 — {_SINGLE_VALUED_IN_2024[name]}"
            continue
        if int(n) < 2:
            thin.append(f"{name} on {view}.{column}: {n} value(s) in 2024")
    assert not thin, thin
