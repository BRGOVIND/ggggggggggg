import argparse
import csv
import json
import statistics
from pathlib import Path

from relay.papers import build_indexes
from relay.pipeline import (
    DRAFT_MAX_TOKENS,
    VERIFY_MAX_TOKENS,
    draft_prompt,
    verify_prompt,
)
from relay.run import PAPER_FOLDER

TOKEN_HEADROOM = 0.85  # Keep space for tokenizer error and other account traffic.
MEASURED_ITPM = 7000  # Call 11's 429 reported this separate input-token cap.
MEASURED_VERIFY_INPUT = 2990  # Call 5 measured this exact medium-context prompt.
ANSWER_IDS = ("T01", "M02", "H02", "T05")  # Compare the same four answerable questions.
SETTINGS = ("small", "medium", "large")  # Match the retrieval comparison.


def load_rows(path: Path) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8") as file:
        return {row["request_id"]: row for row in csv.DictReader(file)}


def load_questions(path: Path) -> dict[str, str]:
    items = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item["question"] for item in items}


def measured_samples(summary: dict, rows: dict, questions: dict, indexes: dict) -> dict:
    samples = {}
    for call in summary["calls"][:4]:
        question_id = call["question_id"]
        question = questions[question_id]
        passages = indexes[call["setting"]].search(question)
        prompt = draft_prompt(question, passages)
        samples[question_id] = {
            "source_setting": call["setting"],
            "input_tokens": call["tokens_in"],
            "output_tokens": call["tokens_out"],
            "draft": rows[call["request_id"]]["answer"],
            "tokens_per_char": call["tokens_in"] / len(prompt),
        }
    return samples


def verifier_scale(
    summary: dict, samples: dict, questions: dict, indexes: dict
) -> float:
    sample = samples["T05"]
    question = questions["T05"]
    passages = indexes["medium"].search(question)
    prompt = verify_prompt(question, sample["draft"], passages)
    predicted = sample["tokens_per_char"] * len(prompt)
    return summary["calls"][4]["tokens_in"] / predicted


def one_question(
    question_id: str,
    setting: str,
    sample: dict,
    question: str,
    index,
    scale: float,
) -> tuple[float, float, float, float, float, float]:
    passages = index.search(question)
    draft = sample["draft"]
    draft_input = round(
        sample["tokens_per_char"] * len(draft_prompt(question, passages))
    )
    draft_output = sample["output_tokens"]
    if setting == sample["source_setting"]:
        draft_input = sample["input_tokens"]
    verify_input = round(
        sample["tokens_per_char"]
        * len(verify_prompt(question, draft, passages))
        * scale
    )
    if question_id == "T05" and setting == "medium":
        verify_input = MEASURED_VERIFY_INPUT
    usage_deterministic = draft_input + draft_output
    usage_model = usage_deterministic + verify_input + 2
    reserved_deterministic = draft_input + DRAFT_MAX_TOKENS
    reserved_model = draft_input + verify_input + DRAFT_MAX_TOKENS + VERIFY_MAX_TOKENS
    return (
        usage_deterministic,
        usage_model,
        reserved_deterministic,
        reserved_model,
        draft_input,
        draft_input + verify_input,
    )


def capacity_rows(
    summary: dict, rows: dict, questions: dict, indexes: dict, tpm: int, itpm: int
) -> list[dict]:
    samples = measured_samples(summary, rows, questions, indexes)
    scale = verifier_scale(summary, samples, questions, indexes)
    result = []
    for setting in SETTINGS:
        values = [
            one_question(
                question_id,
                setting,
                samples[question_id],
                questions[question_id],
                indexes[setting],
                scale,
            )
            for question_id in ANSWER_IDS
        ]
        for mode, usage_at, reserved_at, input_at in (
            ("deterministic", 0, 2, 4),
            ("model", 1, 3, 5),
        ):
            usage = statistics.mean(item[usage_at] for item in values)
            reserved = statistics.mean(item[reserved_at] for item in values)
            input_tokens = statistics.mean(item[input_at] for item in values)
            input_capacity = itpm * TOKEN_HEADROOM / input_tokens
            reserved_capacity = tpm * TOKEN_HEADROOM / reserved
            effective = min(input_capacity, reserved_capacity)
            calls_per_answer = 2 if mode == "model" else 1
            result.append(
                {
                    "setting": setting,
                    "verifier": mode,
                    "api_calls_per_answer": calls_per_answer,
                    "mean_input_tokens_per_answer": round(input_tokens),
                    "mean_usage_tokens_per_answer": round(usage),
                    "mean_reserved_tokens_per_answer": round(reserved),
                    "answers_per_minute_if_usage_counts": round(
                        tpm * TOKEN_HEADROOM / usage, 2
                    ),
                    "answers_per_minute_if_max_reserved": round(reserved_capacity, 2),
                    "answers_per_minute_under_itpm": round(input_capacity, 2),
                    "effective_answers_per_minute_if_max_reserved": round(effective, 2),
                    "api_calls_per_minute_if_max_reserved": round(
                        effective * calls_per_answer, 2
                    ),
                    "evidence": "observed calls 1-5 plus prompt-length extrapolation; revised large setting is unmeasured"
                    if setting == "large"
                    else "observed calls 1-5 plus prompt-length extrapolation",
                }
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("results.csv"))
    parser.add_argument("--summary", type=Path, default=Path("probe_1_9_summary.json"))
    parser.add_argument(
        "--questions", type=Path, default=Path("questions_candidate.json")
    )
    parser.add_argument("--output", type=Path, default=Path("capacity_measured.csv"))
    parser.add_argument("--tpm", type=int, default=8000)
    parser.add_argument("--itpm", type=int, default=MEASURED_ITPM)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    rows = load_rows(args.results)
    questions = load_questions(args.questions)
    indexes = build_indexes(PAPER_FOLDER)
    result = capacity_rows(summary, rows, questions, indexes, args.tpm, args.itpm)
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=result[0])
        writer.writeheader()
        writer.writerows(result)
    print(f"Wrote {args.output}; evidence labels identify extrapolated settings")


if __name__ == "__main__":
    main()
