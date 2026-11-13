## CSV validation
Rows: 2825; duplicate request IDs: 0; required columns: first 12 in brief order; vocabularies: valid.
- burst, stand-in: 400
- daily_exhausted, stand-in: 40
- minute_halved, stand-in: 320
- naive_retry, stand-in: 960
- overload, stand-in: 400
- probe, real: 42
- retrieval_comparison, real: 53
- steady, real: 10
- steady, stand-in: 600
Percentiles: sort delivered samples, interpolate linearly at (n-1) * fraction; time-compressed latencies multiplied by time_scale.
## Table 1: real service limits (probe raw files)
| Limit | Value | How found | Confidence |
|---|---:|---|---|
| RPM | 30 | 30 accepted, then 429 | high at probe time |
| TPM | 8000 | response headers | high at probe time |
| ITPM | 7000 | one 429 body | medium |
| RPD | 1000 | response headers | high at probe time |
| TPD | 200000 | recorded configuration; no provider test | not measured |
| In-flight | at least 2 | calls 8 and 9 overlap: True; maximum not tested | limited observation |
| Recovery | 2, 20 s | retry-after on two 429s | observed twice |
| Token refill | 7 negative header-token deltas | later headers show recovered tokens | inferred |

## Table 2a: load latency (delivered only; p50/p95 linear interpolation)
| Source | Pass | Scenario | Clock | Deadline s | Spread ms | Arrivals | Offered/min | First token ms | Total ms |
|---|---|---|---|---:|---:|---:|---:|---|---|
| stand-in | pass1 | burst | real time | 2.29 | 56.7 | 100 | 1200.0 | 892.5/892.5 (n=1) | 892.5/892.5 (n=1) |
| stand-in | pass1 | burst | real time | 20.0 | 56.7 | 100 | 1200.0 | 850.1/850.1 (n=1) | 850.1/850.1 (n=1) |
| stand-in | pass1 | daily_exhausted | real time | 2.29 | 56.7 | 10 | 120.0 | not measured (n=0) | not measured (n=0) |
| stand-in | pass1 | daily_exhausted | real time | 20.0 | 56.7 | 10 | 120.0 | not measured (n=0) | not measured (n=0) |
| stand-in | pass1 | minute_halved | real time | 2.29 | 56.7 | 40 | 100.0 | 720.7/720.7 (n=1) | 720.7/720.7 (n=1) |
| stand-in | pass1 | minute_halved | real time | 2.29 | 0.0 | 40 | 100.0 | 684.7/684.7 (n=1) | 684.7/684.7 (n=1) |
| stand-in | pass1 | minute_halved | real time | 20.0 | 56.7 | 40 | 100.0 | 717.1/717.1 (n=1) | 717.1/717.1 (n=1) |
| stand-in | pass1 | minute_halved | real time | 20.0 | 0.0 | 40 | 100.0 | 721.7/721.7 (n=1) | 721.7/721.7 (n=1) |
| stand-in | pass1 | overload | real time | 2.29 | 56.7 | 100 | 171.4 | 731.7/731.7 (n=1) | 731.7/731.7 (n=1) |
| stand-in | pass1 | overload | real time | 20.0 | 56.7 | 100 | 171.4 | 721.6/721.6 (n=1) | 721.6/721.6 (n=1) |
| stand-in | pass1 | steady | real time | 2.29 | 56.7 | 100 | 3.0 | 724.8/811.4 (n=33) | 724.8/811.4 (n=33) |
| stand-in | pass1 | steady | real time | 20.0 | 56.7 | 100 | 3.0 | 716.8/801.4 (n=24) | 716.8/801.4 (n=24) |
| stand-in | pass2 | burst | real time | 2.29 | 56.7 | 100 | 1200.0 | 0.8/497.8 (n=99) | 0.8/497.8 (n=99) |
| stand-in | pass2 | burst | real time | 20.0 | 56.7 | 100 | 1200.0 | 0.7/496.6 (n=99) | 0.7/496.6 (n=99) |
| stand-in | pass2 | daily_exhausted | real time | 2.29 | 56.7 | 10 | 120.0 | not measured (n=0) | not measured (n=0) |
| stand-in | pass2 | daily_exhausted | real time | 20.0 | 56.7 | 10 | 120.0 | not measured (n=0) | not measured (n=0) |
| stand-in | pass2 | minute_halved | real time | 2.29 | 56.7 | 40 | 100.0 | 0.7/1.6 (n=39) | 0.7/1.6 (n=39) |
| stand-in | pass2 | minute_halved | real time | 2.29 | 0.0 | 40 | 100.0 | 0.8/1.2 (n=39) | 0.8/1.2 (n=39) |
| stand-in | pass2 | minute_halved | real time | 20.0 | 56.7 | 40 | 100.0 | 0.7/1.3 (n=39) | 0.7/1.3 (n=39) |
| stand-in | pass2 | minute_halved | real time | 20.0 | 0.0 | 40 | 100.0 | 0.8/1.3 (n=39) | 0.8/1.3 (n=39) |
| stand-in | pass2 | overload | real time | 2.29 | 56.7 | 100 | 171.4 | 0.7/1.0 (n=99) | 0.7/1.0 (n=99) |
| stand-in | pass2 | overload | real time | 20.0 | 56.7 | 100 | 171.4 | 0.7/1.2 (n=99) | 0.7/1.2 (n=99) |
| stand-in | pass2 | steady | 5x time-compressed | 2.29 | 56.7 | 100 | 1.5 | 881.9/971.6 (n=37) | 881.9/971.6 (n=37) |
| stand-in | pass2 | steady | 5x time-compressed | 20.0 | 56.7 | 100 | 1.5 | 922.9/1045.5 (n=26) | 922.9/1045.5 (n=26) |
| stand-in | pass2_det | steady | 5x time-compressed | 2.29 | 56.7 | 100 | 1.5 | 424.5/510.3 (n=99) | 424.5/510.3 (n=99) |
| stand-in | pass2_det | steady | 5x time-compressed | 20.0 | 56.7 | 100 | 1.5 | 476.0/590.4 (n=99) | 476.0/590.4 (n=99) |

