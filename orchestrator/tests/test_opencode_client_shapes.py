"""Pins the OpenCode HTTP API shapes the orchestrator assumes (PLAN §14).

opencode_client.py is the single OpenCode integration point; if the pinned
OpenCode version's API drifts, these tests are the tripwire. All HTTP goes
through httpx.MockTransport — no session container involved.
"""

import json

import httpx
import pytest

from app.services import opencode_client

BASE = "http://forge-session-abc123:4096"


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


class TestCreateSession:
    async def test_posts_to_session_with_title(self, httpx_mock):
        httpx_mock.set_handler(
            lambda request: httpx.Response(200, json={"id": "ses_abc", "title": "t"})
        )
        session_id = await opencode_client.create_session(BASE, title="build feature")
        assert session_id == "ses_abc"

        (request,) = httpx_mock.requests
        assert request.method == "POST"
        assert request.url.host == "forge-session-abc123"
        assert request.url.port == 4096
        assert request.url.path == "/session"
        assert body_of(request) == {"title": "build feature"}

    async def test_empty_title_sends_empty_object(self, httpx_mock):
        httpx_mock.set_handler(
            lambda request: httpx.Response(200, json={"id": "ses_xyz"})
        )
        assert await opencode_client.create_session(BASE) == "ses_xyz"
        assert body_of(httpx_mock.requests[0]) == {}

    async def test_http_error_propagates(self, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(500, json={}))
        with pytest.raises(httpx.HTTPStatusError):
            await opencode_client.create_session(BASE)


class TestSendPrompt:
    async def test_prompt_body_shape(self, httpx_mock):
        reply = {
            "info": {"id": "msg_1", "role": "assistant"},
            "parts": [{"type": "text", "text": "done!"}],
        }
        httpx_mock.set_handler(lambda request: httpx.Response(200, json=reply))

        result = await opencode_client.send_prompt(
            BASE,
            "ses_abc",
            "write a fizzbuzz",
            provider_id="forge-local",
            model_id="qwen2-5-coder-14b-instruct",
        )
        assert result == reply

        (request,) = httpx_mock.requests
        assert request.method == "POST"
        assert request.url.path == "/session/ses_abc/message"
        # The exact PromptInput schema OpenCode expects:
        assert body_of(request) == {
            "model": {
                "providerID": "forge-local",
                "modelID": "qwen2-5-coder-14b-instruct",
            },
            "parts": [{"type": "text", "text": "write a fizzbuzz"}],
        }

    async def test_prompt_error_propagates(self, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(422, json={}))
        with pytest.raises(httpx.HTTPStatusError):
            await opencode_client.send_prompt(
                BASE, "ses_abc", "x", provider_id="p", model_id="m"
            )


class TestRespondPermission:
    @pytest.mark.parametrize("answer", ["once", "always", "reject"])
    async def test_permission_response_shape(self, httpx_mock, answer):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json=True))
        await opencode_client.respond_permission(BASE, "ses_abc", "perm_42", answer)

        (request,) = httpx_mock.requests
        assert request.method == "POST"
        assert request.url.path == "/session/ses_abc/permissions/perm_42"
        assert body_of(request) == {"response": answer}


class TestAbortAndMessages:
    async def test_abort_shape(self, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json=True))
        await opencode_client.abort(BASE, "ses_abc")
        (request,) = httpx_mock.requests
        assert request.method == "POST"
        assert request.url.path == "/session/ses_abc/abort"

    async def test_list_messages_shape(self, httpx_mock):
        messages = [{"info": {"id": "msg_1"}, "parts": []}]
        httpx_mock.set_handler(lambda request: httpx.Response(200, json=messages))
        assert await opencode_client.list_messages(BASE, "ses_abc") == messages
        (request,) = httpx_mock.requests
        assert request.method == "GET"
        assert request.url.path == "/session/ses_abc/message"


class TestEventStream:
    async def test_parses_sse_data_lines_and_skips_junk(self, httpx_mock):
        raw = (
            b": connected comment\n\n"
            b'data: {"type": "message.part.updated", "properties": {"x": 1}}\n\n'
            b"data: not-json-at-all\n\n"
            b"data:\n\n"
            b'data: {"type": "session.idle"}\n\n'
        )
        httpx_mock.set_handler(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=raw
            )
        )
        events = [event async for event in opencode_client.event_stream(BASE)]
        assert events == [
            {"type": "message.part.updated", "properties": {"x": 1}},
            {"type": "session.idle"},
        ]
        (request,) = httpx_mock.requests
        assert request.method == "GET"
        assert request.url.path == "/event"


class TestExtractText:
    def test_joins_text_parts_only(self):
        message = {
            "parts": [
                {"type": "step-start"},
                {"type": "text", "text": "first"},
                {"type": "tool", "tool": "bash", "text": "IGNORED"},
                {"type": "text", "text": "second"},
            ]
        }
        assert opencode_client.extract_text(message) == "first\nsecond"

    def test_skips_empty_text_parts(self):
        message = {"parts": [{"type": "text", "text": ""}, {"type": "text", "text": "x"}]}
        assert opencode_client.extract_text(message) == "x"

    def test_missing_or_null_parts(self):
        assert opencode_client.extract_text({}) == ""
        assert opencode_client.extract_text({"parts": None}) == ""
        assert opencode_client.extract_text({"parts": []}) == ""
