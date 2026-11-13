import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

PERCENTILES = (0.5, 0.95, 0.99)  # Match the brief's median and slow-end columns.
VALID_STATUSES = {
    "ok",
    "rate_limited",
    "timeout",
    "error",
}  # Match the raw-file contract.
VALID_GRADES = {
    "",
    "correct",
    "wrong",
    "refused_correctly",
    "refused_wrongly",
}  # Keep non-answers blank.
LOAD_SCENARIOS = {  # Keep probes and retrieval comparison out of load tables.
    "steady",
    "burst",
    "overload",
    "minute_halved",
    "daily_exhausted",
}
ANSWER_KINDS = {"full", "excerpt", "none"}  # A notice is not an answer.
LOAD_ORDER = {  # Keep the two tables in the same readable scenario order.
    "steady": 0,
    "burst": 1,
    "overload": 2,
    "minute_halved": 3,
    "daily_exhausted": 4,
}


def validate_rows(rows: list[dict]) -> None:
    if not rows:
        raise ValueError("No request rows in results.csv")
    seen = set()
    for row in rows:
        request_id = row["request_id"]
        if request_id in seen:
            raise ValueError(f"Duplicate request ID: {request_id}")
        seen.add(request_id)
        if row["source"] not in ("real", "stand-in"):
            raise ValueError(f"Invalid source: {request_id}")
        if row["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {request_id}")
        kind = row.get("answer_kind") or ("full" if row["status"] == "ok" else "none")
        if kind not in ANSWER_KINDS:
            raise ValueError(f"Invalid answer kind: {request_id}")
        if row["status"] == "ok" and kind == "none":
            raise ValueError(f"OK row has no answer: {request_id}")
        if row["status"] != "ok" and kind != "none":
            raise ValueError(f"Non-OK row claims an answer: {request_id}")
        if row["answer_grade"] not in VALID_GRADES:
            raise ValueError(f"Invalid answer grade: {request_id}")
        if row["status"] != "ok" and row["answer_grade"]:
            raise ValueError(f"Non-answer has grade: {request_id}")
        if row["answer_grade"] == "correct" and "PDF p." not in row["answer"]:
            raise ValueError(f"Correct grade lacks citation: {request_id}")
        total = float(row["total_ms"])
        parts = sum(float(row[field]) for field in ("queue_ms", "work_ms", "retry_ms"))
        if abs(total - parts) > 25:
            raise ValueError(f"Latency parts do not match total: {request_id}")
        if row["first_token_ms"] and float(row["first_token_ms"]) > total + 1:
            raise ValueError(f"First token follows completion: {request_id}")
        if int(row["tokens_in"]) < 0 or int(row["tokens_out"]) < 0:
            raise ValueError(f"Negative token count: {request_id}")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def number(value: float | None, digits: int = 0) -> str:
    return "not measured" if value is None else f"{value:.{digits}f}"


def values(rows: list[dict], field: str) -> list[float]:
    return [
        float(row[field]) * float(row.get("time_scale") or 1)
        for row in rows
        if row[field] not in ("", None)
    ]


def delivered(row: dict) -> bool:
    return row["status"] == "ok" and row.get("answer_kind", "full") in (
        "full",
        "excerpt",
    )


def latency(rows: list[dict], field: str, fractions: tuple[float, ...]) -> str:
    samples = values(rows, field)
    return " / ".join(
        number(percentile(samples, fraction), 1) for fraction in fractions
    )


def percentage(rows: list[dict], predicate) -> str:
    return number(100 * sum(predicate(row) for row in rows) / len(rows), 1)


def count_percentage(rows: list[dict], predicate) -> str:
    count = sum(predicate(row) for row in rows)
    return f"{100 * count / len(rows):.1f} ({count}/{len(rows)})"


def mean_tokens(rows: list[dict]) -> str:
    answered = [row for row in rows if delivered(row)]
    if not answered:
        return "not measured"
    total = sum(int(row["tokens_in"]) + int(row["tokens_out"]) for row in answered)
    return number(total / len(answered))


def cost_per_thousand(rows: list[dict]) -> str:
    answered = sum(delivered(row) for row in rows)
    if not answered:
        return "not measured"
    cost = sum(float(row["cost_usd"]) for row in rows) / answered * 1000
    return f"${cost:.2f}"


def ordered_load_groups(groups: dict[str, list[dict]]) -> list[list[dict]]:
    return sorted(
        groups.values(),
        key=lambda group: (
            group[0].get("run_label", ""),
            LOAD_ORDER[group[0]["scenario"]],
            float(group[0]["queue_deadline_s"]),
            -float(group[0].get("latency_spread_ms") or 0),
        ),
    )


