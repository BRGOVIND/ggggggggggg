## Table 2a: latency (delivered answers only; 5x time-compressed runs in virtual ms)
| Pass | Scenario | Deadline s | latency_spread_ms | Clock | Seed | Sent | Answered | Duration s | Offered/min | First token p50/p95/p99 ms | Total p50/p95/p99 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| pass1 | steady | 2.29 | 56.7 | real time | 7 | 100 | 33 | 2000.0 | 3.0 | 724.8 / 811.4 / 847.3 | 724.8 / 811.4 / 847.3 |
| pass1 | steady | 20.0 | 56.7 | real time | 7 | 100 | 24 | 2000.0 | 3.0 | 716.8 / 801.4 / 810.1 | 716.8 / 801.4 / 810.1 |
| pass1 | burst | 2.29 | 56.7 | real time | 7 | 100 | 1 | 5.0 | 1200.0 | 892.5 / 892.5 / 892.5 | 892.5 / 892.5 / 892.5 |
| pass1 | burst | 20.0 | 56.7 | real time | 7 | 100 | 1 | 5.0 | 1200.0 | 850.1 / 850.1 / 850.1 | 850.1 / 850.1 / 850.1 |
| pass1 | overload | 2.29 | 56.7 | real time | 7 | 100 | 1 | 35.0 | 171.4 | 731.7 / 731.7 / 731.7 | 731.7 / 731.7 / 731.7 |
| pass1 | overload | 20.0 | 56.7 | real time | 7 | 100 | 1 | 35.0 | 171.4 | 721.6 / 721.6 / 721.6 | 721.6 / 721.6 / 721.6 |
| pass1 | minute_halved | 2.29 | 56.7 | real time | 7 | 40 | 1 | 24.0 | 100.0 | 720.7 / 720.7 / 720.7 | 720.7 / 720.7 / 720.7 |
| pass1 | minute_halved | 2.29 | 0.0 | real time | 7 | 40 | 1 | 24.0 | 100.0 | 684.7 / 684.7 / 684.7 | 684.7 / 684.7 / 684.7 |
| pass1 | minute_halved | 20.0 | 56.7 | real time | 7 | 40 | 1 | 24.0 | 100.0 | 717.1 / 717.1 / 717.1 | 717.1 / 717.1 / 717.1 |
| pass1 | minute_halved | 20.0 | 0.0 | real time | 7 | 40 | 1 | 24.0 | 100.0 | 721.7 / 721.7 / 721.7 | 721.7 / 721.7 / 721.7 |
| pass1 | daily_exhausted | 2.29 | 56.7 | real time | 7 | 10 | 0 | 5.0 | 120.0 | not measured / not measured / not measured | not measured / not measured / not measured |
| pass1 | daily_exhausted | 20.0 | 56.7 | real time | 7 | 10 | 0 | 5.0 | 120.0 | not measured / not measured / not measured | not measured / not measured / not measured |
| pass2 | steady | 2.29 | 56.7 | 5x time-compressed | 7 | 100 | 37 | 4000.0 | 1.5 | 881.9 / 971.6 / 973.9 | 881.9 / 971.6 / 973.9 |
| pass2 | steady | 20.0 | 56.7 | 5x time-compressed | 7 | 100 | 26 | 4000.0 | 1.5 | 922.9 / 1045.5 / 1046.5 | 922.9 / 1045.5 / 1046.5 |
| pass2 | burst | 2.29 | 56.7 | real time | 7 | 100 | 99 | 5.0 | 1200.0 | 0.8 / 497.8 / 740.2 | 0.8 / 497.8 / 740.2 |
| pass2 | burst | 20.0 | 56.7 | real time | 7 | 100 | 99 | 5.0 | 1200.0 | 0.7 / 496.6 / 724.8 | 0.7 / 496.6 / 724.8 |
| pass2 | overload | 2.29 | 56.7 | real time | 7 | 100 | 99 | 35.0 | 171.4 | 0.7 / 1.0 / 67.2 | 0.7 / 1.0 / 67.2 |
| pass2 | overload | 20.0 | 56.7 | real time | 7 | 100 | 99 | 35.0 | 171.4 | 0.7 / 1.2 / 52.5 | 0.7 / 1.2 / 52.5 |
| pass2 | minute_halved | 2.29 | 56.7 | real time | 7 | 40 | 39 | 24.0 | 100.0 | 0.7 / 1.6 / 433.2 | 0.7 / 1.6 / 433.2 |
| pass2 | minute_halved | 2.29 | 0.0 | real time | 7 | 40 | 39 | 24.0 | 100.0 | 0.8 / 1.2 / 474.5 | 0.8 / 1.2 / 474.5 |
| pass2 | minute_halved | 20.0 | 56.7 | real time | 7 | 40 | 39 | 24.0 | 100.0 | 0.7 / 1.3 / 445.2 | 0.7 / 1.3 / 445.2 |
| pass2 | minute_halved | 20.0 | 0.0 | real time | 7 | 40 | 39 | 24.0 | 100.0 | 0.8 / 1.3 / 465.4 | 0.8 / 1.3 / 465.4 |
| pass2 | daily_exhausted | 2.29 | 56.7 | real time | 7 | 10 | 0 | 5.0 | 120.0 | not measured / not measured / not measured | not measured / not measured / not measured |
| pass2 | daily_exhausted | 20.0 | 56.7 | real time | 7 | 10 | 0 | 5.0 | 120.0 | not measured / not measured / not measured | not measured / not measured / not measured |
| pass2_det | steady | 2.29 | 56.7 | 5x time-compressed | 7 | 100 | 99 | 4000.0 | 1.5 | 424.5 / 510.3 / 517.1 | 424.5 / 510.3 / 517.1 |
| pass2_det | steady | 20.0 | 56.7 | 5x time-compressed | 7 | 100 | 99 | 4000.0 | 1.5 | 476.0 / 590.4 / 598.9 | 476.0 / 590.4 / 598.9 |

