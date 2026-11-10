# The Relay

Small Python pipeline for answering questions from three exact arXiv PDF versions. `answer_question` is a plain async function. PyMuPDF reads pages, local BM25 retrieves passages, a Groq or stand-in client drafts, and either a deterministic citation check or a second model call verifies. The draft stays buffered until checked, so user-visible first-token time includes verification. `model_first_chunk_ms` separately records the provider's first content chunk.

Future latency measurements use `time.perf_counter()` for elapsed time; `start_time` remains UTC wall time. Historical small and medium `total_ms` used `time.monotonic()`. Both are monotonic performance clocks in CPython on Windows. The evaluator paces before request submission, so its historical `total_ms` excludes that pacing wait. Load runs include dispatcher queue wait and local budget pacing wait in `queue_ms` from arrival to model admission.

## Run locally

Use Python 3.12. From this folder:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q tests
python -m uvicorn relay.standin:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
python -m relay.run --source stand-in --scenario burst --setting small --verifier deterministic --output results.csv
python -m relay.report results.csv
```

The local `/control` endpoint changes the stand-in's latency, hang probability, failure probability, RPM, TPM, ITPM and daily budget. Run it only on `127.0.0.1`. The stand-in checks 30 RPM, 8,000 TPM and 7,000 ITPM separately. TPM and ITPM use continuous refill, matching the falling token usage and approximately 20-second retry-after observed in the large-call 429. An assumed RPM token bucket with 25.5 initial request credits reproduces 30 accepted calls over roughly 9 seconds followed by a 2-second retry-after. One burst cannot prove Groq's refill rule. Its 429 bodies name the limit, used and requested values. The five load scenarios are `steady`, `burst`, `overload`, `minute_halved`, and `daily_exhausted`. `--scenario all` runs them in that order. The queue holds 20 waiting requests; two workers process lower-number priorities first. Load requests use priority 0; offline quality evaluation runs separately. An `auto` verifier mode uses the model check at low pressure. At 75% of the local TPM budget it returns a clearly labelled retrieved excerpt from small retrieval, without making an unverified factual claim or using model tokens. These rows have `status=ok`, `answer_kind=excerpt`; a busy notice has `status=rate_limited`, `answer_kind=none`. Explicit deterministic and model modes remain switchable for the comparison.

Run `python -m relay.run --source stand-in --scenario retry_failure --output results.csv` separately. It forces 503 responses to confirm that server failures do not trigger retries. The live policy permits one retry only after a 429 with `retry-after`; timeouts are never retried. `relay.report` excludes this diagnostic from the five load rows.

## Real measurements

Before any real call, fill `groq_limits.json` from your account. It requires model ID, RPM, TPM, RPD, TPD, today's IST date, a UTC snapshot time, and calls/tokens already used today across **all** Groq activity. If current usage is unknown, leave it `null`; the guard refuses to run. Set `GROQ_API_KEY` only in the terminal environment. Never put the key in code, config, CSV, or submission. The account's exact limits take precedence over the simulator's 30 RPM / 8,000 TPM / 1,000 RPD / 200,000 TPD assumptions. Groq's [rate-limit documentation](https://console.groq.com/docs/rate-limits) says response headers report RPD for requests and TPM for tokens; they do not report RPM or TPD directly. The second large call measured a separate **7,000 input tokens/minute (ITPM)** limit in its 429 body; `itpm` in the config records that observation.

Every real CLI batch prints its model name, calls and conservative tokens used today, then checks planned calls, prompt/context tokens, and output reservations against **150 calls and 120,000 tokens per IST day**, also capped at 85% of account RPD and TPD. With the measured 1,000 RPD and 200,000 TPD, the project caps bind first. Normal batches also reserve two retry slots. The nine-call probe has no retries and checks exactly nine calls. `groq_budget_ledger.jsonl` records each reserved model call before HTTP, including failed attempts. A lock keeps concurrent local processes from exceeding the cap. A refreshed account-usage snapshot ignores older ledger entries already included in that snapshot. No real call runs with missing config, an account snapshot older than 30 minutes, a stale date, or an unguarded API key. Other Groq activity after the snapshot is not visible to this project, so refresh usage before a batch and do not run unrelated Groq jobs alongside it.

Token reservation uses `ceil(prompt characters / 3) + maximum output tokens + 32 chat-overhead tokens`, a conservative English-text estimate rather than the model's exact tokenizer. The overhead was added after two 21-token short replies exceeded the old 16-token estimate. The hard stop applies to reservations; refresh account usage before later batches because actual provider usage may differ. The guard also stops a process that crosses IST midnight until its config is refreshed.

For the approved first nine calls, after the config and key are ready:

```powershell
python -m relay.probe_nine
python -m relay.calibrate results.csv
```

The first probe saved nine attempted calls: eight replies and one `ReadTimeout` on call 7. `results.csv` records every attempt, `probe_1_9_raw.jsonl` holds all returned rate-limit headers and request IDs, and `probe_1_9_summary.json` compares estimates with provider usage. Call 7 has no raw header record because no response arrived. A user-supplied Groq Console Logs screenshot shows the matching request as `499 Client Disconnected` with zero input and output tokens. RPD headers suggest it did not consume a request slot: remaining requests fell from 994 after call 6 to 992 after successful calls 8 and 9. This is an inference because the two later calls were concurrent. The runner now stops on any failed call; calls 8–9 in the first run were sent because of a runner bug that has been fixed and regression tested.

The approved follow-up uses a fresh hidden key prompt without re-entering the limits:

```powershell
python -m relay.interactive_followup
```

It preflights all 33 possible calls against the daily guard, sends up to 31 small calls in one fresh minute, stops that batch at the first 429 or other failure, then waits for another fresh minute before two large-context calls. There are no retries. Every call has an absolute timeout; timed-out attempts get `status=timeout` in `results.csv`. `probe_rpm_raw.jsonl` and `probe_token_raw.jsonl` retain complete rate-limit headers, request IDs and full 429 bodies. `probe_followup_summary.json` puts provider usage beside signed header-token deltas and `prompt_tokens + max_output_tokens`; deltas across elapsed time also reflect TPM refill.

`calibrate` uses successful draft calls 1–3 and writes `calibration.json` for the queue deadline and `standin_calibration.json` for the stand-in's first-chunk latency, spread, and chunk interval. The current **2.29-second deadline is an assumption**, calculated as `min(12 s assumed human patience, 2 × measured call p95 + 1 s)`. The 12-second patience cap and 1-second margin are assumptions; p95 from three calls is very noisy. One of 42 mixed-purpose real attempts hung until a 30-second client disconnect. The stand-in uses an **assumed 2.5% hang chance**, rounded from that single observation; uncertainty is wide. Hanging attempts stall before simulated quota admission, matching the observed zero-token 499. The stand-in's load penalty is also an assumption. No 503 occurred in the probes, so ordinary simulated 503 probability is zero; the dedicated `retry_failure` diagnostic injects them to check the no-retry rule.

`latency_spread_ms` is the half-width of the stand-in's uniform random delay offset: each admitted call adds a seeded offset between `-latency_spread_ms` and `+latency_spread_ms`. The calibrated 56.7 ms is estimated from the first-chunk latency range of only three real calls, so it is illustrative rather than a reliable distribution fit. `retry_ms` includes backoff even if a second HTTP call never starts; `retries` counts second HTTP calls actually sent.

Then start the stand-in and run the five scenarios:

```powershell
python -m relay.run --source stand-in --scenario all --setting small --verifier auto --output results.csv
```

The first 12 load runs remain in `results.csv` with `run_label=pass1`. They offered steady traffic at 3/min, which exceeded the intended 1.5/min comparison. Pass2 offers steady traffic at 40 virtual seconds between arrivals. Each run is labelled **"5x time-compressed"**. `--time-scale 5 --spacing-s 40` compresses arrivals to 8 real seconds and the stand-in's minute window from 60 to 12 real seconds. Stand-in delays and request deadlines also divide by five. This preserves the ratios of arrival, quota refill, service time and deadline; latency is stored as real elapsed milliseconds, and Table 2 displays 5x time-compressed results in virtual milliseconds. The assumed hang probability and concurrent scheduling remain approximations. The other pass2 cases repeat both 2.29 s and 20 s deadlines with seed 7: 100 arrivals each for burst and overload, 40 for each halved-limit variant, and 10 for daily exhaustion. Repeat `minute_halved` with `--no-latency-spread` at both deadlines; the ordinary runs use the calibrated `latency_spread_ms=56.7`. `results.csv` records seed, deadline, `queue_ms`, `retry_ms`, `answer_kind`, and `http_attempts` per arrival. Every HTTP attempt, including a 429 followed by a later budget wait or a timeout, has a separate raw JSONL record. The 2.29-second deadline is an assumption derived from three probe latencies; 20 seconds is an experimental comparison, not a measured patience threshold.

At 1.5 arrivals/min, `auto` delivered 37/100 and 26/100 full answers under the two deadlines; its conservative local daily token reservations exhausted the 200K cap. The same schedule with the deterministic verifier (`run_label=pass2_det`) delivered 99/100 at both deadlines and used 176,039 measured stand-in tokens per run. This shows a capacity cost for the extra verifier call, not a quality difference. The stand-in's one seeded hang caused the remaining timeout in each deterministic run.

```powershell
python -m relay.run --source stand-in --scenario steady --verifier auto --spacing-s 40 --time-scale 5 --deadline-s 2.29 --run-label pass2
python -m relay.run --source stand-in --scenario steady --verifier auto --spacing-s 40 --time-scale 5 --deadline-s 20 --run-label pass2
```

Before running the retry comparison, define **"retry made it worse"** for each matched seed and arrival schedule: fewer of the 40 answers delivered by 20 seconds than in the zero-retry arm, or a worse p95 delivery time among the fixed request IDs that succeeded on their first attempt in that zero-retry arm. Percentiles use nearest rank. An undelivered member of that cohort has delivery time "not reached by 20 seconds," worse than a finite baseline p95. Small measured differences may be scheduling noise; report each seed separately. All-request p50/p95 delivery times use the same censored rule, so they are "not reached" when fewer than 20 or 38 of 40 answers arrive.

`python -m relay.retry_experiment` runs a zero-retry baseline first, then no-jitter and 0–2 s jitter arms, each with unlimited retries or a shared 10-retry budget. Each request has at most one retry after a 429 and honours `retry-after`. The client assumes 30 RPM while the local server enforces 10 RPM, and the local gate is off. Compare both 0.1-second arrivals and a burst of 40 arrivals within one second; run burst seeds 7, 8 and 9 separately. Save each attempt's full 429 body and headers in `retry_experiment_v2_raw.jsonl`, and per-arm outcomes, censored latency percentiles, and paired delays in `retry_experiment_v2_summary.json`. The older `retry_experiment_summary.json` and `pass2` CSV rows remain intact. It rejects non-local URLs. This diagnoses retry amplification; it does not measure answer quality. Counts labelled "first-success displaced/slower" compare request indices with their zero-retry baseline; they are matched-run proxies because host scheduling can shift between arms.

In the subsecond bursts, every retry arm delivered 9/40 versus zero-retry's 8/40 for each seed. The predeclared "retry made it worse" flag still varied by seed because baseline-cohort p95 shifts were only about -17 to +21 ms; no burst arm displaced a baseline first-attempt success. These small timing changes are not evidence of a reliable retry benefit or harm. All-request p50 and p95 were not reached in any arm.

Use separate result files if you change the schema or a run's assumptions. `results.csv` rejects schema mismatches rather than silently appending misaligned rows.

## Quality comparison

`questions_candidate.json` has 18 questions. The selected 15 contain four per paper and three unanswerable questions; they are marked `verified: true` after applicant sign-off. Three unused candidates remain false. Real evaluation checks only the selected batch. The comparison runs the same 15 across small (300 words × 3 passages), medium (450 × 4), and large (700 × 4), with the deterministic verifier.

Refresh account-wide usage in `groq_limits.json`, then run `python -m relay.interactive_evaluate --setting small` for the approved small-setting batch. It asks for the key in a hidden terminal prompt and never stores it. Each HTTP attempt is written to `evaluation_raw.jsonl`, including full rate-limit headers, any 429 body, measured usage, and timeouts without a response. `results.csv` gets one row per question with `scenario=retrieval_comparison` and its retrieval setting; older probe rows use `scenario=probe`, with probe subtype in `evaluation_group`. Repeat for medium and large only in batches the guard accepts. `--offset` and `--batch-size` split the same balanced 15 questions across days; keep the same `--evaluation-group` so the report combines non-overlapping batches. A separate small-setting `--verifier model --question-ids T01 M02 H02 M04 --evaluation-group final_v1_model` batch measures verification cost on paired questions. This subset was chosen after inspecting small and medium results; it supersedes earlier U01 and H03 versions and supports a cost measurement only, not a quality claim.

The evaluator paces calls outside the user-facing queue so a quality test does not waste the minute budget on predictable queue timeouts. The model-verifier comparison covers **four paired questions** and measures extra tokens and latency. It is **a cost measurement, not a quality claim**. After selecting a setting from the 15-question comparison, reserve a separate, newly written and human-verified 10-question holdout for that setting only; see `holdout_budget_plan.md`. Do not use holdout answers to choose the setting. Do not treat stand-in answers as a quality result; they are canned. A human should grade real answers with `correct`, `wrong`, `refused_correctly`, or `refused_wrongly`. For non-answers (`status != ok`), leave `answer_grade` blank. Create `grades.csv` with `request_id,answer_grade`, then run:

```powershell
python -m relay.grade results.csv grades.csv
python -m relay.report results.csv
```

`python -m relay.capacity` regenerates `capacity_measured.csv` from measured calls 1–5 and prompt-length extrapolation. It compares the same four answerable questions across all three retrieval settings and counts retrieved context in both draft and verifier. The measured call 10 used the former 750 × 5 large setting, so it is not an exact sample of the revised 700 × 4 setting; the regenerated large rows are explicitly marked unmeasured extrapolations. The table shows capacity from actual usage, the separately measured 7,000 input tokens/minute limit, and a hypothesis that Groq reserves `prompt_tokens + max_output_tokens` at admission. Short-call headers support that hypothesis; the large-call header did not show the same reservation, so both capacity interpretations stay visible.

`results.csv` begins with all columns required by the brief. Extra columns hold queue/work/retry time, model first chunk, provider limit headers, load schedule and queue-deadline basis. Table 2 duration means the offered-arrival window; it excludes queue drain time. Latency percentiles use delivered full answers and excerpts only; timeouts and notices have separate counts. Table 2 outcome percentages use all arrivals and distinguish full answers, excerpts, rate-limited notices, and timeouts. `status=rate_limited, answer_kind=none` never counts as success. Tokens per answer average delivered rows. Table 3 quality uses the manually checked grades; `correct` should mean both the claim and cited PDF page were checked. Its no-answer column includes timeouts and limits while keeping `answer_grade` blank. Percentiles use linear interpolation over sorted samples. Monetary cost is `$0.00` on free services; token usage is the scarce resource.

## Known limits

- The deterministic verifier checks valid passage IDs, not semantic support. The model verifier is measured separately and can still make mistakes.
- The PDF parser uses PyMuPDF's sorted text. Equations, figures, columns, and appendices can hurt retrieval; inspect failures against page images.
- The local minute gate estimates combined tokens from `characters / 4 + max output` and input tokens from `characters × 0.30`. It checks the measured 7,000 ITPM limit as well as 8,000 TPM. Actual provider usage is recorded separately. Estimates and 85% headroom can underuse capacity.
- The stand-in models latency shape and explicit failures but cannot reproduce the provider's hidden scheduling or answer quality.
