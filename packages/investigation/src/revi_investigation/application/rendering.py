"""Unit-aware rendering of every value that reaches a user (design §7.4).

A published title or statement is the part of an answer a human actually
reads, and until this module existed it was assembled with whatever
``str``/``repr`` happened to be at hand. That produced three distinct
classes of defect, all reproduced live against the real warehouse:

1. ``f"{value!r}"`` as the fallback for any non-money measure, so a
   perfectly correct ratio was published as
   ``"Bluestone Mutual / Laboratory: Decimal('1.000000') denials unworked
   pct"``. A Python repr is a debugging artifact; it is not a number a
   revenue-cycle director can act on.
2. Floor-divided dollars printed *beside* raw cents in one sentence —
   ``"cash posted moved from 18722151 to 8812843 cents (down $99,093,
   -52.9% ...)"`` — which states the same quantity in two units, one of
   them silently truncated.
3. Denial codes printed as a bare CARC integer with the group code and the
   governed title stripped. ``16`` is not a denial: **CO-16** (provider
   liability, ~931 denials / $2.03M here) and **PI-16** (payer-initiated,
   ~82 / $132K) are different things that get merged into one row, and
   **PR-2** is a patient-responsibility billing instruction that has
   appeared in a published "top denial driver" list. 14 of the 20 CARCs in
   this warehouse span more than one group code, so the pair — not the
   CARC — is the unit of analysis. ``codes.yaml`` and ``presentation.yaml``
   already say so; nothing read them.

Everything published therefore routes through here, and the unit comes
from the evidence frame's own column (which the compiler stamps from the
metric contract's ``unit``) rather than from a guess at the call site.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from revi_kernel.filters import Scalar

MONEY_UNIT = "money_cents"
RATIO_UNIT = "ratio"
DAYS_UNIT = "days"
COUNT_UNIT = "count"

#: Dimension ids that carry remittance codes, and the pack code system that
#: defines them. A value in one of these columns is never a bare label.
CARC_DIMENSION = "carc"
GROUP_CODE_DIMENSION = "group_code"
RARC_DIMENSION = "rarc_synthetic"

_CODE_SYSTEM_BY_DIMENSION = {
    CARC_DIMENSION: "carc",
    GROUP_CODE_DIMENSION: "group_code",
    RARC_DIMENSION: "rarc",
}


class CodeTitleLookup(Protocol):
    """The one pack lookup rendering needs: a governed code title."""

    def code_title(self, system: str, code: str) -> str | None: ...


# ---------------------------------------------------------------------------
# scalar values


def money(cents: int | Decimal) -> str:
    """Cents → ``$187,221.51``. Signed; cents are never dropped.

    Money is stored in cents everywhere in this system precisely so that no
    stage has to round. Rendering is the only place the decimal point
    appears, and it appears exactly once.
    """
    value = Decimal(int(cents)) / 100
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def magnitude_money(cents: int | Decimal) -> str:
    """Unsigned money, for text that carries direction in words ("down")."""
    return money(abs(int(cents)))


def ratio_pct(value: Decimal | float, *, places: int = 1) -> str:
    """A 0..1 ratio → ``88.9%``. Ratios are never published as raw decimals."""
    return f"{float(value):.{places}%}"


def points(value: Decimal | float, *, places: int = 1) -> str:
    """A ratio *difference* → ``1.3 points``. Unsigned.

    The one rendering a rate's movement may take. "Denial rate up 3.2%" is
    ambiguous between a relative change (5.0% → 5.16%) and an absolute one
    (5.0% → 8.2%) — two different facts, one sentence, and no way for a
    reader to tell which they were given. Percentage points say the second
    unambiguously, and the relative form stays available as ``pct_change``
    where it is labelled as such.
    """
    return f"{abs(float(value)) * 100:.{places}f} points"


def days(value: int | Decimal | float) -> str:
    return f"{float(value):,.1f} days"


def count(value: int | Decimal) -> str:
    return f"{int(value):,}"


def format_value(value: Scalar, unit: str | None) -> str:
    """Render one measure value in its contract unit.

    Unknown units and un-typed values fall back to ``str`` — never
    ``repr``. ``str(Decimal('1.000000'))`` is ``'1.000000'``, which is at
    worst unhelpful; ``repr`` is actively wrong.
    """
    if value is None:
        return "suppressed"
    if unit == MONEY_UNIT and isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return money(value)
    if unit == RATIO_UNIT and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return ratio_pct(Decimal(str(value)))
    if unit == DAYS_UNIT and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return days(value)
    if unit == COUNT_UNIT and isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return count(value)
    if isinstance(value, Decimal):
        return f"{value.normalize():f}"
    return str(value)


def magnitude(value: Scalar, unit: str | None) -> str:
    """An unsigned movement in its own unit, for text that says "up"/"down".

    Money keeps dollars, a rate becomes percentage points, everything else
    falls back to its ordinary rendering of the absolute value. Without
    this a compared *ratio* was rendered by the money path or not at all,
    which is half of why a grouped rate comparison published nothing.
    """
    if isinstance(value, bool) or value is None:
        return format_value(value, unit)
    if unit == MONEY_UNIT and isinstance(value, (int, Decimal)):
        return magnitude_money(value)
    if unit == RATIO_UNIT and isinstance(value, (int, float, Decimal)):
        return points(Decimal(str(value)))
    if isinstance(value, Decimal):
        return format_value(-value if value < 0 else value, unit)
    if isinstance(value, int):
        return format_value(abs(value), unit)
    return format_value(value, unit)


def metric_label(metric_id: str) -> str:
    return metric_id.replace("_", " ")


# ---------------------------------------------------------------------------
# denial codes: GROUP / CARC — Title


def _titled(head: str, title: str | None) -> str:
    return f"{head} — {title}" if title else head


def _code_label(pack: CodeTitleLookup, dimension: str, value: Scalar) -> str:
    """One code value with its governed title appended when the pack has one."""
    code = str(value)
    title = pack.code_title(_CODE_SYSTEM_BY_DIMENSION[dimension], code)
    prefix = {CARC_DIMENSION: "CARC ", RARC_DIMENSION: "RARC "}.get(dimension, "")
    return _titled(f"{prefix}{code}", title)


def render_code_segment(
    pack: CodeTitleLookup, values: Mapping[str, Scalar]
) -> str | None:
    """The remittance-code part of a row label, or ``None`` if the row has no
    code dimensions.

    With both halves of the pair present the result is the governed form
    ``CO / 16 — Claim/service lacks information``. With only the CARC the
    group code cannot be named, and the label says so rather than implying
    a single denial type: ``CARC 16 — ... (all adjustment groups)``. That
    suffix is not decoration — CO-16 and PI-16 carry different liability,
    and a merged row is a different number from either.
    """
    group = values.get(GROUP_CODE_DIMENSION)
    carc = values.get(CARC_DIMENSION)
    rarc = values.get(RARC_DIMENSION)
    parts: list[str] = []
    if carc is not None:
        carc_title = pack.code_title("carc", str(carc))
        if group is not None:
            parts.append(_titled(f"{group} / {carc}", carc_title))
        else:
            parts.append(
                f"{_titled(f'CARC {carc}', carc_title)} (all adjustment groups)"
            )
    elif group is not None:
        parts.append(_code_label(pack, GROUP_CODE_DIMENSION, group))
    if rarc is not None:
        parts.append(_code_label(pack, RARC_DIMENSION, rarc))
    if not parts:
        return None
    return " / ".join(parts)


def render_row_label(
    pack: CodeTitleLookup,
    dimension_columns: Sequence[str],
    values: Mapping[str, Scalar],
) -> str:
    """A dimension-value row label with codes rendered as codes.

    Non-code dimensions keep their order and their raw values (a payer name
    is its own label); code dimensions collapse into one governed segment
    appended at the end, so ``payer`` + ``group_code`` + ``carc`` reads
    ``Bluestone Mutual / CO / 16 — Claim/service lacks information``.
    """
    plain = [
        str(values[dim])
        for dim in dimension_columns
        if dim not in _CODE_SYSTEM_BY_DIMENSION and dim in values
    ]
    code_segment = render_code_segment(
        pack, {dim: values[dim] for dim in dimension_columns if dim in values}
    )
    if code_segment is not None:
        plain.append(code_segment)
    return " / ".join(plain)
