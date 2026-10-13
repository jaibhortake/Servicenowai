import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config import settings
from app.services import llm_client


class Triage(BaseModel):
    intent: str
    keywords: list[str]


def test_extract_json_handles_fences_prose_and_trailing_commas():
    assert llm_client.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm_client.extract_json('Sure! Here you go: {"a": [1, 2,],} Hope that helps') == {"a": [1, 2]}


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        llm_client.extract_json("no json here")


def test_schema_instruction_is_a_fill_in_template_not_a_schema():
    text = llm_client._schema_instruction(Triage)
    assert '"intent"' in text and '"keywords"' in text
    assert "properties" not in text


def test_provider_order_and_fallback(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test")
    monkeypatch.setattr(settings, "llm_fallback_enabled", True)
    assert [p.name for p in llm_client.chat_providers()] == ["ollama", "groq"]

    monkeypatch.setattr(settings, "llm_fallback_enabled", False)
    assert [p.name for p in llm_client.chat_providers()] == ["ollama"]

    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "llm_fallback_enabled", True)
    assert [p.name for p in llm_client.chat_providers()] == ["ollama"]


def _reply(text):
    msg = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, model="m")


def test_parse_retries_once_on_invalid_output(monkeypatch):
    client = llm_client.AsyncLLMClient()
    replies = iter([_reply('{"wrong": true}'), _reply('{"intent": "laptop", "keywords": ["laptop"]}')])

    async def fake_chat(kwargs):
        return next(replies)

    monkeypatch.setattr(client, "_chat", fake_chat)
    result = asyncio.run(
        client.beta.chat.completions.parse(
            model="x", messages=[{"role": "user", "content": "need a laptop"}], response_format=Triage
        )
    )
    assert result.choices[0].message.parsed == Triage(intent="laptop", keywords=["laptop"])


def test_parse_gives_up_after_two_bad_replies(monkeypatch):
    client = llm_client.AsyncLLMClient()

    async def fake_chat(kwargs):
        return _reply("not json")

    monkeypatch.setattr(client, "_chat", fake_chat)
    with pytest.raises(RuntimeError):
        asyncio.run(
            client.beta.chat.completions.parse(
                model="x", messages=[{"role": "user", "content": "hi"}], response_format=Triage
            )
        )


def test_chat_falls_back_to_second_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test")
    monkeypatch.setattr(settings, "llm_fallback_enabled", True)
    client = llm_client.AsyncLLMClient()
    calls = []

    class FakeCompletions:
        def __init__(self, name):
            self.name = name

        async def create(self, **kwargs):
            calls.append((self.name, kwargs["model"]))
            if self.name == "ollama":
                raise ConnectionError("ollama down")
            return _reply("hello")

    def fake_client_for(provider):
        return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(provider.name)))

    monkeypatch.setattr(client, "_client_for", fake_client_for)
    result = asyncio.run(client.chat.completions.create(model="ignored", messages=[]))
    assert result.choices[0].message.content == "hello"
    assert [c[0] for c in calls] == ["ollama", "groq"]
    assert calls[1][1] == settings.groq_model
