import argparse
import asyncio
import csv
import json
import os
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from relay.budget_guard import (
    DEFAULT_CONFIG,
    DEFAULT_LEDGER,
    DailyCapExceeded,
    open_real_guard,
)
from relay.limits import Budget, Limits
from relay.model import ModelClient, set_standin_options
from relay.papers import SETTINGS, build_indexes
from relay.pipeline import (
    Answer,
    answer_question,
    defer_under_pressure,
    planned_call_tokens,
)

PAPER_FOLDER = (
    Path(__file__).resolve().parents[1] / "papers"
)  # Resolve papers beside the code.
DEFAULT_CALIBRATION = Path(
    "calibration.json"
)  # Keep probe observations separate from assumptions.
ASSUMED_CALL_P95_S = 2.0  # Replace with measured real-service p95 after probing.
ASSUMED_PATIENCE_S = 12.0  # Human abandonment threshold is unmeasured.
ASSUMED_DEADLINE_MARGIN_S = 1.0  # Leave one extra second around a short probe sample.
QUEUE_SIZE = 20  # Bound memory and give-up latency during bursts.
WORKERS = 2  # Match a cautious number of simultaneous provider calls.
DEFAULT_SEED = 7  # Reproduce stand-in hang and latency draws.
DEFAULT_LATENCY_SPREAD_MS = 300.0  # Used only when no calibration file exists.

QUESTIONS = [
    "What scaling factor is applied to dot products in scaled dot-product attention?",
    "What optimizer and learning-rate schedule did the Transformer paper use?",
    "How does Moshi handle overlapping speech during dialogue?",
    "What is Mimi's role in Moshi?",
    "What mechanism chooses chunk boundaries in H-Net?",
    "What is the difference between H-Net's dynamic chunking and fixed chunking?",
    "What was the exact electricity bill for the Moshi training run?",
    "Who won the 2026 football World Cup?",
]  # Mix answerable and unanswerable load requests.

SCENARIOS = {  # Arrival schedules exercise distinct pressure without hidden traffic.
    "probe": (4, 20.0, 80.0),
    "steady": (100, 20.0, 2000.0),
    "burst": (100, 0.05, 5.0),
    "overload": (100, 0.35, 35.0),
    "minute_halved": (40, 0.6, 24.0),
    "daily_exhausted": (10, 0.5, 5.0),
    "retry_failure": (3, 0.5, 2.0),
}

CSV_FIELDS = [
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade",
    "run_id",
    "question_id",
    "question",
    "answer",
    "given_up",
    "queue_ms",
    "work_ms",
    "retry_ms",
    "model_first_chunk_ms",
    "usage_measured",
    "verifier_mode",
    "passages",
    "error",
    "peak_queue",
    "scenario_duration_s",
    "queue_deadline_s",
    "queue_deadline_basis",
    "rpm",
    "tpm",
    "rpd",
    "tpd",
    "provider_headers_json",
    "model_total_ms",
    "provider_first_chunk_ms",
    "model_chunk_count",
    "piece_words",
    "pieces_requested",
    "rerank",
    "limits_basis",
    "workers",
    "cost_usd",
    "model_id",
    "evaluation_group",
    "itpm",
    "seed",
    "latency_spread_ms",
    "answer_kind",
    "run_label",
    "time_scale",
    "clock_label",
    "retry_arm",
    "arrival_pattern",
    "first_attempt_status",
    "http_attempts",
]  # Keep all required fields first; extras reproduce tables and calibration.


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def clock_label(scale: float) -> str:
    if scale == 1:
        return "real time"
    return f"{scale:g}x time-compressed"


def queue_deadline(calibration_path: Path) -> tuple[float, str]:
    if calibration_path.exists():
        data = json.loads(calibration_path.read_text(encoding="utf-8"))
        call_p95 = float(data["real_call_p95_s"])
        deadline = min(ASSUMED_PATIENCE_S, 2 * call_p95 + ASSUMED_DEADLINE_MARGIN_S)
        return deadline, "assumed_2xp95_plus_1s"
    deadline = min(
        ASSUMED_PATIENCE_S,
        2 * ASSUMED_CALL_P95_S + ASSUMED_DEADLINE_MARGIN_S,
    )
    return deadline, "assumed_call_p95"


def load_limits(args: argparse.Namespace) -> Limits:
    return Limits(args.rpm, args.tpm, args.rpd, args.tpd, getattr(args, "itpm", None))


