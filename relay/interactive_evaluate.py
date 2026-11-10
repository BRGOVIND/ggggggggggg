import argparse
import getpass
import os
import subprocess
import sys

from relay.budget_guard import DEFAULT_CONFIG, DEFAULT_LEDGER, open_real_guard


def main(setting: str) -> int:
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
        "deterministic",
        "--count",
        "15",
        "--evaluation-group",
        "final_v1",
    ]
    result = subprocess.run(command, env=environment, check=False)
    environment.pop("GROQ_API_KEY", None)
    return result.returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setting", choices=["small", "medium", "large"], default="small")
    try:
        raise SystemExit(main(parser.parse_args().setting))
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Evaluation cancelled") from None