## Table 2b: load outcomes (denominator: all arrivals)
| Source | Pass | Scenario | Clock | Deadline s | Spread ms | Full | Excerpt | Rate-limited | Timeout | Tokens/answer | Cost/1,000 answers |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|
| stand-in | pass1 | burst | real time | 2.29 | 56.7 | 1.0% (1/100) | 0.0% (0/100) | 98.0% (98/100) | 1.0% (1/100) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | burst | real time | 20.0 | 56.7 | 1.0% (1/100) | 0.0% (0/100) | 98.0% (98/100) | 1.0% (1/100) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | daily_exhausted | real time | 2.29 | 56.7 | 0.0% (0/10) | 0.0% (0/10) | 100.0% (10/10) | 0.0% (0/10) | not measured (n=0) | not measured (n=0) |
| stand-in | pass1 | daily_exhausted | real time | 20.0 | 56.7 | 0.0% (0/10) | 0.0% (0/10) | 100.0% (10/10) | 0.0% (0/10) | not measured (n=0) | not measured (n=0) |
| stand-in | pass1 | minute_halved | real time | 2.29 | 56.7 | 2.5% (1/40) | 0.0% (0/40) | 97.5% (39/40) | 0.0% (0/40) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | minute_halved | real time | 2.29 | 0.0 | 2.5% (1/40) | 0.0% (0/40) | 97.5% (39/40) | 0.0% (0/40) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | minute_halved | real time | 20.0 | 56.7 | 2.5% (1/40) | 0.0% (0/40) | 95.0% (38/40) | 2.5% (1/40) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | minute_halved | real time | 20.0 | 0.0 | 2.5% (1/40) | 0.0% (0/40) | 95.0% (38/40) | 2.5% (1/40) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | overload | real time | 2.29 | 56.7 | 1.0% (1/100) | 0.0% (0/100) | 98.0% (98/100) | 1.0% (1/100) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | overload | real time | 20.0 | 56.7 | 1.0% (1/100) | 0.0% (0/100) | 98.0% (98/100) | 1.0% (1/100) | 2982 (n=1) | $0.00 (n=1) |
| stand-in | pass1 | steady | real time | 2.29 | 56.7 | 33.0% (33/100) | 0.0% (0/100) | 34.0% (34/100) | 33.0% (33/100) | 3520 (n=33) | $0.00 (n=33) |
| stand-in | pass1 | steady | real time | 20.0 | 56.7 | 24.0% (24/100) | 0.0% (0/100) | 35.0% (35/100) | 41.0% (41/100) | 3590 (n=24) | $0.00 (n=24) |
| stand-in | pass2 | burst | real time | 2.29 | 56.7 | 1.0% (1/100) | 98.0% (98/100) | 0.0% (0/100) | 1.0% (1/100) | 30 (n=99) | $0.00 (n=99) |
| stand-in | pass2 | burst | real time | 20.0 | 56.7 | 1.0% (1/100) | 98.0% (98/100) | 0.0% (0/100) | 1.0% (1/100) | 30 (n=99) | $0.00 (n=99) |
| stand-in | pass2 | daily_exhausted | real time | 2.29 | 56.7 | 0.0% (0/10) | 0.0% (0/10) | 100.0% (10/10) | 0.0% (0/10) | not measured (n=0) | not measured (n=0) |
| stand-in | pass2 | daily_exhausted | real time | 20.0 | 56.7 | 0.0% (0/10) | 0.0% (0/10) | 100.0% (10/10) | 0.0% (0/10) | not measured (n=0) | not measured (n=0) |
| stand-in | pass2 | minute_halved | real time | 2.29 | 56.7 | 2.5% (1/40) | 95.0% (38/40) | 2.5% (1/40) | 0.0% (0/40) | 76 (n=39) | $0.00 (n=39) |
| stand-in | pass2 | minute_halved | real time | 2.29 | 0.0 | 2.5% (1/40) | 95.0% (38/40) | 2.5% (1/40) | 0.0% (0/40) | 76 (n=39) | $0.00 (n=39) |
| stand-in | pass2 | minute_halved | real time | 20.0 | 56.7 | 2.5% (1/40) | 95.0% (38/40) | 0.0% (0/40) | 2.5% (1/40) | 76 (n=39) | $0.00 (n=39) |
| stand-in | pass2 | minute_halved | real time | 20.0 | 0.0 | 2.5% (1/40) | 95.0% (38/40) | 0.0% (0/40) | 2.5% (1/40) | 76 (n=39) | $0.00 (n=39) |
| stand-in | pass2 | overload | real time | 2.29 | 56.7 | 1.0% (1/100) | 98.0% (98/100) | 0.0% (0/100) | 1.0% (1/100) | 30 (n=99) | $0.00 (n=99) |
| stand-in | pass2 | overload | real time | 20.0 | 56.7 | 1.0% (1/100) | 98.0% (98/100) | 0.0% (0/100) | 1.0% (1/100) | 30 (n=99) | $0.00 (n=99) |
| stand-in | pass2 | steady | 5x time-compressed | 2.29 | 56.7 | 37.0% (37/100) | 0.0% (0/100) | 25.0% (25/100) | 38.0% (38/100) | 3567 (n=37) | $0.00 (n=37) |
| stand-in | pass2 | steady | 5x time-compressed | 20.0 | 56.7 | 26.0% (26/100) | 0.0% (0/100) | 30.0% (30/100) | 44.0% (44/100) | 3453 (n=26) | $0.00 (n=26) |
| stand-in | pass2_det | steady | 5x time-compressed | 2.29 | 56.7 | 99.0% (99/100) | 0.0% (0/100) | 0.0% (0/100) | 1.0% (1/100) | 1778 (n=99) | $0.00 (n=99) |
| stand-in | pass2_det | steady | 5x time-compressed | 20.0 | 56.7 | 99.0% (99/100) | 0.0% (0/100) | 0.0% (0/100) | 1.0% (1/100) | 1778 (n=99) | $0.00 (n=99) |

