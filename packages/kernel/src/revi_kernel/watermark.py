"""Data watermarks and session epochs (design §2.6, §7.1).

Every investigation pins a ``DataWatermark``; every probe in a session reads
as-of the session's watermark. A mid-session data refresh is an explicit,
surfaced event: continuing re-anchored starts a new ``WatermarkEpoch``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class DataWatermark:
    """One completed warehouse load.

    ``id`` is the repository-scoped identifier (locally: a snapshot schema's
    watermark id). ``loaded_at`` is when the load finished; ``newest_data_date``
    is the newest activity date the load can see (used e.g. by days-in-AR,
    which ages to the data's own newest date for reproducibility).
    """

    id: str
    loaded_at: datetime
    newest_data_date: date

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DataWatermark.id must be non-empty")


@dataclass(frozen=True, slots=True)
class WatermarkEpoch:
    """A contiguous stretch of a session pinned to one watermark (§7.1)."""

    index: int
    watermark: DataWatermark
    started_at_turn: str | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("WatermarkEpoch.index must be >= 0")
