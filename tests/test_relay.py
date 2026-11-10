import asyncio
import csv
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from relay.budget_guard import IST, DailyBudgetGuard, DailyCapExceeded, read_config
from relay.calibrate import calibration, real_attempt_rows, real_probe_rows
from relay.evaluate import choose_question_ids, select_questions
from relay.grade import apply_grades
from relay.limits import Budget, Limits
from relay.model import ModelClient, ModelFailure, ModelReply
from relay.papers import PaperIndex, RetrievalSetting
from relay.pipeline import REFUSAL, CallStats, answer_question, call_with_retry
from relay.probe_followup import infer_timeout_count, save_call, send_call
from relay.probe_nine import load_probe_questions, planned_tokens, probe_step
from relay.report import delivered, table3, validate_rows
from relay.retry_experiment import (
    affected_first_successes,
    censored_percentile,
    local_url,
    retry_made_worse,
)
from relay.run import CSV_FIELDS, Dispatcher, queue_deadline
from relay.standin import StandInState, rate_error


def budget_config(calls: int, tokens: int) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "model_id": "qwen/qwen3.8-27b",
        "rpm": 30,
        "tpm": 8000,
        "rpd": 1000,
        "tpd": 200000,
        "day_ist": now.astimezone(IST).date().isoformat(),
        "observed_at_utc": now.isoformat(),
        "calls_used_today": calls,
        "tokens_used_today": tokens,
    }


def test_daily_guard_refuses_batch_and_reserves_before_call(tmp_path, capsys):
    config = budget_config(149, 119868)
    path = tmp_path / "groq_limits.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    guard = DailyBudgetGuard(read_config(path), tmp_path / "ledger.jsonl")
    with pytest.raises(DailyCapExceeded, match="daily call cap"):
        guard.check_batch([100])
    assert "149 calls, 119868 guard tokens" in capsys.readouterr().out
    guard.reserve_call("x" * 270, 10)
    assert guard.used_today() == (150, 120000)
    with pytest.raises(DailyCapExceeded, match="daily_budget_exhausted"):
        guard.reserve_call("x", 1)

    token_guard = DailyBudgetGuard(budget_config(0, 119950), tmp_path / "other.jsonl")
    with pytest.raises(DailyCapExceeded, match="daily token cap"):
        token_guard.check_batch([100], retry_headroom=0)

    account_config = budget_config(0, 0)
    account_config["rpd"] = 100
    account_config["tpd"] = 100000
    account_guard = DailyBudgetGuard(account_config, tmp_path / "account.jsonl")
    assert account_guard.call_cap == 85
    assert account_guard.token_cap == 85000


def test_daily_guard_stops_after_ist_day_changes(tmp_path):
    config = budget_config(0, 0)
    config["day_ist"] = "2000-01-01"
    guard = DailyBudgetGuard(config, tmp_path / "ledger.jsonl")
    with pytest.raises(DailyCapExceeded, match="stale"):
        guard.reserve_call("question", 10)


def test_config_refuses_old_account_snapshot(tmp_path):
    config = budget_config(0, 0)
    config["observed_at_utc"] = (
        datetime.now(timezone.utc) - timedelta(minutes=31)
    ).isoformat()
    path = tmp_path / "groq_limits.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="older than 30 minutes"):
        read_config(path)


def test_real_model_client_requires_guard():
    with pytest.raises(ValueError, match="daily budget guard"):
        ModelClient(
            "https://api.groq.com/openai/v1", "qwen/qwen3.8-27b", api_key="fake"
        )


def test_daily_guard_runs_before_http():
    class RefusingGuard:
        def reserve_call(self, prompt, max_tokens):
            raise DailyCapExceeded("daily_budget_exhausted")

    async def check():
        model = ModelClient(
            "http://local", "test", api_key="fake", daily_guard=RefusingGuard()
        )
        await model.close()
        called = False

        def handler(request):
            nonlocal called
            called = True
            return httpx.Response(200, text="data: [DONE]\n\n")

        model.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://local"
        )
        try:
            with pytest.raises(DailyCapExceeded):
                await model.complete("question", 10)
            assert not called
        finally:
            await model.close()

    asyncio.run(check())


