import argparse
import asyncio
import json
import os
import time
import uuid
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
from relay.model import ModelClient, ModelFailure
from relay.pipeline import Answer
from relay.run import append_rows, result_row

PROBE_PROMPT = (
    "Reply with one word: OK."  # Use few tokens to probe RPM without wasting TPM.
)
PROBE_OUTPUT_TOKENS = 8  # Keep output quota small during the limit experiment.
RAW_FILE = Path("probe_rpm_raw.jsonl")  # Keep complete headers and any 429 body.
CALL_TIMEOUT_S = 5  # Keep a short RPM call from blocking the one-minute window.


def probe_answer(
    reply, status: str, elapsed_ms: float, error: str, headers: dict
) -> Answer:
    return Answer(
        text=reply.text if reply else "",
        status=status,
        given_up=status == "timeout",
        first_token_ms=reply.first_chunk_ms if reply else None,
        total_ms=elapsed_ms,
        queue_ms=0,
        work_ms=elapsed_ms,
        retry_ms=0,
        retries=0,
        tokens_in=reply.tokens_in if reply else 0,
        tokens_out=reply.tokens_out if reply else 0,
        model_first_chunk_ms=reply.first_chunk_ms if reply else None,
        usage_measured=reply.usage_measured if reply else False,
        verifier_mode="none",
        retrieval_setting="none",
        passages=0,
        error=error,
        provider_headers_json=json.dumps([headers]),
        model_total_ms=reply.total_ms if reply else 0,
        provider_first_chunk_ms=reply.first_chunk_ms if reply else None,
        model_chunk_count=reply.chunk_count if reply else 0,
        answer_kind="full" if status == "ok" else "none",
        http_attempts=1,
    )


async def send_probe(client: ModelClient, call_id: str) -> tuple[Answer, float | None]:
    started = time.perf_counter()
    try:
        reply = await asyncio.wait_for(
            client.complete(PROBE_PROMPT, PROBE_OUTPUT_TOKENS, call_id=call_id),
            timeout=CALL_TIMEOUT_S,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return probe_answer(reply, "ok", elapsed_ms, "", reply.headers), None
    except ModelFailure as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        status = "rate_limited" if error.status == 429 else "error"
        return probe_answer(
            None, status, elapsed_ms, str(error), error.headers
        ), error.retry_after_s
    except (
        asyncio.TimeoutError,
        httpx.TimeoutException,
        httpx.TransportError,
    ) as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        status = (
            "timeout"
            if isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException))
            else "error"
        )
        return probe_answer(None, status, elapsed_ms, type(error).__name__, {}), None


async def probe(args: argparse.Namespace) -> None:
    if args.max_requests < 1:
        raise ValueError("max-requests must be positive")
    if args.source == "real" and not os.getenv("GROQ_API_KEY"):
        raise SystemExit("Set GROQ_API_KEY in the environment before a real probe.")
    guard = None
    if args.source == "real":
        guard = open_real_guard(args.budget_config, args.budget_ledger, args.model)
        args.model = guard.config["model_id"]
        args.rpm = guard.config["rpm"]
        args.tpm = guard.config["tpm"]
        args.rpd = guard.config["rpd"]
        args.tpd = guard.config["tpd"]
        args.itpm = guard.config.get("itpm")
        args.limits_basis = "account_console"
        print(f"Model for batch: {args.model}")
        planned_calls = args.max_requests + int(args.check_recovery)
        one_call = estimate_guard_tokens(PROBE_PROMPT, PROBE_OUTPUT_TOKENS)
        guard.check_batch([one_call] * planned_calls, retry_headroom=0)
    url = args.url or (
        "https://api.groq.com/openai/v1"
        if args.source == "real"
        else "http://127.0.0.1:8000"
    )
    model_name = args.model or (
        "qwen/qwen3.8-27b" if args.source == "real" else "stand-in"
    )
    client = ModelClient(
        url,
        model_name,
        os.getenv("GROQ_API_KEY", "") if args.source == "real" else "",
        daily_guard=guard,
        raw_log=args.raw_output if args.source == "real" else None,
    )
    run_id = uuid.uuid4().hex[:10]
    rows = []
    first_429 = None
    try:
        for number in range(args.max_requests):
            start_time = datetime.now(timezone.utc).isoformat()
            try:
                answer, retry_after_s = await send_probe(client, f"{run_id}-{number}")
            except DailyCapExceeded:
                print("Daily guard stopped further probes before HTTP")
                break
            if answer.status == "rate_limited":
                first_429 = (number, retry_after_s)
            row = result_row(
                answer,
                args,
                "limit_probe",
                run_id,
                number,
                PROBE_PROMPT,
                start_time,
                0,
                0,
                0,
                "direct_provider_probe",
            )
            rows.append(row)
            if first_429:
                break
        if not rows:
            return
        duration_s = (
            datetime.fromisoformat(rows[-1]["start_time"])
            - datetime.fromisoformat(rows[0]["start_time"])
        ).total_seconds()
        for row in rows:
            row["scenario_duration_s"] = max(duration_s, 0.001)
        append_rows(args.output, rows)
        if first_429:
            print(f"First 429 on call {first_429[0] + 1}; retry-after {first_429[1]} s")
            if args.check_recovery and first_429[1] is not None and first_429[1] <= 90:
                await asyncio.sleep(first_429[1] + 0.2)
                start_time = datetime.now(timezone.utc).isoformat()
                answer, _ = await send_probe(client, f"{run_id}-{len(rows)}")
                recovery = result_row(
                    answer,
                    args,
                    "limit_recovery",
                    run_id,
                    len(rows),
                    PROBE_PROMPT,
                    start_time,
                    0,
                    first_429[1] + 0.2,
                    0,
                    "provider_retry_after",
                )
                append_rows(args.output, [recovery])
                print(f"Recovery check: {answer.status}")
        else:
            print(f"No 429 in {len(rows)} calls; RPM not established")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["real", "stand-in"], default="real")
    parser.add_argument("--max-requests", type=int, default=12)
    parser.add_argument("--check-recovery", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--model")
    parser.add_argument("--rpm", type=int, default=30)
    parser.add_argument("--tpm", type=int, default=8000)
    parser.add_argument("--rpd", type=int, default=1000)
    parser.add_argument("--tpd", type=int, default=200000)
    parser.add_argument(
        "--limits-basis", choices=["assumed", "account_console"], default="assumed"
    )
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--raw-output", type=Path, default=RAW_FILE)
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--budget-ledger", type=Path, default=DEFAULT_LEDGER)
    try:
        asyncio.run(probe(parser.parse_args()))
    except (ValueError, DailyCapExceeded) as error:
        raise SystemExit(str(error)) from None
