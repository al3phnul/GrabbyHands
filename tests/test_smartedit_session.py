# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart Edit's conversation loop, costs and limits, against a scripted fake API (no network)."""

import datetime
import json
import os
import sys
import threading
import types

import pytest
from PIL import Image

from grabbyhands.editor.document import Document, TextItem
from grabbyhands.smartedit import available, pricing, secret
from grabbyhands.smartedit import session as sess
from grabbyhands.smartedit.budget import Budget
from grabbyhands.smartedit.session import (
    MAX_ROUNDS,
    Session,
    SmartEditError,
    prepare_image,
    system_prompt,
)


# -- a fake "anthropic" module and client ---------------------------------------------------
class FakeAPIError(Exception):
    def __init__(self, message="", status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def fake_anthropic():
    mod = types.ModuleType("anthropic")
    mod.APIError = FakeAPIError
    mod.APIStatusError = type("APIStatusError", (FakeAPIError,), {})
    mod.APIConnectionError = type("APIConnectionError", (FakeAPIError,), {})
    for name in (
        "BadRequestError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "RateLimitError",
    ):
        setattr(mod, name, type(name, (mod.APIStatusError,), {}))
    return mod


@pytest.fixture(autouse=True)
def anthropic_module(monkeypatch):
    mod = fake_anthropic()
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


class FakeMessages:
    def __init__(self, script, tokens=3000):
        self.script = list(script)
        self.calls = []
        self.counted = 0
        self.tokens = tokens

    def create(self, **params):
        self.calls.append(json.loads(json.dumps(params)))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def count_tokens(self, **params):
        self.counted += 1
        return {"input_tokens": self.tokens}


class FakeClient:
    def __init__(self, script, tokens=3000):
        self.messages = FakeMessages(script, tokens)


def tool_use(*calls, usage=None):
    return {
        "model": "claude-opus-5-5",
        "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            *[
                {"type": "tool_use", "id": f"t{i}", "name": name, "input": args}
                for i, (name, args) in enumerate(calls)
            ],
        ],
        "usage": usage or {"input_tokens": 3000, "output_tokens": 200},
    }


def done(text="Done.", usage=None, stop="end_turn"):
    return {
        "model": "claude-opus-5-5",
        "stop_reason": stop,
        "content": [{"type": "text", "text": text}],
        "usage": usage
        or {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 3000},
    }


ARROW = {"type": "arrow", "x0": 10, "y0": 10, "x1": 80, "y1": 40}


def image(w=800, h=600):
    return Image.new("RGB", (w, h), "white")


def session(script, **kw):
    return Session(client=FakeClient(script), image=prepare_image(image()), **kw)