def test_raw_probe_log_keeps_full_headers_and_429_body(tmp_path):
    raw_path = tmp_path / "responses.jsonl"
    body = "rate limit detail " * 50

    def handler(request):
        return httpx.Response(
            429,
            headers={
                "x-ratelimit-limit-requests": "30",
                "x-ratelimit-remaining-tokens": "0",
                "retry-after": "12",
                "set-cookie": "private-cookie=secret",
            },
            text=body,
        )

    async def check():
        model = ModelClient("http://local", "test", raw_log=raw_path)
        await model.close()
        model.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://local"
        )
        try:
            with pytest.raises(ModelFailure, match="rate limit"):
                await model.complete("question", 10, call_id="batch-1")
        finally:
            await model.close()

    asyncio.run(check())
    entry = json.loads(raw_path.read_text(encoding="utf-8"))
    assert entry["call_id"] == "batch-1"
    assert entry["body_429"] == body
    assert ["x-ratelimit-limit-requests", "30"] in entry["headers"]
    assert ["x-ratelimit-remaining-tokens", "0"] in entry["headers"]
    assert not any(name == "set-cookie" for name, _ in entry["headers"])


def test_raw_log_records_timeout_without_response(tmp_path):
    raw_path = tmp_path / "attempts.jsonl"

    def handler(request):
        raise httpx.ReadTimeout("stalled", request=request)

    async def check():
        model = ModelClient("http://local", "test", raw_log=raw_path)
        await model.close()
        model.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://local"
        )
        try:
            with pytest.raises(httpx.ReadTimeout):
                await model.complete("question", 10)
        finally:
            await model.close()

    asyncio.run(check())
    entry = json.loads(raw_path.read_text(encoding="utf-8"))
    assert entry["outcome"] == "timeout"
    assert entry["status_code"] is None
    assert entry["usage"] is None
    assert entry["headers"] == []


def test_rate_limited_notice_never_counts_as_success():
    assert not delivered({"status": "rate_limited", "answer_kind": "none"})
    assert not delivered({"status": "timeout", "answer_kind": "none"})
    assert delivered({"status": "ok", "answer_kind": "excerpt"})
    row = dict.fromkeys(CSV_FIELDS, "")
    row.update(
        request_id="limited-1", source="stand-in", status="rate_limited",
        answer_kind="none", total_ms="1", queue_ms="0", work_ms="1",
        retry_ms="0", tokens_in="0", tokens_out="0",
    )
    validate_rows([row])
    row["status"] = "ok"
    with pytest.raises(ValueError, match="no answer"):
        validate_rows([row])


def test_first_429_headers_survive_later_budget_wait():
    class OneFailure:
        calls = 0

        async def complete(self, prompt, max_tokens):
            self.calls += 1
            raise ModelFailure(429, 0, "minute limit", {"retry-after": "0"})

    class ClosingBudget:
        calls = 0

        async def reserve(self, tokens, deadline, input_tokens=None):
            self.calls += 1
            return None if self.calls == 1 else "queue_deadline"

        async def provider_limit(self, retry_after_s, daily):
            pass

    async def check():
        pages = [("Paper", 1, "Attention uses scaled dot products in this passage.")]
        index = PaperIndex(pages, RetrievalSetting("test", 30, 1, 0))
        model = OneFailure()
        started = time.perf_counter()
        result = await answer_question(
            "attention", index, model, ClosingBudget(), "deterministic",
            started + 1, started,
        )
        assert model.calls == 1
        assert result.status == "timeout"
        assert result.http_attempts == 1
        assert json.loads(result.provider_headers_json) == [{"retry-after": "0"}]

    asyncio.run(check())


def test_retry_experiment_is_local_only():
    assert local_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    with pytest.raises(ValueError, match="local stand-in"):
        local_url("https://api.groq.com/openai/v1")


