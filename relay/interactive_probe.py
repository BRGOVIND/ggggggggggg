import getpass
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from relay.budget_guard import DEFAULT_CONFIG, IST, read_config

DEFAULT_MODEL = "qwen/qwen3.8-27b"  # Use the chosen model unless its limits differ.


def read_positive(name: str) -> int:
    while True:
        value = input(f"{name}: ").strip()
        if value.isdigit() and int(value) > 0:
            return int(value)
        print("Enter a positive whole number from the account page.")


def read_usage(name: str) -> int:
    while True:
        value = input(f"{name} (do not guess zero): ").strip()
        if value.isdigit():
            return int(value)
        print("Enter today's account-wide usage as a whole number.")


def write_limits() -> dict:
    print("Read limits for the exact model you will use.")
    model_id = input(f"Model ID [{DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL
    config = {"model_id": model_id}
    for name in ("rpm", "tpm", "rpd", "tpd"):
        config[name] = read_positive(name.upper())
    print(
        "Refresh account usage now. Include all Groq activity today, not only this project."
    )
    for name in ("calls_used_today", "tokens_used_today"):
        config[name] = read_usage(name)
    now = datetime.now(timezone.utc)
    config["day_ist"] = now.astimezone(IST).date().isoformat()
    config["observed_at_utc"] = now.isoformat()
    DEFAULT_CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return read_config(DEFAULT_CONFIG)


def main() -> int:
    config = write_limits()
    print(f"Model for batch 1-9: {config['model_id']}")
    key = getpass.getpass("Paste GROQ_API_KEY (hidden): ").strip()
    if not key:
        print("No key entered. No real calls made.")
        return 1
    environment = os.environ.copy()
    environment["GROQ_API_KEY"] = key
    del key
    result = subprocess.run(
        [sys.executable, "-m", "relay.probe_nine"],
        env=environment,
        check=False,
    )
    environment.pop("GROQ_API_KEY", None)
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Setup cancelled; no further calls made") from None
