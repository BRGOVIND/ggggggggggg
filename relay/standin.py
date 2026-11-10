import asyncio
import json
import math
import os
import random
import time
from collections import deque
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from relay.limits import estimate_input_tokens
from relay.model import count_tokens_roughly

WINDOW_S = 60.0  # Keep the stand-in's minute window comparable with the provider.
DAY_S = 86400.0  # Reset the simulated daily quota after a day.
CHUNK_WORDS = 3  # Small chunks expose time to first content under streaming.
RPM_BURST_FRACTION = 0.85  # One probe fits a 30/min refill with this burst credit.
DEFAULT_SEED = 7  # Reproduce load outcomes when no seed is supplied.


@dataclass(frozen=True)
class LimitRejection:
    name: str
    kind: str
    limit: int
    used: int
    requested: int
    retry_header_s: int
    retry_body_s: float


class StandInState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.seed = int(os.getenv("STANDIN_SEED", str(DEFAULT_SEED)))
        self.random = random.Random(self.seed)
        self.latency_random = random.Random(self.seed + 1)
        self.time_scale = 1.0
        self.minute_window_s = WINDOW_S
        self.rpm = int(os.getenv("STANDIN_RPM", "30"))
        self.rpm_burst_fraction = float(
            os.getenv("STANDIN_RPM_BURST_FRACTION", str(RPM_BURST_FRACTION))
        )
        self.tpm = int(os.getenv("STANDIN_TPM", "8000"))
        self.itpm = int(os.getenv("STANDIN_ITPM", "7000"))
        self.rpd = int(os.getenv("STANDIN_RPD", "1000"))
        self.tpd = int(os.getenv("STANDIN_TPD", "200000"))
        self.base_ms = float(os.getenv("STANDIN_BASE_MS", "650"))
        self.latency_spread_ms = float(os.getenv("STANDIN_LATENCY_SPREAD_MS", "300"))
        self.chunk_ms = float(os.getenv("STANDIN_CHUNK_MS", "35"))
        self.slow_per_active_ms = float(os.getenv("STANDIN_SLOW_PER_ACTIVE_MS", "220"))
        self.fail_probability = float(os.getenv("STANDIN_FAIL_PROBABILITY", "0"))
        self.hang_probability = float(os.getenv("STANDIN_HANG_PROBABILITY", "0.025"))
        self.hang_ms = float(os.getenv("STANDIN_HANG_MS", "30000"))
        self.requests = deque()
        self.request_credit = self.rpm * self.rpm_burst_fraction
        self.minute_tokens = 0.0
        self.minute_input_tokens = 0.0
        self.last_refill_at = time.perf_counter()
        self.day_requests = 0
        self.day_tokens = 0
        self.day_reset_at = time.perf_counter() + DAY_S
        self.active = 0

    def expire(self, now: float) -> None:
        elapsed = max(0.0, now - self.last_refill_at)
        self.request_credit = min(
            self.rpm * self.rpm_burst_fraction,
            self.request_credit + elapsed * self.rpm / self.minute_window_s,
        )
        self.minute_tokens = max(
            0.0, self.minute_tokens - elapsed * self.tpm / self.minute_window_s
        )
        self.minute_input_tokens = max(
            0.0, self.minute_input_tokens - elapsed * self.itpm / self.minute_window_s
        )
        self.last_refill_at = now
        if now >= self.day_reset_at:
            self.day_requests = 0
            self.day_tokens = 0
            self.day_reset_at = now + DAY_S
        while self.requests and now - self.requests[0] >= self.minute_window_s:
            self.requests.popleft()

    async def admit(
        self, tokens_in: int, tokens_out: int
    ) -> tuple[LimitRejection | None, int]:
        async with self.lock:
            now = time.perf_counter()
            self.expire(now)
            total_tokens = tokens_in + tokens_out
            if self.day_requests >= self.rpd:
                return LimitRejection(
                    "requests per day (RPD)",
                    "requests",
                    self.rpd,
                    self.day_requests,
                    1,
                    int(DAY_S),
                    DAY_S,
                ), self.active
            if self.day_tokens + total_tokens > self.tpd:
                return LimitRejection(
                    "tokens per day (TPD)",
                    "tokens",
                    self.tpd,
                    self.day_tokens,
                    total_tokens,
                    int(DAY_S),
                    DAY_S,
                ), self.active
            if self.request_credit < 1:
                wait = (1 - self.request_credit) * self.minute_window_s / self.rpm
                return LimitRejection(
                    "requests per minute (RPM)",
                    "requests",
                    self.rpm,
                    len(self.requests),
                    1,
                    math.ceil(wait),
                    round(wait, 2),
                ), self.active
            if self.minute_input_tokens + tokens_in > self.itpm:
                wait = (
                    (self.minute_input_tokens + tokens_in - self.itpm)
                    * self.minute_window_s
                    / self.itpm
                )
                return LimitRejection(
                    "input tokens per minute (ITPM)",
                    "tokens",
                    self.itpm,
                    int(self.minute_input_tokens),
                    tokens_in,
                    math.ceil(wait),
                    round(wait, 2),
                ), self.active
            if self.minute_tokens + total_tokens > self.tpm:
                wait = (
                    (self.minute_tokens + total_tokens - self.tpm)
                    * self.minute_window_s / self.tpm
                )
                return LimitRejection(
                    "tokens per minute (TPM)",
                    "tokens",
                    self.tpm,
                    int(self.minute_tokens),
                    total_tokens,
                    math.ceil(wait),
                    round(wait, 2),
                ), self.active
            self.requests.append(now)
            self.request_credit -= 1
            self.minute_tokens += total_tokens
            self.minute_input_tokens += tokens_in
            self.day_requests += 1
            self.day_tokens += total_tokens
            self.active += 1
            return None, self.active

    async def leave(self) -> None:
        async with self.lock:
            self.active -= 1

    async def delay_ms(self, active: int) -> float:
        async with self.lock:
            offset = self.latency_random.uniform(-self.latency_spread_ms, self.latency_spread_ms)
        return max(
            0, self.base_ms + offset + self.slow_per_active_ms * max(0, active - 1)
        )

    async def should_fail(self) -> bool:
        async with self.lock:
            return self.random.random() < self.fail_probability

    async def should_hang(self) -> bool:
        async with self.lock:
            return self.random.random() < self.hang_probability

    async def quota_headers(self) -> dict[str, str]:
        async with self.lock:
            self.expire(time.perf_counter())
            return {
                "x-ratelimit-limit-requests": str(self.rpd),
                "x-ratelimit-remaining-requests": str(
                    max(0, self.rpd - self.day_requests)
                ),
                "x-ratelimit-limit-tokens": str(self.tpm),
                "x-ratelimit-remaining-tokens": str(
                    max(0, int(self.tpm - self.minute_tokens))
                ),
            }


