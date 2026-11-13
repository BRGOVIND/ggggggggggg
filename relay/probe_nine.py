import argparse
import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import httpx

from relay.budget_guard import (
    DEFAULT_CONFIG,
    DEFAULT_LEDGER,
    DailyCapExceeded,
    estimate_guard_tokens,
    open_real_guard,
)
from relay.limits import Budget, Limits
from relay.model import ModelClient, ModelFailure, ModelReply
from relay.papers import build_indexes
from relay.pipeline import (
    DRAFT_MAX_TOKENS,
    MAX_DRAFT_CHARS,
    VERIFY_MAX_TOKENS,
    draft_prompt,
    verify_prompt,
)
from relay.probe_limit import probe_answer
from relay.run import PAPER_FOLDER, append_rows, result_row

QUESTION_FILE = Path("questions_candidate.json")  # Probe questions remain ungraded.
RAW_FILE = Path("probe_1_9_raw.jsonl")  # Keep exact response headers and 429 bodies.
SUMMARY_FILE = Path("probe_1_9_summary.json")  # Compare measured usage with estimates.
SHORT_PROMPT = "Reply with one word: OK."  # Spend few tokens on concurrency checks.
SHORT_MAX_TOKENS = 8  # Bound the output for direct calls.
PACE_WAIT_S = 180  # Allow minute windows to clear without forcing a 429.
CALL_TIMEOUT_S = 10  # Bound a call well above the measured subsecond baseline.


@dataclass(frozen=True)
class ProbeCall:
    number: int
    question_id: str
    setting: str
    prompt: str
    max_tokens: int
    passage_count: int
    estimated_tokens: int


@dataclass(frozen=True)
class ProbeOutcome:
    reply: ModelReply | None
    status: str
    error_text: str
    headers: dict[str, str]
    elapsed_ms: float
    started_at: str


def load_probe_questions(path: Path) -> dict[str, str]:
    items = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item["question"] for item in items}


def planned_tokens(indexes: dict, questions: dict[str, str]) -> list[int]:
    tokens = []
    drafts = {"T05": "x" * MAX_DRAFT_CHARS, "U01": "x" * MAX_DRAFT_CHARS}
    for number in range(1, 10):
        call = probe_step(number, indexes, questions, drafts)
        tokens.append(call.estimated_tokens)
    return tokens


def probe_step(
    number: int, indexes: dict, questions: dict[str, str], drafts: dict
) -> ProbeCall:
    items = {
        1: ("T01", "small", "draft"),
        2: ("M02", "small", "draft"),
        3: ("H02", "small", "draft"),
        4: ("T05", "medium", "draft"),
        5: ("T05", "medium", "verify"),
        6: ("U01", "small", "draft"),
        7: ("U01", "small", "verify"),
    }
    if number in (8, 9):
        return ProbeCall(
            number,
            "short",
            "none",
            SHORT_PROMPT,
            SHORT_MAX_TOKENS,
            0,
            estimate_guard_tokens(SHORT_PROMPT, SHORT_MAX_TOKENS),
        )
    question_id, setting, mode = items[number]
    question = questions[question_id]
    passages = indexes[setting].search(question)
    if mode == "draft":
        prompt = draft_prompt(question, passages)
        max_tokens = DRAFT_MAX_TOKENS
    else:
        prompt = verify_prompt(question, drafts[question_id], passages)
        max_tokens = VERIFY_MAX_TOKENS
    return ProbeCall(
        number,
        question_id,
        setting,
        prompt,
        max_tokens,
        len(passages),
        estimate_guard_tokens(prompt, max_tokens),
    )


async def get_probe_reply(
    model: ModelClient, call: ProbeCall, call_id: str
) -> ProbeOutcome:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    reply = None
    status = "ok"
    error_text = ""
    headers = {}
    try:
        reply = await asyncio.wait_for(
            model.complete(call.prompt, call.max_tokens, call_id=call_id),
            timeout=CALL_TIMEOUT_S,
        )
        headers = reply.headers
    except ModelFailure as error:
        status = "rate_limited" if error.status == 429 else "error"
        error_text = str(error)
        headers = error.headers
    except (
        asyncio.TimeoutError,
        httpx.TimeoutException,
        httpx.TransportError,
    ) as error:
        status = (
            "timeout"
            if isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException))
            else "error"
        )
        error_text = type(error).__name__
    elapsed_ms = (time.perf_counter() - started) * 1000
    return ProbeOutcome(reply, status, error_text, headers, elapsed_ms, started_at)


