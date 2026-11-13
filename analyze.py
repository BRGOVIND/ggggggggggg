import argparse
import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

from relay.report import delivered, percentile, validate_rows
from relay.run import CSV_FIELDS

RESULTS = Path("results.csv")  # Read saved attempts without changing them.
PROBE_FILES = (
    Path("probe_rpm_raw.jsonl"),
    Path("probe_token_raw.jsonl"),
)  # Provider limit evidence.
STEADY_CONSOLE = Path(
    "real_steady_console.csv"
)  # Exported provider latency for nine delivered calls.
SCENARIOS = {
    "probe",
    "retrieval_comparison",
    "steady",
    "burst",
    "overload",
    "minute_halved",
    "daily_exhausted",
    "naive_retry",
}  # Include saved diagnostics.
SETTINGS = {"small", "medium", "large", "none"}  # Preserve recorded retrieval labels.
VERIFIERS = {
    "deterministic",
    "model",
    "deferred",
    "none",
    "auto",
}  # Include pressure fallback.


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames[:12] != CSV_FIELDS[:12]:
            raise ValueError("Required results.csv columns are out of order")
        rows = list(reader)
    validate_rows(rows)
    for row in rows:
        if row["scenario"] not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {row['request_id']}")
        if row["retrieval_setting"] not in SETTINGS:
            raise ValueError(f"Unknown retrieval setting: {row['request_id']}")
        if row["verifier_mode"] not in VERIFIERS:
            raise ValueError(f"Unknown verifier mode: {row['request_id']}")
    return rows


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def header(record: dict, name: str) -> str | None:
    return dict(record.get("headers") or []).get(name)


def limit_in_body(record: dict, unit: str) -> str:
    body = record.get("body_429") or ""
    match = re.search(rf"\({unit}\): Limit (\d+)", body)
    return match.group(1) if match else "not measured"


def overlapping_calls(records: list[dict], first_id: str, second_id: str) -> bool:
    selected = [
        record
        for record in records
        if record.get("call_id", "").endswith((first_id, second_id))
    ]
    if len(selected) != 2:
        return False
    spans = []
    for record in selected:
        end = datetime.fromisoformat(record["at_utc"])
        start = end - timedelta(milliseconds=record["total_ms"])
        spans.append((start, end))
    return max(span[0] for span in spans) < min(span[1] for span in spans)


def table1(rows: list[dict]) -> None:
    rpm = read_jsonl(PROBE_FILES[0])
    token = read_jsonl(PROBE_FILES[1])
    rpm_failure = next(record for record in rpm if record["status_code"] == 429)
    token_failure = next(record for record in token if record["status_code"] == 429)
    accepted = sum(record["status_code"] == 200 for record in rpm)
    tpm = set()
    rpd = set()
    for record in rpm + token:
        if header(record, "x-ratelimit-limit-tokens"):
            tpm.add(header(record, "x-ratelimit-limit-tokens"))
        if header(record, "x-ratelimit-limit-requests"):
            rpd.add(header(record, "x-ratelimit-limit-requests"))
    tpd = {row["tpd"] for row in rows if row["source"] == "real"}
    refill = sum((record.get("header_token_delta") or 0) < 0 for record in rpm)
    after = sorted(
        {header(record, "retry-after") for record in (rpm_failure, token_failure)}
    )
    probe = read_jsonl(Path("probe_1_9_raw.jsonl"))
    overlap = overlapping_calls(probe, "-8", "-9")
    print("## Table 1: real service limits (probe raw files)")
    print("| Limit | Value | How found | Confidence |")
    print("|---|---:|---|---|")
    print(
        f"| RPM | {limit_in_body(rpm_failure, 'RPM')} | {accepted} accepted, then 429 | high at probe time |"
    )
    print(f"| TPM | {', '.join(sorted(tpm))} | response headers | high at probe time |")
    print(f"| ITPM | {limit_in_body(token_failure, 'ITPM')} | one 429 body | medium |")
    print(f"| RPD | {', '.join(sorted(rpd))} | response headers | high at probe time |")
    print(
        f"| TPD | {', '.join(sorted(tpd))} | recorded configuration; no provider test | not measured |"
    )
    print(
        f"| In-flight | at least 2 | calls 8 and 9 overlap: {overlap}; maximum not tested | limited observation |"
    )
    print(
        f"| Recovery | {', '.join(after)} s | retry-after on two 429s | observed twice |"
    )
    print(
        f"| Token refill | {refill} negative header-token deltas | later headers show recovered tokens | inferred |"
    )


