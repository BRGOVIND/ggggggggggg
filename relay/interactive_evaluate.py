import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

from relay.budget_guard import DEFAULT_CONFIG, DEFAULT_LEDGER, open_real_guard


def main(
    setting: str,
    verifier: str,
    question_ids: list[str] | None,
    quality_run: bool = False,
    evaluation_group: str | None = None,
    questions: Path = Path("questions_candidate.json"),
    count: int = 15,
) -> int:
    if count < 1:
        raise ValueError("Question count must be positive")
    if verifier == "model" and not question_ids:
        raise ValueError("Model verifier requires explicit question IDs")
    guard = open_real_guard(DEFAULT_CONFIG, DEFAULT_LEDGER)
    calls, tokens = guard.used_today()
    print(f"Model for batch: {guard.config['model_id']}")
    print(f"Retrieval setting: {setting}")
    print(
        f"Account snapshot: {guard.config['calls_used_today']} attempts, "
        f"{guard.config['tokens_used_today']} measured tokens"
    )
    print(f"Guard now: {calls} calls, {tokens} counted tokens")
    key = getpass.getpass("Paste GROQ_API_KEY (hidden): ").strip()
    if not key:
        print("No key entered. No real calls made.")
        return 1
    environment = os.environ.copy()
    environment["GROQ_API_KEY"] = key
    del key
    command = [
        sys.executable,
        "-m",
        "relay.evaluate",
        "--source",
        "real",
        "--setting",
        setting,
        "--verifier",
        verifier,
        "--count",
        str(count),
        "--questions",
        str(questions),
        "--evaluation-group",
        evaluation_group or ("final_v1_model" if verifier == "model" else "final_v1"),
    ]
    if quality_run:
        command.append("--quality-run")
    if question_ids:
        command.extend(["--question-ids", *question_ids])
    result = subprocess.run(command, env=environment, check=False)
    environment.pop("GROQ_API_KEY", None)
    return result.returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--setting", choices=["small", "medium", "large"], default="small"
    )
    parser.add_argument(
        "--verifier", choices=["deterministic", "model"], default="deterministic"
    )
    parser.add_argument("--question-ids", nargs="+")
    parser.add_argument("--quality-run", action="store_true")
    parser.add_argument("--evaluation-group")
    parser.add_argument(
        "--questions", type=Path, default=Path("questions_candidate.json")
    )
    parser.add_argument("--count", type=int, default=15)
    try:
        args = parser.parse_args()
        raise SystemExit(
            main(
                args.setting,
                args.verifier,
                args.question_ids,
                args.quality_run,
                args.evaluation_group,
                args.questions,
                args.count,
            )
        )
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Evaluation cancelled") from None