def planned_batch_tokens(scenarios: list[str], index, verifier_mode: str) -> list[int]:
    planned = []
    for scenario in scenarios:
        count = SCENARIOS[scenario][0]
        for number in range(count):
            question = QUESTIONS[number % len(QUESTIONS)]
            planned.extend(planned_call_tokens(question, index, verifier_mode))
    return planned


def immediate_answer(
    text: str, status: str, given_up: bool, elapsed_ms: float, setting: str
) -> Answer:
    return Answer(
        text=text,
        status=status,
        given_up=given_up,
        first_token_ms=elapsed_ms,
        total_ms=elapsed_ms,
        queue_ms=elapsed_ms,
        work_ms=0,
        retry_ms=0,
        retries=0,
        tokens_in=0,
        tokens_out=0,
        model_first_chunk_ms=None,
        usage_measured=True,
        verifier_mode="none",
        retrieval_setting=setting,
        passages=0,
    )


class Dispatcher:
    def __init__(
        self,
        index,
        model,
        budget,
        verifier_mode: str,
        deadline_s: float,
        small_index=None,
    ):
        self.index = index
        self.small_index = small_index or index
        self.model = model
        self.budget = budget
        self.verifier_mode = verifier_mode
        self.deadline_s = deadline_s
        self.queue = asyncio.PriorityQueue(maxsize=QUEUE_SIZE)
        self.workers = []
        self.serial = 0
        self.peak_queue = 0

    async def start(self) -> None:
        self.workers = [asyncio.create_task(self.work()) for _ in range(WORKERS)]

    async def close(self) -> None:
        await self.queue.join()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def submit(
        self, question: str, priority: int = 0
    ) -> tuple[Answer, float, str]:
        submitted_at = time.perf_counter()
        start_time = datetime.now(timezone.utc).isoformat()
        future = asyncio.get_running_loop().create_future()
        self.serial += 1
        job = (priority, self.serial, question, submitted_at, future)
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            elapsed = (time.perf_counter() - submitted_at) * 1000
            return (
                immediate_answer(
                    "The service is busy; please retry shortly.",
                    "timeout",
                    True,
                    elapsed,
                    self.index.setting.name,
                ),
                submitted_at,
                start_time,
            )
        self.peak_queue = max(self.peak_queue, self.queue.qsize())
        return await future, submitted_at, start_time

    async def work(self) -> None:
        while True:
            _, _, question, submitted_at, future = await self.queue.get()
            try:
                if time.perf_counter() - submitted_at >= self.deadline_s:
                    elapsed = (time.perf_counter() - submitted_at) * 1000
                    result = immediate_answer(
                        "The request waited too long; please retry.",
                        "timeout",
                        True,
                        elapsed,
                        self.index.setting.name,
                    )
                else:
                    if self.verifier_mode == "auto" and await self.budget.pressure():
                        result = defer_under_pressure(
                            question, self.small_index, submitted_at
                        )
                    else:
                        result = await answer_question(
                            question,
                            self.index,
                            self.model,
                            self.budget,
                            self.verifier_mode,
                            submitted_at + self.deadline_s,
                            submitted_at,
                        )
                future.set_result(result)
            except Exception as error:  # noqa: BLE001 - Every queued request must receive a result.
                elapsed = (time.perf_counter() - submitted_at) * 1000
                result = immediate_answer(
                    "The request failed.",
                    "error",
                    False,
                    elapsed,
                    self.index.setting.name,
                )
                future.set_result(result)
                print(f"worker error: {type(error).__name__}: {error}")
            finally:
                self.queue.task_done()


