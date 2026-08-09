"""The governed display names, and the promise they make (review F9).

The load-bearing assertion is not that a display name exists. It is that
the display name and the CONTRACT'S OWN mandatory caveat say the same
thing — two files, one claim. If they can drift, the surface a reader
happens to look at decides what they are told.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from revi_api.metric_display import load_metric_display
from revi_investigation.application.validation import population_caveat
from revi_testing.engine_wiring import load_base_pack

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPLAY_PATH = REPO_ROOT / "packs" / "base-rcm" / "metric_display.yaml"

TIMELY = "timely_filing_at_risk_dollars"


@pytest.fixture(scope="module")
def rules():
    return load_metric_display(DISPLAY_PATH)


def test_the_overclaiming_id_has_a_governed_display_name(rules) -> None:
    name = rules.name_for(TIMELY)
    assert name == "Unbilled open inventory (timely-filing watch proxy)"
    # The correction has to be visible in the name itself: a reader who
    # sees only the label must not still believe it measures exposure.
    assert "inventory" in name.lower() and "proxy" in name.lower()


def test_the_contract_emits_the_same_caveat_the_display_file_carries(rules) -> None:
    """Two doors onto one claim: the §6.6 validation pass publishes the
    contract's ``Population caveat:`` on every answer that reads the
    metric, and this file carries the same correction to surfaces with no
    answer to hang a warning on. They must not be able to disagree."""
    contract = load_base_pack().metric(TIMELY)
    assert contract is not None
    caveat = population_caveat(contract.description)
    assert caveat is not None, "the contract must declare a mandatory population caveat"

    entry = rules.by_metric[TIMELY]
    assert entry.caveat is not None
    # Same substance, checked on the load-bearing tokens rather than by
    # string equality — the two are written for different lengths.
    for token in ("deadline", "filing_rules", "upper bound"):
        assert token in caveat.lower() or token in caveat, token
        assert token in entry.caveat.lower() or token in entry.caveat, token
    assert "NOT" in caveat  # the negation is the whole point
    assert "NOT" in entry.caveat


def test_the_contract_caveat_names_what_the_formula_does_not_do(rules) -> None:
    contract = load_base_pack().metric(TIMELY)
    assert contract is not None
    caveat = population_caveat(contract.description) or ""
    assert "no deadline predicate" in caveat.lower()
    # And the description must no longer promise filing exposure in its
    # own voice. "at risk" survives only inside the id itself.
    body = contract.description.lower().replace(TIMELY, "")
    assert "dollars at risk of a timely-filing denial" not in body.split("read it as")[0]


def test_every_entry_records_why_it_exists(rules) -> None:
    """An entry with no rationale is somebody's preference. The file is a
    correction log and has to read as one."""
    assert rules.by_metric
    for entry in rules.by_metric.values():
        assert entry.rationale, entry.metric_id


def test_entries_name_metrics_the_pack_actually_ships(rules) -> None:
    pack = load_base_pack()
    for metric_id in rules.by_metric:
        assert pack.metric(metric_id) is not None, metric_id


def test_only_the_corrected_ids_come_back(rules) -> None:
    """Most ids say what they measure; asking about one returns nothing
    rather than a manufactured label."""
    assert rules.payloads_for(["cash_posted"]) == []
    [payload] = rules.payloads_for([TIMELY, TIMELY])  # deduplicated
    assert payload.metric_id == TIMELY


def test_a_missing_file_is_an_empty_ruleset_not_a_crash(tmp_path: Path) -> None:
    """A pack that needs no corrections should not have to ship an empty
    file — but a MALFORMED one must fail loudly, because a display name
    that silently failed to load leaves the id wearing its old name."""
    assert load_metric_display(tmp_path / "absent.yaml").by_metric == {}
    bad = tmp_path / "bad.yaml"
    bad.write_text("metrics:\n  x: {rationale: no name}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="display_name"):
        load_metric_display(bad)


def test_the_ruleset_is_content_hashed(rules) -> None:
    assert len(rules.content_hash) == 64
