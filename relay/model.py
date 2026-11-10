import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from relay.budget_guard import DailyBudgetGuard, estimate_guard_tokens

REQUEST_TIMEOUT_S = 30.0  # Slow calls must eventually release a worker.
CONNECT_TIMEOUT_S = (
    5.0  # A connection problem should fail before the whole request deadline.
)
LIMIT_HEADER_PREFIX = "x-ratelimit-"  # Preserve provider quota evidence in raw results.


@dataclass(frozen=True)
class ModelReply:
    text: str
    tokens_in: int
    tokens_out: int
    first_chunk_ms: float | None
    total_ms: float
    usage_measured: bool
    headers: dict[str, str]
    chunk_count: int


@dataclass
class StreamCapture:
    pieces: list[str] = field(default_factory=list)
    usage: dict | None = None
    first_chunk_ms: float | None = None
    body_429: str | None = None


class ModelFailure(Exception):
    def __init__(
        self,
        status: int,
        retry_after_s: float | None,
        message: str,
        headers: dict[str, str],
    ):
        super().__init__(message)
        self.status = status
        self.retry_after_s = retry_after_s
        self.headers = headers


def count_tokens_roughly(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def quota_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.startswith(LIMIT_HEADER_PREFIX)
        or name in ("retry-after", "x-request-id")
    }


def raw_rate_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    return [
        (name, value)
        for name, value in headers.multi_items()
        if name.startswith(LIMIT_HEADER_PREFIX)
        or name in ("retry-after", "x-request-id")
    ]