## Table 2b: outcomes
| Pass | Scenario | Deadline s | Clock | latency_spread_ms | Full % (count) | Excerpt % (count) | Rate-limited % (count) | Timeout/given-up % (count) | Retries/request | Peak queue | Tokens/answer | Cost/1,000 answers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| pass1 | steady | 2.29 | real time | 56.7 | 33.0 (33/100) | 0.0 (0/100) | 34.0 (34/100) | 33.0 (33/100) | 0.00 | 1 | 3520 | $0.00 |
| pass1 | steady | 20.0 | real time | 56.7 | 24.0 (24/100) | 0.0 (0/100) | 35.0 (35/100) | 41.0 (41/100) | 0.00 | 1 | 3590 | $0.00 |
| pass1 | burst | 2.29 | real time | 56.7 | 1.0 (1/100) | 0.0 (0/100) | 98.0 (98/100) | 1.0 (1/100) | 0.00 | 14 | 2982 | $0.00 |
| pass1 | burst | 20.0 | real time | 56.7 | 1.0 (1/100) | 0.0 (0/100) | 98.0 (98/100) | 1.0 (1/100) | 0.00 | 13 | 2982 | $0.00 |
| pass1 | overload | 2.29 | real time | 56.7 | 1.0 (1/100) | 0.0 (0/100) | 98.0 (98/100) | 1.0 (1/100) | 0.00 | 1 | 2982 | $0.00 |
| pass1 | overload | 20.0 | real time | 56.7 | 1.0 (1/100) | 0.0 (0/100) | 98.0 (98/100) | 1.0 (1/100) | 0.00 | 1 | 2982 | $0.00 |
| pass1 | minute_halved | 2.29 | real time | 56.7 | 2.5 (1/40) | 0.0 (0/40) | 97.5 (39/40) | 0.0 (0/40) | 0.00 | 1 | 2982 | $0.00 |
| pass1 | minute_halved | 2.29 | real time | 0.0 | 2.5 (1/40) | 0.0 (0/40) | 97.5 (39/40) | 0.0 (0/40) | 0.00 | 1 | 2982 | $0.00 |
| pass1 | minute_halved | 20.0 | real time | 56.7 | 2.5 (1/40) | 0.0 (0/40) | 95.0 (38/40) | 2.5 (1/40) | 0.00 | 1 | 2982 | $0.00 |
| pass1 | minute_halved | 20.0 | real time | 0.0 | 2.5 (1/40) | 0.0 (0/40) | 95.0 (38/40) | 2.5 (1/40) | 0.00 | 1 | 2982 | $0.00 |
| pass1 | daily_exhausted | 2.29 | real time | 56.7 | 0.0 (0/10) | 0.0 (0/10) | 100.0 (10/10) | 0.0 (0/10) | 0.00 | 1 | not measured | not measured |
| pass1 | daily_exhausted | 20.0 | real time | 56.7 | 0.0 (0/10) | 0.0 (0/10) | 100.0 (10/10) | 0.0 (0/10) | 0.00 | 1 | not measured | not measured |
| pass2 | steady | 2.29 | 5x time-compressed | 56.7 | 37.0 (37/100) | 0.0 (0/100) | 25.0 (25/100) | 38.0 (38/100) | 0.00 | 1 | 3567 | $0.00 |
| pass2 | steady | 20.0 | 5x time-compressed | 56.7 | 26.0 (26/100) | 0.0 (0/100) | 30.0 (30/100) | 44.0 (44/100) | 0.00 | 1 | 3453 | $0.00 |
| pass2 | burst | 2.29 | real time | 56.7 | 1.0 (1/100) | 98.0 (98/100) | 0.0 (0/100) | 1.0 (1/100) | 0.00 | 13 | 30 | $0.00 |
| pass2 | burst | 20.0 | real time | 56.7 | 1.0 (1/100) | 98.0 (98/100) | 0.0 (0/100) | 1.0 (1/100) | 0.00 | 13 | 30 | $0.00 |
| pass2 | overload | 2.29 | real time | 56.7 | 1.0 (1/100) | 98.0 (98/100) | 0.0 (0/100) | 1.0 (1/100) | 0.00 | 1 | 30 | $0.00 |
| pass2 | overload | 20.0 | real time | 56.7 | 1.0 (1/100) | 98.0 (98/100) | 0.0 (0/100) | 1.0 (1/100) | 0.00 | 1 | 30 | $0.00 |
| pass2 | minute_halved | 2.29 | real time | 56.7 | 2.5 (1/40) | 95.0 (38/40) | 2.5 (1/40) | 0.0 (0/40) | 0.00 | 1 | 76 | $0.00 |
| pass2 | minute_halved | 2.29 | real time | 0.0 | 2.5 (1/40) | 95.0 (38/40) | 2.5 (1/40) | 0.0 (0/40) | 0.00 | 1 | 76 | $0.00 |
| pass2 | minute_halved | 20.0 | real time | 56.7 | 2.5 (1/40) | 95.0 (38/40) | 0.0 (0/40) | 2.5 (1/40) | 0.00 | 1 | 76 | $0.00 |
| pass2 | minute_halved | 20.0 | real time | 0.0 | 2.5 (1/40) | 95.0 (38/40) | 0.0 (0/40) | 2.5 (1/40) | 0.00 | 1 | 76 | $0.00 |
| pass2 | daily_exhausted | 2.29 | real time | 56.7 | 0.0 (0/10) | 0.0 (0/10) | 100.0 (10/10) | 0.0 (0/10) | 0.00 | 1 | not measured | not measured |
| pass2 | daily_exhausted | 20.0 | real time | 56.7 | 0.0 (0/10) | 0.0 (0/10) | 100.0 (10/10) | 0.0 (0/10) | 0.00 | 1 | not measured | not measured |
| pass2_det | steady | 2.29 | 5x time-compressed | 56.7 | 99.0 (99/100) | 0.0 (0/100) | 0.0 (0/100) | 1.0 (1/100) | 0.00 | 1 | 1778 | $0.00 |
| pass2_det | steady | 20.0 | 5x time-compressed | 56.7 | 99.0 (99/100) | 0.0 (0/100) | 0.0 (0/100) | 1.0 (1/100) | 0.00 | 1 | 1778 | $0.00 |