state = StandInState()
app = FastAPI()


def sse_chunk(content: str = "", usage: dict | None = None) -> str:
    choice = [{"delta": {"content": content}}] if content else []
    return "data: " + json.dumps({"choices": choice, "usage": usage}) + "\n\n"


def standin_reply(prompt: str) -> str:
    if prompt.startswith("Check whether every factual claim"):
        return "PASS"
    return "The supplied passage describes the result [P1]."


def rate_error(rejection: LimitRejection, headers: dict[str, str]) -> JSONResponse:
    message = (
        f"Rate limit reached for model `stand-in` on {rejection.name}: "
        f"Limit {rejection.limit}, Used {rejection.used}, "
        f"Requested {rejection.requested}. "
        f"Please try again in {rejection.retry_body_s:g}s."
    )
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": rejection.kind,
                "code": "rate_limit_exceeded",
            }
        },
        status_code=429,
        headers={**headers, "retry-after": str(rejection.retry_header_s)},
    )


@app.post("/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    prompt = payload["messages"][-1]["content"]
    max_tokens = int(
        payload.get("max_completion_tokens", payload.get("max_tokens", 400))
    )
    output = standin_reply(prompt)[: max_tokens * 4]
    tokens_in = estimate_input_tokens(prompt)
    tokens_out = count_tokens_roughly(output)
    if await state.should_hang():

        async def delayed_stream():
            await asyncio.sleep(state.hang_ms / 1000)
            yield sse_chunk(output)
            yield sse_chunk(
                usage={"prompt_tokens": tokens_in, "completion_tokens": tokens_out}
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(delayed_stream(), media_type="text/event-stream")
    rejection, active = await state.admit(tokens_in, tokens_out)
    headers = await state.quota_headers()
    if rejection:
        return rate_error(rejection, headers)
    if await state.should_fail():
        await state.leave()
        return JSONResponse(
            {"error": {"message": "injected failure"}}, status_code=503, headers=headers
        )

    async def stream():
        try:
            await asyncio.sleep(await state.delay_ms(active) / 1000)
            words = output.split()
            for start in range(0, len(words), CHUNK_WORDS):
                yield sse_chunk(" ".join(words[start : start + CHUNK_WORDS]) + " ")
                await asyncio.sleep(state.chunk_ms / 1000)
            yield sse_chunk(
                usage={"prompt_tokens": tokens_in, "completion_tokens": tokens_out}
            )
            yield "data: [DONE]\n\n"
        finally:
            await state.leave()

    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@app.post("/control")
async def control(request: Request):
    changes = await request.json()
    async with state.lock:
        if "time_scale" in changes:
            scale = float(changes["time_scale"])
            if scale <= 0:
                raise ValueError("Time scale must be positive")
            state.time_scale = scale
            state.minute_window_s = WINDOW_S / state.time_scale
        for field in (
            "rpm",
            "rpm_burst_fraction",
            "tpm",
            "itpm",
            "rpd",
            "tpd",
            "base_ms",
            "latency_spread_ms",
            "chunk_ms",
            "slow_per_active_ms",
            "fail_probability",
            "hang_probability",
            "hang_ms",
        ):
            if field in changes:
                setattr(state, field, changes[field])
        if changes.get("reset"):
            state.seed = int(changes.get("seed", state.seed))
            state.random = random.Random(state.seed)
            state.latency_random = random.Random(state.seed + 1)
            state.requests.clear()
            state.request_credit = state.rpm * state.rpm_burst_fraction
            state.minute_tokens = 0.0
            state.minute_input_tokens = 0.0
            state.last_refill_at = time.perf_counter()
            state.day_requests = 0
            state.day_tokens = 0
            state.day_reset_at = time.perf_counter() + DAY_S
        if changes.get("exhaust_day"):
            state.day_requests = state.rpd
    return {"ok": True}
