"""OpenAI-compatible streaming model client."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import aiohttp.web as web
import pytest

from harness_config_manager.agent import OpenAICompatibleModelClient


async def _start(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


@pytest.fixture
def openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1")


def test_model_client_streams_text_and_tool_calls(openai_env) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert body["stream"] is True
        assert request.headers["Authorization"] == "Bearer test-key"
        chunks = [
            {"choices": [{"delta": {"content": "hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "call-1",
                "function": {"name": "read_", "arguments": "{\"pa"},
            }]}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": "th\":\"a.txt\"}"},
            }]}}]},
        ]
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for chunk in chunks:
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def scenario() -> None:
        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        runner, base = await _start(app)
        try:
            monkeypatch_base = base
            import os
            old = os.environ["OPENAI_BASE_URL"]
            os.environ["OPENAI_BASE_URL"] = monkeypatch_base
            try:
                deltas: list[str] = []

                async def on_delta(text: str) -> None:
                    deltas.append(text)

                result = await OpenAICompatibleModelClient().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                    model="openai/test-model",
                    stream_delta=on_delta,
                )
            finally:
                os.environ["OPENAI_BASE_URL"] = old
        finally:
            await runner.cleanup()
        assert result.streamed is True
        assert result.content == "hello"
        assert deltas == ["hel", "lo"]
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call.id == "call-1"
        assert call.name == "read_"
        assert call.arguments == {"path": "a.txt"}

    asyncio.run(scenario())


def test_model_client_accepts_json_fallback(openai_env) -> None:
    async def handler(request: web.Request) -> web.Response:
        assert (await request.json())["stream"] is True
        return web.json_response({
            "choices": [{
                "message": {
                    "content": "non-stream fallback",
                    "tool_calls": [{
                        "id": "call-json",
                        "function": {"name": "git_status", "arguments": "{}"},
                    }],
                }
            }]
        })

    async def scenario() -> None:
        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        runner, base = await _start(app)
        import os
        old = os.environ["OPENAI_BASE_URL"]
        os.environ["OPENAI_BASE_URL"] = base
        try:
            result = await OpenAICompatibleModelClient().complete(
                messages=[], tools=[], model="gpt-test", stream_delta=lambda _text: None
            )
        finally:
            os.environ["OPENAI_BASE_URL"] = old
            await runner.cleanup()
        assert result.streamed is False
        assert result.content == "non-stream fallback"
        assert result.tool_calls[0].name == "git_status"

    asyncio.run(scenario())

def test_model_client_allows_local_openai_compatible_endpoint_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
        assert "Authorization" not in request.headers
        return aiohttp.web.json_response({
            "choices": [{"message": {"content": "local model"}}]
        })

    async def scenario() -> None:
        app = aiohttp.web.Application()
        app.router.add_post("/chat/completions", handler)
        runner, base = await _start(app)
        monkeypatch.setenv("OPENAI_BASE_URL", base)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        try:
            result = await OpenAICompatibleModelClient().complete(
                messages=[], tools=[], model="qwen-coder", stream_delta=None
            )
        finally:
            await runner.cleanup()
        assert result.content == "local model"

    asyncio.run(scenario())

def test_configured_local_model_provider_needs_no_api_key() -> None:
    from harness_config_manager.config import HalterConfig

    async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
        assert (await request.json())["model"] == "qwen-coder"
        return aiohttp.web.json_response({
            "choices": [{"message": {"content": "configured local provider"}}]
        })

    async def scenario() -> None:
        app = aiohttp.web.Application()
        app.router.add_post("/chat/completions", handler)
        runner, base = await _start(app)
        cfg = HalterConfig(model_providers={
            "local": {"base_url": base, "api_key_env": ""},
        })
        try:
            result = await OpenAICompatibleModelClient(cfg).complete(
                messages=[], tools=[], model="local/qwen-coder", stream_delta=None
            )
        finally:
            await runner.cleanup()
        assert result.content == "configured local provider"

    asyncio.run(scenario())
