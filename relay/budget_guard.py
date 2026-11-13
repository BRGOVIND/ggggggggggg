import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

HARD_CALL_CAP = 150  # Limit assignment experiments across one IST day.
HARD_TOKEN_CAP = 120000  # Limit assignment token reservations across one IST day.
ACCOUNT_HEADROOM = 0.85  # Leave part of the account's daily quota unused.
SNAPSHOT_MAX_AGE_S = 1800  # Other Groq work can make an older snapshot unsafe.
RETRY_HEADROOM_CALLS = 2  # Reserve room for a small number of unexpected retries.
CHAT_OVERHEAD_TOKENS = 32  # Short prompts still pay for chat framing and model tokens.
IST = timezone(timedelta(hours=5, minutes=30))  # India has no daylight-saving changes.
PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)  # Keep one ledger across CLI processes.
DEFAULT_CONFIG = (
    PROJECT_ROOT / "groq_limits.json"
)  # Applicant fills account limits here.
DEFAULT_LEDGER = (
    PROJECT_ROOT / "groq_budget_ledger.jsonl"
)  # Reserve before every HTTP attempt.


class DailyCapExceeded(RuntimeError):
    pass


def estimate_guard_tokens(prompt: str, max_output_tokens: int) -> int:
    return (len(prompt) + 2) // 3 + max_output_tokens + CHAT_OVERHEAD_TOKENS


@contextmanager
def ledger_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def read_config(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"Fill {path} before any real Groq run")
    config = json.loads(path.read_text(encoding="utf-8"))
    required = (
        "model_id",
        "rpm",
        "tpm",
        "rpd",
        "tpd",
        "day_ist",
        "observed_at_utc",
        "calls_used_today",
        "tokens_used_today",
    )
    missing = [name for name in required if config.get(name) is None]
    if missing:
        raise ValueError(f"Fill budget config fields: {', '.join(missing)}")
    if not isinstance(config["model_id"], str) or not config["model_id"].strip():
        raise ValueError("model_id must name the Groq model")
    for name in ("rpm", "tpm", "rpd", "tpd"):
        if not isinstance(config[name], int) or config[name] <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if config.get("itpm") is not None and (
        not isinstance(config["itpm"], int) or config["itpm"] <= 0
    ):
        raise ValueError("itpm must be a positive integer")
    for name in ("calls_used_today", "tokens_used_today"):
        if not isinstance(config[name], int) or config[name] < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    today = datetime.now(IST).date().isoformat()
    if config["day_ist"] != today:
        raise ValueError(f"Budget config day_ist must be {today}")
    observed = datetime.fromisoformat(config["observed_at_utc"])
    if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
        raise ValueError("observed_at_utc needs an explicit UTC offset")
    now_utc = datetime.now(timezone.utc)
    if observed > now_utc:
        raise ValueError("observed_at_utc cannot be in the future")
    if (now_utc - observed).total_seconds() > SNAPSHOT_MAX_AGE_S:
        raise ValueError(
            "Usage snapshot is older than 30 minutes; refresh account usage"
        )
    if observed.astimezone(IST).date().isoformat() != today:
        raise ValueError("Usage snapshot must be from today in IST")
    return config


class DailyBudgetGuard:
    def __init__(self, config: dict, ledger: Path):
        self.config = config
        self.ledger = ledger
        self.snapshot = datetime.fromisoformat(config["observed_at_utc"])
        self.day = config["day_ist"]
        self.call_cap = min(HARD_CALL_CAP, int(config["rpd"] * ACCOUNT_HEADROOM))
        self.token_cap = min(HARD_TOKEN_CAP, int(config["tpd"] * ACCOUNT_HEADROOM))

    def ensure_today(self) -> None:
        if datetime.now(IST).date().isoformat() != self.day:
            raise DailyCapExceeded("Budget config is stale; refresh today's usage")

    def used_today(self) -> tuple[int, int]:
        calls = self.config["calls_used_today"]
        tokens = self.config["tokens_used_today"]
        if not self.ledger.exists():
            return calls, tokens
        with self.ledger.open(encoding="utf-8") as file:
            for line in file:
                entry = json.loads(line)
                when = datetime.fromisoformat(entry["at_utc"])
                if entry["day_ist"] != self.day or when <= self.snapshot:
                    continue
                calls += 1
                tokens += int(entry["reserved_tokens"])
        return calls, tokens

    def check_batch(
        self, planned_tokens: list[int], retry_headroom: int = RETRY_HEADROOM_CALLS
    ) -> None:
        self.ensure_today()
        with ledger_lock(self.ledger):
            used_calls, used_tokens = self.used_today()
        reserve_calls = len(planned_tokens) + (retry_headroom if planned_tokens else 0)
        reserve_tokens = sum(planned_tokens)
        if planned_tokens:
            reserve_tokens += retry_headroom * max(planned_tokens)
        print(
            f"Groq today ({self.day} IST): {used_calls} calls, {used_tokens} guard tokens "
            "(measured snapshot plus later reservations). "
            f"Batch plans {reserve_calls} calls and {reserve_tokens} tokens "
            f"({retry_headroom} retry slots). "
            f"Caps: {self.call_cap} calls, {self.token_cap} tokens."
        )
        if used_calls + reserve_calls > self.call_cap:
            raise DailyCapExceeded("Batch would exceed daily call cap")
        if used_tokens + reserve_tokens > self.token_cap:
            raise DailyCapExceeded("Batch would exceed daily token cap")

    def reserve_call(self, prompt: str, max_output_tokens: int) -> None:
        self.ensure_today()
        tokens = estimate_guard_tokens(prompt, max_output_tokens)
        with ledger_lock(self.ledger):
            used_calls, used_tokens = self.used_today()
            if used_calls + 1 > self.call_cap or used_tokens + tokens > self.token_cap:
                raise DailyCapExceeded("daily_budget_exhausted")
            entry = {
                "at_utc": datetime.now(timezone.utc).isoformat(),
                "day_ist": self.day,
                "model_id": self.config["model_id"],
                "reserved_tokens": tokens,
            }
            with self.ledger.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")
                file.flush()
                os.fsync(file.fileno())


def open_real_guard(
    config_path: Path, ledger_path: Path, model_id: str | None = None
) -> DailyBudgetGuard:
    config = read_config(config_path)
    if model_id and model_id != config["model_id"]:
        raise ValueError("Requested model differs from budget config model_id")
    return DailyBudgetGuard(config, ledger_path)