def percentile_cell(rows: list[dict], field: str) -> str:
    samples = [
        float(row[field]) * float(row.get("time_scale") or 1)
        for row in rows
        if row[field] not in ("", None)
    ]
    if not samples:
        return "not measured (n=0)"
    p50 = percentile(samples, 0.5)
    p95 = percentile(samples, 0.95)
    return f"{p50:.1f}/{p95:.1f} (n={len(samples)})"


def fraction_cell(rows: list[dict], predicate) -> str:
    count = sum(predicate(row) for row in rows)
    return f"{100 * count / len(rows):.1f}% ({count}/{len(rows)})"


def tokens_cell(rows: list[dict]) -> str:
    answers = [row for row in rows if delivered(row)]
    if not answers:
        return "not measured (n=0)"
    used = sum(int(row["tokens_in"]) + int(row["tokens_out"]) for row in answers)
    return f"{used / len(answers):.0f} (n={len(answers)})"


def cost_cell(rows: list[dict]) -> str:
    answers = sum(delivered(row) for row in rows)
    if not answers:
        return "not measured (n=0)"
    cost = sum(float(row["cost_usd"]) for row in rows)
    return f"${cost / answers * 1000:.2f} (n={answers})"


def load_groups(rows: list[dict]) -> list[list[dict]]:
    groups = defaultdict(list)
    load = {"steady", "burst", "overload", "minute_halved", "daily_exhausted"}
    for row in rows:
        if row["source"] == "stand-in" and row["scenario"] in load:
            groups[row["run_id"]].append(row)
    order = {name: number for number, name in enumerate(sorted(load))}
    return sorted(
        groups.values(),
        key=lambda group: (
            group[0]["run_label"],
            order[group[0]["scenario"]],
            float(group[0]["queue_deadline_s"]),
            -float(group[0].get("latency_spread_ms") or 0),
        ),
    )


def table2(rows: list[dict]) -> None:
    groups = load_groups(rows)
    print("\n## Table 2a: load latency (delivered only; p50/p95 linear interpolation)")
    print(
        "| Source | Pass | Scenario | Clock | Deadline s | Spread ms | Arrivals | Offered/min | First token ms | Total ms |"
    )
    print("|---|---|---|---|---:|---:|---:|---:|---|---|")
    for group in groups:
        first = group[0]
        answers = [row for row in group if delivered(row)]
        duration = float(first["scenario_duration_s"])
        offered = 60 * len(group) / duration if duration else 0
        print(
            f"| {first['source']} | {first['run_label']} | {first['scenario']} | {first['clock_label']} | "
            f"{first['queue_deadline_s']} | {float(first['latency_spread_ms']):.1f} | {len(group)} | "
            f"{offered:.1f} | {percentile_cell(answers, 'first_token_ms')} | "
            f"{percentile_cell(answers, 'total_ms')} |"
        )
    print("\n## Table 2b: load outcomes (denominator: all arrivals)")
    print(
        "| Source | Pass | Scenario | Clock | Deadline s | Spread ms | Full | Excerpt | Rate-limited | Timeout | Tokens/answer | Cost/1,000 answers |"
    )
    print("|---|---|---|---|---:|---:|---:|---:|---:|---|---|")
    for group in groups:
        first = group[0]
        print(
            f"| {first['source']} | {first['run_label']} | {first['scenario']} | {first['clock_label']} | "
            f"{first['queue_deadline_s']} | {float(first['latency_spread_ms']):.1f} | "
            f"{fraction_cell(group, lambda row: delivered(row) and row['answer_kind'] == 'full')} | "
            f"{fraction_cell(group, lambda row: delivered(row) and row['answer_kind'] == 'excerpt')} | "
            f"{fraction_cell(group, lambda row: row['status'] == 'rate_limited')} | "
            f"{fraction_cell(group, lambda row: row['status'] == 'timeout')} | "
            f"{tokens_cell(group)} | {cost_cell(group)} |"
        )


