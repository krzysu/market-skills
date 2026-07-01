"""ExecutionProvider Protocol + Intent / FillConfirmation TypedDicts.

Mirrors the data-provider split in `analysis/providers/base.py:Provider`. Data
providers fetch OHLC; execution providers place orders. They are intentionally
separate interfaces — the same registry pattern works for both, and either
side can be implemented independently (e.g. a read-only data vendor with no
execution, or a paper-style risk engine with no live execution).

Layer contract
--------------

::

    L3 strategy --emits--> Intent  --validated by--> Risk.vet
                                                |
                                                v
                                         ExecutionProvider.place_order
                                                |
                                                v
                                         FillConfirmation
                                                |
                                                v
                                         portfolio-mgmt add_transaction

`Intent` is the single shape consumed by both Risk vetting and Execution
placement. Risk sets ``status`` ("APPROVED" / "SCALED" / "REJECT") before
handing the Intent downstream; if Risk is not in the call chain, the
execution provider treats every Intent as APPROVED by default.
"""

from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable


class BracketSpec(TypedDict, total=False):
    """Open + stop + TP bundled for a single perps entry.

    Perps intents place a bracket of three orders: a market open, a
    protective stop, and a take-profit. Spot orders ignore this field.
    ``stop_loss`` and ``take_profit`` are both required when a bracket is
    supplied. ``entry_price`` is reserved for limit-open variants (the
    perps adapter currently opens at market).
    """

    stop_loss: float
    take_profit: float
    entry_price: NotRequired[float | None]


class BracketFill(TypedDict, total=False):
    """Per-order IDs from a perps bracket fill.

    Populated by perps providers on successful bracket submission. The
    open-order id is mirrored on ``FillConfirmation.order_id``; this
    nested object carries the protective stop and take-profit ids. The
    full submit envelope still lands in ``FillConfirmation.raw``.
    """

    open_order_id: str
    stop_order_id: str
    take_profit_order_id: str


class Intent(TypedDict):
    """Single trade request. One Intent = one order on one venue.

    Fields:
      intent_id     — caller-generated UUID. Plumbed through to the venue's
                     ``cl-ord-id`` for idempotent retries (Kraken supports
                     ``--cl-ord-id``; HL SDK has its own equivalent).
      pair          — venue-native pair symbol, e.g. ``"BTCUSD"``. Provider
                     resolution uses the Intent's ``venue`` field, not a
                     ``provider:`` prefix, because intents are user-/strategy-
                     authored and the venue is an explicit choice. To route to
                     a non-default venue, set ``venue``.
      venue         — execution provider name (``"kraken"``, ``"kraken-perps"``,
                     ``"hl"``). Required.
      side          — ``"buy"`` or ``"sell"``.
      order_type    — one of ``"market"``, ``"limit"``, ``"stop-loss"``,
                     ``"take-profit"``, ``"stop-loss-limit"``,
                     ``"take-profit-limit"``, ``"trailing-stop"``,
                     ``"trailing-stop-limit"``. Provider-specific extras belong
                     in the ``extras`` dict.
      volume        — base-asset quantity (e.g. 0.01 BTC). Always positive.
      limit_price   — required for non-market orders. Trigger price for
                     stop/take-profit variants; primary price for limit.
      stop_price    — secondary trigger price for ``-limit`` order variants.
      time_in_force — optional. ``"GTC"`` / ``"IOC"`` / ``"FOK"`` / ``"GTD"``.
      deadline      — optional. RFC3339 deadline for matching-engine arrival
                     (provider-dependent; Kraken accepts ``--deadline``).

    Perps-only fields (ignored by spot providers):
      leverage      — integer leverage multiplier (1x–50x typical). Required
                     by perps providers; ignored by spot.
      bracket       — :class:`BracketSpec` with ``stop_loss`` + ``take_profit``.
                     Required by perps providers for full-bracket submission;
                     ignored by spot.

    Risk fields (set by Risk.vet, default APPROVED):
      status         — ``"APPROVED"`` / ``"SCALED"`` / ``"REJECT"``.
      reject_reason  — populated when status != APPROVED.
      scaled_volume  — populated when status == SCALED; execution uses this
                       value instead of ``volume``.

    Provenance (free-form, persisted into portfolio-mgmt notes):
      thesis, source_skills, conviction, strategy, notes.
    """

    intent_id: str
    pair: str
    venue: str
    side: str
    order_type: str
    volume: float

    limit_price: NotRequired[float | None]
    stop_price: NotRequired[float | None]
    time_in_force: NotRequired[str]
    deadline: NotRequired[str]

    leverage: NotRequired[int | None]
    bracket: NotRequired[BracketSpec | None]

    status: NotRequired[str]
    reject_reason: NotRequired[str | None]
    scaled_volume: NotRequired[float | None]

    thesis: NotRequired[str]
    source_skills: NotRequired[list[str]]
    conviction: NotRequired[int]
    strategy: NotRequired[str]
    notes: NotRequired[dict[str, Any]]

    extras: NotRequired[dict[str, Any]]  # venue-specific kwargs (e.g. position_value)

    decision_decoration: NotRequired[dict[str, Any] | None]
    """Optional decision-context augmentation contributed by the caller
    (the LLM, which has the risk verdict + macro snapshot the execution
    skill doesn't fetch). Forwarded to
    ``analysis.decision.build_decision_context_from_idea`` and merged
    into the auto-built ``DecisionContext`` written to the ``decisions``
    table. Recognised keys: ``regime_label``, ``regime_fng``,
    ``regime_btc_dominance``, ``regime_divergence``, ``macro_signals``,
    ``risk_status``, ``risk_position_size_pct``, ``risk_concerns``,
    ``override_from_suggestion``, ``override_field``, ``override_reason``.
    Keys outside this set are passed through unchanged so future
    fields don't require a schema bump."""


