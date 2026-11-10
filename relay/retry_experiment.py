import argparse
import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx

from relay.model import ModelClient, ModelFailure, set_standin_options
from relay.pipeline import Answer
from relay.run import append_rows, result_row

REQUESTS = 40  # Keep each arm large enough to expose a lower server RPM.
ARRIVAL_SPACING_S = 0.1  # Send demand faster than the server's credit refill.
DEADLINE_S = 20.0  # Compare retries under the long experimental deadline.
SERVER_RPM = 10  # Deliberately below the client's assumed 30 RPM.
CLIENT_ASSUMED_RPM = 30  # The naive client does not enforce this limit.
RETRY_BUDGET = 10  # Cap extra calls in the budgeted arms.
JITTER_MAX_S = 2.0  # Spread retries after the provider's retry-after.
SEED = 7  # Reproduce arrival and retry choices.
PROMPT = "Answer from the supplied passage: attention uses scaled dot products [P1]."  # Keep TPM out of this RPM experiment.


@dataclass
class RetryPool:
    remaining: int | None

    def take(self) -> bool:
        if self.remaining is None:
            return True
        if self.remaining == 0:
            return False
        self.remaining -= 1
        return True


def local_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Retry experiment accepts only a local stand-in URL")
    return url


def result_answer(
    text: str, status: str, elapsed_ms: float, retry_ms: float,
    attempts: int, headers: list[dict], tokens_in: int, tokens_out: int,
) -> Answer:
    return Answer(
        text=text,
        status=status,
        given_up=status == "timeout",
        first_token_ms=elapsed_ms if status == "ok" else None,
        total_ms=elapsed_ms,
        queue_ms=0,
        work_ms=max(0, elapsed_ms - retry_ms),
        retry_ms=retry_ms,
        retries=max(0, attempts - 1),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model_first_chunk_ms=None,
        usage_measured=status == "ok",
        verifier_mode="deterministic",
        retrieval_setting="small",
        passages=1,
        provider_headers_json=json.dumps(headers),
        answer_kind="full" if status == "ok" else "none",
        http_attempts=attempts,
    )


async def send_one(
    number: int, run_id: str, model: ModelClient, pool: RetryPool,
    jitter: bool,
) -> tuple[Answer, str, float]:
    arrived = time.perf_counter()
    at_utc = datetime.now(timezone.utc).isoformat()
    deadline = arrived + DEADLINE_S
    headers = []
    attempts = 0
    retry_ms = 0.0
    tokens_in = 0
    tokens_out = 0
    text = "The model is busy at its current limit. Please retry shortly."
    status = "rate_limited"
    for attempt in range(2):
        if time.perf_counter() >= deadline:
            status = "timeout"
            text = "The request took too long; please retry."
            break
        attempts += 1
        try:
            remaining = deadline - time.perf_counter()
            reply = await asyncio.wait_for(
                model.complete(PROMPT, 40, call_id=f"{run_id}-{number}-{attempt + 1}"),
                timeout=remaining,
            )
            headers.append(reply.headers)
            tokens_in += reply.tokens_in
            tokens_out += reply.tokens_out
            text = reply.text
            status = "ok"
            break
        except ModelFailure as error:
            headers.append(error.headers)
            if error.status != 429:
                status = "error"
                text = "The request failed."
                break
            if attempt or error.retry_after_s is None:
                break
            spread = random.Random(SEED + number).uniform(0, JITTER_MAX_S) if jitter else 0
            delay = error.retry_after_s + spread
            if time.perf_counter() + delay >= deadline or not pool.take():
                break
            wait_started = time.perf_counter()
            await asyncio.sleep(delay)
            retry_ms += (time.perf_counter() - wait_started) * 1000
        except (asyncio.TimeoutError, httpx.TimeoutException):
            status = "timeout"
            text = "The request took too long; please retry."
            break
        except httpx.TransportError:
            status = "error"
            text = "The request failed."
            break
    completed_at = time.perf_counter()
    elapsed_ms = (completed_at - arrived) * 1000
    return result_answer(
        text, status, elapsed_ms, retry_ms, attempts, headers, tokens_in, tokens_out
    ), at_utc, completed_at