def test_retry_delivery_percentiles_keep_all_arrivals():
    answered = [
        SimpleNamespace(answer=SimpleNamespace(
            status="ok", answer_kind="full", total_ms=350 + number,
        ))
        for number in range(10)
    ]
    missing = [
        SimpleNamespace(answer=SimpleNamespace(
            status="rate_limited", answer_kind="none", total_ms=20_000,
        ))
        for _ in range(30)
    ]
    assert censored_percentile(answered + missing, 0.5) is None
    assert censored_percentile(answered + missing, 0.95) is None
    assert censored_percentile(answered, 0.95) == 359
    assert retry_made_worse(9, 10, 350, 350)
    assert retry_made_worse(10, 10, None, 350)
    assert not retry_made_worse(10, 10, 350, 350)


def test_retry_affected_first_successes_counts_displaced_and_slower():
    def entry(status, first_status, total, started, finished, retry_start=None, retry_end=None):
        return SimpleNamespace(
            answer=SimpleNamespace(status=status, total_ms=total),
            first_attempt_status=first_status,
            first_attempt_started_at=started,
            first_attempt_finished_at=finished,
            retry_started_at=retry_start,
            retry_finished_at=retry_end,
        )

    baseline = [entry("ok", "ok", 100, 1, 1.1) for _ in range(3)]
    current = [
        entry("rate_limited", "rate_limited", 200, 3, 3.1),
        entry("ok", "rate_limited", 200, 2, 2.1, 2.5, 3.5),
        entry("ok", "ok", 150, 3, 3.2),
    ]
    assert affected_first_successes(current, baseline) == ([0], [2])


def test_retry_only_once_after_429_with_retry_after():
    class FakeModel:
        def __init__(self, failure):
            self.failure = failure
            self.calls = 0

        async def complete(self, prompt, max_tokens):
            self.calls += 1
            if self.calls == 1:
                raise self.failure
            return ModelReply("OK", 10, 2, 1, 2, True, {}, 1)

    async def check(failure):
        model = FakeModel(failure)
        budget = Budget(Limits(30, 8000, 1000, 200000))
        stats = CallStats()
        try:
            reply = await call_with_retry(
                model, budget, "question", 10, time.perf_counter() + 1, stats
            )
        except (ModelFailure, httpx.ReadTimeout):
            reply = None
        return model.calls, stats.retries, reply

    calls, retries, reply = asyncio.run(
        check(ModelFailure(429, 0, "minute limit", {}))
    )
    assert (calls, retries, reply.text) == (2, 1, "OK")
    for failure in (
        ModelFailure(429, None, "minute limit", {}),
        ModelFailure(503, 0, "server failure", {}),
        httpx.ReadTimeout("stalled"),
    ):
        calls, retries, reply = asyncio.run(check(failure))
        assert (calls, retries, reply) == (1, 0, None)


def test_raw_probe_log_keeps_measured_usage(tmp_path):
    raw_path = tmp_path / "responses.jsonl"
    stream = (
        'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
        'data: {"usage":{"prompt_tokens":8,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )

    async def check():
        model = ModelClient("http://local", "test", raw_log=raw_path)
        await model.close()
        model.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={
                        "x-ratelimit-limit-tokens": "8000",
                        "x-ratelimit-remaining-tokens": "7982",
                    },
                    text=stream,
                )
            ),
            base_url="http://local",
        )
        try:
            reply = await model.complete("question", 10, call_id="batch-2")
            assert reply.tokens_in == 8
            assert reply.tokens_out == 2
        finally:
            await model.close()

    asyncio.run(check())
    entry = json.loads(raw_path.read_text(encoding="utf-8"))
    assert entry["usage"] == {"prompt_tokens": 8, "completion_tokens": 2}
    assert ["x-ratelimit-limit-tokens", "8000"] in entry["headers"]
    assert entry["max_output_tokens"] == 10
    assert entry["header_token_delta"] == 18
    assert entry["header_delta_basis"] == "limit_minus_remaining"


