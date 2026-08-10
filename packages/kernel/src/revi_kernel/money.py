"""Exact money: integer cents, ``Decimal`` parsing, ``ROUND_HALF_UP``.

Rules, each guarded by a test that fails if it is "simplified" away:

- All monetary amounts are ``int`` cents. No monetary float exists anywhere.
- Dollar strings/Decimals convert with ``ROUND_HALF_UP`` (half a cent rounds
  away from zero — ``"0.005"`` → 1¢, ``"-0.005"`` → -1¢).
- ``float`` input is rejected outright: binary floats cannot represent most
  decimal dollar values, so a silent cast loses cents, and the loss is
  directionally biased rather than cancelling out.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)


def dollars_to_cents(value: str | Decimal | int) -> int:
    """Convert a dollar amount to integer cents with ROUND_HALF_UP.

    Accepts a decimal string (``"123.456"``), a ``Decimal``, or an ``int``
    number of whole dollars. ``float`` is deliberately unsupported.
    """
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        raise TypeError("bool is not a monetary amount")
    if isinstance(value, float):  # pragma: no cover - typing forbids, runtime guards
        raise TypeError(
            "float dollar amounts are forbidden (binary floats lose cents); "
            "pass a str or Decimal instead"
        )
    if isinstance(value, int):
        return value * 100
    if isinstance(value, str):
        try:
            value = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal amount: {value!r}") from exc
    quantized = (value * _HUNDRED).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(quantized)


def cents_to_dollars(cents: int) -> Decimal:
    """Exact ``Decimal`` dollar value for integer cents."""
    return (Decimal(cents) * _CENT).quantize(_CENT)


def format_cents(cents: int) -> str:
    """Human dollar string, e.g. ``-1234550`` → ``"-$12,345.50"``."""
    sign = "-" if cents < 0 else ""
    dollars = cents_to_dollars(abs(cents))
    return f"{sign}${dollars:,.2f}"
