# AI Usage Disclosure

## Tools used
Claude (Anthropic), via the Claude app.

## What I used them for
- Reading and summarising the assignment, and comparing A against B before I chose.
- Writing the first draft of the whole solution: the schema, ingestion, the derivation rules,
  the five reports, the CLI and the tests.
- Building a separate in-memory reference implementation, used only to cross-check the
  pipeline's output on five seeds and on the large dataset.
- Drafting `SUBMISSION.md`.

## What I wrote or decided myself
[ANJALI: write this in your own words after the walkthrough. Be specific and only list what
is true. For example: choosing assignment A, the decisions you reviewed and agree with (or
changed), anything you re-wrote, which tests you added, and the numbers you re-ran yourself.]

## Where the AI was wrong or unhelpful
- In the end-to-end test, the AI's hand-computed expected `events_total` was 15. The real
  count is 16: it missed the duplicate line. The test failed, and recounting the fixture line
  by line confirmed 16 was correct.
- [ANJALI: add anything you found yourself while reviewing.]

## How I verified AI-generated code
- Ran `pytest` (89 tests) and the full ingest/report commands on the small and large data.
- Compared the pipeline's output against an independent reference implementation on 5 seeds
  and on the large dataset (identical), and against two values printed in the assignment
  README (they match).
- Checked by hand that re-ingesting changes nothing and that the database file contains no
  plaintext names or phones.
- [ANJALI: add how you read and reviewed the code yourself.]
