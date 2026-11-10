import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass

WINDOW_S = 60.0  # Provider minute limits use a rolling minute in our conservative gate.
DAY_S = 86400.0  # Fallback daily window until the provider gives a better reset time.
TOKEN_HEADROOM = 0.85  # Token estimates and other account traffic need space.
INPUT_TOKENS_PER_CHAR = 0.30  # Measured PDF prompts reached 0.29 tokens per character.
MIN_WAIT_S = 0.05  # Avoid spinning when a reservation becomes available soon.


@dataclass(frozen=True)
class Limits:
    requests_per_minute: int
    tokens_per_minute: int
    requests_per_day: int
    tokens_per_day: int
    input_tokens_per_minute: int | None = None


def estimate_input_tokens(prompt: str) -> int:
    return math.ceil(len(prompt) * INPUT_TOKENS_PER_CHAR)


class Budget:
    def __init__(self, limits: Limits, time_scale: float = 1.0):
        if time_scale <= 0:
            raise ValueError("Time scale must be positive")
        self.limits = limits
        self.minute_window_s = WINDOW_S / time_scale
        self.min_wait_s = MIN_WAIT_S / time_scale
        self.lock = asyncio.Lock()
        self.requests = deque()
        self.tokens = deque()
        self.input_tokens = deque()
        self.day_requests = 0
        self.day_tokens = 0
        self.day_reset_at = time.perf_counter() + DAY_S
        self.cooldown_until = 0.0
        self.day_exhausted = False

    def _expire(self, now: float) -> None:
        if now >= self.day_reset_at:
            self.day_requests = 0
            self.day_tokens = 0
            self.day_exhausted = False
            self.day_reset_at = now + DAY_S
        while self.requests and now - self.requests[0] >= self.minute_window_s:
            self.requests.popleft()
        while self.tokens and now - self.tokens[0][0] >= self.minute_window_s:
            self.tokens.popleft()
        while self.input_tokens and now - self.input_tokens[0][0] >= self.minute_window_s:
            self.input_tokens.popleft()

    def _minute_tokens(self) -> int:
        return sum(amount for _, amount in self.tokens)

    def _minute_input_tokens(self) -> int:
        return sum(amount for _, amount in self.input_tokens)

    def _usable_tpm(self) -> int:
        return int(self.limits.tokens_per_minute * TOKEN_HEADROOM)

    def _usable_itpm(self) -> int | None:
        if self.limits.input_tokens_per_minute is None:
            return None
        return int(self.limits.input_tokens_per_minute * TOKEN_HEADROOM)

    async def pressure(self) -> bool:
        async with self.lock:
            self._expire(time.perf_counter())
            used = self._minute_tokens()
            if used >= self._usable_tpm() * 0.75:
                return True
            itpm = self._usable_itpm()
            return itpm is not None and self._minute_input_tokens() >= itpm * 0.75

    async def wait_for_room(self, tokens: int, input_tokens: int | None = None) -> bool:
        if tokens > self._usable_tpm():
            return False
        input_tokens = tokens if input_tokens is None else input_tokens
        itpm = self._usable_itpm()
        if itpm is not None and input_tokens > itpm:
            return False
        while True:
            async with self.lock:
                now = time.perf_counter()
                self._expire(now)
                if (
                    self.day_exhausted
                    or self.day_requests >= self.limits.requests_per_day
                ):
                    return False
                if self.day_tokens + tokens > self.limits.tokens_per_day:
                    return False
                request_room = len(self.requests) < self.limits.requests_per_minute
                token_room = self._minute_tokens() + tokens <= self._usable_tpm()
                input_room = (
                    itpm is None or self._minute_input_tokens() + input_tokens <= itpm
                )
                if (
                    request_room
                    and token_room
                    and input_room
                    and now >= self.cooldown_until
                ):
                    return True
                delays = [self.cooldown_until - now]
                if not request_room:
                    delays.append(self.requests[0] + self.minute_window_s - now)
                if not token_room:
                    delays.append(self.tokens[0][0] + self.minute_window_s - now)
                if not input_room:
                    delays.append(self.input_tokens[0][0] + self.minute_window_s - now)
            await asyncio.sleep(max(self.min_wait_s, max(delays)))

    async def reserve(
        self, tokens: int, deadline: float, input_tokens: int | None = None
    ) -> str | None:
        if tokens > self._usable_tpm():
            return "request_exceeds_tpm"
        input_tokens = tokens if input_tokens is None else input_tokens
        itpm = self._usable_itpm()
        if itpm is not None and input_tokens > itpm:
            return "request_exceeds_itpm"
        while True:
            async with self.lock:
                now = time.perf_counter()
                if now >= deadline:
                    return "queue_deadline"
                self._expire(now)
                if self.day_exhausted:
                    return "daily_budget_exhausted"
                if self.day_requests >= self.limits.requests_per_day:
                    return "daily_budget_exhausted"
                if self.day_tokens + tokens > self.limits.tokens_per_day:
                    return "daily_budget_exhausted"
                request_room = len(self.requests) < self.limits.requests_per_minute
                token_room = self._minute_tokens() + tokens <= self._usable_tpm()
                input_room = (
                    itpm is None or self._minute_input_tokens() + input_tokens <= itpm
                )
                if (
                    request_room
                    and token_room
                    and input_room
                    and now >= self.cooldown_until
                ):
                    self.requests.append(now)
                    self.tokens.append((now, tokens))
                    self.input_tokens.append((now, input_tokens))
                    self.day_requests += 1
                    self.day_tokens += tokens
                    return None
                delays = [self.cooldown_until - now]
                if not request_room:
                    delays.append(self.requests[0] + self.minute_window_s - now)
                if not token_room:
                    delays.append(self.tokens[0][0] + self.minute_window_s - now)
                if not input_room:
                    delays.append(self.input_tokens[0][0] + self.minute_window_s - now)
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return "queue_deadline"
            delay = max(self.min_wait_s, max(delays))
            await asyncio.sleep(min(delay, remaining))

    async def provider_limit(self, retry_after_s: float | None, daily: bool) -> None:
        async with self.lock:
            if daily:
                self.day_exhausted = True
                self.day_reset_at = time.perf_counter() + (
                    retry_after_s if retry_after_s is not None else DAY_S
                )
            if retry_after_s is not None:
                self.cooldown_until = max(
                    self.cooldown_until, time.perf_counter() + retry_after_s
                )