def real_steady_table(rows: list[dict]) -> None:
    steady = sorted(
        (
            row
            for row in rows
            if row["source"] == "real" and row["run_label"] == "real_steady"
        ),
        key=lambda row: row["start_time"],
    )
    if not steady:
        print("Real steady: pending (not measured).")
        return
    with STEADY_CONSOLE.open(newline="", encoding="utf-8") as file:
        console = sorted(
            csv.DictReader(file), key=lambda row: int(row["created_at_ms"])
        )
    answered = [row for row in steady if delivered(row)]
    if len(answered) != len(console):
        raise ValueError("Real steady delivered count differs from console export")
    provider = iter(console)
    differences = []
    print("\n### Real steady: paired client and console latency")
    print(
        "Assumption: pair delivered requests with the nine vaani#3 console rows "
        "by chronological order; request 8 has no console row. Client `total_ms` "
        "comes from `results.csv`; provider latency comes from the 11 Nov "
        "export. Round each displayed latency to whole milliseconds before subtraction."
    )
    print("| Request | Client total_ms | Console latency_ms | Difference_ms |")
    print("|---:|---:|---:|---:|")
    for number, row in enumerate(steady, 1):
        client_ms = round(float(row["total_ms"]))
        if not delivered(row):
            print(
                f"| {number} | {client_ms:,} | no row | excluded (`{row['error']}`) |"
            )
            continue
        observed = next(provider)
        if (int(row["tokens_in"]), int(row["tokens_out"])) != (
            int(observed["input_tokens"]),
            int(observed["output_tokens"]),
        ):
            raise ValueError(f"Real steady token counts differ for request {number}")
        console_ms = round(float(observed["time_to_completion_ms"]))
        difference = client_ms - console_ms
        differences.append(difference)
        print(f"| {number} | {client_ms:,} | {console_ms:,} | {difference:,} |")
    median = round(statistics.median(float(row["total_ms"]) for row in answered))
    maximum = round(max(float(row["total_ms"]) for row in answered))
    print(
        f"Real steady: sent {len(steady)}, delivered {len(answered)}, "
        f"ConnectTimeout {len(steady) - len(answered)}. Delivered client total_ms "
        f"p50 {median:,}, max {maximum:,} (n={len(answered)}); no p95 or p99."
    )
    print(
        f"Difference median: {statistics.median(differences):,.0f} ms; "
        f"range: {min(differences):,}–{max(differences):,} ms (n={len(differences)}). "
        "The cause of the client-minus-console gap is not isolated; connection "
        "and response-header times were not recorded."
    )


def grade_cell(rows: list[dict], labels: set[str]) -> str:
    answered = [row for row in rows if delivered(row)]
    graded = sum(bool(row["answer_grade"]) for row in answered)
    if not answered or graded < len(answered):
        return f"not measured ({graded}/{len(answered)} answered graded)"
    return fraction_cell(rows, lambda row: row["answer_grade"] in labels)


def table3(rows: list[dict]) -> None:
    print("\n## Table 3: real retrieval comparison")
    print(
        "| Setting | n | Correct+cited | Wrong | Refused correctly | No answer | Tokens/answer | First token p50/p95 ms | Total p50/p95 ms |"
    )
    print("|---|---:|---|---|---|---|---|---|---|")
    for setting in ("small", "medium", "large"):
        group = [
            row
            for row in rows
            if row["source"] == "real"
            and row["scenario"] == "retrieval_comparison"
            and row["retrieval_setting"] == setting
            and row["verifier_mode"] == "deterministic"
        ]
        if not group:
            print(
                f"| {setting} | 0 | not measured | not measured | not measured | not measured | not measured | not measured | not measured |"
            )
            continue
        answers = [row for row in group if delivered(row)]
        print(
            f"| {setting} | {len(group)} | {grade_cell(group, {'correct'})} | "
            f"{grade_cell(group, {'wrong', 'refused_wrongly'})} | "
            f"{grade_cell(group, {'refused_correctly'})} | "
            f"{fraction_cell(group, lambda row: row['status'] != 'ok')} | "
            f"{tokens_cell(group)} | {percentile_cell(answers, 'first_token_ms')} | "
            f"{percentile_cell(answers, 'total_ms')} |"
        )


