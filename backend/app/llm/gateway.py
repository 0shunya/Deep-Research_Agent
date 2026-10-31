"""The model gateway: one door for every model call.

It sends a versioned prompt with a JSON schema, parses and validates the reply, retries only what is
worth retrying (bounded, exponential backoff with jitter), falls back to the next configured model when a
model's daily quota is used up, and records every attempt — success or failure — through ``record_call``.
Planner, researchers, verifier and writer never touch a vendor SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.llm.prompts import Prompt
from app.llm.providers import GeminiProvider, Provider, ProviderError, ProviderRequest, ProviderResponse

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
INVALID_OUTPUT_RETRIES = 1  # malformed/schema-invalid replies get one more try, never an endless loop
MAX_BACKOFF_SECONDS = 30.0


@dataclass
class CallContext:
    run_id: str | None = None
    task_id: str | None = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class CallRecord:
    trace_id: str
    run_id: str | None
    task_id: str | None
    feature: str
    provider: str
    model: str
    prompt_version: str
    attempt: int
    status: str                 # success | error
    latency_ms: int
    cost_usd: Decimal = Decimal("0")
    error_category: str | None = None
    error_message: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0) + (self.thinking_tokens or 0)


@dataclass
class GatewayResult(Generic[T]):
    output: T
    trace_id: str
    model: str
    attempts: int
    input_tokens: int
    output_tokens: int


class ModelCallFailed(Exception):
    def __init__(self, category: str, message: str, *, trace_id: str, attempts: int):
        super().__init__(message)
        self.category = category
        self.message = message
        self.trace_id = trace_id
        self.attempts = attempts


class InvalidOutput(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


def parse_reply(text: str, model: type[T]) -> T:
    """Second line of defence after provider-side structured output: JSON parse + Pydantic validation."""
    if not text or not text.strip():
        raise InvalidOutput("empty_response", "The model returned an empty response.")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise InvalidOutput("invalid_json", f"The response was not valid JSON ({exc.msg}).") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise InvalidOutput("schema_mismatch",
                            f"The response did not match the {model.__name__} schema ({len(exc.errors())} error(s)).") from exc


_SIZE_KEYWORDS = {"minLength", "maxLength", "minItems", "maxItems"}


def provider_schema(model: type[BaseModel]) -> dict:
    """The JSON schema sent to the provider: $refs inlined, title annotations and size limits removed.

    Gemini rejected the writer's schema with a bare 400 "invalid argument" until the nested
    length/item limits were removed (measured on gemini-3.5-flash-lite). The limits are not lost: every
    reply is still validated against the full Pydantic model in ``parse_reply``.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(dict(defs[node["$ref"].rsplit("/", 1)[-1]]))
            # drop "title" annotations (strings), never a property that happens to be called "title"
            return {k: inline(v) for k, v in node.items()
                    if k not in _SIZE_KEYWORDS and not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    return inline(schema)


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, MAX_BACKOFF_SECONDS)
    return min(MAX_BACKOFF_SECONDS, (2 ** (attempt - 1)) + random.uniform(0, 0.5))


def cost_of(input_tokens: int | None, output_tokens: int | None, thinking_tokens: int | None,
            settings: Settings | None = None) -> Decimal:
    """List-price cost of one call from its real token counts. Thinking tokens bill as output tokens."""
    settings = settings or get_settings()
    usd = ((input_tokens or 0) * settings.price_input_per_mtok
           + ((output_tokens or 0) + (thinking_tokens or 0)) * settings.price_output_per_mtok) / 1_000_000
    return Decimal(str(round(usd, 6)))


