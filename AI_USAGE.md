# AI Usage Disclosure

## Tools used
Claude (Anthropic).

## What I used them for
I used Claude as a pair-programmer: to analyse both assignments, draft the implementation against the spec, generate tests, and build an independent reference implementation used only to cross-check outputs. I treated its output as a draft to verify, not as an answer.

## What I wrote or decided myself
- **Choice of assignment:** picked A over B because its output is deterministic and verifiable within the time box, and it matches my background in data-heavy projects.
- **Engineering constraints I set:** follow the spec exactly; keep the original files unmodified; document every judgement call in SUBMISSION.md rather than guessing silently; keep the submission free of data, databases and secrets.
- **Verification on my own machine:** set up from a clean clone on Windows, ran the full pipeline and the test suite (89 passed), and confirmed my seed-42 reports matched the reference run exactly.
- **Debugging the environment:** diagnosed and fixed the setup failures described below.
- **Ownership:** I have reviewed the design (schema, PII approach, report-time linking, idempotency) and can explain and modify any part of it.

## Where the AI was wrong or unhelpful
- **Windows setup:** its instructions failed on my machine. PowerShell needed `.\` before the activate script. My default `python` turned out to be a MinGW build, where `pip install cryptography` fails because there is no prebuilt wheel and it needs Rust.I read the error output, identified the platform mismatch, and rebuilt the venv on standard CPython 3.12 (`py -3.12 -m venv .venv`).
- **Test expectation:** in one end-to-end test, the AI's hand-computed `events_total` was 15. The test failed, and recounting the fixture line by line showed the correct value was 16 (it had missed the duplicate line).

## How I verified AI-generated code
- 89 automated tests: unit tests for every normalisation rule, plus end-to-end tests with hand-computed expected reports.
- Cross-checked the pipeline's output against an independent reference implementation on 5 seeds and the large dataset: identical output.
- Checked results against values printed in the assignment README (they match).
- Re-ran everything on my own laptop and compared the reports with the reference run.
- Confirmed by test that the database contains no plaintext names or phones, and that the ledger rejects updates and deletes.