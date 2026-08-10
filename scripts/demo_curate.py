"""Demo-tenant curation, as a command you can run twice.

Round-10 R10-7. On the morning of the first owner demo the tenant held 163
sessions, and the first eighteen rows of the rail — the left edge of every
screen and every screenshot a prospect takes home — were reviewer probes
("Who is my worst payer on deni…" four times, "(typed investigation)" six).
Beside the seven curated watches sat an eighth minted by a review battery,
measuring the same metric as its curated twin under a different spelling.
Curation had been done once, by hand, and there was no way to do it again.

So this is a COMMAND, not a runbook. It is idempotent — running it twice
changes nothing the second time — and it does three things in this order:

1. **Curate.** Archive every session and every watch that is not on the
   keep list. Both archives are SOFT (``DELETE /v1/sessions/{id}`` and
   ``DELETE /v1/rounds/pins/{id}``), so a permalink somebody shared does
   not 404 because the rail was tidied.
2. **Verify.** Read the monitors surface back and check what a room will
   see: the expected number of tiles, every tile naming the cell its
   number is about, every watch's delta agreeing between the tile grid and
   the brief, and the brief carrying entries at all.
3. **Report.** Print a demo-readiness checklist, including the one item
   that is a decision rather than a check.

**The keep list is named, never guessed.** With no ``--keep-session`` and
no manifest this refuses to archive anything and instead prints the tenant
as it stands, with ids, so the operator can name the demo's own sessions
and re-run. Archiving "everything that looks like a probe" is exactly the
kind of invention the product itself refuses, and getting it wrong here
deletes the demo from the rail four minutes before it starts.

Usage::

    uv run python scripts/demo_curate.py                 # discovery + verify
    uv run python scripts/demo_curate.py --manifest demo/curation.json
    uv run python scripts/demo_curate.py --keep-session sess_abc --keep-pin pin_x
    uv run python scripts/demo_curate.py --manifest … --dry-run

Exit status is 0 only when every verification passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))


def _load_dotenv() -> None:
    """The same repo-root ``.env`` the uvicorn entry point loads.

    A curation run must talk to the tenant the demo will be given, which
    means the same store, warehouse and pack the API is wired to. Read here
    rather than in the wiring for the reason ``revi_api.main`` states:
    everything downstream takes an explicit environment mapping so tests
    never inherit a developer's file.
    """
    root_env = _REPO_ROOT / ".env"
    path = str(root_env) if root_env.is_file() else find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)


_load_dotenv()

from revi_api.auth import Principal  # noqa: E402
from revi_api.scripted_llm import demo_language_model  # noqa: E402
from revi_api.service import MAX_SESSION_LIST_LIMIT, ApiService  # noqa: E402
from revi_api.wiring import build_components  # noqa: E402

#: The documented shape of a curated demo tenant. Seven watches is what the
#: monitors surface is built around and what every screenshot in the review
#: record shows; it is an argument rather than a constant because a
#: different demo may walk a different set.
DEFAULT_EXPECTED_PINS = 7

#: The fixture decision, resolved (R10-7). The synthetic warehouse retires
#: receivables slowly enough that the book reads 179.5 days in A/R with 48%
#: over 120 days — filed in rounds 8, 9 and 10 with the same sentence: the
#: first revenue-cycle person in the room will say the data is not real,
#: out loud, and then say it about the analytics.
#:
#: Two ways out were priced. Fixing the generator's retirement behaviour is
#: a warehouse change plus a regenerated answer key plus a re-run of every
#: reference test that pins a figure — the report's own words are "not this
#: morning", and shipping it untested under time pressure risks the numbers
#: the demo is FOR. So: DISCLOSE. The presenter names the fixture and its
#: scale before a customer discovers it, which costs one sentence and turns
#: the weakest number on the screen into evidence that the platform's
#: operators know their own data.
FIXTURE_TALKING_POINT = (
    "Say this before the A/R tile is on screen, not after: \"This is a generated "
    "book — 179.5 days in A/R with about half of it over 120 days. No real "
    "provider looks like that; the generator retires receivables more slowly "
    "than a billing office does. The figures are internally consistent and "
    "every one of them is checkable, which is what we are showing you. Your own "
    "numbers will be smaller and the analysis will read the same way.\""
)


@dataclass
class Manifest:
    """What the demo walks. Named by the operator, never inferred."""

    sessions: tuple[str, ...] = ()
    pins: tuple[str, ...] = ()
    expected_pins: int = DEFAULT_EXPECTED_PINS

    @property
    def names_anything(self) -> bool:
        return bool(self.sessions or self.pins)

    @classmethod
    def load(cls, path: Path) -> Manifest:
        raw: dict[str, Any] = json.loads(path.read_text())
        return cls(
            sessions=tuple(raw.get("sessions", ())),
            pins=tuple(raw.get("pins", ())),
            expected_pins=int(raw.get("expected_pins", DEFAULT_EXPECTED_PINS)),
        )


@dataclass
class Report:
    """Everything the run learned, so the checklist is printed once."""

    checks: list[tuple[bool, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, ok: bool, statement: str) -> None:
        self.checks.append((bool(ok), statement))

    def note(self, statement: str) -> None:
        self.notes.append(statement)

    @property
    def failed(self) -> list[str]:
        return [statement for ok, statement in self.checks if not ok]


def _matches(needle: str, *haystack: str | None) -> bool:
    """Does this keep-list entry name this record?

    An id matches exactly; anything else matches a title or label
    case-insensitively, either as the whole string or as a substring — the
    rail truncates titles, so an operator copying one off the screen is
    copying a prefix.
    """
    folded = needle.strip().casefold()
    if not folded:
        return False
    return any(
        value is not None
        and (value.casefold() == folded or folded in value.casefold())
        for value in haystack
    )


async def _curate_sessions(
    service: ApiService,
    caller: Principal,
    manifest: Manifest,
    *,
    apply: bool,
    clean_rail: bool,
    report: Report,
) -> None:
    """Bring the rail down to the sessions the demo walks.

    ``GET /v1/sessions`` has no offset — it is the rail, and the rail shows
    the newest page — so the loop is read-archive-reread rather than
    paginate: each pass archives everything on the page that is not kept and
    reads again, which terminates when a page holds only kept rows. The
    tenant this was written for held 653 sessions behind a 200-row cap, so a
    single read would have curated the newest third and reported success.
    """
    page = await service.list_sessions(caller, limit=MAX_SESSION_LIST_LIMIT)
    print(f"\nSESSIONS — {page.total} unarchived on this tenant")
    if not manifest.sessions and not clean_rail:
        # Named, never guessed. Getting this wrong archives the demo from
        # the rail four minutes before it starts, and there is no un-archive
        # route to undo it with.
        report.note(
            f"{page.total} session(s) left as they are. Name the ones the demo walks with "
            "--keep-session (or a manifest), or pass --clean-rail to archive every one of "
            "them — a demo walked live wants an empty rail, and archiving is soft."
        )
        for row in page.sessions[:20]:
            print(f"  · {row.session_id}  {row.turn_count:>3} turns  {row.title[:64]!r}")
        if page.total > 20:
            print(f"  … and {page.total - 20} more")
        report.check(False, "the rail carries the demo's sessions and nothing else")
        return

    def _kept(row: Any) -> bool:
        return any(_matches(name, row.session_id, row.title) for name in manifest.sessions)

    seen_names: set[str] = set()
    archived = 0
    keep: dict[str, Any] = {}
    while True:
        rows = page.sessions
        for name in manifest.sessions:
            if any(_matches(name, row.session_id, row.title) for row in rows):
                seen_names.add(name)
        keep.update({row.session_id: row for row in rows if _kept(row)})
        drop = [row for row in rows if not _kept(row)]
        if not drop:
            break
        for row in drop[:12] if archived == 0 else []:
            print(f"  {'archive  ' if apply else 'would    '}{row.session_id}  {row.title[:56]!r}")
        if not apply:
            archived = len(drop)
            break
        for row in drop:
            await service.archive_session(caller, row.session_id)
        archived += len(drop)
        page = await service.list_sessions(caller, limit=MAX_SESSION_LIST_LIMIT)
    for row in keep.values():
        print(f"  keep      {row.session_id}  {row.title[:64]!r}")
    print(f"  {'archived' if apply else 'would archive'}: {archived} session(s)")
    unmatched = [name for name in manifest.sessions if name not in seen_names]
    for name in unmatched:
        report.note(f"session {name!r} was named to keep and matched nothing on this tenant")
    report.check(not unmatched, "every session the manifest names exists on this tenant")
    remaining = (await service.list_sessions(caller, limit=1)).total
    report.check(
        remaining == len(keep),
        f"the rail carries the demo's sessions and nothing else "
        f"({remaining} row(s) remain, {len(keep)} of them named)",
    )


async def _curate_pins(
    service: ApiService, caller: Principal, manifest: Manifest, *, apply: bool, report: Report
) -> None:
    listing = await service.rounds.list_pins(caller)
    pins = [pin for pin in listing.pins if pin.archived_at is None]
    print(f"\nWATCHES — {len(pins)} active")
    for pin in pins:
        print(f"  · {pin.pin_id}  {pin.label[:60]!r}  ({pin.created_from_kind})")
    if manifest.pins:
        drop = [
            pin
            for pin in pins
            if not any(_matches(name, pin.pin_id, pin.label) for name in manifest.pins)
        ]
        for pin in drop:
            print(f"  {'archive ' if apply else 'would   '} {pin.pin_id}  {pin.label[:56]!r}")
            if apply:
                await service.rounds.archive_pin(caller, pin.pin_id)
        if apply and drop:
            listing = await service.rounds.list_pins(caller)
            pins = [pin for pin in listing.pins if pin.archived_at is None]
    else:
        report.note(
            "no watch was named to keep, so none was archived — a probe pin beside its "
            "curated twin is invisible to this command until the curated set is named."
        )
    report.check(
        len(pins) == manifest.expected_pins,
        f"the monitors surface carries {manifest.expected_pins} watches "
        f"(it carries {len(pins)})",
    )
    if listing.unreadable:
        report.check(False, f"every stored watch is readable ({listing.unreadable} are not)")


async def _verify_surface(service: ApiService, caller: Principal, report: Report) -> None:
    """What a room will see, read back off the same routes the room reads."""
    surface = await service.rounds.rounds(caller)
    brief = await service.rounds.brief(caller)
    pins = {
        pin.pin_id: pin
        for pin in (await service.rounds.list_pins(caller)).pins
        if pin.archived_at is None
    }
    tiles = {tile.pin_id: tile for tile in surface.tiles}

    print(f"\nMONITORS — {len(tiles)} tile(s) at watermark {surface.watermark_id}")
    subjectless: list[str] = []
    first_readings: list[str] = []
    for pin_id, tile in tiles.items():
        delta = tile.delta
        movement = "no delta payload"
        if delta is not None:
            movement = (
                f"{delta.direction} {delta.delta_text} vs {delta.prior_watermark_id}"
                if delta.comparable
                else f"not comparable — {delta.not_comparable_reason[:60]}"
            )
        print(f"  · {pin_id}  {tile.label[:44]!r}  {tile.value_text}  [{movement}]")
        pin = pins.get(pin_id)
        names_a_cell = bool(pin is not None and pin.spec.dimensions)
        if tile.status == "ok" and names_a_cell and not tile.headline_subject:
            subjectless.append(pin_id)
        if delta is not None and not delta.comparable and "first reading" in (
            delta.not_comparable_reason or ""
        ):
            first_readings.append(pin_id)

    report.check(
        not subjectless,
        f"every tile names the cell its number is about ({subjectless} do not)",
    )
    report.check(
        not first_readings,
        "every watch has history to back-walk — none is stuck on 'first reading' "
        f"({first_readings} are)",
    )

    # R10-2, live: one pin is one fact. The tile grid and the brief are two
    # renderings of one default view, and a VP who scrolls reads both.
    disagreed: list[str] = []
    for entry in brief.entries:
        if entry.pin_id is None or entry.delta is None:
            continue
        tile = tiles.get(entry.pin_id)
        if tile is None or tile.delta is None:
            disagreed.append(f"{entry.pin_id} (briefed with no tile)")
            continue
        if (
            entry.delta.comparable != tile.delta.comparable
            or entry.delta.delta_text != tile.delta.delta_text
            or entry.delta.prior_watermark_id != tile.delta.prior_watermark_id
        ):
            disagreed.append(
                f"{entry.pin_id}: brief {entry.delta.delta_text!r}/"
                f"{entry.delta.prior_watermark_id!r} vs tile "
                f"{tile.delta.delta_text!r}/{tile.delta.prior_watermark_id!r}"
            )
    report.check(
        not disagreed,
        f"the brief and the tiles agree on every watch ({disagreed} do not)",
    )

    print(f"\nBRIEF — {brief.status}, {len(brief.entries)} entry(ies)")
    print(f"  {brief.headline}")
    for entry in brief.entries:
        print(f"  · [{entry.kind}] {entry.statement[:110]}")
    if brief.immaterial.note:
        print(f"  held back: {brief.immaterial.note[:160]}")
    report.check(bool(brief.entries), "the brief has something to say this morning")
    report.check(
        brief.pins_evaluated == len(tiles),
        f"the brief and the grid count the same watches "
        f"({brief.pins_evaluated} briefed vs {len(tiles)} tiled)",
    )


def _print_checklist(report: Report, *, apply: bool) -> None:
    print("\n" + "=" * 72)
    print("DEMO-READINESS CHECKLIST" + ("" if apply else "  (dry run — nothing was archived)"))
    print("=" * 72)
    for ok, statement in report.checks:
        print(f"  [{'x' if ok else ' '}] {statement}")
    for note in report.notes:
        print(f"   !  {note}")
    print("\n  [ ] OWNER DECISION — the A/R fixture. Resolved as DISCLOSE, not fix:")
    for line in _wrap(FIXTURE_TALKING_POINT, 68):
        print(f"        {line}")
    print(
        "\n  [ ] Read the brief out loud once before the room arrives. It is the\n"
        "        first thing on screen and the only copy nobody rehearses."
    )
    print("=" * 72)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


async def _run(args: argparse.Namespace) -> int:
    manifest = Manifest(
        sessions=tuple(args.keep_session),
        pins=tuple(args.keep_pin),
        expected_pins=args.expect_pins,
    )
    if args.manifest is not None:
        path = Path(args.manifest)
        if not path.is_file():
            print(f"manifest {path} does not exist", file=sys.stderr)
            return 2
        loaded = Manifest.load(path)
        manifest = Manifest(
            sessions=manifest.sessions + loaded.sessions,
            pins=manifest.pins + loaded.pins,
            expected_pins=loaded.expected_pins
            if args.expect_pins == DEFAULT_EXPECTED_PINS
            else args.expect_pins,
        )

    env = dict(os.environ)
    tenant = args.tenant or env.get("REVI_AUTH_DEV_TENANT", "demo")
    # Scripted model, always: curation reads and archives, and a command run
    # four minutes before a demo must not be able to spend money or block on
    # a provider.
    service = ApiService(build_components(env, llm=demo_language_model()))
    caller = Principal(tenant=tenant, subject="demo-curate")
    apply = not args.dry_run and (manifest.names_anything or args.clean_rail)

    print("=" * 72)
    print(f"DEMO CURATION — tenant {tenant!r}")
    print(f"store: {'postgres' if env.get('REVI_DATABASE_URL') else 'in-memory'}")
    print("=" * 72)

    report = Report()
    await _curate_sessions(
        service, caller, manifest, apply=apply, clean_rail=args.clean_rail, report=report
    )
    await _curate_pins(service, caller, manifest, apply=apply, report=report)
    await _verify_surface(service, caller, report)
    _print_checklist(report, apply=apply)

    if report.failed:
        print(f"\n{len(report.failed)} check(s) did not pass — this tenant is NOT demo-ready.")
        return 1
    print("\nEvery check passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", default=None, help="defaults to REVI_AUTH_DEV_TENANT")
    parser.add_argument(
        "--keep-session",
        action="append",
        default=[],
        metavar="ID_OR_TITLE",
        help="a session the demo walks; repeatable",
    )
    parser.add_argument(
        "--keep-pin",
        action="append",
        default=[],
        metavar="ID_OR_LABEL",
        help="a watch the demo shows; repeatable",
    )
    parser.add_argument("--manifest", default=None, help="JSON: {sessions, pins, expected_pins}")
    parser.add_argument(
        "--clean-rail",
        action="store_true",
        help="archive every session not named to keep — a demo walked live wants an "
        "empty rail, and the archive is soft (permalinks keep working)",
    )
    parser.add_argument("--expect-pins", type=int, default=DEFAULT_EXPECTED_PINS)
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would be archived and archive nothing"
    )
    parser.add_argument("--verbose", action="store_true", help="show wiring logs")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
