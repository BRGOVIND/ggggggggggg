import getpass
import os
import subprocess
import sys

from relay.budget_guard import DEFAULT_CONFIG, DEFAULT_LEDGER, open_real_guard


def main() -> int:
    guard = open_real_guard(DEFAULT_CONFIG, DEFAULT_LEDGER)
    calls, tokens = guard.used_today()
    print(f"Model: {guard.config['model_id']}")
    print(f"Guard ledger today: {calls} calls, {tokens} reserved tokens")
    print(
        "Use this only if no other Groq calls occurred since the saved usage snapshot."
    )
    key = getpass.getpass("Paste GROQ_API_KEY (hidden): ").strip()
    if not key:
        print("No key entered. No real calls made.")
        return 1
    environment = os.environ.copy()
    environment["GROQ_API_KEY"] = key
    del key
    result = subprocess.run(
        [sys.executable, "-m", "relay.probe_followup"],
        env=environment,
        check=False,
    )
    environment.pop("GROQ_API_KEY", None)
    if result.returncode == 0:
        subprocess.run([sys.executable, "-m", "relay.capacity"], check=False)
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Setup cancelled; no further calls made") from None
