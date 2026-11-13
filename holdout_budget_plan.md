# Holdout budget plan (planning file, not submission write-up)

The first comparison uses 15 verified questions across all three retrieval settings. Small and medium are complete; large remains pending. Pick one setting after reviewing the comparison, then evaluate **10 new questions** with that setting and the deterministic verifier. Keep holdout questions out of retrieval tuning and setting selection. Draft three questions from each paper and one question that none of the three papers can answer. They must differ from every question in `questions_candidate.json`. The applicant verifies each expected answer and PDF page before the holdout is frozen. Do not run the holdout until then.

The 10 questions are budget slots in `questions_holdout_candidate.json`, not yet written or graded. Their token cost is an **estimate** from the current 15-question prompt sizes; exact preflight follows the frozen wording. The guard counts prompt/context tokens, the 400-token output allowance, and framing overhead before each call. Local minute admission keeps 15% headroom under 8,000 TPM and 7,000 ITPM.

| Chosen setting | 15-question comparison reservation | Estimated 10-question holdout | Original total plan, including 20,506-token verifier subset | High-side holdout preflight with 2 retry slots |
|---|---:|---:|---:|---:|
| Small: 300 words × 3 passages | 35,365 | 23,580 | 212,050 | 32,640 |
| Medium: 450 × 4 | 58,096 | 38,740 | 227,210 | 55,104 |
| Large: 700 × 4 | 74,503 | 49,670 | 238,140 | 73,668 |

The original total plan includes all three 15-question comparison settings (35,365 + 58,096 + 74,503 = 167,964 reserved tokens), the four-question model-verifier cost subset (20,506 reserved tokens), and **one** holdout row above. These are planning reservations, not today's remaining budget or measured usage. Holdout estimates use each setting's 15-question mean, rounded up to a whole token. The high-side preflight uses 12 times the largest current per-question reservation for that setting; new questions could exceed this, so recompute after freeze and let the daily guard refuse an oversized batch. A fresh IST day has a 120,000-token and 150-call project cap, also bounded by 85% of account TPD and RPD. Plan a separate day for the holdout if the selected setting or earlier calls would exceed that day's remaining room.

The model-verifier subset is T01, M02, H02, M04. It was chosen **after inspecting small and medium results** on 10 Nov 2026, superseding earlier U01 and H03 versions; this post-result choice must be disclosed. The 20,506-token figure was its pre-run reservation estimate. Both attempts are now saved; they measure extra calls, tokens, and latency only. Four post-selected questions cannot support a quality claim. Grade only real model answers; leave `answer_grade` blank for non-answers.
