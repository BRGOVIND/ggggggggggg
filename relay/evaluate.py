import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from relay.budget_guard import (
    DEFAULT_CONFIG,
    DEFAULT_LEDGER,
    DailyCapExceeded,
    open_real_guard,
)
from relay.limits import Budget, estimate_input_tokens
from relay.model import ModelClient, count_tokens_roughly
from relay.papers import build_indexes
from relay.pipeline import (
    DRAFT_MAX_TOKENS,
    answer_question,
    draft_prompt,
    planned_call_tokens,
)
from relay.run import (
    PAPER_FOLDER,
    append_rows,
    load_limits,
    queue_deadline,
    result_row,
)

QUESTION_FILE = Path(
    "questions_candidate.json"
)  # Human verification precedes gold grading.


def load_questions(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_questions(questions: list[dict], count: int) -> list[dict]:
    if count < 1:
        raise ValueError("Question count must be positive")
    if count >= len(questions):
        return questions
    unanswerable = [item for item in questions if item["paper"] == "none"]
    answerable_total = len(questions) - len(unanswerable)
    refusals = min(len(unanswerable), max(min(3, count // 4), count - answerable_total))
    by_paper = {
        paper: [item for item in questions if item["paper"] == paper]
        for paper in ("Attention Is All You Need", "Moshi", "Dynamic Chunking")
    }
    answerable = []
    while len(answerable) < count - refusals:
        added = False
        for items in by_paper.values():
            if items and len(answerable) < count - refusals:
                answerable.append(items.pop(0))
                added = True
        if not added:
            break
    return answerable + unanswerable[:refusals]


def choose_question_ids(questions: list[dict], ids: list[str]) -> list[dict]:
    if len(ids) != len(set(ids)):
        raise ValueError("Question IDs must be unique")
    by_id = {item["id"]: item for item in questions}
    unknown = [question_id for question_id in ids if question_id not in by_id]
    if unknown:
        raise ValueError(f"Unknown question IDs: {', '.join(unknown)}")
    return [by_id[question_id] for question_id in ids]


async def evaluate(args: argparse.Namespace) -> None:
    if args.source == "real" and not os.getenv("GROQ_API_KEY"):
        raise SystemExit(
            "Set GROQ_API_KEY in your environment; do not paste it into a file."
        )
    if args.source == "real" and args.allow_unverified:
        raise SystemExit("Real quality evaluation requires verified questions")
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
    selected = select_questions(
        load_questions(args.questions), args.count
    )
    if args.question_ids:
        selected = choose_question_ids(selected, args.question_ids)
    if args.offset < 0 or args.offset >= len(selected):
        raise ValueError("Question offset is outside selected set")
    if args.batch_size is not None and args.batch_size < 1:
        raise ValueError("Batch size must be positive")
    end = None if args.batch_size is None else args.offset + args.batch_size
    questions = selected[args.offset : end]
    if not args.allow_unverified and any(not item["verified"] for item in questions):
        raise SystemExit(
            "Verify every selected question and PDF page before evaluation."
        )
    indexes = build_indexes(PAPER_FOLDER)
    settings = list(indexes) if args.setting == "all" else [args.setting]
    if guard is not None:
        print(f"Model for batch: {args.model}")
        planned = []
        for setting in settings:
            for item in questions:
                planned.extend(
                    planned_call_tokens(
                        item["question"], indexes[setting], args.verifier
                    )
                )
        guard.check_batch(planned)
    limits = load_limits(args)
    budget = Budget(limits)
    deadline_s, basis = queue_deadline(args.calibration)
    url = args.url or (
        "https://api.groq.com/openai/v1"
        if args.source == "real"
        else "http://127.0.0.1:8000"
    )
    model_name = args.model or (
        "qwen/qwen3.8-27b" if args.source == "real" else "stand-in"
    )
    model = ModelClient(
        url,
        model_name,
        os.getenv("GROQ_API_KEY", "") if args.source == "real" else "",
        daily_guard=guard,
        raw_log=Path("evaluation_raw.jsonl") if args.source == "real" else None,
        show_usage=args.source == "real",
    )
    try:
        for setting in settings:
            run_id = uuid.uuid4().hex[:10]
            index = indexes[setting]
            for number, item in enumerate(questions):
                question = item["question"]
                passages = index.search(question)
                prompt = draft_prompt(question, passages)
                estimate = count_tokens_roughly(prompt) + DRAFT_MAX_TOKENS
                if not await budget.wait_for_room(
                    estimate, input_tokens=estimate_input_tokens(prompt)
                ):
                    print("Daily budget or per-call TPM exhausted; evaluation stopped.")
                    return
                submitted_at = time.perf_counter()
                start_time = datetime.now(timezone.utc).isoformat()
                answer = await answer_question(
                    question,
                    index,
                    model,
                    budget,
                    args.verifier,
                    submitted_at + deadline_s,
                    submitted_at,
                )
                row = result_row(
                    answer,
                    args,
                    "retrieval_comparison",
                    run_id,
                    number,
                    question,
                    start_time,
                    0,
                    0,
                    deadline_s,
                    basis,
                )
                row["question_id"] = item["id"]
                append_rows(args.output, [row])
                print(
                    f"{setting} {item['id']}: {answer.status}, {answer.tokens_in + answer.tokens_out} tokens"
                )
                if answer.status == "rate_limited" and "daily" in answer.error.lower():
                    return
    finally:
        await model.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=QUESTION_FILE)
    parser.add_argument("--allow-unverified", action="store_true")
    parser.add_argument("--source", choices=["real", "stand-in"], default="real")
    parser.add_argument(
        "--setting", choices=["all", "small", "medium", "large"], default="all"
    )
    parser.add_argument(
        "--verifier", choices=["deterministic", "model"], default="deterministic"
    )
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--question-ids", nargs="+")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--evaluation-group")
    parser.add_argument("--url")
    parser.add_argument("--model")
    parser.add_argument("--rpm", type=int, default=30)
    parser.add_argument("--tpm", type=int, default=8000)
    parser.add_argument("--rpd", type=int, default=1000)
    parser.add_argument("--tpd", type=int, default=200000)
    parser.add_argument("--itpm", type=int, default=7000)
    parser.add_argument(
        "--limits-basis", choices=["assumed", "account_console"], default="assumed"
    )
    parser.add_argument("--calibration", type=Path, default=Path("calibration.json"))
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--budget-ledger", type=Path, default=DEFAULT_LEDGER)
    try:
        asyncio.run(evaluate(parser.parse_args()))
    except (ValueError, DailyCapExceeded) as error:
        raise SystemExit(str(error)) from None
