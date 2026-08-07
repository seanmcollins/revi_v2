"""Evidence grades and the grade law (design §5.3, §5.5).

Strength ordering (strongest → weakest):

    DIRECT > DERIVED > PROXY > DISCOVERY > UNAVAILABLE

- DIRECT: the field explicitly represents the concept.
- DERIVED: deterministically calculated from validated fields.
- PROXY: correlated with the concept but does not prove it.
- DISCOVERY: evidence involving uncertified catalog fields (design §2.3 —
  scoping over uncertified fields downgrades the entire chain).
- UNAVAILABLE: no adequate measurement exists.

**The grade law:** every transform output carries the weakest grade among its
inputs. Proxy evidence cannot launder into a certified conclusion through
arithmetic.
"""

from __future__ import annotations

from enum import StrEnum


class EvidenceGrade(StrEnum):
    DIRECT = "direct"
    DERIVED = "derived"
    PROXY = "proxy"
    DISCOVERY = "discovery"
    UNAVAILABLE = "unavailable"

    @property
    def strength(self) -> int:
        return _STRENGTH[self]


_STRENGTH: dict[EvidenceGrade, int] = {
    EvidenceGrade.DIRECT: 4,
    EvidenceGrade.DERIVED: 3,
    EvidenceGrade.PROXY: 2,
    EvidenceGrade.DISCOVERY: 1,
    EvidenceGrade.UNAVAILABLE: 0,
}


def min_grade(first: EvidenceGrade, *rest: EvidenceGrade) -> EvidenceGrade:
    """The weakest grade among the inputs (the grade law)."""
    weakest = first
    for grade in rest:
        if grade.strength < weakest.strength:
            weakest = grade
    return weakest