def table2(rows: list[dict]) -> None:
    groups = defaultdict(list)
    for row in rows:
        if row["source"] != "stand-in" or row["scenario"] not in LOAD_SCENARIOS:
            continue
        groups[row["run_id"]].append(row)
    print(
        "## Table 2a: latency (delivered answers only; 5x time-compressed runs in virtual ms)"
    )
    print(
        "| Pass | Scenario | Deadline s | latency_spread_ms | Clock | Seed | Sent | Answered | Duration s | Offered/min | First token p50/p95/p99 ms | Total p50/p95/p99 ms |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for group in ordered_load_groups(groups):
        first = group[0]
        duration = float(first["scenario_duration_s"])
        offered = len(group) / duration * 60 if duration else 0
        spread = float(first.get("latency_spread_ms") or 0)
        answered = [row for row in group if delivered(row)]
        print(
            f"| {first.get('run_label', '')} | {first['scenario']} | {first['queue_deadline_s']} | "
            f"{spread:.1f} | {first.get('clock_label') or 'real time'} | {first.get('seed', '')} | "
            f"{len(group)} | {len(answered)} | {duration:.1f} | {offered:.1f} | "
            f"{latency(answered, 'first_token_ms', PERCENTILES)} | {latency(answered, 'total_ms', PERCENTILES)} |"
        )
    print("\n## Table 2b: outcomes")
    print(
        "| Pass | Scenario | Deadline s | Clock | latency_spread_ms | Full % (count) | Excerpt % (count) | Rate-limited % (count) | Timeout/given-up % (count) | Retries/request | Peak queue | Tokens/answer | Cost/1,000 answers |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for group in ordered_load_groups(groups):
        first = group[0]
        retries = sum(int(row["retries"]) for row in group) / len(group)
        peak = max(int(row["peak_queue"]) for row in group)
        spread = float(first.get("latency_spread_ms") or 0)
        print(
            f"| {first.get('run_label', '')} | {first['scenario']} | {first['queue_deadline_s']} | "
            f"{first.get('clock_label') or 'real time'} | "
            f"{spread:.1f} | "
            f"{count_percentage(group, lambda row: delivered(row) and row.get('answer_kind', 'full') == 'full')} | "
            f"{count_percentage(group, lambda row: delivered(row) and row.get('answer_kind') == 'excerpt')} | "
            f"{count_percentage(group, lambda row: row['status'] == 'rate_limited')} | "
            f"{count_percentage(group, lambda row: row['status'] == 'timeout' or row['given_up'] == 'True')} | "
            f"{retries:.2f} | {peak} | {mean_tokens(group)} | {cost_per_thousand(group)} |"
        )


def table3(rows: list[dict]) -> None:
    groups = defaultdict(list)
    for row in rows:
        if row["scenario"] == "retrieval_comparison" and row["source"] == "real":
            group = row.get("evaluation_group") or row["run_id"]
            groups[
                (group, row["retrieval_setting"], row["verifier_mode"], row["model_id"])
            ].append(row)
    print("\n## Table 3: retrieval")
    print(
        "| Setting | Questions | Correct and cited % | Wrong % | Correctly refused % | No answer % | Tokens/answer | First token p50/p95 ms | Total p50/p95 ms |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---|---|")
    model_rows = 0
    model_questions = set()
    for (group_name, setting, verifier, _), group in groups.items():
        question_ids = [row["question_id"] for row in group]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError(
                f"Repeated question in evaluation group {group_name}: {setting}"
            )
        graded = all(
            row["answer_grade"] if row["status"] == "ok" else not row["answer_grade"]
            for row in group
        )
        correct = (
            percentage(
                group,
                lambda row: (
                    row["answer_grade"] == "correct" and "PDF p." in row["answer"]
                ),
            )
            if graded
            else "not measured"
        )
        wrong = (
            percentage(
                group, lambda row: row["answer_grade"] in ("wrong", "refused_wrongly")
            )
            if graded
            else "not measured"
        )
        refused = (
            percentage(group, lambda row: row["answer_grade"] == "refused_correctly")
            if graded
            else "not measured"
        )
        if verifier == "model":
            model_rows += len(group)
            model_questions.update(row["question_id"] for row in group)
            correct = wrong = refused = "cost only"
        answered = [row for row in group if delivered(row)]
        first = group[0]
        label = (
            f"{setting}: {first['piece_words']} words, {first['pieces_requested']} pieces, "
            f"re-rank {first['rerank']}; {verifier}; group {group_name}"
        )
        print(
            f"| {label} | {len(group)} | {correct} | {wrong} | {refused} | "
            f"{percentage(group, lambda row: row['status'] != 'ok')} | "
            f"{mean_tokens(group)} | {latency(answered, 'first_token_ms', (0.5, 0.95))} | "
            f"{latency(answered, 'total_ms', (0.5, 0.95))} |"
        )
    if model_rows:
        print(
            f"Model-verifier comparison: {len(model_questions)} questions, "
            f"{model_rows} saved rows across attempts. Tokens and latency are "
            "cost measurements, not a quality claim."
        )


def limits_evidence(rows: list[dict]) -> None:
    observations = []
    for row in rows:
        if row["source"] != "real":
            continue
        observations.extend(json.loads(row.get("provider_headers_json") or "[]"))
    seen = defaultdict(set)
    for item in observations:
        for name, value in item.items():
            if name != "x-request-id":
                seen[name].add(value)
    print("\n## Real-service limit headers")
    for name, found in sorted(seen.items()):
        print(f"- {name}: {', '.join(sorted(found))}")
    if not seen:
        print("- not measured")


def slow_example(rows: list[dict]) -> None:
    answered = [
        row
        for row in rows
        if row["status"] == "ok" and row["scenario"] != "retrieval_comparison"
    ]
    if not answered:
        return
    slow = max(answered, key=lambda row: float(row["total_ms"]))
    print("\n## Slow successful request")
    print(
        f"- {slow['request_id']}: total {number(float(slow['total_ms']))} ms; "
        f"queue {number(float(slow['queue_ms']))} ms; "
        f"work {number(float(slow['work_ms']))} ms; "
        f"retry {number(float(slow['retry_ms']))} ms; "
        f"model first chunk {slow['model_first_chunk_ms'] or 'not measured'} ms."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, nargs="?", default=Path("results.csv"))
    args = parser.parse_args()
    with args.results.open(newline="", encoding="utf-8") as file:
        raw = list(csv.DictReader(file))
    validate_rows(raw)
    table2(raw)
    table3(raw)
    limits_evidence(raw)
    slow_example(raw)