### Real steady: paired client and console latency
Assumption: pair delivered requests with the nine vaani#3 console rows by chronological order; request 8 has no console row. Client `total_ms` comes from `results.csv`; provider latency comes from the 11 Nov export. Round each displayed latency to whole milliseconds before subtraction.
| Request | Client total_ms | Console latency_ms | Difference_ms |
|---:|---:|---:|---:|
| 1 | 2,096 | 334 | 1,762 |
| 2 | 2,911 | 438 | 2,473 |
| 3 | 2,795 | 414 | 2,381 |
| 4 | 1,666 | 458 | 1,208 |
| 5 | 1,372 | 277 | 1,095 |
| 6 | 3,168 | 342 | 2,826 |
| 7 | 1,945 | 180 | 1,765 |
| 8 | 5,059 | no row | excluded (`ConnectTimeout`) |
| 9 | 6,537 | 317 | 6,220 |
| 10 | 865 | 411 | 454 |
Real steady: sent 10, delivered 9, ConnectTimeout 1. Delivered client total_ms p50 2,096, max 6,537 (n=9); no p95 or p99.
Difference median: 1,765 ms; range: 454–6,220 ms (n=9). The cause of the client-minus-console gap is not isolated; connection and response-header times were not recorded.

## Table 3: real retrieval comparison (grades pending)
| Setting | n | Correct+cited | Wrong | Refused correctly | No answer | Tokens/answer | First token p50/p95 ms | Total p50/p95 ms |
|---|---:|---|---|---|---|---|---|---|
| small | 15 | 60.0% (9/15) | 20.0% (3/15) | 20.0% (3/15) | 0.0% (0/15) | 1528 (n=15) | 563.0/764.1 (n=15) | 563.0/764.1 (n=15) |
| medium | 15 | 66.7% (10/15) | 13.3% (2/15) | 20.0% (3/15) | 0.0% (0/15) | 2653 (n=15) | 734.0/1697.1 (n=15) | 734.0/1697.1 (n=15) |
| large | 15 | 66.7% (10/15) | 13.3% (2/15) | 20.0% (3/15) | 0.0% (0/15) | 3446 (n=15) | 697.8/1066.5 (n=15) | 697.8/1066.5 (n=15) |

