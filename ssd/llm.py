"""Model access, in one small file so the model behind the desk can be swapped here.

- Live: the official Anthropic SDK, streamed, so the screen can show each web search as it happens.
- Stub: scripted answers, used by the tests so they run with no network and no key.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")
MAX_CONTINUATIONS = 4

_stub: Callable[..., dict[str, Any]] | None = None
_client_lock = threading.Lock()
_client: Any = None
_fallbacks_off = False


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    searches: int = 0
    calls: int = 0
    usd: float = 0.0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, model: str, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0,
            searches: int = 0) -> float:
        usd = estimate_cost(model, tokens_in, tokens_out, cache_read, cache_write, searches)
        with self._lock:
            self.tokens_in += tokens_in + cache_read + cache_write
            self.tokens_out += tokens_out
            self.cache_read += cache_read
            self.cache_write += cache_write
            self.searches += searches
            self.calls += 1
            self.usd += usd
            row = self.by_model.setdefault(model, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0})
            row["calls"] += 1
            row["tokens_in"] += tokens_in + cache_read + cache_write
            row["tokens_out"] += tokens_out
            row["usd"] = round(row["usd"] + usd, 4)
        return usd

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "searches": self.searches,
                    "calls": self.calls, "usd": round(self.usd, 4), "by_model": dict(self.by_model)}


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0,
                  searches: int = 0) -> float:
    for key, (pin, pout) in config.PRICES.items():
        if model.startswith(key):
            return ((tokens_in + 1.25 * cache_write + 0.1 * cache_read) / 1e6 * pin + tokens_out / 1e6 * pout
                    + searches * config.WEB_SEARCH_USD)
    return searches * config.WEB_SEARCH_USD


def set_stub(fn: Callable[..., dict[str, Any]] | None) -> None:
    """Tests install a scripted model here. fn(role=, model=, system=, content=, tools=, schema=) -> {"blocks": [...]}"""
    global _stub
    _stub = fn


def available() -> dict[str, Any]:
    if _stub is not None:
        return {"ok": True, "transport": "stub"}
    if not config.ensure_api_key():
        return {"ok": False, "reason": "No ANTHROPIC_API_KEY was found, so live runs are off. The recorded run still plays."}
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return {"ok": False, "reason": "The anthropic package is not installed (pip install -r requirements.txt)."}
    return {"ok": True, "transport": "anthropic"}


def _get_client() -> Any:
    global _client
    with _client_lock:
        if _client is None:
            import anthropic

            config.ensure_api_key()
            _client = anthropic.Anthropic(max_retries=4)
        return _client


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "to_dict"):
        return block.to_dict()
    return dict(block)


def _notify(on_event: Callable[[dict[str, Any]], None] | None, block: dict[str, Any]) -> None:
    """Turn finished content blocks into progress events the screen can show."""
    if on_event is None:
        return
    kind = block.get("type")
    if kind == "server_tool_use" and block.get("name") == "web_search":
        query = (block.get("input") or {}).get("query")
        if query:
            on_event({"kind": "search", "query": str(query)})
    elif kind == "web_search_tool_result":
        content = block.get("content")
        if isinstance(content, list):
            on_event({"kind": "results", "count": len(content),
                      "sources": [{"title": r.get("title", ""), "url": r.get("url", "")} for r in content]})
        else:
            on_event({"kind": "search_error", "code": (content or {}).get("error_code", "unknown")})


def _stream_once(kwargs: dict[str, Any], on_event: Callable[[dict[str, Any]], None] | None) -> Any:
    """One streamed request. Returns the final message."""
    global _fallbacks_off
    import anthropic

    client = _get_client()
    model = kwargs["model"]
    use_fallbacks = (config.FALLBACKS == "default" and not _fallbacks_off and model.startswith(FALLBACK_MODELS))

    def run(with_fallbacks: bool) -> Any:
        if with_fallbacks:
            manager = client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            manager = client.messages.stream(**kwargs)
        with manager as stream:
            for event in stream:
                if on_event is not None and event.type == "content_block_stop":
                    block = getattr(event, "content_block", None)
                    if block is not None:
                        _notify(on_event, _block_dict(block))
            return stream.get_final_message()

    try:
        try:
            return run(use_fallbacks)
        except anthropic.BadRequestError as e:
            if use_fallbacks and "fallback" in str(getattr(e, "message", e)).lower():
                _fallbacks_off = True  # this account or model does not take the parameter
                return run(False)
            raise
    except anthropic.AuthenticationError as e:
        raise LLMError("The API key was rejected. Check ANTHROPIC_API_KEY.") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError(f"The API key lacks permission for this request: {getattr(e, 'message', e)}") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"Model not found: {model}.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the API. Wait a minute and press Start again.") from e
    except anthropic.BadRequestError as e:
        message = str(getattr(e, "message", e))
        if "credit balance" in message.lower():
            raise LLMError("The API account is out of credit. Add credit in the Anthropic console, or play the recorded run.") from e
        raise LLMError(f"The API rejected the request: {message[:300]}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"API error {e.status_code}: {str(getattr(e, 'message', e))[:300]}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the API. Check the internet connection, or play the recorded run.") from e


def run(role: str, model: str, system: str, content: Any, *, usage: Usage | None = None,
        tools: list[dict[str, Any]] | None = None, schema: dict[str, Any] | None = None, effort: str = "medium",
        max_tokens: int = 8000, on_event: Callable[[dict[str, Any]], None] | None = None,
        history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One model job. Returns {"blocks": [content blocks as dicts], "raw": the same blocks as the API gave them,
    "text": str, "truncated": bool, "model": str}.

    `history` is an earlier exchange to continue: [{"role": "user", ...}, {"role": "assistant", "content": raw}].
    The assistant turn must be the `raw` of an earlier answer, unchanged, so search results stay readable to the model.
    """
    if _stub is not None:
        out = _stub(role=role, model=model, system=system, content=content, tools=tools, schema=schema)
        blocks = out.get("blocks", [])
        for b in blocks:
            _notify(on_event, b)
        if usage is not None:
            usage.add("stub", 0, 0)
        return {"blocks": blocks, "raw": blocks, "text": _text_of(blocks), "truncated": False, "model": "stub"}

    output_config: dict[str, Any] = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    messages: list[dict[str, Any]] = list(history or []) + [{"role": "user", "content": content}]
    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system, messages=messages,
                                  output_config=output_config)
    if tools:
        kwargs["tools"] = tools

    blocks: list[dict[str, Any]] = []
    raw: list[Any] = []
    truncated = False
    served_by = model
    for _ in range(MAX_CONTINUATIONS + 1):
        message = _stream_once(kwargs, on_event)
        raw += list(message.content)
        served_by = getattr(message, "model", model) or model
        u = message.usage
        server = getattr(u, "server_tool_use", None)
        if usage is not None:
            usage.add(model, int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0),
                      int(getattr(u, "cache_read_input_tokens", 0) or 0),
                      int(getattr(u, "cache_creation_input_tokens", 0) or 0),
                      int(getattr(server, "web_search_requests", 0) or 0) if server is not None else 0)
        blocks += [_block_dict(b) for b in message.content]
        if message.stop_reason == "pause_turn":
            # A long server-side search turn paused: send it back unchanged and the server carries on.
            messages.append({"role": "assistant", "content": message.content})
            continue
        if message.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        truncated = message.stop_reason == "max_tokens"
        break
    else:
        raise LLMError("The search turn kept pausing and did not finish.")
    return {"blocks": blocks, "raw": raw, "text": _text_of(blocks), "truncated": truncated, "model": served_by}


def _text_of(blocks: list[dict[str, Any]]) -> str:
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def parse_json(text: str) -> Any:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            return json.loads(t[start:end + 1])
        raise LLMError("The model's answer was not valid JSON.") from None


def key_in_env() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