def build_providers(settings: Settings | None = None) -> list[Provider]:
    settings = settings or get_settings()
    if not settings.gemini_api_key:
        raise ProviderError("config_missing", "GEMINI_API_KEY is not set on the server.", retryable=False)
    return [GeminiProvider(settings.gemini_api_key, model, timeout_seconds=settings.model_timeout_seconds,
                           thinking_level=settings.gemini_thinking_level) for model in settings.model_list]


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@dataclass
class ModelGateway:
    providers: list[Provider]
    record_call: Callable[[CallRecord], None]
    max_attempts: int = 3
    sleep: Callable[[float], Awaitable[None]] = _async_sleep
    allow_call: Callable[[], bool] | None = None  # daily spend guard; False -> fail before calling
    # Per-model daily cap (free-tier quotas are per model): False -> skip this model, try the next one.
    allow_model: Callable[[str], bool] | None = None
    # Per-run call budget, so one report cannot drain the day: False -> fail this call.
    allow_run_call: Callable[[str], bool] | None = None

    async def generate(self, prompt: Prompt, output_model: type[T], ctx: CallContext | None = None,
                       *, max_output_tokens: int = 8192) -> GatewayResult[T]:
        ctx = ctx or CallContext()
        request = ProviderRequest(prompt.feature, prompt.system, prompt.user, provider_schema(output_model),
                                  max_output_tokens)
        attempt = 0
        last: ModelCallFailed | None = None
        for provider in self.providers:
            invalid_retries_left = INVALID_OUTPUT_RETRIES
            if self.allow_model is not None and not self.allow_model(provider.model):
                log.warning("model's daily cap reached; trying next model",
                            extra={"trace_id": ctx.trace_id, "model": provider.model})
                last = ModelCallFailed("daily_limit", "Every configured model has reached its daily call cap.",
                                       trace_id=ctx.trace_id, attempts=attempt)
                continue
            for _ in range(self.max_attempts):
                if self.allow_call is not None and not self.allow_call():
                    raise ModelCallFailed("daily_limit", "The server's daily model-call limit is reached.",
                                          trace_id=ctx.trace_id, attempts=attempt)
                if ctx.run_id and self.allow_run_call is not None and not self.allow_run_call(ctx.run_id):
                    raise ModelCallFailed("run_call_budget", "This run has used its model-call budget.",
                                          trace_id=ctx.trace_id, attempts=attempt)
                attempt += 1
                started = time.perf_counter()
                response: ProviderResponse | None = None
                try:
                    response = await provider.generate(request)
                    output = parse_reply(response.text, output_model)
                except ProviderError as exc:
                    self._record(ctx, prompt, provider, attempt, started, "error", exc.category, exc.message, None)
                    last = ModelCallFailed(exc.category, exc.message, trace_id=ctx.trace_id, attempts=attempt)
                    if exc.category == "quota_exhausted":
                        log.warning("model quota exhausted; trying next model",
                                    extra={"trace_id": ctx.trace_id, "model": provider.model})
                        break  # next provider
                    if not exc.retryable:
                        raise last from exc
                    await self.sleep(_backoff(attempt, exc.retry_after))
                    continue
                except InvalidOutput as exc:
                    self._record(ctx, prompt, provider, attempt, started, "error", exc.category, exc.message, response)
                    last = ModelCallFailed(exc.category, exc.message, trace_id=ctx.trace_id, attempts=attempt)
                    if invalid_retries_left <= 0:
                        raise last from exc
                    invalid_retries_left -= 1
                    continue
                self._record(ctx, prompt, provider, attempt, started, "success", None, None, response)
                return GatewayResult(output, ctx.trace_id, provider.model, attempt,
                                     response.input_tokens or 0,
                                     (response.output_tokens or 0) + (response.thinking_tokens or 0))
            else:
                # Retries on this model ran out on transient errors (5xx "high demand", timeouts): a preview
                # model can stay overloaded for minutes, so try the next configured model instead of failing.
                log.warning("model kept failing; trying next model",
                            extra={"trace_id": ctx.trace_id, "model": provider.model,
                                   "error_category": last.category if last else None})
                continue
        assert last is not None
        raise last

    def _record(self, ctx: CallContext, prompt: Prompt, provider: Provider, attempt: int, started: float,
                status: str, category: str | None, message: str | None, response: ProviderResponse | None) -> None:
        tokens = dict(input_tokens=getattr(response, "input_tokens", None),
                      output_tokens=getattr(response, "output_tokens", None),
                      thinking_tokens=getattr(response, "thinking_tokens", None))
        record = CallRecord(
            trace_id=ctx.trace_id, run_id=ctx.run_id, task_id=ctx.task_id, feature=prompt.feature,
            provider=provider.name, model=provider.model, prompt_version=prompt.version, attempt=attempt,
            status=status, latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=cost_of(**tokens), error_category=category,
            error_message=(message or None) and message[:300], **tokens)
        try:
            self.record_call(record)
        except Exception:  # observability must never break the pipeline
            log.exception("failed to record model call", extra={"trace_id": ctx.trace_id})
