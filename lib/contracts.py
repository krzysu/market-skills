"""TypedDict contracts for L1 and L2 skill return shapes."""

from typing import Any, NotRequired, TypedDict


class L2Pattern(TypedDict):
    present: bool
    confidence: int
    max_confidence: int
    classification: str | None
    type: str


class L2Signal(TypedDict):
    present: bool
    weight: float


class L2Result(TypedDict):
    pattern: L2Pattern
    signals: dict[str, L2Signal]
    input_scores: dict[str, Any]
    narrative: str


class L1Result(TypedDict):
    """Minimal shared fields across all L1 skills. Skill-specific keys are added as extras."""

    current_price: NotRequired[float | None]
    score: NotRequired[int | None]
    signal: NotRequired[str | None]
    zone: NotRequired[str | None]
