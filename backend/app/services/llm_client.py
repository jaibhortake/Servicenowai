"""OpenAI-compatible LLM client with provider fallback and structured-output repair.

The rest of the backend was written against the OpenAI SDK surface:

    client.chat.completions.create(...)
    client.beta.chat.completions.parse(..., response_format=PydanticModel)
    client.embeddings.create(...)

This module exposes the same surface but routes calls to free/open providers:

* Ollama (local, default) - chat + embeddings
* Groq (hosted, free tier) - chat only, used as an automatic fallback

Call sites keep working unchanged. Small local models do not support strict JSON
schemas reliably, so ``parse`` asks for JSON, repairs it, validates it with
pydantic and retries once with the validation error before giving up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Type

import httpx
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    chat_model: str


def chat_providers() -> List[Provider]:
    """Ordered chat providers: primary first, then the fallback if configured."""
    ollama = Provider(
        "ollama",
        settings.ollama_base_url.rstrip("/") + "/v1",
        "ollama",
        settings.ollama_chat_model,
    )
    groq: Optional[Provider] = None
    if settings.groq_api_key.strip():
        groq = Provider("groq", settings.groq_base_url.rstrip("/"), settings.groq_api_key.strip(), settings.groq_model)

    if settings.llm_provider_normalized == "groq":
        ordered = [groq, ollama]
    else:
        ordered = [ollama, groq]
    providers = [p for p in ordered if p is not None]
    if not settings.llm_fallback_enabled:
        providers = providers[:1]
    return providers


def embedding_provider() -> Provider:
    return Provider(
        "ollama",
        settings.ollama_base_url.rstrip("/") + "/v1",
        "ollama",
        settings.ollama_embedding_model,
    )


# --------------------------------------------------------------------------- JSON repair

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating fences, prose and trailing commas."""
    raw = (text or "").strip()
    raw = _FENCE_RE.sub("", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Take the outermost {...} or [...] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = raw.find(open_ch), raw.rfind(close_ch)
        if start != -1 and end > start:
            candidate = raw[start : end + 1]
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)  # trailing commas
            try:
                return json.loads(candidate)
            except Exception:
                continue
    raise ValueError(f"Model did not return valid JSON: {raw[:200]!r}")


def _template(schema: Dict[str, Any], defs: Dict[str, Any], depth: int = 0) -> Any:
    """Render a JSON Schema as a fill-in example (small models copy schemas verbatim)."""
    if depth > 6:
        return "..."
    if "$ref" in schema:
        return _template(defs.get(schema["$ref"].split("/")[-1], {}), defs, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return _template(options[0], defs, depth + 1)
    if "enum" in schema:
        return "one of: " + " | ".join(str(v) for v in schema["enum"])
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        out = {}
        for name, sub in (schema.get("properties") or {}).items():
            value = _template(sub, defs, depth + 1)
            desc = sub.get("description")
            out[name] = value if not desc or not isinstance(value, str) else f"{value} ({desc})"
        return out
    if kind == "array":
        return [_template(schema.get("items", {}), defs, depth + 1)]
    return {"string": "<string>", "integer": "<integer>", "number": "<number>", "boolean": "<true|false>"}.get(kind, "<value>")


def _schema_instruction(model_cls: Type[BaseModel]) -> str:
    schema = model_cls.model_json_schema()
    example = json.dumps(_template(schema, schema.get("$defs", {})), indent=1)
    return (
        "Reply with ONLY a JSON object (no markdown, no explanation). Fill in real values for this "
        "structure - do not copy the placeholders and do not return a schema:\n" + example
    )


def _with_schema(messages: List[Dict[str, Any]], model_cls: Type[BaseModel]) -> List[Dict[str, Any]]:
    out = [dict(m) for m in messages]
    note = _schema_instruction(model_cls)
    if out and out[0].get("role") == "system" and isinstance(out[0].get("content"), str):
        out[0]["content"] = f"{out[0]['content']}\n\n{note}"
    else:
        out.insert(0, {"role": "system", "content": note})
    return out


def _coerce(model_cls: Type[BaseModel], data: Any) -> BaseModel:
    if isinstance(data, list) and len(model_cls.model_fields) == 1:
        data = {next(iter(model_cls.model_fields)): data}
    return model_cls.model_validate(data)


# --------------------------------------------------------------------------- shared call logic

# Params the OpenAI SDK accepts that OpenAI-compatible servers may reject.
_DROP_PARAMS = {"reasoning_effort", "max_completion_tokens", "store", "metadata", "service_tier"}


def _clean_kwargs(kwargs: Dict[str, Any], provider: Provider) -> Dict[str, Any]:
    out = {k: v for k, v in kwargs.items() if k not in _DROP_PARAMS and v is not None}
    out["model"] = provider.chat_model  # the active provider decides the model
    out.setdefault("timeout", settings.llm_timeout_seconds)
    return out


def _make_message_result(content: str, parsed: Any = None, raw: Any = None) -> Any:
    message = SimpleNamespace(content=content, parsed=parsed, refusal=None, role="assistant")
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop", index=0)],
        usage=getattr(raw, "usage", None),
        model=getattr(raw, "model", None),
    )


