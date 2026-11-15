# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""One Smart Edit conversation: requests to Claude and the loop that applies its tool calls.

A message is handled on a copy of the document; the editor swaps the copy in as one undo
step when the message is done, or drops it if the message fails or is stopped. Requests go
only to Anthropic's API with the key the person entered. The client is passed in, so tests
use a fake and nothing in the test suite goes online.

Runs in a worker thread: no GTK in here.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from grabbyhands.editor.document import Document
from grabbyhands.smartedit import pricing
from grabbyhands.smartedit.budget import Budget
from grabbyhands.smartedit.tools import Context, apply_call, describe, tool_definitions

API_BASE = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high")
MAX_TOKENS = 16000
MAX_ROUNDS = 8
MAX_EDGE = 1568
MAX_PIXELS = 1_150_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side fallbacks ("default" picks the fallback by refusal reason).
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-")
PROMPT_FILES = ("role.md", "editing.md", "safety.md")
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


class SmartEditError(Exception):
    """Something the person should be told. ``kind`` picks the message the panel shows."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


@dataclass
class TurnResult:
    doc: Document
    reply: str
    summary: dict
    cost: float | None
    changed: bool
    notice: str = ""  # "rounds" when stopped at the tool-round limit, "max_tokens", ...


@dataclass
class SentImage:
    data: str  # base64 PNG
    scale: float  # sent pixels per original pixel
    width: int  # original size
    height: int


def system_prompt() -> str:
    return "\n\n".join((PROMPT_DIR / name).read_text("utf-8").strip() for name in PROMPT_FILES)


def prepare_image(image: Image.Image, hide: list[tuple[int, int, int, int]] = ()) -> SentImage:
    """The copy of the screenshot that is sent: scaled to the model's image size, with the
    boxes in ``hide`` (personal details found on this computer) blacked out."""
    width, height = image.size
    scale = min(1.0, MAX_EDGE / max(width, height), math.sqrt(MAX_PIXELS / (width * height)))
    picture = image.convert("RGB")
    if hide:
        picture = picture.copy()
        draw = ImageDraw.Draw(picture)
        for x0, y0, x1, y1 in hide:
            draw.rectangle((x0, y0, x1, y1), fill="black")
    if scale < 1.0:
        size = (max(1, math.floor(width * scale)), max(1, math.floor(height * scale)))
        picture = picture.resize(size, Image.Resampling.LANCZOS)
        scale = picture.width / width
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG", optimize=True)
    return SentImage(
        base64.standard_b64encode(buffer.getvalue()).decode("ascii"), scale, width, height
    )


def make_client(api_key: str):
    """Anthropic's client with the person's key and the official address, set explicitly so
    environment variables can't swap either."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key, base_url=API_BASE, max_retries=2, timeout=180.0)


def _as_dict(response) -> dict:
    if isinstance(response, dict):
        return response
    return response.model_dump(mode="json", by_alias=True, exclude_unset=True)


