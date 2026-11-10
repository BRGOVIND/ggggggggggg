import argparse
import asyncio
import json
import math
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
SCHEDULES = (  # Pair each retry arm with a no-retry baseline at the same arrival rate.
    ("spaced_0.1s", 0.1, (7,)),
    ("burst_under_1s", 0.02, (7, 8, 9)),
)
RETRY_BUDGET = 10  # Cap extra calls in the budgeted arms.
ARMS = (  # Run zero-retry first to freeze the matched baseline.
    ("zero_retry", False, 0),
    ("no_jitter_unlimited", False, None),
    ("jitter_unlimited", True, None),
    ("no_jitter_budget10", False, RETRY_BUDGET),
    ("jitter_budget10", True, RETRY_BUDGET),
)
DEADLINE_S = 20.0  # Compare retries under the long experimental deadline.
SERVER_RPM = 10  # Deliberately below the client's assumed 30 RPM.
CLIENT_ASSUMED_RPM = 30  # The naive client does not enforce this limit.
JITTER_MAX_S = 2.0  # Spread retries after the provider's retry-after.
RETRY_EFFECT_TOLERANCE_MS = 20.0  # Ignore small host-scheduling shifts in paired latency.
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


@dataclass(frozen=True)
class RequestResult:
    answer: Answer
    at_utc: str
    completed_at: float
    first_attempt_status: str
    first_attempt_started_at: float | None
    first_attempt_finished_at: float | None
    retry_started_at: float | None
    retry_finished_at: float | None


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
    jitter: bool, seed: int,
) -> RequestResult:
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
    first_attempt_status = "not_sent"
    first_started = None
    first_finished = None
    retry_started = None
    retry_finished = None
    for attempt in range(2):
        if time.perf_counter() >= deadline:
            status = "timeout"
            text = "The request took too long; please retry."
            break
        attempts += 1
        attempt_started = time.perf_counter()
        if attempt == 0:
            first_started = attempt_started
        else:
            retry_started = attempt_started
        try:
            remaining = deadline - time.perf_counter()
            reply = await asyncio.wait_for(
                model.complete(PROMPT, 40, call_id=f"{run_id}-{number}-{attempt + 1}"),
                timeout=remaining,
            )
            attempt_finished = time.perf_counter()
            if attempt == 0:
                first_attempt_status = "ok"
                first_finished = attempt_finished
            else:
                retry_finished = attempt_finished
            headers.append(reply.headers)
            tokens_in += reply.tokens_in
            tokens_out += reply.tokens_out
            text = reply.text
            status = "ok"
            break
        except ModelFailure as error:
            attempt_finished = time.perf_counter()
            if attempt == 0:
                first_attempt_status = "rate_limited" if error.status == 429 else "error"
                first_finished = attempt_finished
            else:
                retry_finished = attempt_finished
            headers.append(error.headers)
            if error.status != 429:
                status = "error"
                text = "The request failed."
                break
            if attempt or error.retry_after_s is None:
                break
            spread = random.Random(seed + number).uniform(0, JITTER_MAX_S) if jitter else 0
            delay = error.retry_after_s + spread
            if time.perf_counter() + delay >= deadline or not pool.take():
                break
            wait_started = time.perf_counter()
            await asyncio.sleep(delay)
            retry_ms += (time.perf_counter() - wait_started) * 1000
        except (asyncio.TimeoutError, httpx.TimeoutException):
            attempt_finished = time.perf_counter()
            if attempt == 0:
                first_attempt_status = "timeout"
                first_finished = attempt_finished
            else:
                retry_finished = attempt_finished
            status = "timeout"
            text = "The request took too long; please retry."
            break
        except httpx.TransportError:
            attempt_finished = time.perf_counter()
            if attempt == 0:
                first_attempt_status = "error"
                first_finished = attempt_finished
            else:
                retry_finished = attempt_finished
            status = "error"
            text = "The request failed."
            break
    completed_at = time.perf_counter()
    elapsed_ms = (completed_at - arrived) * 1000
    answer = result_answer(
        text, status, elapsed_ms, retry_ms, attempts, headers, tokens_in, tokens_out
    )
    return RequestResult(
        answer, at_utc, completed_at, first_attempt_status,
        first_started, first_finished, retry_started, retry_finished,
    )


