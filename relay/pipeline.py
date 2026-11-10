import asyncio
import json
import re
import time
from dataclasses import dataclass, field

import httpx

from relay.budget_guard import estimate_guard_tokens
from relay.limits import Budget, estimate_input_tokens
from relay.model import ModelClient, ModelFailure, ModelReply, count_tokens_roughly
from relay.papers import PaperIndex, Passage

DRAFT_MAX_TOKENS = 400  # Keep answers short enough for the free token budget.
VERIFY_MAX_TOKENS = 64  # A support verdict needs only a few words.
MAX_DRAFT_CHARS = 3200  # Cover long text from the draft's 400-token output cap.
MAX_ATTEMPTS = 2  # A second failure costs more than most requests can tolerate.
EXCERPT_WORDS = 70  # Keep pressure fallback short and clearly incomplete.
CITATION_PATTERN = re.compile(r"\[P(\d+)\]")  # Only retrieved passage IDs may be cited.
REFUSAL = "I cannot answer that from the three supplied papers."
DAILY_REPLY = "The daily model budget is exhausted. I cannot check the papers right now. Please try after the quota resets."
MINUTE_REPLY = "The model is busy at its current limit. Please retry shortly."
TIMEOUT_REPLY = "The request took too long; please retry."


@dataclass(frozen=True)
class Answer:
    text: str
    status: str
    given_up: bool
    first_token_ms: float | None
    total_ms: float
    queue_ms: float
    work_ms: float
    retry_ms: float
    retries: int
    tokens_in: int
    tokens_out: int
    model_first_chunk_ms: float | None
    usage_measured: bool
    verifier_mode: str
    retrieval_setting: str
    passages: int
    error: str = ""
    provider_headers_json: str = "[]"
    model_total_ms: float = 0.0
    provider_first_chunk_ms: float | None = None
    model_chunk_count: int = 0
    answer_kind: str = "none"
    http_attempts: int = 0


@dataclass
class CallStats:
    retries: int = 0
    retry_ms: float = 0.0
    wait_ms: float = 0.0
    headers: list[dict[str, str]] = field(default_factory=list)
    http_attempts: int = 0


def passage_context(passages: list[Passage]) -> str:
    return "\n\n".join(
        f"[P{number}] {passage.citation}\n{passage.text}"
        for number, passage in enumerate(passages, start=1)
    )


def draft_prompt(question: str, passages: list[Passage]) -> str:
    return (
        "Answer the question only from the numbered passages. "
        "Cite each factual claim with [P1], [P2], etc. "
        "If the passages do not establish the answer, reply exactly NOT_IN_PAPERS. "
        "Do not use outside knowledge. Keep the answer under 120 words.\n\n"
        f"Question: {question}\n\nPassages:\n{passage_context(passages)}"
    )


def verify_prompt(question: str, draft: str, passages: list[Passage]) -> str:
    return (
        "Check whether every factual claim in the draft is supported by its cited passage. "
        "Reply PASS or FAIL only. A refusal passes if the passages do not answer the question. "
        "Do not use outside knowledge.\n\n"
        f"Question: {question}\nDraft: {draft}\n\nPassages:\n{passage_context(passages)}"
    )


def planned_call_tokens(
    question: str, index: PaperIndex, verifier_mode: str
) -> list[int]:
    passages = index.search(question)
    if not passages:
        return []
    planned = [
        estimate_guard_tokens(draft_prompt(question, passages), DRAFT_MAX_TOKENS)
    ]
    if verifier_mode in ("model", "auto"):
        draft = "x" * MAX_DRAFT_CHARS
        planned.append(
            estimate_guard_tokens(
                verify_prompt(question, draft, passages), VERIFY_MAX_TOKENS
            )
        )
    return planned


def check_citations(draft: str, passages: list[Passage]) -> bool:
    if draft.strip() == "NOT_IN_PAPERS":
        return True
    numbers = [int(number) for number in CITATION_PATTERN.findall(draft)]
    return bool(numbers) and all(1 <= number <= len(passages) for number in numbers)