def checks(rows: list[dict]) -> None:
    print("## CSV validation")
    print(
        f"Rows: {len(rows)}; duplicate request IDs: 0; required columns: first 12 in brief order; vocabularies: valid."
    )
    for (scenario, source), count in sorted(
        Counter((r["scenario"], r["source"]) for r in rows).items()
    ):
        print(f"- {scenario}, {source}: {count}")
    print(
        "Percentiles: sort delivered samples, interpolate linearly at (n-1) * fraction; time-compressed latencies multiplied by time_scale."
    )


def narrative_evidence(rows: list[dict]) -> None:
    console = json.loads(Path("console_log_excerpt.json").read_text(encoding="utf-8"))
    failures = [row for row in console["rows"] if row["code"] == 429]
    provider_ms = sorted(row["latency_s"] * 1000 for row in failures)
    hang_log = next(row for row in console["rows"] if row["code"] == 499)
    probe = next(row for row in rows if row["request_id"] == "725755cd0a-7")
    trace = json.loads(Path("probe_1_9_summary.json").read_text(encoding="utf-8"))
    call = next(item for item in trace["calls"] if item["call"] == 7)
    retry = json.loads(
        Path("retry_experiment_v2_summary.json").read_text(encoding="utf-8")
    )
    diagnostics = json.loads(
        Path("retriever_diagnostics.json").read_text(encoding="utf-8")
    )
    print("\n## Narrative evidence (saved rows and raw files)")
    print(
        f"Probe call {call['call']}, {probe['request_id']}: {probe['status']} "
        f"after {float(probe['total_ms']):.0f} ms; queue {float(probe['queue_ms']):.0f}, "
        f"model-call wait {float(probe['work_ms']):.0f}, retry {float(probe['retry_ms']):.0f} ms; "
        f"first token not observed, no headers. Console excerpt: code {hang_log['code']}, "
        f"latency {hang_log['latency_s']:.3f}s, TTFT {hang_log['ttft_s']}s, "
        f"tokens {hang_log['input_tokens']}+{hang_log['output_tokens']}. "
        "Provider processing during wait is not measured."
    )
    print(
        f"Two 429s in user-pasted console excerpt took {provider_ms[0]:.0f}-{provider_ms[-1]:.0f} ms "
        "provider time; their saved client round trips were "
        + ", ".join(
            f"{float(row['total_ms']):.0f} ms"
            for row in rows
            if row["scenario"] == "probe" and row["status"] == "rate_limited"
        )
        + "."
    )
    for deadline in ("2.29", "20.0"):
        for label in ("pass2", "pass2_det"):
            group = [
                row
                for row in rows
                if row["scenario"] == "steady"
                and row["run_label"] == label
                and row["queue_deadline_s"] == deadline
            ]
            full = sum(row["answer_kind"] == "full" and delivered(row) for row in group)
            daily = sum(row["error"] == "daily_budget_exhausted" for row in group)
            tokens = sum(
                int(row["tokens_in"]) + int(row["tokens_out"]) for row in group
            )
            print(
                f"Stand-in steady {label}, {deadline}s, 5x time-compressed: "
                f"full {full}/{len(group)}, daily-budget notices {daily}, measured tokens {tokens}."
            )
    burst = [
        arm
        for arm in retry["arms"]
        if arm["arrival_pattern"] == "burst_under_1s" and arm["seed"] == 7
    ]
    for arm in burst:
        print(
            f"Stand-in retry burst seed 7, {arm['arm']}: {arm['delivered']}/{arm['arrivals']} delivered; "
            f"{arm['http_attempts']} HTTP calls; {arm['extra_http_calls_per_delivered_answer']:.2f} "
            f"extra calls per delivered answer; displaced first successes "
            f"{arm['baseline_first_successes_displaced_after_retry']}."
        )
    other_arms = [arm for arm in retry["arms"] if arm["arm"] != "zero_retry"]
    displaced = sum(
        arm["baseline_first_successes_displaced_after_retry"] for arm in other_arms
    )
    worse = sum(arm["retry_made_it_worse"] for arm in other_arms)
    print(
        f"Across saved retry arms: displaced first successes {displaced}; predeclared worse flag {worse}/{len(other_arms)} arms."
    )
    for setting in ("small", "medium"):
        item = diagnostics["H02"][setting]
        ranks = [chunk["rank"] for chunk in item["target_page_chunks"]]
        print(
            f"H02 {setting}: p.3 chunk rank {ranks} among {item['corpus_chunks']} chunks."
        )
    for request_id in ("2d4699a7de-1", "ad5e68e20a-1"):
        row = next(row for row in rows if row["request_id"] == request_id)
        print(
            f"Halved-limit {request_id}: retry wait {float(row['retry_ms']):.0f} ms, "
            f"HTTP attempts {row['http_attempts']}, retries {row['retries']}, "
            f"status {row['status']}."
        )
    busy = next(
        row["answer"]
        for row in rows
        if row["scenario"] == "burst" and row["status"] == "rate_limited"
    )
    daily = next(
        row["answer"]
        for row in rows
        if row["scenario"] == "daily_exhausted" and row["status"] == "rate_limited"
    )
    print(f"Busy notice example, exact saved text: {busy}")
    print(f"Daily-exhausted notice, exact saved text: {daily}")