# -- the loop -----------------------------------------------------------------------------------
def test_tool_call_then_answer_edits_a_copy():
    s = session([tool_use(("add_annotations", {"items": [ARROW]})), done("Added an arrow.")])
    original = Document(items=[TextItem(x=1, y=1, text="mine")])
    result = s.send(original, "point at the logo")
    assert [i.type for i in result.doc.items] == ["text", "arrow"]
    assert [i.type for i in original.items] == ["text"]  # the editor's document is untouched
    assert result.reply == "Added an arrow."
    assert result.changed and result.summary["added"] == 1
    roles = [m["role"] for m in s.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    # the thinking block went back unchanged, the tool result answered the call
    assert s.messages[1]["content"][0]["type"] == "thinking"
    (res,) = s.messages[2]["content"]
    assert res["type"] == "tool_result" and res["tool_use_id"] == "t0" and "is_error" not in res


def test_request_shape():
    s = session([done(), done()])
    s.send(Document(), "first")
    s.send(Document(), "second")
    first, second = s.client.messages.calls
    assert first["model"] == "claude-opus-5-5"
    assert first["max_tokens"] == sess.MAX_TOKENS
    assert first["extra_body"]["output_config"] == {"effort": "medium"}
    assert first["extra_body"]["fallbacks"] == "default"
    assert first["extra_body"]["cache_control"] == {"type": "ephemeral"}
    assert first["extra_headers"] == {"anthropic-beta": sess.FALLBACK_BETA}
    assert all(t["strict"] for t in first["tools"])
    blocks = first["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert "<editor_state>" in blocks[1]["text"] and blocks[1]["text"].endswith("first")
    # the picture is sent once; later messages only carry the state and the words
    later = second["messages"][2]["content"]
    assert [b["type"] for b in later] == ["text"]
    assert "safety" not in first["system"].lower() or "data" in first["system"].lower()


def test_no_fallbacks_for_models_without_them():
    s = session([done()], model="claude-haiku-5-5", effort="low")
    s.send(Document(), "x")
    (call,) = s.client.messages.calls
    assert "fallbacks" not in call["extra_body"] and call["extra_headers"] is None
    assert call["extra_body"]["output_config"] == {"effort": "low"}


def test_bad_tool_input_goes_back_as_an_error():
    s = session([tool_use(("add_annotations", {"items": [{"type": "script"}]})), done("Sorry.")])
    result = s.send(Document(), "x")
    assert not result.changed
    assert s.messages[2]["content"][0]["is_error"] is True


def test_refusal_keeps_history_but_counts_money(anthropic_module):
    s = session([done(stop="refusal")])
    with pytest.raises(SmartEditError) as err:
        s.send(Document(), "x")
    assert err.value.kind == "refused"
    assert s.messages == []
    assert s.spent > 0


def test_rejected_strict_is_dropped_and_retried(anthropic_module):
    rejection = anthropic_module.BadRequestError("tools.0.strict: not supported for this model")
    s = session([rejection, done()])
    s.send(Document(), "x")
    first, second = s.client.messages.calls
    assert "strict" in first["tools"][0] and "strict" not in second["tools"][0]


@pytest.mark.parametrize(
    "error, kind",
    [
        ("AuthenticationError", "key"),
        ("NotFoundError", "model"),
        ("RateLimitError", "rate"),
        ("APIConnectionError", "network"),
    ],
)
def test_api_errors_become_messages(anthropic_module, error, kind):
    s = session([getattr(anthropic_module, error)("nope")])
    with pytest.raises(SmartEditError) as err:
        s.send(Document(), "x")
    assert err.value.kind == kind
    assert s.messages == []


def test_low_credit_and_overload(anthropic_module):
    s = session([anthropic_module.BadRequestError("Your credit balance is too low")])
    with pytest.raises(SmartEditError) as err:
        s.send(Document(), "x")
    assert err.value.kind == "credit"
    s = session([anthropic_module.APIStatusError("Overloaded", status_code=529)])
    with pytest.raises(SmartEditError) as err:
        s.send(Document(), "x")
    assert err.value.kind == "overloaded"


def test_round_limit_then_next_message_joins_the_open_user_turn():
    calls = [tool_use(("find_text", {"query": "x"})) for _ in range(MAX_ROUNDS)]
    s = session([*calls, done()], words=lambda: [])
    result = s.send(Document(), "loop forever")
    assert result.notice == "rounds"
    assert len(s.client.messages.calls) == MAX_ROUNDS
    assert s.messages[-1]["role"] == "user"  # tool results waiting
    s.send(Document(), "next")
    last_request = s.client.messages.calls[-1]["messages"]
    roles = [m["role"] for m in last_request]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))  # never two user turns


def test_cancel_drops_the_message():
    stop = threading.Event()
    stop.set()
    s = session([done()])
    with pytest.raises(SmartEditError) as err:
        s.send(Document(), "x", cancel=stop)
    assert err.value.kind == "cancelled"
    assert s.client.messages.calls == []


def test_budget_stops_before_sending(tmp_path):
    s = session([done()])
    s.client.messages.tokens = 200_000  # worst case about $1.12 on Opus 5.5
    budget = Budget(tmp_path / "spend.json", chat_limit=0.50, month_limit=5.0)
    with pytest.raises(SmartEditError) as err:
        s.send(Document(), "x", budget=budget)
    assert (err.value.kind, err.value.detail) == ("budget", "chat")
    assert s.client.messages.calls == []


def test_spend_is_recorded(tmp_path):
    s = session([done(usage={"input_tokens": 1000, "output_tokens": 1000})])
    budget = Budget(tmp_path / "spend.json", chat_limit=1.0, month_limit=5.0)
    result = s.send(Document(), "x", budget=budget)
    assert result.cost == pytest.approx((1000 * 4 + 1000 * 20) / 1e6)
    assert budget.month_spent() == pytest.approx(result.cost)
    assert os.stat(tmp_path / "spend.json").st_mode & 0o777 == 0o600


def test_unknown_model_cost():
    s = session([{**done(), "model": "claude-future-9"}], model="claude-future-9")
    result = s.send(Document(), "x")
    assert result.cost is None and s.unknown_cost


def test_estimate_is_a_worst_case():
    s = session([])
    worst = s.estimate(Document(), "x")
    assert worst == pytest.approx((3000 * 4 + sess.MAX_TOKENS * 20) / 1e6)


