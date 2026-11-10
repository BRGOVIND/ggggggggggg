import argparse
import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
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
from relay.model import ModelClient, ModelFailure, ModelReply
from relay.papers import build_indexes
from relay.pipeline import DRAFT_MAX_TOKENS, draft_prompt
from relay.probe_limit import PROBE_OUTPUT_TOKENS, PROBE_PROMPT, probe_answer
from relay.run import PAPER_FOLDER, append_rows, result_row

RPM_CALLS = 31  # One more than the account's displayed 30 RPM limit.
MINUTE_S = 60.0  # Keep the RPM experiment inside one minute.
QUIET_S = 61.0  # Isolate each experiment from the previous minute's traffic.
SHORT_TIMEOUT_S = 5.0  # Measured short-call latency was below one second.
LARGE_TIMEOUT_S = 15.0  # Long context may need more time but must stay bounded.
RPM_RAW = Path("probe_rpm_raw.jsonl")  # Keep rate headers and full 429 bodies.
TOKEN_RAW = Path("probe_token_raw.jsonl")  # Keep the two large-context responses.
SUMMARY = Path("probe_followup_summary.json")  # Keep usage beside header deltas.


@dataclass(frozen=True)
class CallOutcome:
    reply: ModelReply | None
    status: str
    error: str
    headers: dict[str, str]
    retry_after_s: float | None
    elapsed_ms: float
    started_at: str


def last_reservation(path: Path) -> datetime | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as file:
        lines = [line for line in file if line.strip()]
    if not lines:
        return None
    return datetime.fromisoformat(json.loads(lines[-1])["at_utc"])


async def wait_for_quiet(path: Path, extra_s: float = 0) -> None:
    last = last_reservation(path)
    since_last = (
        (datetime.now(timezone.utc) - last).total_seconds() if last else QUIET_S
    )
    wait_s = max(0.0, QUIET_S - since_last, extra_s)
    if wait_s > 0:
        print(f"Waiting {wait_s:.1f}s for a fresh minute window")
        await asyncio.sleep(wait_s)


def raw_record(path: Path, call_id: str) -> dict | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as file:
        for line in file:
            record = json.loads(line)
            if record["call_id"] == call_id:
                return record
    return None


async def send_call(
    model: ModelClient, prompt: str, max_tokens: int, timeout_s: float, call_id: str
) -> CallOutcome:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    reply = None
    status = "ok"
    error_text = ""
    headers = {}
    retry_after_s = None
    try:
        reply = await asyncio.wait_for(
            model.complete(prompt, max_tokens, call_id=call_id), timeout_s
        )
        headers = reply.headers
    except ModelFailure as error:
        status = "rate_limited" if error.status == 429 else "error"
        error_text = str(error)
        headers = error.headers
        retry_after_s = error.retry_after_s
    except (asyncio.TimeoutError, httpx.TimeoutException) as error:
        status = "timeout"
        error_text = type(error).__name__
    except httpx.TransportError as error:
        status = "error"
        error_text = type(error).__name__
    elapsed_ms = (time.perf_counter() - started) * 1000
    return CallOutcome(
        reply, status, error_text, headers, retry_after_s, elapsed_ms, started_at
    )


def save_call(
    args: argparse.Namespace,
    outcome: CallOutcome,
    raw_path: Path,
    scenario: str,
    run_id: str,
    number: int,
    prompt: str,
    max_tokens: int,
    display_question: str | None = None,
) -> dict:
    call_id = f"{run_id}-{number}"
    answer = probe_answer(
        outcome.reply,
        outcome.status,
        outcome.elapsed_ms,
        outcome.error,
        outcome.headers,
    )
    row = result_row(
        answer,
        args,
        scenario,
        run_id,
        number,
        display_question or prompt,
        outcome.started_at,
        0,
        0,
        0,
        "direct_provider_probe",
    )
    append_rows(args.output, [row])
    raw = raw_record(raw_path, call_id)
    usage = raw["usage"] if raw else None
    record = {
        "call_id": call_id,
        "status": outcome.status,
        "usage": usage,
        "actual_usage_tokens": usage.get("total_tokens") if usage else None,
        "prompt_plus_max_tokens": usage.get("prompt_tokens", 0) + max_tokens
        if usage
        else None,
        "unused_output_tokens": max_tokens - usage.get("completion_tokens", 0)
        if usage
        else None,
        "header_token_delta": raw.get("header_token_delta") if raw else None,
        "header_request_delta": raw.get("header_request_delta") if raw else None,
        "header_delta_basis": raw.get("header_delta_basis") if raw else None,
        "response_gap_ms": raw.get("response_gap_ms") if raw else None,
        "max_output_tokens": max_tokens,
        "estimated_tokens": estimate_guard_tokens(prompt, max_tokens),
        "first_chunk_ms": outcome.reply.first_chunk_ms if outcome.reply else None,
        "total_ms": outcome.elapsed_ms,
        "retry_after_s": outcome.retry_after_s,
        "request_remaining": outcome.headers.get("x-ratelimit-remaining-requests"),
        "token_remaining": outcome.headers.get("x-ratelimit-remaining-tokens"),
    }
    used = usage.get("total_tokens") if usage else "unknown"
    print(
        f"{scenario} {number}: {outcome.status}; usage {used}, "
        f"header token delta {record['header_token_delta']}, "
        f"total {outcome.elapsed_ms:.0f}ms"
    )
    return record