def test_followup_timeout_is_saved_without_retry(tmp_path):
    class SlowModel:
        async def complete(self, prompt, max_tokens, call_id):
            await asyncio.sleep(0.1)

    async def check():
        outcome = await send_call(SlowModel(), "question", 8, 0.001, "run-1")
        assert outcome.status == "timeout"
        args = SimpleNamespace(
            source="real",
            model="qwen/qwen3.8-27b",
            rpm=30,
            tpm=8000,
            rpd=1000,
            tpd=200000,
            output=tmp_path / "results.csv",
        )
        save_call(
            args, outcome, tmp_path / "raw.jsonl", "rpm_probe", "run", 1, "question", 8
        )
        with args.output.open(newline="", encoding="utf-8") as file:
            row = next(csv.DictReader(file))
        assert row["status"] == "timeout"
        assert row["given_up"] == "True"
        assert row["retries"] == "0"

    asyncio.run(check())


def test_timeout_count_inference_keeps_unknown_cases():
    assert (
        infer_timeout_count({"status": "ok", "request_remaining": "991"})[
            "call_7_counted"
        ]
        is False
    )
    assert (
        infer_timeout_count({"status": "ok", "request_remaining": "990"})[
            "call_7_counted"
        ]
        is True
    )
    assert (
        infer_timeout_count({"status": "ok", "request_remaining": "999"})[
            "call_7_counted"
        ]
        is None
    )


def test_rpm_probe_stops_on_first_429(tmp_path, monkeypatch):
    from relay import probe_followup

    config = budget_config(9, 17665)
    guard = DailyBudgetGuard(config, tmp_path / "ledger.jsonl")
    attempted = []

    class FakeModel:
        def __init__(self, base_url, model, api_key, daily_guard, raw_log):
            self.guard = daily_guard

        async def complete(self, prompt, max_tokens, call_id):
            self.guard.reserve_call(prompt, max_tokens)
            attempted.append(call_id)
            if len(attempted) == 5:
                raise ModelFailure(429, 2.0, "mock limit", {})
            return ModelReply("OK", 19, 2, 10.0, 20.0, True, {}, 1)

        async def close(self):
            pass

    async def no_wait(path):
        pass

    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    monkeypatch.setattr(probe_followup, "ModelClient", FakeModel)
    monkeypatch.setattr(probe_followup, "wait_for_quiet", no_wait)
    args = SimpleNamespace(
        model="qwen/qwen3.8-27b",
        url="http://local",
        budget_ledger=tmp_path / "ledger.jsonl",
        rpm_raw=tmp_path / "raw.jsonl",
        output=tmp_path / "results.csv",
        source="real",
        rpm=30,
        tpm=8000,
        rpd=1000,
        tpd=200000,
    )
    records, retry_after = asyncio.run(probe_followup.run_rpm(args, guard))
    with args.output.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert len(records) == len(rows) == len(attempted) == 5
    assert rows[-1]["status"] == "rate_limited"
    assert retry_after == 2.0


def test_nine_call_plan_counts_context_in_verifiers():
    from relay.papers import build_indexes
    from relay.run import PAPER_FOLDER

    questions = load_probe_questions(Path("questions_candidate.json"))
    indexes = build_indexes(PAPER_FOLDER)
    planned = planned_tokens(indexes, questions)
    assert len(planned) == 9
    assert planned[4] > planned[3]
    assert planned[6] > planned[5]
    assert planned[7] == planned[8]
    assert probe_step(9, indexes, questions, {}).setting == "none"


@pytest.mark.parametrize(("fail_at", "expected"), [(None, 9), (7, 7)])
def test_nine_call_runner_stops_on_failure(tmp_path, monkeypatch, fail_at, expected):
    from relay import probe_nine

    config = budget_config(0, 0)
    config["tpm"] = 100000
    config_path = tmp_path / "limits.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    calls = []

    class FakeModel:
        def __init__(self, base_url, model, api_key, daily_guard, raw_log):
            assert model == config["model_id"]
            self.guard = daily_guard

        async def complete(self, prompt, max_tokens, call_id):
            self.guard.reserve_call(prompt, max_tokens)
            calls.append(call_id)
            if call_id.endswith(f"-{fail_at}"):
                raise httpx.ReadTimeout("mock timeout")
            return ModelReply("PASS [P1]", 12, 3, 5.0, 10.0, True, {}, 1)

        async def close(self):
            pass

    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    monkeypatch.setattr(probe_nine, "ModelClient", FakeModel)
    args = SimpleNamespace(
        budget_config=config_path,
        budget_ledger=tmp_path / "ledger.jsonl",
        model=None,
        questions=Path("questions_candidate.json"),
        output=tmp_path / "results.csv",
        raw_output=tmp_path / "raw.jsonl",
        summary_output=tmp_path / "summary.json",
        url="http://local",
    )
    asyncio.run(probe_nine.run_probe(args))
    summary = json.loads(args.summary_output.read_text(encoding="utf-8"))
    with args.output.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert len(calls) == len(rows) == len(summary["calls"]) == expected
    assert [row["scenario"] for row in rows[:3]] == ["probe"] * 3
    assert all(row["answer_grade"] == "" for row in rows)
    if fail_at:
        assert rows[-1]["status"] == "timeout"