def _translate(err: Exception) -> SmartEditError:
    """The SDK's typed errors -> what the panel should say."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return SmartEditError("unknown", str(err))
    message = getattr(err, "message", "") or str(err)
    if isinstance(err, anthropic.AuthenticationError):
        return SmartEditError("key")
    if isinstance(err, anthropic.PermissionDeniedError):
        return SmartEditError("permission", message)
    if isinstance(err, anthropic.NotFoundError):
        return SmartEditError("model", message)
    if isinstance(err, anthropic.RateLimitError):
        return SmartEditError("rate")
    if isinstance(err, anthropic.BadRequestError):
        if "credit balance" in message.lower():
            return SmartEditError("credit")
        return SmartEditError("request", message)
    if isinstance(err, anthropic.APIStatusError):
        if getattr(err, "status_code", 0) == 529:
            return SmartEditError("overloaded")
        return SmartEditError("server", message)
    if isinstance(err, anthropic.APIConnectionError):
        return SmartEditError("network")
    return SmartEditError("unknown", message)


@dataclass
class Session:
    client: object
    image: SentImage
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    words: object = None  # () -> list[ocr.Word], or None without text recognition
    strict: bool = True
    use_fallbacks: bool = True
    use_effort: bool = True
    messages: list[dict] = field(default_factory=list)
    spent: float = 0.0
    unknown_cost: bool = False  # a response whose cost couldn't be worked out
    _system: str = field(default_factory=system_prompt)

    # -- request pieces -------------------------------------------------------------------------
    def _context(self) -> Context:
        return Context(
            width=self.image.width,
            height=self.image.height,
            scale=self.image.scale,
            words=self.words,
        )

    def _user_turn(self, doc: Document, text: str) -> list[dict]:
        blocks: list[dict] = []
        if not self.messages:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": self.image.data,
                    },
                }
            )
        state = json.dumps(describe(doc, self._context()), ensure_ascii=False, sort_keys=True)
        blocks.append(
            {"type": "text", "text": f"<editor_state>\n{state}\n</editor_state>\n\n{text}"}
        )
        return blocks

    def _append_user(self, messages: list[dict], blocks: list[dict]) -> None:
        if messages and messages[-1]["role"] == "user":
            messages[-1] = {"role": "user", "content": [*messages[-1]["content"], *blocks]}
        else:
            messages.append({"role": "user", "content": blocks})

    def _base(self, messages: list[dict]) -> dict:
        return {
            "model": self.model,
            "system": self._system,
            "tools": tool_definitions(self.strict),
            "messages": messages,
        }

    def _extras(self) -> tuple[dict, dict]:
        body: dict = {"cache_control": {"type": "ephemeral"}}
        headers: dict = {}
        if self.use_effort and self.effort in EFFORTS:
            body["output_config"] = {"effort": self.effort}
        if self.use_fallbacks and self.model.startswith(FALLBACK_MODELS):
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = FALLBACK_BETA
        return body, headers

    def _input_tokens(self, messages: list[dict]) -> int:
        """Tokens a request would send (free to ask). A rough count if counting fails."""
        try:
            counted = self.client.messages.count_tokens(**self._base(messages))
            return int(_as_dict(counted)["input_tokens"])
        except Exception:  # noqa: BLE001 (the estimate must never block a request by itself)
            text = json.dumps(self._base(messages)).replace(self.image.data, "")
            pixels = (self.image.width * self.image.scale) * (self.image.height * self.image.scale)
            return len(text) // 3 + int(pixels / 750) + 1000

    def _create(self, messages: list[dict]) -> dict:
        """One request. Drops an optional feature the API rejects and tries once more."""
        import anthropic

        for _ in range(4):
            body, headers = self._extras()
            try:
                response = self.client.messages.create(
                    **self._base(messages),
                    max_tokens=MAX_TOKENS,
                    extra_body=body,
                    extra_headers=headers or None,
                )
                return _as_dict(response)
            except anthropic.BadRequestError as err:
                text = (getattr(err, "message", "") or str(err)).lower()
                if self.strict and "strict" in text:
                    self.strict = False
                elif self.use_fallbacks and "fallback" in text:
                    self.use_fallbacks = False
                elif self.use_effort and "effort" in text:
                    self.use_effort = False
                else:
                    raise _translate(err) from err
            except anthropic.APIError as err:
                raise _translate(err) from err
        raise SmartEditError("request", "the request was rejected")

    # -- public ---------------------------------------------------------------------------------
    def estimate(self, doc: Document, text: str) -> float | None:
        """Worst-case cost of the next message's first request."""
        messages = copy.deepcopy(self.messages)
        self._append_user(messages, self._user_turn(doc, text))
        return pricing.worst_case(self._input_tokens(messages), MAX_TOKENS, self.model)

    def send(
        self,
        doc: Document,
        text: str,
        budget: Budget | None = None,
        cancel: threading.Event | None = None,
    ) -> TurnResult:
        """Handle one message. The conversation only keeps it if it completes; on an error or
        a stop the history is as before (money already spent is still counted)."""
        cancel = cancel or threading.Event()
        work = Document.from_dict(doc.to_dict())
        ctx = self._context()
        messages = copy.deepcopy(self.messages)
        self._append_user(messages, self._user_turn(work, text))
        cost: float | None = 0.0
        reply = ""
        notice = ""
        for round_no in range(MAX_ROUNDS):
            if cancel.is_set():
                raise SmartEditError("cancelled")
            if budget is not None:
                worst = pricing.worst_case(self._input_tokens(messages), MAX_TOKENS, self.model)
                reason = budget.refusal(worst, self.spent)
                if reason:
                    raise SmartEditError("budget", reason)
            response = self._create(messages)
            spent = pricing.response_cost(response, self.model)
            if spent is None:
                self.unknown_cost = True
                cost = None
            else:
                self.spent += spent
                if cost is not None:
                    cost += spent
                if budget is not None:
                    budget.record(spent)
            if cancel.is_set():
                raise SmartEditError("cancelled")
            stop = response.get("stop_reason")
            if stop == "refusal":
                raise SmartEditError("refused")
            content = response.get("content") or []
            messages.append({"role": "assistant", "content": content})
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if any(t.strip() for t in texts):
                reply = "\n".join(t.strip() for t in texts if t.strip())
            calls = [b for b in content if b.get("type") == "tool_use"]
            if stop == "tool_use" and calls:
                results = []
                for block in calls:
                    result = apply_call(work, block.get("name", ""), block.get("input"), ctx)
                    entry = {
                        "type": "tool_result",
                        "tool_use_id": block.get("id"),
                        "content": result.content,
                    }
                    if result.is_error:
                        entry["is_error"] = True
                    results.append(entry)
                messages.append({"role": "user", "content": results})
                if round_no == MAX_ROUNDS - 1:
                    notice = "rounds"
                continue
            if stop == "max_tokens":
                notice = "max_tokens"
            break
        self.messages = messages
        return TurnResult(work, reply, ctx.summary(), cost, ctx.changed, notice)
