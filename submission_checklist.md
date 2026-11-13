# Submission status

Deadline: Friday 13 November 2026, 4:11 PM IST.

## Ready

- The main async pipeline, local stand-in, guard, retrieval, load generator, and raw attempt logging are implemented.
- Small, medium, and large real comparisons cover the same 15 frozen questions. Their 45 saved answers are graded in `results.csv` and `grades.csv`; non-answers remain ungraded.
- Real probes, a four-question model-verifier cost comparison, ten real steady requests, and stand-in load/retry runs are saved.
- Required tables and limitations are in the two-page `writeup.pdf`.
- The exact user prompts from this chat are in `prompts_from_chat.md`.
- The clean upload folder is `C:\Users\LOQ\TheRelay_submission`. It includes code, papers, data, raw evidence, and the PDF. Its copied files match the tested package by hash.
- Package checks: 42 tests passed; Ruff lint and format checks passed; table generation and report validation passed; no key-like strings or sensitive config files were found.

## Remaining

1. Read and approve the first-person reflection, Vaani feedback, and assistant-aided grades. These are drafted from saved evidence; they should reflect your own view.
2. The three-setting requirement is now met. A new-question holdout was planned but is not required by the brief. Inspect the large run's H02 and H03 grades before defending its quality figure.
3. Upload the clean folder to Google Drive, set it to **Anyone with the link can view**, paste that folder link into the submission form using your application email, and check for the confirmation message.

The final large-setting run used 15 Groq calls on 13 November 2026.