def test_search_keeps_pdf_page():
    pages = [
        (
            "First",
            3,
            "Scaled dot product attention divides by the square root of key dimension.",
        ),
        ("Second", 8, "A speech codec turns audio into tokens."),
    ]
    index = PaperIndex(pages, RetrievalSetting("test", 30, 1, 0))
    result = index.search("scaled dot product key dimension")
    assert result[0].citation == "First, PDF p. 3"


def test_budget_waits_then_gives_up():
    async def check():
        budget = Budget(Limits(1, 100, 10, 1000))
        assert await budget.reserve(20, time.perf_counter() + 1) is None
        assert await budget.reserve(20, time.perf_counter() + 0.01) == "queue_deadline"

    asyncio.run(check())


def test_input_token_limit_blocks_second_large_prompt():
    async def check():
        budget = Budget(Limits(30, 100000, 1000, 200000, 7000))
        first = await budget.reserve(5000, time.perf_counter() + 1, input_tokens=5000)
        second = await budget.reserve(5000, time.perf_counter() + 0.01, input_tokens=5000)
        assert first is None
        assert second == "queue_deadline"

    asyncio.run(check())


def test_daily_budget_stops_new_calls():
    async def check():
        budget = Budget(Limits(10, 100, 1, 1000))
        assert await budget.reserve(20, time.perf_counter() + 1) is None
        assert (
            await budget.reserve(20, time.perf_counter() + 1) == "daily_budget_exhausted"
        )
        budget.day_reset_at = time.perf_counter() - 1
        assert await budget.reserve(20, time.perf_counter() + 1) is None

    asyncio.run(check())


def test_slow_model_obeys_user_deadline():
    class SlowModel:
        async def complete(self, prompt, max_tokens):
            await asyncio.sleep(0.2)
            return ModelReply("Answer [P1].", 20, 8, 10, 200, True, {}, 1)

    async def check():
        pages = [("Paper", 1, "An attention result from a real paper.")]
        index = PaperIndex(pages, RetrievalSetting("test", 30, 1, 0))
        budget = Budget(Limits(10, 1000, 100, 10000))
        start = time.perf_counter()
        result = await answer_question(
            "attention",
            index,
            SlowModel(),
            budget,
            "deterministic",
            start + 0.03,
            start,
        )
        assert result.status == "timeout"
        assert result.given_up
        assert not result.usage_measured
        assert result.total_ms < 100

    asyncio.run(check())


def test_unsupported_citation_becomes_refusal():
    class FakeModel:
        async def complete(self, prompt, max_tokens):
            return ModelReply("A claim [P99].", 20, 8, 10, 20, True, {}, 1)

    async def check():
        pages = [("Paper", 1, "A claim from a real paper about attention mechanisms.")]
        index = PaperIndex(pages, RetrievalSetting("test", 30, 1, 0))
        budget = Budget(Limits(10, 1000, 100, 10000))
        start = time.perf_counter()
        result = await answer_question(
            "attention", index, FakeModel(), budget, "deterministic", start + 1, start
        )
        assert result.status == "ok"
        assert result.text == REFUSAL

    asyncio.run(check())