def verifier_cost_table(rows: list[dict]) -> None:
    ids = ("T01", "M02", "H02", "M04")
    with Path("run_annotations.csv").open(newline="", encoding="utf-8") as file:
        attempt1_ids = {
            row["question_id"]: row["request_id"]
            for row in csv.DictReader(file)
            if row["attempt_label"] == "verifier_subset_attempt1"
        }
    by_request = {row["request_id"]: row for row in rows}
    print("\n## Model-verifier subset: cost measurement, not a quality claim")
    print(
        "| ID | Deterministic small: input/output, total ms, status | Attempt 1: input/output, total ms, status | Attempt 2: input/output, total ms, status |"
    )
    print("|---|---|---|---|")
    for question_id in ids:
        deterministic = next(
            row
            for row in rows
            if row["question_id"] == question_id
            and row["retrieval_setting"] == "small"
            and row["evaluation_group"] == "final_v1"
            and row["verifier_mode"] == "deterministic"
        )
        attempt1 = by_request[attempt1_ids[question_id]]
        attempt2 = next(
            (
                row
                for row in rows
                if row["question_id"] == question_id
                and row["evaluation_group"] == "verifier_subset_attempt2"
            ),
            None,
        )
        print(
            f"| {question_id} | {verifier_cell(deterministic)} | "
            f"{verifier_cell(attempt1)} | {verifier_cell(attempt2)} |"
        )
    print(
        "Attempt 1 used an assumed 2.29s load-style deadline. Attempt 2 uses pre-run pacing outside total_ms, a 30s cap per HTTP call, and a 180s total bound."
    )


def verifier_cell(row: dict | None) -> str:
    if row is None:
        return "pending"
    return (
        f"{row['tokens_in']}/{row['tokens_out']}, "
        f"{float(row['total_ms']):.0f}, {row['status']}"
    )


def print_tables(rows: list[dict]) -> None:
    checks(rows)
    table1(rows)
    table2(rows)
    real_steady_table(rows)
    table3(rows)
    narrative_evidence(rows)
    verifier_cost_table(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = read_rows(RESULTS)
    if args.output is None:
        print_tables(rows)
        return
    with args.output.open("w", encoding="utf-8") as file, redirect_stdout(file):
        print_tables(rows)


if __name__ == "__main__":
    main()