def _http_client_args() -> Dict[str, Any]:
    return {"trust_env": False, "timeout": settings.llm_timeout_seconds}


# --------------------------------------------------------------------------- async client


class _AsyncChatCompletions:
    def __init__(self, owner: "AsyncLLMClient") -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> Any:
        return await self._owner._chat(kwargs)

    async def parse(self, *, response_format: Type[BaseModel], messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        return await self._owner._parse(response_format, messages, kwargs)


class _AsyncEmbeddings:
    def __init__(self, owner: "AsyncLLMClient") -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> Any:
        provider = embedding_provider()
        client = self._owner._client_for(provider)
        kwargs = {k: v for k, v in kwargs.items() if k in {"input", "dimensions", "encoding_format"}}
        return await client.embeddings.create(model=provider.chat_model, **kwargs)


class AsyncLLMClient:
    def __init__(self) -> None:
        self._clients: Dict[tuple, AsyncOpenAI] = {}
        self.chat = SimpleNamespace(completions=_AsyncChatCompletions(self))
        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=_AsyncChatCompletions(self)))
        self.embeddings = _AsyncEmbeddings(self)

    def _client_for(self, provider: Provider) -> AsyncOpenAI:
        key = (provider.base_url, provider.api_key)
        if key not in self._clients:
            self._clients[key] = AsyncOpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key or "none",
                max_retries=0,
                http_client=httpx.AsyncClient(**_http_client_args()),
            )
        return self._clients[key]

    async def _chat(self, kwargs: Dict[str, Any]) -> Any:
        last: Optional[Exception] = None
        for provider in chat_providers():
            try:
                return await self._client_for(provider).chat.completions.create(**_clean_kwargs(kwargs, provider))
            except Exception as exc:  # connection errors, 4xx/5xx, timeouts
                last = exc
                logger.warning("[LLM] %s chat failed (%s); trying next provider", provider.name, _short(exc))
        raise RuntimeError(f"All LLM providers failed: {_short(last)}") from last

    async def _parse(self, model_cls: Type[BaseModel], messages: List[Dict[str, Any]], kwargs: Dict[str, Any]) -> Any:
        kwargs = dict(kwargs)
        kwargs["response_format"] = {"type": "json_object"}
        convo = _with_schema(messages, model_cls)
        last: Optional[Exception] = None
        for attempt in range(2):
            raw = await self._chat({**kwargs, "messages": convo})
            content = raw.choices[0].message.content or ""
            try:
                parsed = _coerce(model_cls, extract_json(content))
                return _make_message_result(content, parsed, raw)
            except (ValueError, ValidationError) as exc:
                last = exc
                convo = convo + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That was invalid ({_short(exc)}). Return corrected JSON only."},
                ]
        raise RuntimeError(f"Model output failed schema validation: {_short(last)}") from last


