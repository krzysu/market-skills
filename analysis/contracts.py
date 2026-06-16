"""TypedDict contracts for L1, L2, and L3 skill return shapes."""

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


class L3Idea(TypedDict):
    pair: str
    direction: str  # "long" | "short"
    conviction: int  # 1–5
    entry_type: str  # "limit" | "market" | "stop"
    entry_price: float | None
    entry_range: NotRequired[list[float]]  # [low, high] acceptable entry window
    stop_loss: float | None
    take_profit: list[float]
    reasoning: str
    source_skills: list[str]


class L3Result(TypedDict):
    ideas: list[L3Idea]
    narrative: str
