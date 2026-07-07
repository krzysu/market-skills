"""analysis.track_record — per-ticker hit-rate signal from DTP journal.

Read-side helper used by the DTP tick to size ``suggested_size_eur`` based
on a ticker's recent closed-idea track record.  Reads the parsed
``picks.json`` array in memory; no I/O, no caching, no class.

The headline output is ``multiplier`` — a float in [1.0, 3.0] that the
DTP tick multiplies into the per-source base cap.  Calibration: 50 % hit
rate = 1.0× (no signal), 100 % hit rate with 5+ samples = 3.0× (cap).
Multiplier grows linearly with hit_rate above 0.5 and asymptotically
with sample count via a min(n_closed / 5, 1.0) scale factor.
"""

from __future__ import annotations

from typing import TypedDict

MAX_MULTIPLIER = 3.0
MIN_CLOSED_DEFAULT = 3
LOOKBACK_SCANS_DEFAULT = 20


class TrackRecord(TypedDict):
    hit_rate: float
    n_closed: int
    n_hits: int
    n_misses: int
    avg_return_pct: float
    multiplier: float
    eligible: bool


def compute_track_record(
    pair: str,
    *,
    picks: list[dict],
    min_closed: int = MIN_CLOSED_DEFAULT,
    lookback_scans: int = LOOKBACK_SCANS_DEFAULT,
) -> TrackRecord:
    """Compute the track record for `pair` over the last `lookback_scans` scans.

    ``picks`` is the parsed ``picks.json`` array (list of scan records, each
    with ``ideas[]``).  The function flattens ideas for ``pair`` over the
    lookback window, counts closed outcomes, and returns the ``TrackRecord``
    dict.  Read-only; does not mutate ``picks``.
    """
    closed = []
    start = max(0, len(picks) - lookback_scans)
    for scan in picks[start:]:
        for idea in scan.get("ideas", []):
            if idea.get("pair") != pair:
                continue
            if idea.get("status") != "closed":
                continue
            verdict = idea.get("outcome_verdict")
            if verdict not in ("hit", "miss"):
                continue
            closed.append(idea)

    n_closed = len(closed)
    n_hits = sum(1 for c in closed if c.get("outcome_verdict") == "hit")
    n_misses = n_closed - n_hits

    eligible = n_closed >= min_closed

    if n_closed > 0:
        hit_rate = n_hits / n_closed
        returns = [c.get("actual_return_pct") or 0.0 for c in closed]
        avg_return_pct = sum(returns) / n_closed
    else:
        hit_rate = 0.0
        avg_return_pct = 0.0

    if eligible:
        scale = min(1.0, n_closed / 5.0)
        multiplier = 1.0 + (hit_rate - 0.5) * 4.0 * scale
        multiplier = max(1.0, min(multiplier, MAX_MULTIPLIER))
    else:
        multiplier = 1.0

    return TrackRecord(
        hit_rate=hit_rate,
        n_closed=n_closed,
        n_hits=n_hits,
        n_misses=n_misses,
        avg_return_pct=avg_return_pct,
        multiplier=multiplier,
        eligible=eligible,
    )
