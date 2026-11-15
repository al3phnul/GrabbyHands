# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Spending limits for Smart Edit: one per chat, one per calendar month.

The monthly total is kept in a small file (one number per month, nothing else). It only
knows what GrabbyHands spent; a hard limit in the Anthropic Console is the real backstop.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from grabbyhands.smartedit.pricing import format_usd

LEDGER_NAME = "smart-edit-spend.json"


def _month(today: datetime.date | None = None) -> str:
    return (today or datetime.date.today()).strftime("%Y-%m")


class Budget:
    def __init__(self, path: Path, chat_limit: float, month_limit: float) -> None:
        self.path = path
        self.chat_limit = chat_limit
        self.month_limit = month_limit

    # -- ledger ------------------------------------------------------------------------------
    def _load(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            k: float(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, int | float) and not isinstance(v, bool)
        }

    def _save(self, data: dict[str, float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp.replace(self.path)

    def month_spent(self, today: datetime.date | None = None) -> float:
        return self._load().get(_month(today), 0.0)

    def record(self, amount: float | None, today: datetime.date | None = None) -> None:
        if not amount or amount <= 0:
            return
        data = self._load()
        key = _month(today)
        data[key] = round(data.get(key, 0.0) + amount, 6)
        self._save(data)

    # -- checks --------------------------------------------------------------------------------
    def refusal(
        self, worst_case: float | None, chat_spent: float, today: datetime.date | None = None
    ) -> str | None:
        """Why a request whose cost could reach ``worst_case`` must not be sent, or None."""
        if worst_case is None:
            return "unknown-price"
        if self.chat_limit > 0 and chat_spent + worst_case > self.chat_limit:
            return "chat"
        if self.month_limit > 0 and self.month_spent(today) + worst_case > self.month_limit:
            return "month"
        return None

    def describe(self, chat_spent: float, today: datetime.date | None = None) -> str:
        return (
            f"chat {format_usd(chat_spent)} of {format_usd(self.chat_limit)}, "
            f"month {format_usd(self.month_spent(today))} of {format_usd(self.month_limit)}"
        )