def test_probe_latencies_set_queue_deadline(tmp_path):
    rows = [
        {
            "provider_first_chunk_ms": str(first),
            "model_total_ms": str(total),
            "model_chunk_count": "4",
        }
        for first, total in [(100, 400), (200, 600), (300, 800)]
    ]
    attempts = [{"status": "ok"}] * 41 + [{"status": "timeout"}]
    queue, standin = calibration(rows, attempts)
    assert standin["base_ms"] == 200
    assert standin["hang_probability"] == 0.025
    assert standin["hang_observed_count"] == 1
    assert standin["hang_attempt_count"] == 42
    assert standin["hang_ms"] == 30000
    assert queue["sample_count"] == 3
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(queue), encoding="utf-8")
    deadline, basis = queue_deadline(path)
    assert 2 < deadline < 3
    assert basis == "assumed_2xp95_plus_1s"


def test_calibration_selects_latest_probe_run(tmp_path):
    path = tmp_path / "results.csv"
    rows = [
        {
            **dict.fromkeys(CSV_FIELDS, ""),
            "source": "real",
            "scenario": "probe",
            "status": "ok",
            "verifier_mode": "deterministic",
            "run_id": run_id,
            "start_time": started,
        }
        for run_id, started in [
            ("old", "2026-11-10T01:00:00+00:00"),
            ("new", "2026-11-10T02:00:00+00:00"),
        ]
    ]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    assert [row["run_id"] for row in real_probe_rows(path)] == ["new"]
    assert len(real_attempt_rows(path)) == 2


def test_standin_separates_rpm_itpm_and_tpm():
    async def check():
        rpm = StandInState()
        rpm.hang_probability = 0
        for number in range(30):
            if number:
                rpm.last_refill_at -= 0.32
            rejection, _ = await rpm.admit(19, 2)
            assert rejection is None
            await rpm.leave()
        rejection, _ = await rpm.admit(19, 2)
        assert rejection.name == "requests per minute (RPM)"
        assert rejection.retry_header_s == 2
        assert "Requested 1" in rate_error(rejection, {}).body.decode()

        itpm = StandInState()
        rejection, _ = await itpm.admit(4673, 95)
        assert rejection is None
        await itpm.leave()
        itpm.last_refill_at -= 0.79
        rejection, _ = await itpm.admit(4673, 95)
        assert rejection.name == "input tokens per minute (ITPM)"
        response = rate_error(rejection, {})
        assert response.headers["retry-after"] == "20"
        assert "Please try again in 19." in response.body.decode()

        tpm = StandInState()
        tpm.itpm = 20000
        rejection, _ = await tpm.admit(4000, 300)
        assert rejection is None
        await tpm.leave()
        rejection, _ = await tpm.admit(4000, 300)
        assert rejection.name == "tokens per minute (TPM)"

    asyncio.run(check())


def test_nonanswers_keep_blank_grade(tmp_path):
    results = tmp_path / "results.csv"
    grades = tmp_path / "grades.csv"
    rows = [
        {
            **dict.fromkeys(CSV_FIELDS, ""),
            "request_id": "ok-1",
            "status": "ok",
            "source": "real",
        },
        {
            **dict.fromkeys(CSV_FIELDS, ""),
            "request_id": "timeout-1",
            "status": "timeout",
        },
    ]
    with results.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    grades.write_text("request_id,answer_grade\nok-1,correct\n", encoding="utf-8")
    assert apply_grades(results, grades) == 1
    with results.open(newline="", encoding="utf-8") as file:
        graded = list(csv.DictReader(file))
    assert graded[0]["answer_grade"] == "correct"
    assert graded[1]["answer_grade"] == ""


def test_retrieval_table_counts_ungraded_timeout(capsys):
    shared = {
        "scenario": "retrieval_comparison",
        "source": "real",
        "evaluation_group": "test",
        "retrieval_setting": "small",
        "verifier_mode": "deterministic",
        "model_id": "test",
        "piece_words": "300",
        "pieces_requested": "3",
        "rerank": "none",
        "tokens_in": "10",
        "tokens_out": "2",
        "first_token_ms": "10",
        "total_ms": "20",
    }
    rows = [
        {
            **shared,
            "question_id": "T01",
            "status": "ok",
            "answer_grade": "correct",
            "answer": "Supported [Paper, PDF p. 4]",
        },
        {
            **shared,
            "question_id": "T02",
            "status": "timeout",
            "answer_grade": "",
            "answer": "Please retry.",
        },
    ]
    table3(rows)
    output = capsys.readouterr().out
    assert "50.0 | 0.0 | 0.0 | 50.0" in output
    assert "not measured" not in output