class FillConfirmation(TypedDict):
    """Result of a successful (or terminal-failed) order placement.

    Field semantics:
      intent_id          — echoes the Intent.intent_id.
      order_id           — venue-native order ID (Kraken txid, HL oid). For a
                           perps bracket fill this is the open-order id;
                           ``bracket`` carries stop / TP ids.
      cl_ord_id          — client order ID echoed back (when supported).
      pair, side, order_type — echoes the Intent.
      requested_volume   — what was sent.
      filled_volume      — what the venue reported as filled. ``0.0`` for
                           open/cancelled orders.
      fill_price         — weighted-average fill price for partials; ``None``
                           when no fills yet.
      cost_quote         — ``filled_volume * fill_price`` when fully filled;
                           ``None`` otherwise.
      fee, fee_currency  — venue-reported fees.
      status             — terminal status: ``"filled"`` / ``"partial"`` /
                           ``"open"`` / ``"rejected"`` / ``"cancelled"`` /
                           ``"expired"`` / ``"error"``.
      reason             — human-readable status detail; populated for rejected
                           and error.
      timestamp          — ISO 8601 UTC.
      venue              — provider name (echoes Intent.venue).
      bracket            — :class:`BracketFill` (perps only) with per-order
                           IDs from the bracket submission.
      raw                — raw venue response, for debugging / audit.
    """

    intent_id: str
    order_id: str
    cl_ord_id: NotRequired[str | None]
    pair: str
    side: str
    order_type: str
    requested_volume: float
    filled_volume: float
    fill_price: NotRequired[float | None]
    cost_quote: NotRequired[float | None]
    fee: NotRequired[float]
    fee_currency: NotRequired[str]
    status: str
    reason: NotRequired[str]
    timestamp: str
    venue: str
    bracket: NotRequired[BracketFill | None]
    raw: NotRequired[dict[str, Any]]


@runtime_checkable
class ExecutionProvider(Protocol):
    """Order-placement interface. Implementations live in
    ``analysis/providers/execution_<venue>.py`` and register in
    ``_EXECUTION_REGISTRY`` below.
    """

    name: str

    def supports(self, pair: str, venue: str | None = None) -> bool:
        """Return True if this provider can serve ``pair`` on the named venue.

        ``venue`` is the Intent.venue field; some providers only handle their
        own venue so the check is mostly defensive.
        """
        ...

    def place_order(self, intent: Intent, *, wait: bool = True, timeout_s: float = 5.0) -> FillConfirmation:
        """Submit the order and (by default) block until terminal status.

        ``wait=False`` returns immediately with ``status="submitted"`` if the
        venue supports asynchronous submission, or ``status="open"`` if the
        order is resting on the book. Use ``wait=True`` (default) for
        synchronous semantics — the LLM gets a populated fill price before
        narrating the result and recording it to portfolio-mgmt.
        """
        ...

    def get_balance(self) -> dict[str, float]:
        """Cash balances keyed by currency code (e.g. ``{"USD": 1234.5}``).

        Implementations should map venue-native asset codes to canonical
        ISO-ish codes where possible (Kraken's ``ZUSD`` -> ``USD``,
        ``XXBT`` -> ``BTC``).
        """
        ...

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Open orders on this venue, venue-native shape.

        Returned list items should at minimum include ``order_id``, ``pair``,
        ``side``, ``volume``, ``order_type``, and ``limit_price`` (when
        applicable). Extra venue-specific fields are fine.
        """
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success, False on failure
        (already filled, already cancelled, or order not found)."""
        ...


