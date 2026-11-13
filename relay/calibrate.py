import argparse
import csv
import json
import statistics
from pathlib import Path

from relay.run import ASSUMED_DEADLINE_MARGIN_S, ASSUMED_PATIENCE_S, percentile

MIN_PROBES = 3  # Fewer observations cannot justify even a rough latency spread.
ASSUMED_HANG_PROBABILITY = 0.025  # One timeout in 42 mixed probes is weak evidence.
OBSERVED_HANG_MS = 30000  # Groq logged the disconnected call at 30.000 seconds.
ASSUMED_RPM_BURST_FRACTION = (
    0.85  # Thirty calls in nine seconds then a 2s retry fit this bucket.
)


def real_probe_rows(path: Path, run_id: str | None = None) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as file:
        rows = [
            row
            for row in csv.DictReader(file)
            if row["source"] == "real"
            and row["scenario"] == "probe"
            and row["status"] == "ok"
            and row["verifier_mode"] == "deterministic"
        ]
    if not rows:
        return []
    chosen = run_id or max(rows, key=lambda row: row["start_time"])["run_id"]
    return [row for row in rows if row["run_id"] == chosen]


def real_attempt_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as file:
        return [
            row
            for row in csv.DictReader(file)
            if row["source"] == "real"
            and row["scenario"]
            in ("probe", "probe_nine", "rpm_probe", "token_limit_probe")
        ]


def calibration(
    rows: list[dict], attempts: list[dict] | None = None
) -> tuple[dict, dict]:
    if len(rows) < MIN_PROBES:
        raise ValueError("Need at least three successful real probe rows")
    first = [float(row["provider_first_chunk_ms"]) for row in rows]
    total = [float(row["model_total_ms"]) for row in rows]
    per_chunk = [
        (float(row["model_total_ms"]) - float(row["provider_first_chunk_ms"]))
        / max(1, int(row["model_chunk_count"]) - 1)
        for row in rows
    ]
    observed_hangs = sum(row["status"] == "timeout" for row in attempts or [])
    queue = {
        "real_call_p95_s": percentile(total, 0.95) / 1000,
        "sample_count": len(rows),
        "probe_run_id": rows[0].get("run_id", ""),
        "model_id": rows[0].get("model_id", ""),
    }
    queue["queue_deadline_s"] = min(
        ASSUMED_PATIENCE_S,
        2 * queue["real_call_p95_s"] + ASSUMED_DEADLINE_MARGIN_S,
    )
    queue["deadline_basis"] = "assumed_2xp95_plus_1s"
    queue["assumptions"] = [
        "12-second human patience cap",
        "2x measured p95 from a very small sample",
        "1-second safety margin",
    ]
    standin = {
        "base_ms": statistics.median(first),
        "latency_spread_ms": max(
            0, (percentile(first, 0.95) - percentile(first, 0.05)) / 2
        ),
        "chunk_ms": statistics.median(per_chunk),
        "slow_per_active_ms": 220,
        "rpm_burst_fraction": ASSUMED_RPM_BURST_FRACTION,
        "fail_probability": 0.0,
        "hang_probability": ASSUMED_HANG_PROBABILITY,
        "hang_ms": OBSERVED_HANG_MS,
        "sample_count": len(rows),
        "hang_attempt_count": len(attempts or []),
        "hang_observed_count": observed_hangs,
        "probe_run_id": rows[0].get("run_id", ""),
        "model_id": rows[0].get("model_id", ""),
        "assumptions": [
            "slow_per_active_ms",
            "rpm_burst_fraction assumes one token-bucket explanation of the RPM probe",
            "hang_probability is a rounded scenario assumption, not a reliable rate estimate",
            "one 30-second timeout among mixed probes leaves wide uncertainty",
            "zero observed 503s does not prove zero failure risk",
        ],
    }
    return queue, standin


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, nargs="?", default=Path("results.csv"))
    parser.add_argument("--run-id")
    parser.add_argument("--queue-output", type=Path, default=Path("calibration.json"))
    parser.add_argument(
        "--standin-output", type=Path, default=Path("standin_calibration.json")
    )
    args = parser.parse_args()
    queue, standin = calibration(
        real_probe_rows(args.results, args.run_id), real_attempt_rows(args.results)
    )
    args.queue_output.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    args.standin_output.write_text(json.dumps(standin, indent=2), encoding="utf-8")
    print(f"Calibrated from {standin['sample_count']} successful real probe calls")