def integer_header(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def save_raw_response(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())


async def read_model_response(
    response: httpx.Response, capture: StreamCapture, started: float
) -> None:
    if response.status_code >= 400:
        body = (await response.aread()).decode("utf-8", errors="replace")
        if response.status_code == 429:
            capture.body_429 = body
        raise ModelFailure(
            response.status_code,
            retry_after(response.headers),
            body[:500],
            quota_headers(response.headers),
        )
    done = False
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        if line == "data: [DONE]":
            done = True
            break
        chunk = json.loads(line[6:])
        if chunk.get("error"):
            raise ModelFailure(
                502, None, str(chunk["error"]), quota_headers(response.headers)
            )
        if chunk.get("usage"):
            capture.usage = chunk["usage"]
        for choice in chunk.get("choices", []):
            content = choice.get("delta", {}).get("content")
            if content:
                capture.pieces.append(content)
                if capture.first_chunk_ms is None:
                    capture.first_chunk_ms = (time.perf_counter() - started) * 1000
    if not done:
        raise ModelFailure(
            502, None, "incomplete model stream", quota_headers(response.headers)
        )


class ModelClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        daily_guard: DailyBudgetGuard | None = None,
        raw_log: Path | None = None,
        show_usage: bool = False,
    ):
        if api_key and daily_guard is None:
            raise ValueError("Real model calls require the daily budget guard")
        self.model = model
        self.daily_guard = daily_guard
        self.raw_log = raw_log
        self.show_usage = show_usage
        self.last_remaining_tokens = None
        self.last_remaining_requests = None
        self.last_response_at = None
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        )

    async def close(self) -> None:
        await self.client.aclose()

    def header_deltas(self, headers: httpx.Headers, at_utc: datetime) -> dict:
        current_tokens = integer_header(headers, "x-ratelimit-remaining-tokens")
        current_requests = integer_header(headers, "x-ratelimit-remaining-requests")
        token_limit = integer_header(headers, "x-ratelimit-limit-tokens")
        request_limit = integer_header(headers, "x-ratelimit-limit-requests")
        token_start = self.last_remaining_tokens
        request_start = self.last_remaining_requests
        basis = (
            "previous_response" if token_start is not None else "limit_minus_remaining"
        )
        if token_start is None:
            token_start = token_limit
        if request_start is None:
            request_start = request_limit
        delta = {
            "header_token_delta": token_start - current_tokens
            if token_start is not None and current_tokens is not None
            else None,
            "header_request_delta": request_start - current_requests
            if request_start is not None and current_requests is not None
            else None,
            "header_delta_basis": basis,
            "previous_remaining_tokens": self.last_remaining_tokens,
            "current_remaining_tokens": current_tokens,
            "response_gap_ms": (at_utc - self.last_response_at).total_seconds() * 1000
            if self.last_response_at is not None
            else None,
        }
        self.last_remaining_tokens = current_tokens
        self.last_remaining_requests = current_requests
        self.last_response_at = at_utc
        return delta

    def record_attempt(
        self,
        call_id: str,
        prompt: str,
        max_tokens: int,
        response: httpx.Response | None,
        capture: StreamCapture,
        outcome: str,
        started: float,
    ) -> None:
        at_utc = datetime.now(timezone.utc)
        deltas = self.header_deltas(response.headers, at_utc) if response else {}
        actual = None
        if capture.usage is not None:
            actual = capture.usage.get("total_tokens")
            if actual is None:
                actual = capture.usage.get("prompt_tokens", 0) + capture.usage.get(
                    "completion_tokens", 0
                )
        if self.raw_log is not None:
            save_raw_response(
                self.raw_log,
                {
                    "call_id": call_id,
                    "at_utc": at_utc.isoformat(),
                    "model_id": self.model,
                    "outcome": outcome,
                    "status_code": response.status_code if response else None,
                    "headers": raw_rate_headers(response.headers) if response else [],
                    "body_429": capture.body_429,
                    "usage": capture.usage,
                    "estimated_tokens": estimate_guard_tokens(prompt, max_tokens),
                    "max_output_tokens": max_tokens,
                    **deltas,
                    "first_chunk_ms": capture.first_chunk_ms,
                    "total_ms": (time.perf_counter() - started) * 1000,
                },
            )
        if self.show_usage:
            measured = f"{actual} actual tokens" if actual is not None else "usage unknown"
            calls, tokens = self.daily_guard.used_today()
            print(
                f"Call {call_id}: {outcome}, {measured}; "
                f"today guard {calls} calls / {tokens} tokens "
                "(measured baseline plus later reservations)"
            )

    async def complete(
        self, prompt: str, max_tokens: int, call_id: str = ""
    ) -> ModelReply:
        if self.daily_guard is not None:
            self.daily_guard.reserve_call(prompt, max_tokens)
        call_id = call_id or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.model.startswith("qwen/"):
            payload["reasoning_effort"] = "none"
        capture = StreamCapture()
        response = None
        outcome = "ok"
        try:
            async with self.client.stream(
                "POST", "/chat/completions", json=payload
            ) as response:
                await read_model_response(response, capture, started)
        except ModelFailure as error:
            outcome = f"http_{error.status}"
            raise
        except (asyncio.TimeoutError, httpx.TimeoutException, asyncio.CancelledError):
            outcome = "timeout"
            raise
        except httpx.TransportError:
            outcome = "transport_error"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            if self.raw_log is not None or self.show_usage:
                self.record_attempt(
                    call_id, prompt, max_tokens, response, capture, outcome, started
                )
        text = "".join(capture.pieces)
        return ModelReply(
            text=text,
            tokens_in=capture.usage.get("prompt_tokens", 0)
            if capture.usage
            else count_tokens_roughly(prompt),
            tokens_out=capture.usage.get("completion_tokens", 0)
            if capture.usage
            else count_tokens_roughly(text),
            first_chunk_ms=capture.first_chunk_ms,
            total_ms=(time.perf_counter() - started) * 1000,
            usage_measured=capture.usage is not None,
            headers=quota_headers(response.headers),
            chunk_count=len(capture.pieces),
        )


async def set_standin_options(base_url: str, changes: dict) -> None:
    async with httpx.AsyncClient(
        base_url=base_url, timeout=REQUEST_TIMEOUT_S
    ) as client:
        response = await client.post("/control", json=changes)
        response.raise_for_status()