_EXECUTION_REGISTRY: dict[str, ExecutionProvider] = {}


def register_execution_provider(provider: ExecutionProvider) -> None:
    """Register an ExecutionProvider under its ``name`` attribute. Idempotent."""
    _EXECUTION_REGISTRY[provider.name] = provider


def get_execution_provider(venue: str) -> ExecutionProvider:
    """Resolve a registered ExecutionProvider by venue name.

    Raises ``ValueError`` for unknown venues — the caller can catch and route
    to a default (e.g. ``kraken``) if that is its convention.
    """
    provider = _EXECUTION_REGISTRY.get(venue)
    if provider is None:
        raise ValueError(
            f"Unknown execution venue: {venue!r}. "
            f"Registered: {sorted(_EXECUTION_REGISTRY)}. "
            "Register via register_execution_provider()."
        )
    return provider


def registered_venues() -> list[str]:
    """Names of all registered execution providers (insertion order)."""
    return list(_EXECUTION_REGISTRY)


def validate_intent(intent: dict[str, Any]) -> Intent:
    """Shape-check an intent dict and return the validated version.

    Raises ``ValueError`` for missing required fields or invalid enums.
    Pure validation — no network or state.

    Side validation (presence of ``venue``, status enum, etc.) belongs here.
    Provider-specific validation lives in the provider itself.
    """
    if not isinstance(intent, dict):
        raise ValueError(f"Intent must be a dict, got {type(intent).__name__}")

    required = ("intent_id", "pair", "venue", "side", "order_type", "volume")
    missing = [k for k in required if k not in intent]
    if missing:
        raise ValueError(f"Intent missing required fields: {missing}")

    if intent["side"] not in ("buy", "sell"):
        raise ValueError(f"Intent.side must be 'buy' or 'sell', got {intent['side']!r}")

    valid_types = (
        "market",
        "limit",
        "stop-loss",
        "take-profit",
        "stop-loss-limit",
        "take-profit-limit",
        "trailing-stop",
        "trailing-stop-limit",
    )
    if intent["order_type"] not in valid_types:
        raise ValueError(f"Intent.order_type must be one of {valid_types}, got {intent['order_type']!r}")

    if not isinstance(intent["volume"], (int, float)) or intent["volume"] <= 0:
        raise ValueError(f"Intent.volume must be a positive number, got {intent['volume']!r}")

    if intent["order_type"] != "market":
        lp = intent.get("limit_price")
        if lp is None or not isinstance(lp, (int, float)) or lp <= 0:
            raise ValueError(f"Intent.limit_price required and must be > 0 for order_type={intent['order_type']!r}")

    # Perps-only validation. leverage must be a positive int when present;
    # bracket must have both stop_loss and take_profit when present.
    if intent.get("leverage") is not None:
        lev = intent["leverage"]
        if not isinstance(lev, int) or isinstance(lev, bool) or lev <= 0:
            raise ValueError(f"Intent.leverage must be a positive int, got {lev!r}")
    if intent.get("bracket") is not None:
        bracket = intent["bracket"]
        if not isinstance(bracket, dict):
            raise ValueError(f"Intent.bracket must be a dict, got {type(bracket).__name__}")
        for key in ("stop_loss", "take_profit"):
            v = bracket.get(key)
            if v is None or not isinstance(v, (int, float)) or v <= 0:
                raise ValueError(f"Intent.bracket.{key} required and must be > 0, got {v!r}")

    status = intent.get("status") or "APPROVED"
    if status not in ("APPROVED", "SCALED", "REJECT"):
        raise ValueError(f"Intent.status must be APPROVED|SCALED|REJECT, got {status!r}")
    intent = {**intent, "status": status}

    if status == "REJECT" and not intent.get("reject_reason"):
        raise ValueError("Intent.reject_reason required when status == REJECT")

    if status == "SCALED" and intent.get("scaled_volume") is None:
        raise ValueError("Intent.scaled_volume required when status == SCALED")

    return intent  # type: ignore[return-value]


__all__ = [
    "BracketFill",
    "BracketSpec",
    "ExecutionProvider",
    "FillConfirmation",
    "Intent",
    "get_execution_provider",
    "register_execution_provider",
    "registered_venues",
    "validate_intent",
]