def display_answer(draft: str, passages: list[Passage]) -> str:
    if draft.strip() == "NOT_IN_PAPERS":
        return REFUSAL

    def replace(match: re.Match) -> str:
        number = int(match.group(1))
        return f"[{passages[number - 1].citation}]"

    return CITATION_PATTERN.sub(replace, draft.strip())


def is_daily_limit(error: ModelFailure) -> bool:
    if error.status != 429:
        return False
    if "daily" in str(error).lower():
        return True
    if error.headers.get("x-ratelimit-remaining-requests") == "0":
        return True
    return error.retry_after_s is not None and error.retry_after_s >= 3600


def defer_under_pressure(
    question: str, index: PaperIndex, submitted_at: float
) -> Answer:
    started = time.perf_counter()
    passages = index.search(question)
    text = MINUTE_REPLY
    answer_kind = "none"
    if passages:
        words = passages[0].text.split()
        excerpt = " ".join(words[:EXCERPT_WORDS])
        if len(words) > EXCERPT_WORDS:
            excerpt += " ..."
        text = (
            f"Retrieved passage (not verified as an answer) "
            f"[{passages[0].citation}]: {excerpt}"
        )
        answer_kind = "excerpt"
    finished = time.perf_counter()
    queue_ms = (started - submitted_at) * 1000
    total_ms = (finished - submitted_at) * 1000
    return Answer(
        text=text,
        status="ok" if answer_kind == "excerpt" else "rate_limited",
        given_up=False,
        first_token_ms=total_ms,
        total_ms=total_ms,
        queue_ms=queue_ms,
        work_ms=total_ms - queue_ms,
        retry_ms=0,
        retries=0,
        tokens_in=0,
        tokens_out=0,
        model_first_chunk_ms=None,
        usage_measured=True,
        verifier_mode="deferred",
        retrieval_setting=index.setting.name,
        passages=len(passages),
        error="minute_pressure",
        answer_kind=answer_kind,
    )


async def call_with_retry(
    model: ModelClient,
    budget: Budget,
    prompt: str,
    max_tokens: int,
    deadline: float,
    stats: CallStats,
) -> ModelReply:
    for attempt in range(MAX_ATTEMPTS):
        wait_started = time.perf_counter()
        reason = await budget.reserve(
            count_tokens_roughly(prompt) + max_tokens,
            deadline,
            input_tokens=estimate_input_tokens(prompt),
        )
        stats.wait_ms += (time.perf_counter() - wait_started) * 1000
        if reason:
            raise RuntimeError(reason)
        if attempt > 0:
            stats.retries += 1
        call_started = time.perf_counter()
        try:
            remaining = max(0.0, deadline - time.perf_counter())
            if remaining <= 0:
                raise asyncio.TimeoutError
            stats.http_attempts += 1
            reply = await asyncio.wait_for(
                model.complete(prompt, max_tokens), timeout=remaining
            )
            stats.headers.append(reply.headers)
            return reply
        except ModelFailure as error:
            stats.headers.append(error.headers)
            daily = is_daily_limit(error)
            await budget.provider_limit(error.retry_after_s, daily)
            if daily:
                raise
            if error.status != 429 or attempt + 1 == MAX_ATTEMPTS:
                raise
            if error.retry_after_s is None:
                raise
            delay = error.retry_after_s
            if time.perf_counter() + delay >= deadline:
                raise
            retry_started = time.perf_counter()
            await asyncio.sleep(delay)
            stats.retry_ms += (time.perf_counter() - retry_started) * 1000
        finally:
            if attempt > 0:
                stats.retry_ms += (time.perf_counter() - call_started) * 1000
    raise RuntimeError("unreachable")