# --------------------------------------------------------------------------- sync client


class _SyncChatCompletions:
    def __init__(self, owner: "SyncLLMClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        return self._owner._chat(kwargs)

    def parse(self, *, response_format: Type[BaseModel], messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        return self._owner._parse(response_format, messages, kwargs)


class _SyncEmbeddings:
    def __init__(self, owner: "SyncLLMClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        provider = embedding_provider()
        client = self._owner._client_for(provider)
        kwargs = {k: v for k, v in kwargs.items() if k in {"input", "dimensions", "encoding_format"}}
        return client.embeddings.create(model=provider.chat_model, **kwargs)


class SyncLLMClient:
    def __init__(self) -> None:
        self._clients: Dict[tuple, OpenAI] = {}
        self.chat = SimpleNamespace(completions=_SyncChatCompletions(self))
        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=_SyncChatCompletions(self)))
        self.embeddings = _SyncEmbeddings(self)

    def _client_for(self, provider: Provider) -> OpenAI:
        key = (provider.base_url, provider.api_key)
        if key not in self._clients:
            self._clients[key] = OpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key or "none",
                max_retries=0,
                http_client=httpx.Client(**_http_client_args()),
            )
        return self._clients[key]

    def _chat(self, kwargs: Dict[str, Any]) -> Any:
        last: Optional[Exception] = None
        for provider in chat_providers():
            try:
                return self._client_for(provider).chat.completions.create(**_clean_kwargs(kwargs, provider))
            except Exception as exc:
                last = exc
                logger.warning("[LLM] %s chat failed (%s); trying next provider", provider.name, _short(exc))
        raise RuntimeError(f"All LLM providers failed: {_short(last)}") from last

    def _parse(self, model_cls: Type[BaseModel], messages: List[Dict[str, Any]], kwargs: Dict[str, Any]) -> Any:
        kwargs = dict(kwargs)
        kwargs["response_format"] = {"type": "json_object"}
        convo = _with_schema(messages, model_cls)
        last: Optional[Exception] = None
        for attempt in range(2):
            raw = self._chat({**kwargs, "messages": convo})
            content = raw.choices[0].message.content or ""
            try:
                parsed = _coerce(model_cls, extract_json(content))
                return _make_message_result(content, parsed, raw)
            except (ValueError, ValidationError) as exc:
                last = exc
                convo = convo + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That was invalid ({_short(exc)}). Return corrected JSON only."},
                ]
        raise RuntimeError(f"Model output failed schema validation: {_short(last)}") from last


# --------------------------------------------------------------------------- helpers


def _short(exc: Optional[BaseException]) -> str:
    text = str(exc or "").replace("\n", " ")
    return text[:240]


_sync_singleton: Optional[SyncLLMClient] = None
_async_singleton: Optional[AsyncLLMClient] = None


def get_sync_client() -> SyncLLMClient:
    global _sync_singleton
    if _sync_singleton is None:
        _sync_singleton = SyncLLMClient()
    return _sync_singleton


def get_async_llm_client() -> AsyncLLMClient:
    global _async_singleton
    if _async_singleton is None:
        _async_singleton = AsyncLLMClient()
    return _async_singleton


def llm_status() -> Dict[str, Any]:
    """Cheap reachability probe for the health endpoint / admin panel."""
    status: Dict[str, Any] = {"providers": [], "embedding_model": settings.ollama_embedding_model}
    try:
        r = httpx.get(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=3.0, trust_env=False)
        models = [m.get("name", "") for m in r.json().get("models", [])]
        status["providers"].append({"name": "ollama", "ok": r.status_code == 200, "models": models})
    except Exception as exc:
        status["providers"].append({"name": "ollama", "ok": False, "error": _short(exc)})
    status["providers"].append({"name": "groq", "ok": bool(settings.groq_api_key.strip()), "configured": bool(settings.groq_api_key.strip())})
    status["primary"] = settings.llm_provider_normalized
    return status
