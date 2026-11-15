# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""What a Smart Edit request costs, from the token counts the API reports.

These are estimates from Anthropic's published per-token prices on the date below. The bill
in the Anthropic Console is what counts; GrabbyHands says "≈" everywhere for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICES_AS_OF = "2026-11-14"
MTOK = 1_000_000


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_write: float  # 5-minute cache
    cache_read: float


# Anthropic's published prices (platform.claude.com/docs/en/about-claude/pricing).
PRICES: dict[str, Price] = {
    "claude-fable-5-2": Price(8.0, 40.0, 10.0, 0.20),
    "claude-opus-5-5": Price(4.0, 20.0, 5.0, 0.20),
    "claude-opus-5": Price(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": Price(5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5-5": Price(2.0, 10.0, 2.50, 0.10),
    "claude-sonnet-5": Price(2.0, 10.0, 2.50, 0.20),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.125, 0.01),
}
# Claude Haiku 5.5 costs more once a prompt is over 100,000 tokens.
LONG_PROMPT = {"claude-haiku-5-5": (100_000, Price(0.50, 2.50, 0.625, 0.05))}


def price_for(model: str, prompt_tokens: int = 0) -> Price | None:
    long = LONG_PROMPT.get(model)
    if long and prompt_tokens > long[0]:
        return long[1]
    return PRICES.get(model)


def _tokens(usage: dict, key: str) -> int:
    value = usage.get(key) or 0
    return int(value) if isinstance(value, int | float) else 0


def usage_cost(usage: dict, model: str) -> float | None:
    """Dollars for one attempt's usage, or None for a model without a known price."""
    fresh = _tokens(usage, "input_tokens")
    written = _tokens(usage, "cache_creation_input_tokens")
    read = _tokens(usage, "cache_read_input_tokens")
    out = _tokens(usage, "output_tokens")
    price = price_for(model, fresh + written + read)
    if price is None:
        return None
    return (
        fresh * price.input + written * price.cache_write + read * price.cache_read
    ) / MTOK + out * price.output / MTOK


def response_cost(response: dict, requested_model: str) -> float | None:
    """Dollars for one API response.

    When a declined request was re-run on a fallback model, ``usage.iterations`` lists each
    attempt: plain ``message`` attempts ran on the requested model, the ``fallback_message``
    one on the model that answered (``response["model"]``).
    """
    usage = response.get("usage") or {}
    served_by = response.get("model") or requested_model
    iterations = usage.get("iterations")
    if isinstance(iterations, list) and iterations:
        total = 0.0
        for entry in iterations:
            if not isinstance(entry, dict):
                continue
            model = served_by if entry.get("type") == "fallback_message" else requested_model
            cost = usage_cost(entry, model)
            if cost is None:
                return None
            total += cost
        return total
    return usage_cost(usage, served_by)


def worst_case(input_tokens: int, max_tokens: int, model: str) -> float | None:
    """Most a request could cost: all input uncached, all output tokens used."""
    price = price_for(model, input_tokens)
    if price is None:
        return None
    return (input_tokens * price.input + max_tokens * price.output) / MTOK


def format_usd(amount: float | None) -> str:
    if amount is None:
        return "?"
    if amount == 0:
        return "$0.00"
    if amount < 0.001:
        return "< $0.001"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:.2f}"