async def answer_question(
    question: str,
    index: PaperIndex,
    model: ModelClient,
    budget: Budget,
    verifier_mode: str,
    deadline: float,
    submitted_at: float,
) -> Answer:
    started = time.perf_counter()
    queue_ms = (started - submitted_at) * 1000
    tokens_in = 0
    tokens_out = 0
    stats = CallStats()
    first_chunk_ms = None
    usage_measured = True
    model_total_ms = 0.0
    provider_first_chunk_ms = None
    model_chunk_count = 0
    passages = index.search(question)
    mode = "model" if verifier_mode == "auto" else verifier_mode
    status = "ok"
    given_up = False
    error_text = ""
    answer = REFUSAL
    try:
        if not passages:
            return Answer(
                text=REFUSAL,
                status="ok",
                given_up=False,
                first_token_ms=queue_ms,
                total_ms=queue_ms,
                queue_ms=queue_ms,
                work_ms=0,
                retry_ms=0,
                retries=0,
                tokens_in=0,
                tokens_out=0,
                model_first_chunk_ms=None,
                usage_measured=True,
                verifier_mode=mode,
                retrieval_setting=index.setting.name,
                passages=0,
                answer_kind="full",
            )
        draft = await call_with_retry(
            model,
            budget,
            draft_prompt(question, passages),
            DRAFT_MAX_TOKENS,
            deadline,
            stats,
        )
        tokens_in += draft.tokens_in
        tokens_out += draft.tokens_out
        first_chunk_ms = (
            (time.perf_counter() - submitted_at) * 1000
            - draft.total_ms
            + draft.first_chunk_ms
            if draft.first_chunk_ms is not None
            else None
        )
        usage_measured &= draft.usage_measured
        model_total_ms += draft.total_ms
        provider_first_chunk_ms = draft.first_chunk_ms
        model_chunk_count += draft.chunk_count
        valid = check_citations(draft.text, passages)
        if valid and mode == "model":
            verdict = await call_with_retry(
                model,
                budget,
                verify_prompt(question, draft.text, passages),
                VERIFY_MAX_TOKENS,
                deadline,
                stats,
            )
            tokens_in += verdict.tokens_in
            tokens_out += verdict.tokens_out
            usage_measured &= verdict.usage_measured
            model_total_ms += verdict.total_ms
            model_chunk_count += verdict.chunk_count
            valid = verdict.text.strip().upper().startswith("PASS")
        if valid:
            answer = display_answer(draft.text, passages)
    except RuntimeError as error:
        error_text = str(error)
        if error_text == "daily_budget_exhausted":
            status = "rate_limited"
            answer = DAILY_REPLY
        elif error_text in (
            "queue_deadline",
            "request_exceeds_tpm",
            "request_exceeds_itpm",
        ):
            status = "rate_limited" if error_text != "queue_deadline" else "timeout"
            given_up = error_text == "queue_deadline"
            answer = MINUTE_REPLY if status == "rate_limited" else TIMEOUT_REPLY
        else:
            status = "error"
    except ModelFailure as error:
        error_text = str(error)
        status = "rate_limited" if error.status == 429 else "error"
        usage_measured = False
        if is_daily_limit(error):
            answer = DAILY_REPLY
        elif error.status == 429:
            answer = MINUTE_REPLY
    except asyncio.TimeoutError:
        error_text = "deadline_exceeded"
        status = "timeout"
        given_up = True
        usage_measured = False
        answer = TIMEOUT_REPLY
    except (httpx.TimeoutException, httpx.TransportError) as error:
        error_text = type(error).__name__
        status = "timeout" if isinstance(error, httpx.TimeoutException) else "error"
        given_up = status == "timeout"
        usage_measured = False
        if given_up:
            answer = TIMEOUT_REPLY
    finished = time.perf_counter()
    total_ms = (finished - submitted_at) * 1000
    first_token_ms = total_ms if answer else None
    waiting_ms = queue_ms + stats.wait_ms
    return Answer(
        text=answer,
        status=status,
        given_up=given_up,
        first_token_ms=first_token_ms,
        total_ms=total_ms,
        queue_ms=waiting_ms,
        work_ms=max(0, total_ms - waiting_ms - stats.retry_ms),
        retry_ms=stats.retry_ms,
        retries=stats.retries,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model_first_chunk_ms=first_chunk_ms,
        usage_measured=usage_measured,
        verifier_mode=mode,
        retrieval_setting=index.setting.name,
        passages=len(passages),
        error=error_text,
        provider_headers_json=json.dumps(stats.headers),
        model_total_ms=model_total_ms,
        provider_first_chunk_ms=provider_first_chunk_ms,
        model_chunk_count=model_chunk_count,
        answer_kind="full" if status == "ok" else "none",
        http_attempts=stats.http_attempts,
    )