async def configure_standin(
    url: str, scenario: str, limits: Limits, calibration_path: Path,
    seed: int, no_latency_spread: bool, time_scale: float,
) -> float:
    if time_scale != 1 and not calibration_path.exists():
        raise ValueError("Compressed runs need stand-in calibration")
    changes = {
        "reset": True,
        "seed": seed,
        "time_scale": time_scale,
        "rpm": limits.requests_per_minute,
        "tpm": limits.tokens_per_minute,
        "itpm": limits.input_tokens_per_minute or 7000,
        "rpd": limits.requests_per_day,
        "tpd": limits.tokens_per_day,
        "fail_probability": 0.0,
        "hang_probability": 0.025,
    }
    if calibration_path.exists():
        measured = json.loads(calibration_path.read_text(encoding="utf-8"))
        for field in (
            "base_ms",
            "latency_spread_ms",
            "chunk_ms",
            "slow_per_active_ms",
            "rpm_burst_fraction",
            "fail_probability",
            "hang_probability",
            "hang_ms",
        ):
            changes[field] = measured[field]
    latency_spread_ms = 0.0 if no_latency_spread else float(
        changes.get("latency_spread_ms", DEFAULT_LATENCY_SPREAD_MS)
    )
    changes["latency_spread_ms"] = latency_spread_ms
    for field in (
        "base_ms", "latency_spread_ms", "chunk_ms", "slow_per_active_ms", "hang_ms"
    ):
        if field in changes:
            changes[field] /= time_scale
    if scenario == "minute_halved":
        changes["tpm"] = max(1, limits.tokens_per_minute // 2)
    if scenario == "daily_exhausted":
        changes["exhaust_day"] = True
    if scenario == "retry_failure":
        changes["fail_probability"] = 1.0
    await set_standin_options(url, changes)
    return latency_spread_ms


def result_row(
    answer: Answer,
    args: argparse.Namespace,
    scenario: str,
    run_id: str,
    question_id: int,
    question: str,
    start_time: str,
    peak: int,
    duration_s: float,
    deadline_s: float,
    basis: str,
) -> dict:
    setting = SETTINGS.get(answer.retrieval_setting)
    row = {
        "request_id": f"{run_id}-{question_id}",
        "scenario": scenario,
        "source": args.source,
        "start_time": start_time,
        "answer_grade": "",
        "run_id": run_id,
        "question_id": question_id,
        "question": question,
        "answer": answer.text,
        "peak_queue": peak,
        "scenario_duration_s": duration_s,
        "queue_deadline_s": deadline_s,
        "queue_deadline_basis": basis,
        "rpm": args.rpm,
        "tpm": args.tpm,
        "rpd": args.rpd,
        "tpd": args.tpd,
        "piece_words": setting.words if setting else "",
        "pieces_requested": setting.pieces if setting else "",
        "rerank": "no" if setting else "",
        "limits_basis": getattr(args, "limits_basis", "assumed"),
        "workers": getattr(args, "workers", WORKERS if scenario in SCENARIOS else 1),
        "cost_usd": 0.0,
        "model_id": args.model
        or ("qwen/qwen3.8-27b" if args.source == "real" else "stand-in"),
        "evaluation_group": getattr(args, "evaluation_group", "") or "",
        "itpm": getattr(args, "itpm", None) or "",
        "seed": args.seed if args.source == "stand-in" else "",
        "latency_spread_ms": (
            getattr(args, "latency_spread_ms", "")
            if args.source == "stand-in" else ""
        ),
        "run_label": getattr(args, "run_label", ""),
        "time_scale": getattr(args, "time_scale", 1.0),
        "clock_label": clock_label(getattr(args, "time_scale", 1.0)),
        "retry_arm": getattr(args, "retry_arm", ""),
        "arrival_pattern": getattr(args, "arrival_pattern", ""),
        "first_attempt_status": getattr(args, "first_attempt_status", ""),
    }
    row.update(asdict(answer))
    return row


def append_rows(path: Path, rows: list[dict]) -> None:
    fresh = not path.exists()
    if not fresh:
        with path.open(newline="", encoding="utf-8") as file:
            header = next(csv.reader(file))
        if header != CSV_FIELDS:
            raise ValueError(f"{path} has an older schema; choose a fresh results file")
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if fresh:
            writer.writeheader()
        writer.writerows(rows)


async def run_scenario(
    args: argparse.Namespace,
    scenario: str,
    indexes: dict,
    model: ModelClient,
    deadline_s: float,
    basis: str,
) -> list[dict]:
    count, spacing_s, duration_s = SCENARIOS[scenario]
    if args.spacing_s is not None:
        spacing_s = args.spacing_s
        duration_s = count * spacing_s
    scale = args.time_scale if args.source == "stand-in" else 1.0
    limits = load_limits(args)
    if args.source == "stand-in":
        args.latency_spread_ms = await configure_standin(
            args.url, scenario, limits, args.standin_calibration,
            args.seed, args.no_latency_spread, scale,
        )
    budget = Budget(limits, time_scale=scale)
    dispatcher = Dispatcher(
        indexes[args.setting],
        model,
        budget,
        args.verifier,
        deadline_s / scale,
        indexes["small"],
    )
    await dispatcher.start()
    run_id = uuid.uuid4().hex[:10]
    tasks = []
    for number in range(count):
        question = QUESTIONS[number % len(QUESTIONS)]
        tasks.append(asyncio.create_task(dispatcher.submit(question)))
        if scenario == "steady" and (number + 1) % 3 == 0:
            print(f"steady: offered {number + 1}/{count}", flush=True)
        if number + 1 < count:
            await asyncio.sleep(spacing_s / scale)
    answers = await asyncio.gather(*tasks)
    await dispatcher.close()
    rows = [
        result_row(
            answer,
            args,
            scenario,
            run_id,
            number,
            QUESTIONS[number % len(QUESTIONS)],
            start_time,
            dispatcher.peak_queue,
            duration_s,
            deadline_s,
            basis,
        )
        for number, (answer, _, start_time) in enumerate(answers)
    ]
    return rows


async def main(args: argparse.Namespace) -> None:
    if args.source == "real" and not os.getenv("GROQ_API_KEY"):
        raise SystemExit(
            "Set GROQ_API_KEY in your environment. Never put it in the submission."
        )
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
    if args.url is None:
        args.url = (
            "https://api.groq.com/openai/v1"
            if args.source == "real"
            else "http://127.0.0.1:8000"
        )
    if args.model is None:
        args.model = "qwen/qwen3.8-27b" if args.source == "real" else "stand-in"
    if args.time_scale <= 0:
        raise ValueError("Time scale must be positive")
    if args.source != "stand-in" and args.time_scale != 1:
        raise ValueError("Time compression is stand-in only")
    if args.spacing_s is not None and (args.scenario != "steady" or args.spacing_s <= 0):
        raise ValueError("--spacing-s requires steady and a positive interval")
    indexes = build_indexes(PAPER_FOLDER)
    deadline_s, basis = queue_deadline(args.calibration)
    if args.deadline_s is not None:
        deadline_s = args.deadline_s
        basis = "assumed_probe_derived" if abs(deadline_s - 2.29) < 0.001 else "experimental_fixed"
    if deadline_s <= 0:
        raise ValueError("Deadline must be positive")
    if args.no_latency_spread and args.scenario != "minute_halved":
        raise ValueError("--no-latency-spread is only for the minute_halved scenario")
    scenarios = (
        ["steady", "burst", "overload", "minute_halved", "daily_exhausted"]
        if args.scenario == "all"
        else [args.scenario]
    )
    if guard is not None:
        print(f"Model for batch: {args.model}")
        guard.check_batch(
            planned_batch_tokens(scenarios, indexes[args.setting], args.verifier)
        )
    model = ModelClient(
        args.url,
        args.model,
        os.getenv("GROQ_API_KEY", "") if args.source == "real" else "",
        daily_guard=guard,
        raw_log=args.attempt_log,
    )
    try:
        for scenario in scenarios:
            rows = await run_scenario(args, scenario, indexes, model, deadline_s, basis)
            append_rows(args.output, rows)
            succeeded = sum(
                row["status"] == "ok" and row["answer_kind"] in ("full", "excerpt")
                for row in rows
            )
            print(
                f"{scenario}: {succeeded}/{len(rows)} ok; peak queue {rows[0]['peak_queue']}"
            )
    finally:
        await model.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["real", "stand-in"], default="stand-in")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="probe")
    parser.add_argument(
        "--setting", choices=["small", "medium", "large"], default="small"
    )
    parser.add_argument(
        "--verifier",
        choices=["deterministic", "model", "auto"],
        default="deterministic",
    )
    parser.add_argument("--url")
    parser.add_argument("--model")
    parser.add_argument("--rpm", type=int, default=30)
    parser.add_argument("--tpm", type=int, default=8000)
    parser.add_argument("--rpd", type=int, default=1000)
    parser.add_argument("--tpd", type=int, default=200000)
    parser.add_argument("--itpm", type=int, default=7000)
    parser.add_argument("--deadline-s", type=float)
    parser.add_argument("--spacing-s", type=float)
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--attempt-log", type=Path, default=Path("attempts_raw.jsonl"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--no-latency-spread", action="store_true")
    parser.add_argument(
        "--limits-basis", choices=["assumed", "account_console"], default="assumed"
    )
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument(
        "--standin-calibration", type=Path, default=Path("standin_calibration.json")
    )
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--budget-ledger", type=Path, default=DEFAULT_LEDGER)
    try:
        asyncio.run(main(parser.parse_args()))
    except (ValueError, DailyCapExceeded) as error:
        raise SystemExit(str(error)) from None