## Narrative evidence (saved rows and raw files)
Probe call 7, 725755cd0a-7: timeout after 30171 ms; queue 0, model-call wait 30171, retry 0 ms; first token not observed, no headers. Console excerpt: code 499, latency 30.000s, TTFT 0s, tokens 0+0. Provider processing during wait is not measured.
Two 429s in user-pasted console excerpt took 1-11 ms provider time; their saved client round trips were 203 ms, 203 ms.
Stand-in steady pass2, 2.29s, 5x time-compressed: full 37/100, daily-budget notices 25, measured tokens 199134.
Stand-in steady pass2_det, 2.29s, 5x time-compressed: full 99/100, daily-budget notices 0, measured tokens 176039.
Stand-in steady pass2, 20.0s, 5x time-compressed: full 26/100, daily-budget notices 30, measured tokens 167743.
Stand-in steady pass2_det, 20.0s, 5x time-compressed: full 99/100, daily-budget notices 0, measured tokens 176039.
Stand-in retry burst seed 7, zero_retry: 8/40 delivered; 40 HTTP calls; 0.00 extra calls per delivered answer; displaced first successes 0.
Stand-in retry burst seed 7, no_jitter_unlimited: 9/40 delivered; 72 HTTP calls; 3.56 extra calls per delivered answer; displaced first successes 0.
Stand-in retry burst seed 7, jitter_unlimited: 9/40 delivered; 72 HTTP calls; 3.56 extra calls per delivered answer; displaced first successes 0.
Stand-in retry burst seed 7, no_jitter_budget10: 9/40 delivered; 50 HTTP calls; 1.11 extra calls per delivered answer; displaced first successes 0.
Stand-in retry burst seed 7, jitter_budget10: 9/40 delivered; 50 HTTP calls; 1.11 extra calls per delivered answer; displaced first successes 0.
Across saved retry arms: displaced first successes 2; predeclared worse flag 9/16 arms.
H02 small: p.3 chunk rank [6] among 270 chunks.
H02 medium: p.3 chunk rank [9] among 196 chunks.
Halved-limit 2d4699a7de-1: retry wait 12003 ms, HTTP attempts 1, retries 0, status timeout.
Halved-limit ad5e68e20a-1: retry wait 12017 ms, HTTP attempts 1, retries 0, status timeout.
Busy notice example, exact saved text: The model is busy at its current limit. Please retry shortly. Possible source pages: [Moshi, PDF p. 15], [Moshi, PDF p. 1]. I cannot verify an answer now.
Daily-exhausted notice, exact saved text: The daily model budget is exhausted. I cannot check the papers right now. Please try after the quota resets.

## Model-verifier subset: cost measurement, not a quality claim
| ID | Deterministic small: input/output, total ms, status | Attempt 1: input/output, total ms, status | Attempt 2: input/output, total ms, status |
|---|---|---|---|
| T01 | 914/112, 797, ok | 914/112, 2312, timeout | 1923/114, 1118, ok |
| M02 | 1238/106, 671, ok | 1238/106, 2300, timeout | 2565/108, 1188, ok |
| H02 | 1629/6, 500, ok | 1629/6, 2292, timeout | 3247/8, 1983, ok |
| M04 | 1558/127, 750, ok | 3226/129, 1302, ok | 3226/129, 1893, ok |
Attempt 1 used an assumed 2.29s load-style deadline. Attempt 2 uses pre-run pacing outside total_ms, a 30s cap per HTTP call, and a 180s total bound.