## Table 3: retrieval
| Setting | Questions | Correct and cited % | Wrong % | Correctly refused % | No answer % | Tokens/answer | First token p50/p95 ms | Total p50/p95 ms |
|---|---:|---:|---:|---:|---:|---:|---|---|
| small: 300 words, 3 pieces, re-rank no; deterministic; group final_v1 | 15 | 60.0 | 20.0 | 20.0 | 0.0 | 1528 | 563.0 / 764.1 | 563.0 / 764.1 |
| medium: 450 words, 4 pieces, re-rank no; deterministic; group final_v1 | 15 | 66.7 | 13.3 | 20.0 | 0.0 | 2653 | 734.0 / 1697.1 | 734.0 / 1697.1 |
| small: 300 words, 3 pieces, re-rank no; model; group final_v1_model | 4 | cost only | cost only | cost only | 75.0 | 3355 | 1302.4 / 1302.4 | 1302.4 / 1302.4 |
| small: 300 words, 3 pieces, re-rank no; model; group verifier_subset_attempt2 | 4 | cost only | cost only | cost only | 0.0 | 2830 | 1540.3 / 1969.8 | 1540.3 / 1969.8 |
| large: 700 words, 4 pieces, re-rank no; deterministic; group final_v1 | 15 | 66.7 | 13.3 | 20.0 | 0.0 | 3446 | 697.8 / 1066.5 | 697.8 / 1066.5 |
Model-verifier comparison: 4 questions, 8 saved rows across attempts. Tokens and latency are cost measurements, not a quality claim.