def test_model_stream_requires_done_and_uses_provider_counts():
    async def check(body: str):
        model = ModelClient("http://local", "stand-in")
        await model.close()
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
        model.client = httpx.AsyncClient(transport=transport, base_url="http://local")
        try:
            return await model.complete("question", 20)
        finally:
            await model.close()

    content = 'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n'
    usage = 'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
    reply = asyncio.run(check(content + usage + "data: [DONE]\n\n"))
    assert reply.text == "Answer"
    assert (reply.tokens_in, reply.tokens_out) == (5, 2)
    assert reply.usage_measured

    try:
        asyncio.run(check(content))
    except ModelFailure as error:
        assert error.status == 502
        assert str(error) == "incomplete model stream"
    else:
        raise AssertionError("Incomplete stream counted as success")


def test_auto_mode_defers_without_model_when_tokens_tight():
    class NoModelCall:
        async def complete(self, prompt, max_tokens):
            raise AssertionError("Model must not run under pressure")

    async def check():
        pages = [("Paper", 1, "Attention uses the paper's stated scaling factor.")]
        index = PaperIndex(pages, RetrievalSetting("small", 30, 1, 0))
        budget = Budget(Limits(10, 1000, 100, 10000))
        assert await budget.reserve(650, time.perf_counter() + 1) is None
        dispatcher = Dispatcher(index, NoModelCall(), budget, "auto", 1)
        await dispatcher.start()
        answer, _, _ = await dispatcher.submit("attention scaling")
        await dispatcher.close()
        assert answer.status == "ok"
        assert answer.answer_kind == "excerpt"
        assert answer.tokens_in == 0
        assert "Paper, PDF p. 1" in answer.text

    asyncio.run(check())


def test_quality_sample_covers_all_three_papers():
    path = Path(__file__).resolve().parents[1] / "questions_candidate.json"
    questions = json.loads(path.read_text(encoding="utf-8"))
    selected = select_questions(questions, 15)
    counts = Counter(item["paper"] for item in selected)
    assert sorted(counts.values()) == [3, 4, 4, 4]
    assert len({item["id"] for item in selected}) == 15


def test_model_verifier_subset_keeps_paired_questions():
    questions = json.loads(Path("questions_candidate.json").read_text(encoding="utf-8"))
    selected = select_questions(questions, 15)
    subset = choose_question_ids(selected, ["T01", "M02", "H02", "M04"])
    assert [item["id"] for item in subset] == ["T01", "M02", "H02", "M04"]
    with pytest.raises(ValueError, match="unique"):
        choose_question_ids(selected, ["T01", "T01"])


def test_verifier_switch_changes_calls_and_tokens():
    class FakeModel:
        def __init__(self):
            self.calls = 0

        async def complete(self, prompt, max_tokens):
            self.calls += 1
            if prompt.startswith("Check whether every factual claim"):
                return ModelReply("PASS", 50, 1, 10, 20, True, {}, 1)
            return ModelReply(
                "The paper gives a scaling rule [P1].", 40, 8, 10, 20, True, {}, 1
            )

    async def check(mode):
        pages = [("Paper", 4, "The paper gives a scaling rule for attention.")]
        index = PaperIndex(pages, RetrievalSetting("small", 30, 1, 0))
        model = FakeModel()
        budget = Budget(Limits(10, 8000, 100, 100000))
        start = time.perf_counter()
        answer = await answer_question(
            "What scaling rule?", index, model, budget, mode, start + 1, start
        )
        return answer, model.calls

    deterministic, one_call = asyncio.run(check("deterministic"))
    verified, two_calls = asyncio.run(check("model"))
    assert one_call == 1
    assert two_calls == 2
    assert (deterministic.tokens_in, verified.tokens_in) == (40, 90)
    assert "[Paper, PDF p. 4]" in verified.text