def infer_timeout_count(first: dict | None) -> dict:
    result = {
        "call_7_counted": None,
        "basis": "Assumes no unrelated Groq requests and no RPD reset since calls 1-9.",
    }
    if not first or first["status"] != "ok":
        return result
    remaining = first["request_remaining"]
    if remaining == "999":
        result["basis"] = (
            "RPD capacity replenished before the follow-up; request logs are needed."
        )
        return result
    if remaining == "991":
        result["call_7_counted"] = False
    elif remaining == "990":
        result["call_7_counted"] = True
    return result


async def run_rpm(args: argparse.Namespace, guard) -> tuple[list[dict], float]:
    print(f"Model for RPM batch: {args.model}")
    one_call = estimate_guard_tokens(PROBE_PROMPT, PROBE_OUTPUT_TOKENS)
    guard.check_batch([one_call] * RPM_CALLS, retry_headroom=0)
    await wait_for_quiet(args.budget_ledger)
    model = ModelClient(
        args.url,
        args.model,
        os.environ["GROQ_API_KEY"],
        daily_guard=guard,
        raw_log=args.rpm_raw,
    )
    run_id = uuid.uuid4().hex[:10]
    records = []
    retry_after_s = 0.0
    started = time.perf_counter()
    try:
        for number in range(1, RPM_CALLS + 1):
            if time.perf_counter() - started >= MINUTE_S:
                print("RPM window ended before all 31 calls")
                break
            outcome = await send_call(
                model,
                PROBE_PROMPT,
                PROBE_OUTPUT_TOKENS,
                SHORT_TIMEOUT_S,
                f"{run_id}-{number}",
            )
            record = save_call(
                args,
                outcome,
                args.rpm_raw,
                "rpm_probe",
                run_id,
                number,
                PROBE_PROMPT,
                PROBE_OUTPUT_TOKENS,
            )
            records.append(record)
            if outcome.retry_after_s is not None:
                retry_after_s = outcome.retry_after_s
            if outcome.status != "ok":
                break
    finally:
        await model.close()
    return records, retry_after_s


async def run_token(args: argparse.Namespace, guard, extra_wait_s: float) -> list[dict]:
    print(f"Model for token batch: {args.model}")
    indexes = build_indexes(PAPER_FOLDER)
    question = (
        "About how many bytes per chunk does H-Net's learned dynamic chunking produce?"
    )
    passages = indexes["large"].search(question)
    prompt = draft_prompt(question, passages)
    one_call = estimate_guard_tokens(prompt, DRAFT_MAX_TOKENS)
    guard.check_batch([one_call, one_call], retry_headroom=0)
    await wait_for_quiet(args.budget_ledger, extra_wait_s)
    model = ModelClient(
        args.url,
        args.model,
        os.environ["GROQ_API_KEY"],
        daily_guard=guard,
        raw_log=args.token_raw,
    )
    run_id = uuid.uuid4().hex[:10]
    records = []
    try:
        for number in (10, 11):
            outcome = await send_call(
                model,
                prompt,
                DRAFT_MAX_TOKENS,
                LARGE_TIMEOUT_S,
                f"{run_id}-{number}",
            )
            record = save_call(
                args,
                outcome,
                args.token_raw,
                "token_limit_probe",
                run_id,
                number,
                prompt,
                DRAFT_MAX_TOKENS,
                display_question=question,
            )
            records.append(record)
            if outcome.status != "ok":
                break
    finally:
        await model.close()
    return records


async def run_followup(args: argparse.Namespace) -> None:
    if not os.getenv("GROQ_API_KEY"):
        raise ValueError("Set GROQ_API_KEY in the hidden prompt before this run")
    guard = open_real_guard(args.budget_config, args.budget_ledger, args.model)
    args.model = guard.config["model_id"]
    args.rpm = guard.config["rpm"]
    args.tpm = guard.config["tpm"]
    args.rpd = guard.config["rpd"]
    args.tpd = guard.config["tpd"]
    args.itpm = guard.config.get("itpm")
    args.source = "real"
    args.limits_basis = "account_console"
    args.url = args.url or "https://api.groq.com/openai/v1"
    indexes = build_indexes(PAPER_FOLDER)
    question = (
        "About how many bytes per chunk does H-Net's learned dynamic chunking produce?"
    )
    large_prompt = draft_prompt(question, indexes["large"].search(question))
    short_tokens = estimate_guard_tokens(PROBE_PROMPT, PROBE_OUTPUT_TOKENS)
    large_tokens = estimate_guard_tokens(large_prompt, DRAFT_MAX_TOKENS)
    print(f"Model for approved follow-up: {args.model}")
    guard.check_batch([short_tokens] * RPM_CALLS + [large_tokens] * 2, retry_headroom=0)
    rpm_records = []
    token_records = []
    try:
        rpm_records, retry_after_s = await run_rpm(args, guard)
        token_records = await run_token(args, guard, retry_after_s + 1)
    finally:
        args.summary.write_text(
            json.dumps(
                {
                    "model_id": args.model,
                    "rpm_calls": rpm_records,
                    "token_calls": token_records,
                    "timed_out_call_7": infer_timeout_count(
                        rpm_records[0] if rpm_records else None
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"Saved summary to {args.summary}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--rpm-raw", type=Path, default=RPM_RAW)
    parser.add_argument("--token-raw", type=Path, default=TOKEN_RAW)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--budget-ledger", type=Path, default=DEFAULT_LEDGER)
    try:
        asyncio.run(run_followup(parser.parse_args()))
    except (ValueError, DailyCapExceeded) as error:
        raise SystemExit(str(error)) from None