async def configure_server(url: str, seed: int) -> None:
    await set_standin_options(url, {
        "reset": True,
        "seed": seed,
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


def make_rows(
    outcomes: list[RequestResult], arm: str, run_id: str,
    pattern: str, spacing_s: float, seed: int,
) -> list[dict]:
    row_args = SimpleNamespace(
        source="stand-in", model="stand-in", rpm=SERVER_RPM, tpm=8000,
        rpd=1000, tpd=200000, itpm=7000, seed=seed,
        latency_spread_ms=0, run_label="pass3", time_scale=1,
        retry_arm=arm, arrival_pattern=pattern, limits_basis="assumed",
        evaluation_group=f"{pattern}_seed{seed}", workers=REQUESTS,
    )
    rows = []
    for number, outcome in enumerate(outcomes):
        row = result_row(
            outcome.answer, row_args, "naive_retry", run_id, number, PROMPT,
            outcome.at_utc, 0, REQUESTS * spacing_s, DEADLINE_S,
            "experimental_fixed",
        )
        row["first_attempt_status"] = outcome.first_attempt_status
        rows.append(row)
    return rows


def delivered(result: RequestResult) -> bool:
    return result.answer.status == "ok" and result.answer.answer_kind == "full"


def censored_percentile(results: list[RequestResult], fraction: float) -> float | None:
    if not results:
        return None
    rank = math.ceil(len(results) * fraction)
    times = sorted(result.answer.total_ms for result in results if delivered(result))
    return times[rank - 1] if len(times) >= rank else None


def first_success_cohort(baseline: list[RequestResult]) -> list[int]:
    return [
        number for number, result in enumerate(baseline)
        if result.first_attempt_status == "ok"
    ]


def affected_first_successes(
    results: list[RequestResult], baseline: list[RequestResult],
) -> tuple[list[int], list[int]]:
    displaced = []
    slower = []
    for number in first_success_cohort(baseline):
        current = results[number]
        if current.first_attempt_started_at is None:
            continue
        prior_retry = any(
            other.retry_started_at is not None
            and other.retry_started_at <= current.first_attempt_started_at
            for other in results if other is not current
        )
        if current.first_attempt_status != "ok" and prior_retry:
            displaced.append(number)
            continue
        overlapping_retry = any(
            other.retry_started_at is not None
            and other.retry_finished_at is not None
            and current.first_attempt_finished_at is not None
            and other.retry_started_at <= current.first_attempt_finished_at
            and other.retry_finished_at >= current.first_attempt_started_at
            for other in results if other is not current
        )
        if (
            current.first_attempt_status == "ok"
            and overlapping_retry
            and current.answer.total_ms
            > baseline[number].answer.total_ms + RETRY_EFFECT_TOLERANCE_MS
        ):
            slower.append(number)
    return displaced, slower


def retry_made_worse(
    delivered_count: int, baseline_count: int,
    cohort_p95: float | None, baseline_p95: float | None,
) -> bool:
    if delivered_count < baseline_count:
        return True
    if baseline_p95 is None:
        return False
    return cohort_p95 is None or cohort_p95 > baseline_p95


def summarize_arm(
    rows: list[dict], outcomes: list[RequestResult],
    baseline: list[RequestResult], arm: str, run_id: str,
    pattern: str, spacing_s: float, seed: int, arm_started: float,
) -> dict:
    answered = [result for result in outcomes if delivered(result)]
    attempts = sum(row["http_attempts"] for row in rows)
    first_ms = (
        (min(result.completed_at for result in answered) - arm_started) * 1000
        if answered else None
    )
    cohort = first_success_cohort(baseline)
    cohort_p95 = censored_percentile([outcomes[number] for number in cohort], 0.95)
    baseline_p95 = censored_percentile([baseline[number] for number in cohort], 0.95)
    displaced, slower = affected_first_successes(outcomes, baseline)
    waited = [result for result in outcomes if result.answer.retry_ms > 0]
    return {
        "arm": arm,
        "run_id": run_id,
        "arrival_pattern": pattern,
        "arrival_spacing_s": spacing_s,
        "seed": seed,
        "clock_label": "real time",
        "arrivals": len(rows),
        "delivered": len(answered),
        "http_attempts": attempts,
        "extra_http_calls_per_delivered_answer":
            (attempts - len(rows)) / len(answered) if answered else None,
        "time_to_first_delivered_answer_ms": first_ms,
        "p50_delivery_ms_all_40": censored_percentile(outcomes, 0.5),
        "p95_delivery_ms_all_40": censored_percentile(outcomes, 0.95),
        "baseline_first_successes": len(cohort),
        "p95_baseline_first_success_cohort_ms": cohort_p95,
        "zero_retry_cohort_p95_ms": baseline_p95,
        "retry_made_it_worse": retry_made_worse(
            len(answered), sum(delivered(result) for result in baseline),
            cohort_p95, baseline_p95,
        ),
        "delayed_requests": len(waited),
        "delayed_delivered": sum(delivered(result) for result in waited),
        "delayed_without_answer": sum(not delivered(result) for result in waited),
        "retry_wait_ms_total": sum(result.answer.retry_ms for result in waited),
        "baseline_first_successes_displaced_after_retry": len(displaced),
        "baseline_first_successes_slower_with_overlapping_retry": len(slower),
        "displaced_request_indices": displaced,
        "slower_request_indices": slower,
        "rate_limited": sum(row["status"] == "rate_limited" for row in rows),
        "timeout": sum(row["status"] == "timeout" for row in rows),
        "client_assumed_rpm": CLIENT_ASSUMED_RPM,
        "server_rpm": SERVER_RPM,
    }


async def run_arm(
    args: argparse.Namespace, arm: str, jitter: bool, retry_limit: int | None,
    pattern: str, spacing_s: float, seed: int,
    baseline: list[RequestResult] | None,
) -> tuple[dict, list[RequestResult]]:
    await configure_server(args.url, seed)
    model = ModelClient(args.url, "stand-in", raw_log=args.attempt_log)
    pool = RetryPool(retry_limit)
    run_id = uuid.uuid4().hex[:10]
    arm_started = time.perf_counter()
    tasks = []
    try:
        for number in range(REQUESTS):
            tasks.append(asyncio.create_task(
                send_one(number, run_id, model, pool, jitter, seed)
            ))
            if number + 1 < REQUESTS:
                next_at = arm_started + (number + 1) * spacing_s
                await asyncio.sleep(max(0, next_at - time.perf_counter()))
        outcomes = await asyncio.gather(*tasks)
    finally:
        await model.close()
    rows = make_rows(outcomes, arm, run_id, pattern, spacing_s, seed)
    append_rows(args.output, rows)
    summary = summarize_arm(
        rows, outcomes, baseline or outcomes, arm, run_id,
        pattern, spacing_s, seed, arm_started,
    )
    return summary, outcomes


def save_summary(args: argparse.Namespace, arms: list[dict]) -> None:
    run_ids = {arm["run_id"] for arm in arms}
    bodies = []
    with args.attempt_log.open(encoding="utf-8") as file:
        for line in file:
            record = json.loads(line)
            if record["call_id"].split("-")[0] not in run_ids:
                continue
            if record["body_429"]:
                bodies.append({
                    "call_id": record["call_id"],
                    "body": record["body_429"],
                    "headers": record["headers"],
                })
    summary = {"arms": arms, "responses_429": bodies}
    args.summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved {len(bodies)} full 429 bodies to {args.summary}")


def print_table(arms: list[dict]) -> None:
    print("| Pattern | Seed | Arm | Delivered | Calls | Extra calls/delivery | First delivery ms | p50/p95 all 40 ms | Baseline cohort p95 ms | Delayed (delivered/failed) | First successes displaced/slower | Worse? |")
    print("|---|---:|---|---:|---:|---:|---:|---|---:|---|---|---|")
    for arm in arms:
        p50 = arm["p50_delivery_ms_all_40"]
        p95 = arm["p95_delivery_ms_all_40"]
        p50_text = f"{p50:.0f}" if p50 is not None else "not reached"
        p95_text = f"{p95:.0f}" if p95 is not None else "not reached"
        extra = arm["extra_http_calls_per_delivered_answer"]
        extra_text = f"{extra:.2f}" if extra is not None else "not measured"
        first = arm["time_to_first_delivered_answer_ms"]
        first_text = f"{first:.0f}" if first is not None else "not reached"
        cohort = arm["p95_baseline_first_success_cohort_ms"]
        cohort_text = f"{cohort:.0f}" if cohort is not None else "not reached"
        print(
            f"| {arm['arrival_pattern']} | {arm['seed']} | {arm['arm']} | "
            f"{arm['delivered']}/{arm['arrivals']} | {arm['http_attempts']} | "
            f"{extra_text} | {first_text} | {p50_text} / {p95_text} | {cohort_text} | "
            f"{arm['delayed_requests']} ({arm['delayed_delivered']}/{arm['delayed_without_answer']}) | "
            f"{arm['baseline_first_successes_displaced_after_retry']}/"
            f"{arm['baseline_first_successes_slower_with_overlapping_retry']} | "
            f"{arm['retry_made_it_worse']} |"
        )


async def main(args: argparse.Namespace) -> None:
    local_url(args.url)
    arms = []
    for pattern, spacing_s, seeds in SCHEDULES:
        for seed in seeds:
            baseline = None
            for arm, jitter, retry_limit in ARMS:
                summary, outcomes = await run_arm(
                    args, arm, jitter, retry_limit, pattern, spacing_s,
                    seed, baseline,
                )
                if baseline is None:
                    baseline = outcomes
                arms.append(summary)
                print(f"{pattern} seed {seed}: {arm} {summary['delivered']}/{REQUESTS}", flush=True)
    save_summary(args, arms)
    print_table(arms)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--attempt-log", type=Path, default=Path("retry_experiment_v2_raw.jsonl"))
    parser.add_argument("--summary", type=Path, default=Path("retry_experiment_v2_summary.json"))
    asyncio.run(main(parser.parse_args()))
