# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart Edit with Anthropic's real Python library, against a local stand-in server.

Checks what the fake-client tests can't: that the library sends the request GrabbyHands
builds (extra fields, beta header, key) and hands back every block, including ones it may
not know yet. Nothing leaves this computer. Skipped when the library isn't installed.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

anthropic = pytest.importorskip("anthropic")

from grabbyhands.editor.document import Document  # noqa: E402
from grabbyhands.smartedit import session as sess  # noqa: E402
from grabbyhands.smartedit.session import Session, SmartEditError, prepare_image  # noqa: E402

KEY = "sk-ant-test-" + "x" * 40


class Server:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                if self.path.endswith("/count_tokens"):
                    status, reply = 200, {"input_tokens": 2500}
                else:
                    status, reply = outer.replies.pop(0)
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()


def message(content, stop, model="claude-opus-5-5", usage=None):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 2500, "output_tokens": 120},
    }


@pytest.fixture
def server():
    servers = []

    def make(replies):
        s = Server(replies)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def client_for(srv):
    return anthropic.Anthropic(api_key=KEY, base_url=srv.url, max_retries=0)


def test_full_turn_through_the_real_library(server):
    srv = server(
        [
            (
                200,
                message(
                    [
                        {
                            "type": "fallback",
                            "from": {"model": "claude-opus-5-5"},
                            "to": {"model": "claude-opus-4-8"},
                        },
                        {"type": "thinking", "thinking": "", "signature": "c2ln"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "add_annotations",
                            "input": {
                                "items": [
                                    {
                                        "type": "rect",
                                        "x0": 5,
                                        "y0": 5,
                                        "x1": 50,
                                        "y1": 40,
                                        "fill": "translucent",
                                    }
                                ]
                            },
                        },
                    ],
                    "tool_use",
                    model="claude-opus-4-8",
                ),
            ),
            (200, message([{"type": "text", "text": "Highlighted it."}], "end_turn")),
        ]
    )
    s = Session(client=client_for(srv), image=prepare_image(Image.new("RGB", (300, 200))))
    result = s.send(Document(), "highlight the corner")
    assert result.reply == "Highlighted it." and result.summary["added"] == 1

    calls = [r for r in srv.requests if r["path"] == "/v1/messages"]
    first = calls[0]
    headers = first["headers"]
    # older releases send x-api-key, newer ones "Authorization: Bearer"
    sent_key = headers.get("x-api-key") or headers["authorization"].removeprefix("Bearer ")
    assert sent_key == KEY
    assert first["headers"]["anthropic-beta"] == sess.FALLBACK_BETA
    body = first["body"]
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "medium"}
    assert body["cache_control"] == {"type": "ephemeral"}
    strict = {t["name"] for t in body["tools"] if t.get("strict")}
    assert "find_text" in strict and "add_annotations" not in strict
    # the second request echoes the first answer's blocks unchanged, fallback block included
    echoed = calls[1]["body"]["messages"][1]["content"]
    assert [b["type"] for b in echoed] == ["fallback", "thinking", "tool_use"]
    assert echoed[0]["from"] == {"model": "claude-opus-5-5"}
    assert echoed[1]["signature"] == "c2ln"


def test_errors_from_the_real_library(server):
    srv = server(
        [
            (
                401,
                {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}},
            ),
        ]
    )
    s = Session(client=client_for(srv), image=prepare_image(Image.new("RGB", (100, 100))))
    with pytest.raises(SmartEditError) as err:
        s.send(Document(), "x")
    assert err.value.kind == "key"


def test_rejected_strict_is_retried_without_it(server):
    srv = server(
        [
            (
                400,
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "tools.0.strict: Extra inputs are not permitted",
                    },
                },
            ),
            (200, message([{"type": "text", "text": "ok"}], "end_turn")),
        ]
    )
    s = Session(client=client_for(srv), image=prepare_image(Image.new("RGB", (100, 100))))
    s.send(Document(), "x")
    calls = [r for r in srv.requests if r["path"] == "/v1/messages"]
    assert any("strict" in t for t in calls[0]["body"]["tools"])
    assert not any("strict" in t for t in calls[1]["body"]["tools"])


def test_make_client_pins_the_official_address(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://evil.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-other")
    client = sess.make_client(KEY)
    assert str(client.base_url).rstrip("/") == sess.API_BASE
    assert client.api_key == KEY