## Real-service limit headers
- retry-after: 2, 20
- x-ratelimit-limit-requests: 1000
- x-ratelimit-limit-tokens: 8000
- x-ratelimit-remaining-requests: 969, 970, 971, 972, 973, 974, 975, 976, 977, 978, 979, 980, 981, 982, 983, 984, 985, 986, 987, 988, 989, 990, 991, 992, 993, 994, 995, 996, 997, 998, 999
- x-ratelimit-remaining-tokens: 2755, 3023, 3166, 3244, 3307, 3325, 3329, 3330, 3393, 3577, 3648, 3770, 3893, 3949, 3963, 4119, 4128, 4301, 4331, 4418, 4473, 4484, 4534, 4567, 4604, 4683, 4731, 4773, 4783, 4829, 4867, 4897, 4917, 4968, 4971, 5037, 5085, 5149, 5161, 5168, 5243, 5253, 5326, 5336, 5340, 5357, 5385, 5405, 5427, 5458, 5489, 5874, 5985, 6205, 6235, 6251, 6291, 6330, 6339, 6353, 6377, 6385, 6421, 6442, 6463, 6466, 6494, 6686, 6705, 6718, 6796, 6913, 6959, 6972, 7973, 7979
- x-ratelimit-reset-requests: 10m4.8s, 11m31.2s, 12m57.599s, 14m24s, 15m50.4s, 17m16.8s, 18m43.2s, 1m26.4s, 20m9.6s, 21m36s, 23m2.4s, 24m28.8s, 25m55.199s, 27m21.6s, 28m48s, 2m52.8s, 30m14.4s, 31m40.8s, 33m7.2s, 34m33.6s, 36m0s, 37m26.4s, 38m52.8s, 40m19.2s, 41m45.6s, 43m12s, 44m38.4s, 4m19.2s, 5m45.6s, 7m12s, 8m38.4s
- x-ratelimit-reset-tokens: 11.295s, 11.505s, 11.527s, 11.685s, 11.842s, 12.112s, 12.172s, 12.352s, 12.457s, 12.524s, 12.817s, 13.117s, 13.237s, 13.462s, 15.84s, 15.944s, 157ms, 18.832s, 19.297s, 19.462s, 19.612s, 19.822s, 19.825s, 19.95s, 19.98s, 20.055s, 20.602s, 20.677s, 202ms, 21.24s, 21.382s, 21.862s, 22.222s, 22.717s, 22.945s, 23.122s, 23.272s, 23.497s, 23.782s, 24.127s, 24.877s, 25.457s, 25.47s, 25.747s, 25.995s, 26.452s, 26.588s, 26.865s, 27.171s, 27.517s, 27.742s, 29.04s, 29.425s, 29.631s, 30.802s, 31.971s, 32.64s, 33.017s, 34.431s, 34.552s, 35.025s, 35.062s, 35.67s, 37.448s, 38.348s, 39.265s, 40.722s, 41.134s, 44.408s, 7.71s, 7.807s, 8.152s, 9.03s, 9.615s, 9.712s, 9.855s

## Slow successful request
- 16044fb894-32: total 6809 ms; queue 0 ms; work 379 ms; retry 6430 ms; model first chunk not measured ms.