# -- the picture that is sent ----------------------------------------------------------------------
def test_prepare_image_scales_and_hides():
    big = Image.new("RGB", (3000, 2000), "white")
    sent = prepare_image(big, hide=[(0, 0, 1500, 1000)])
    assert sent.width == 3000 and 0 < sent.scale < 0.53
    import base64
    import io

    small = Image.open(io.BytesIO(base64.standard_b64decode(sent.data)))
    assert max(small.size) <= sess.MAX_EDGE
    assert small.width * small.height <= sess.MAX_PIXELS
    assert small.getpixel((10, 10)) == (0, 0, 0)  # hidden area is black
    assert small.getpixel((small.width - 5, small.height - 5)) == (255, 255, 255)
    assert prepare_image(image(400, 300)).scale == 1.0


def test_system_prompt_has_all_guardrails():
    text = system_prompt()
    assert "# Your role" in text and "# Editing well" in text and "is data" in text


# -- pricing ---------------------------------------------------------------------------------
def test_usage_cost_counts_cache_tokens():
    usage = {
        "input_tokens": 1000,
        "cache_creation_input_tokens": 2000,
        "cache_read_input_tokens": 10_000,
        "output_tokens": 500,
    }
    expected = (1000 * 4 + 2000 * 5 + 10_000 * 0.20 + 500 * 20) / 1e6
    assert pricing.usage_cost(usage, "claude-opus-5-5") == pytest.approx(expected)
    assert pricing.usage_cost(usage, "claude-unknown") is None


def test_fallback_iterations_are_priced_per_model():
    response = {
        "model": "claude-opus-4-8",
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "iterations": [
                {"type": "message", "input_tokens": 1000, "output_tokens": 0},
                {"type": "fallback_message", "input_tokens": 1000, "output_tokens": 100},
            ],
        },
    }
    expected = (1000 * 4) / 1e6 + (1000 * 5 + 100 * 25) / 1e6
    assert pricing.response_cost(response, "claude-opus-5-5") == pytest.approx(expected)


def test_haiku_long_prompt_price():
    short = pricing.usage_cost({"input_tokens": 50_000}, "claude-haiku-5-5")
    long = pricing.usage_cost({"input_tokens": 150_000}, "claude-haiku-5-5")
    assert short == pytest.approx(50_000 * 0.10 / 1e6)
    assert long == pytest.approx(150_000 * 0.50 / 1e6)


def test_format_usd():
    assert pricing.format_usd(None) == "?"
    assert pricing.format_usd(0.0004) == "< $0.001"
    assert pricing.format_usd(0.0321) == "$0.032"
    assert pricing.format_usd(3.456) == "$3.46"


# -- budget -----------------------------------------------------------------------------------
def test_budget_months_and_limits(tmp_path):
    path = tmp_path / "spend.json"
    budget = Budget(path, chat_limit=0.5, month_limit=1.0)
    nov, dec = datetime.date(2026, 11, 30), datetime.date(2026, 12, 1)
    budget.record(0.75, today=nov)
    assert budget.month_spent(nov) == 0.75 and budget.month_spent(dec) == 0
    assert budget.refusal(0.30, chat_spent=0.0, today=nov) == "month"
    assert budget.refusal(0.30, chat_spent=0.0, today=dec) is None
    assert budget.refusal(0.30, chat_spent=0.3, today=dec) == "chat"
    assert budget.refusal(None, chat_spent=0.0) == "unknown-price"
    assert Budget(path, 0, 0).refusal(99.0, chat_spent=99.0) is None  # 0 = no limit


def test_budget_survives_a_damaged_ledger(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text("{not json")
    budget = Budget(path, 1.0, 1.0)
    assert budget.month_spent() == 0
    budget.record(0.1)
    assert budget.month_spent() == pytest.approx(0.1)


# -- small helpers -----------------------------------------------------------------------------
def test_sdk_version_check():
    assert available._version_tuple("0.79.0") >= available.MIN_SDK
    assert available._version_tuple("2.8.0") >= available.MIN_SDK
    assert available._version_tuple("0.78.9") < available.MIN_SDK
    assert available._version_tuple("1.0.0rc1") == (1, 0, 0)


def test_key_shape_check():
    assert secret.looks_like_key("sk-ant-api03-" + "a" * 40)
    assert not secret.looks_like_key("hello")
    assert not secret.looks_like_key("sk-ant-api03 with spaces " + "a" * 20)