async def configure_server(url: str) -> None:
    await set_standin_options(url, {
        "reset": True,
        "seed": SEED,
        "time_scale": 1,
        "rpm": SERVER_RPM,
        "tpm": 8000,
        "itpm": 7000,
        "rpd": 1000,
        "tpd": 200000,
        "base_ms": 328,
        "latency_spread_ms": 0,
        "chunk_ms": 2,
        "slow_per_active_ms": 0,
        "hang_probability": 0,
        "fail_probability": 0,
    })


def make_rows(outcomes: list[tuple[Answer, str, float]], arm: str, run_id: str) -> list[dict]:
    row_args = SimpleNamespace(
        source="stand-in", model="stand-in", rpm=SERVER_RPM, tpm=8000,
        rpd=1000, tpd=200000, itpm=7000, seed=SEED,
        latency_spread_ms=0, run_label="pass2", time_scale=1,
        retry_arm=arm, limits_basis="assumed", workers=REQUESTS,
    )
    return [
        result_row(
            answer, row_args, "naive_retry", run_id, number, PROMPT,
            at_utc, 0, REQUESTS * ARRIVAL_SPACING_S, DEADLINE_S,
            "experimental_fixed",
        )
        for number, (answer, at_utc, _) in enumerate(outcomes)
    ]


def summarize_arm(
    rows: list[dict], outcomes: list[tuple[Answer, str, float]],
    arm: str, run_id: str, arm_started: float,
) -> dict:
    delivered = [row for row in rows if row["status"] == "ok" and row["answer_kind"] == "full"]
    attempts = sum(row["http_attempts"] for row in rows)
    completion = [done_at for answer, _, done_at in outcomes if answer.status == "ok"]
    first_ms = (min(completion) - arm_started) * 1000 if completion else None
    return {
        "arm": arm,
        "run_id": run_id,
        "arrivals": len(rows),
        "delivered": len(delivered),
        "http_attempts": attempts,
        "extra_http_calls_per_delivered_answer":
            (attempts - len(rows)) / len(delivered) if delivered else None,
        "time_to_first_delivered_answer_ms": first_ms,
        "rate_limited": sum(row["status"] == "rate_limited" for row in rows),
        "timeout": sum(row["status"] == "timeout" for row in rows),
        "client_assumed_rpm": CLIENT_ASSUMED_RPM,
        "server_rpm": SERVER_RPM,
        "seed": SEED,
    }


async def run_arm(args: argparse.Namespace, jitter: bool, budgeted: bool) -> dict:
    arm = f"{'jitter' if jitter else 'no_jitter'}_{'budget10' if budgeted else 'unlimited'}"
    await configure_server(args.url)
    model = ModelClient(args.url, "stand-in", raw_log=args.attempt_log)
    pool = RetryPool(RETRY_BUDGET if budgeted else None)
    run_id = uuid.uuid4().hex[:10]
    arm_started = time.perf_counter()
    tasks = []
    try:
        for number in range(REQUESTS):
            tasks.append(asyncio.create_task(
                send_one(number, run_id, model, pool, jitter)
            ))
            if number + 1 < REQUESTS:
                await asyncio.sleep(ARRIVAL_SPACING_S)
        outcomes = await asyncio.gather(*tasks)
    finally:
        await model.close()
    rows = make_rows(outcomes, arm, run_id)
    append_rows(args.output, rows)
    return summarize_arm(rows, outcomes, arm, run_id, arm_started)


async def main(args: argparse.Namespace) -> None:
    local_url(args.url)
    arms = []
    for jitter, budgeted in ((False, False), (True, False), (False, True), (True, True)):
        summary = await run_arm(args, jitter, budgeted)
        arms.append(summary)
        print(json.dumps(summary), flush=True)
    records = [
        json.loads(line) for line in args.attempt_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    run_ids = {arm["run_id"] for arm in arms}
    bodies = [
        {"call_id": record["call_id"], "body": record["body_429"],
         "headers": record["headers"]}
        for record in records
        if record["call_id"].split("-")[0] in run_ids and record["body_429"]
    ]
    summary = {"arms": arms, "responses_429": bodies}
    args.summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved {len(bodies)} full 429 bodies to {args.summary}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--attempt-log", type=Path, default=Path("retry_attempts_raw.jsonl"))
    parser.add_argument("--summary", type=Path, default=Path("retry_experiment_summary.json"))
    asyncio.run(main(parser.parse_args()))