def save_step(
    call: ProbeCall,
    args: argparse.Namespace,
    outcome: ProbeOutcome,
    run_id: str,
    questions: dict[str, str],
) -> dict:
    reply = outcome.reply
    answer = probe_answer(
        reply, outcome.status, outcome.elapsed_ms, outcome.error_text, outcome.headers
    )
    mode = (
        "deterministic"
        if call.number <= 3
        else "model"
        if call.number in (5, 7)
        else "none"
    )
    answer = replace(
        answer,
        verifier_mode=mode,
        retrieval_setting=call.setting,
        passages=call.passage_count,
    )
    scenario = "probe" if call.number <= 3 else "probe_nine"
    row = result_row(
        answer,
        args,
        scenario,
        run_id,
        call.number,
        questions.get(call.question_id, SHORT_PROMPT),
        outcome.started_at,
        0,
        0,
        0,
        "direct_probe",
    )
    append_rows(args.output, [row])
    actual = (
        reply.tokens_in + reply.tokens_out if reply and reply.usage_measured else None
    )
    summary = {
        "call": call.number,
        "question_id": call.question_id,
        "setting": call.setting,
        "status": outcome.status,
        "estimated_tokens": call.estimated_tokens,
        "actual_tokens": actual,
        "tokens_in": reply.tokens_in if reply and reply.usage_measured else None,
        "tokens_out": reply.tokens_out if reply and reply.usage_measured else None,
        "first_chunk_ms": reply.first_chunk_ms if reply else None,
        "total_ms": outcome.elapsed_ms,
        "request_id": f"{run_id}-{call.number}",
    }
    print(
        f"{call.number}: {outcome.status}; estimated {call.estimated_tokens}, actual {actual if actual is not None else 'unmeasured'} tokens; "
        f"first chunk {summary['first_chunk_ms']} ms; total {outcome.elapsed_ms:.0f} ms"
    )
    return {"summary": summary, "draft": reply.text if reply else None}


async def send_step(
    number: int,
    args: argparse.Namespace,
    model: ModelClient,
    budget: Budget,
    indexes: dict,
    questions: dict[str, str],
    drafts: dict,
    run_id: str,
) -> dict:
    call = probe_step(number, indexes, questions, drafts)
    reason = await budget.reserve(
        call.estimated_tokens, time.perf_counter() + PACE_WAIT_S
    )
    if reason:
        raise RuntimeError(f"Call {number} stopped before HTTP: {reason}")
    call_id = f"{run_id}-{number}"
    outcome = await get_probe_reply(model, call, call_id)
    return save_step(call, args, outcome, run_id, questions)


async def run_probe(args: argparse.Namespace) -> None:
    if not os.getenv("GROQ_API_KEY"):
        raise ValueError("Set GROQ_API_KEY before the real nine-call probe")
    guard = open_real_guard(args.budget_config, args.budget_ledger, args.model)
    args.model = guard.config["model_id"]
    args.rpm = guard.config["rpm"]
    args.tpm = guard.config["tpm"]
    args.rpd = guard.config["rpd"]
    args.tpd = guard.config["tpd"]
    args.itpm = guard.config.get("itpm")
    args.source = "real"
    args.limits_basis = "account_console"
    print(f"Model for batch 1-9: {args.model}")
    questions = load_probe_questions(args.questions)
    indexes = build_indexes(PAPER_FOLDER)
    planned = planned_tokens(indexes, questions)
    guard.check_batch(planned, retry_headroom=0)
    usable_tpm = int(args.tpm * 0.85)
    if max(planned) > usable_tpm:
        raise ValueError(
            f"Largest probe call needs {max(planned)} estimated tokens; usable TPM is {usable_tpm}"
        )
    budget = Budget(Limits(args.rpm, args.tpm, args.rpd, args.tpd))
    model = ModelClient(
        args.url,
        args.model,
        os.environ["GROQ_API_KEY"],
        daily_guard=guard,
        raw_log=args.raw_output,
    )
    run_id = uuid.uuid4().hex[:10]
    drafts = {}
    summaries = []
    try:
        for number in range(1, 8):
            result = await send_step(
                number, args, model, budget, indexes, questions, drafts, run_id
            )
            summaries.append(result["summary"])
            if result["summary"]["status"] != "ok":
                break
            if number in (4, 6):
                drafts[result["summary"]["question_id"]] = result["draft"]
        if len(summaries) == 7 and all(item["status"] == "ok" for item in summaries):
            pair = await asyncio.gather(
                send_step(8, args, model, budget, indexes, questions, drafts, run_id),
                send_step(9, args, model, budget, indexes, questions, drafts, run_id),
            )
            summaries.extend(item["summary"] for item in pair)
    finally:
        await model.close()
        args.summary_output.write_text(
            json.dumps(
                {"run_id": run_id, "model_id": args.model, "calls": summaries}, indent=2
            ),
            encoding="utf-8",
        )
    print(
        f"Saved {len(summaries)} calls to {args.output}, {args.raw_output}, and {args.summary_output}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=QUESTION_FILE)
    parser.add_argument("--url", default="https://api.groq.com/openai/v1")
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--raw-output", type=Path, default=RAW_FILE)
    parser.add_argument("--summary-output", type=Path, default=SUMMARY_FILE)
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--budget-ledger", type=Path, default=DEFAULT_LEDGER)
    try:
        asyncio.run(run_probe(parser.parse_args()))
    except (ValueError, DailyCapExceeded, RuntimeError) as error:
        raise SystemExit(str(error)) from None
