# The Relay

A small Python system for answering questions from three fixed research papers while measuring how it behaves under service limits. The main entry point is `answer_question`, a plain async function. FastAPI serves only the local stand-in model.

## Read the code in this order

| File | Role |
|---|---|
| `relay/papers.py` | Extract PDF pages, split them without crossing page boundaries, and rank passages with BM25. |
| `relay/pipeline.py` | Draft an answer, check its citations, optionally ask the model to verify it, and choose the user-facing response. |
| `relay/model.py` | Wrap streaming HTTP, timeouts, usage, rate-limit headers, and raw attempt logging. Other pipeline code never handles model HTTP. |
| `relay/limits.py` | Admit calls under estimated minute and daily limits. |
| `relay/budget_guard.py` | Refuse real calls when the fresh account snapshot and local reservations exceed the daily cap. |
| `relay/run.py` | Generate load, manage the bounded priority queue, and write `results.csv`. |
| `relay/standin.py` | Simulate latency, hangs, and separate RPM, TPM, and input-TPM limits locally. |
| `relay/evaluate.py`, `relay/report.py` | Run the frozen question set and summarize saved results. |

`answer_question` retrieves passages, makes one draft call, and checks citation IDs. The optional model verifier makes a second call with the same passages. The deterministic check verifies citation IDs only; it does not prove factual support. The draft stays buffered until checking finishes, so `first_token_ms` is user-visible time, while `model_first_chunk_ms` records first provider content. An `auto` load mode can return a clearly labelled retrieved excerpt when the local token budget is under pressure.

## Run locally without Groq

Use Python 3.12. From the project folder:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q tests
python -m ruff check .
python -m uvicorn relay.standin:app --host 127.0.0.1 --port 8000
```

In a second terminal, make three local requests. `smoke.csv` is ignored by Git; this leaves saved `results.csv` intact.

```powershell
python -m relay.run --source stand-in --scenario steady --count 3 --spacing-s 1 --setting small --verifier deterministic --deadline-s 20 --output smoke.csv
python -m relay.report smoke.csv
```

The five load scenarios are `steady`, `burst`, `overload`, `minute_halved`, and `daily_exhausted`. The queue holds 20 waiting requests and uses two workers. A request can receive a full answer, a labelled excerpt, or a notice. `status=rate_limited, answer_kind=none` never counts as a delivered answer. After a provider 429, the client honours `retry-after` and retries at most once; a timeout is never retried. Raw JSONL records each HTTP attempt, including 429 bodies and response rate-limit headers.

## Real calls and saved evidence

Real calls require `groq_limits.json` and a fresh account-wide usage snapshot. Evaluation batches read the key through `relay.interactive_evaluate`'s hidden prompt; `relay.run --source real` reads `GROQ_API_KEY` from the terminal environment. Both use the same guard. The config and reservation ledger are ignored by Git. The guard refuses a batch beyond 150 calls or 120,000 reserved tokens per IST day, or 85% of account RPD or TPD. It reserves before each HTTP call; other account activity requires a refreshed snapshot. Do not put the key in code, files, or `results.csv`.

`results.csv` already contains the saved probes, question comparisons, and load runs. Use a different output file for new experiments. The first 12 stand-in runs remain labelled `pass1`; later runs carry their own labels. The `5x time-compressed` label means arrivals, quota refill, stand-in latency, and deadline were scaled together. The 2.29-second deadline and stand-in's roughly 2.5% hang chance are assumptions from small probe samples, not measured user patience or a reliable failure rate.

Small, medium, and large question comparisons each have 15 real responses. Their saved answers were checked against the cited PDF pages and graded through `grades.csv`; non-answers remain blank. The four-question model-verifier subset was selected after inspecting results; it measures cost, not quality. A new-question holdout was not run. `questions_candidate.json` holds the selected questions and unused reserves.

`python analyze.py --output draft_tables.md` reads saved results and companion raw evidence and writes draft tables; it does not grade answers. The ignored raw JSONL files must be copied from the submission folder when reproducing tables from a fresh checkout. `real_steady_console.csv` copies nine provider latency rows from the 11 November console export. Its paired table assumes chronological order because the local request IDs and provider IDs differ. The timed-out eighth request has no provider row. The cause of the client-minus-console latency gap is not isolated.

The reviewed grade decisions are in `grades.csv`. Reapply them with `python -m relay.grade results.csv grades.csv` and rebuild summary tables with `python analyze.py`. The two-page `writeup.pdf` is generated from `writeup.md` by `python render_writeup.py`. The applicant should read its first-person reflection and the grades before submitting. `prompts_from_chat.md` contains the exact user prompts extracted from this project chat because the app did not provide a full transcript export.
