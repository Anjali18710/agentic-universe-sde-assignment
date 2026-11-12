# Submission

## Candidate
- **Name:** Anjali
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):**  6 hours

## How to run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

python -m solution ingest data/drop_1
python -m solution ingest data/drop_2
python -m solution report --out reports/

pytest
```
See `solution/README.md` for options (`--db`, `LEDGER_SECRET`) and a map of the code.

## Design

**Principle: store facts at ingest time, derive reports at report time.** Ingest only decides
what is true about an input line or row on its own: valid or rejected, malformed, duplicate.
Anything that depends on other data (is this event an orphan? which lead does an override
belong to? is a disposition known?) is decided when reports are built, from everything
ingested so far. This is what makes "an orphan after drop_1 counts once its lead arrives in
drop_2" work with no special backfill code. It also guarantees that drop-by-drop and
all-at-once ingestion produce identical reports.

**Schema (SQLite, `solution/schema.sql`)**

| Table | Holds | Key / constraints |
|---|---|---|
| `ingested_files` | One row per fully ingested file, with its SHA-256 and its counters (rows, rejections, merges, warnings, lines, malformed, duplicates) | `UNIQUE(kind, workflow_id, file_name)` |
| `leads` | One row per lead identity; non-PII fields as JSON, PII fields encrypted | `PK(workflow_id, phone_hash)`; `batch_id` set on insert and never updated |
| `rejections` | Where a rejected row was and why. No PII. | `PK(workflow_id, batch_id, row_number)` |
| `events` | **The interaction ledger**: every well-formed, first-seen event | `PK(event_id)` = dedupe across drops; triggers abort any `UPDATE`/`DELETE` |

- **Append-only ledger.** Overrides are new rows. Database triggers reject every update or
  delete on `events`, so append-only is enforced by the database, not just by convention.
- **Idempotency and crash safety.** Each file is ingested in one transaction together with
  its `ingested_files` row, so a file is either fully in or fully out. Re-ingesting a known
  file (same name, same hash) is skipped. The same name with different contents is refused
  loudly, because silently skipping it could lose data and silently re-processing it could
  double-count rows. Event IDs are a primary key, so a duplicate can never be stored twice.
- **PII at rest.** Fields marked `"pii": true` (name, phone) are stored only inside
  `pii_enc`, encrypted with Fernet (AES + HMAC). To match events to leads by phone without
  storing it, the canonical phone is turned into an **HMAC-SHA256 keyed hash**
  (`phone_hash`). A plain SHA-256 would be unsafe: there are only about 4×10⁹ Indian mobile
  numbers, so every one could be hashed and reversed in minutes. With a secret key, that
  can't be done. Event phones are stored the same way (hash for matching, encrypted raw value
  for audit). Both keys are derived from one secret (`LEDGER_SECRET` env var, or a generated
  key file). `phone_masked` is produced by decrypting at report time, so not even the last 4
  digits are stored in plaintext.
- **Incremental.** Ingesting drop 2 does not re-read drop 1's files: leads merge into the
  existing table and events append to the ledger. Reports are recomputed from the database
  each time (about 11s at large scale), which keeps them always correct with respect to
  "everything ingested so far".

```
CSV / JSONL ──ingest──▶ leads (merged, PII encrypted) ─┐
                        rejections                     ├─report──▶ link (phone_hash / call_id)
                        events (append-only ledger) ───┘           ▶ calls ▶ effective disposition
                        ingested_files (counters)                  ▶ buckets ▶ 5 files
```

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Orphan and unknown-disposition status computed at report time, not stored | Status changes when later drops add leads; the spec says reports reflect the final state | Storing a `linked` flag and re-linking on each drop: more code, and it can drift |
| An event whose `occurred_at` is missing or unparseable is counted as **malformed** | Without a time it can't be dated or ordered against overrides. None occur in the generated data. | Storing it and ignoring it in reports (it would then be counted nowhere) |
| A required non-phone field that is present but invalid rejects the row as `invalid:<field>` | Not reachable with the given schemas (no required date/decimal/enum), but such a row can't form a valid lead | Treating it as a warning |
| If two columns map to the same field, the first (leftmost) wins | Deterministic; the spec doesn't say | Last wins / reject the file |
| A lead folder with no matching schema is skipped with a warning on stderr | Don't fail a whole drop for one unknown workflow | Hard fail |
| Same file name with different contents → refuse with an error | Prevents both double counting and silent data loss | Treat it as a new file |
| A call belongs to the lead of its `call.initiated` event; overrides link via `(workflow_id, call_id)` | The spec defines a call by its `call.initiated` | Majority vote across the call's events |
| Multiple `call.initiated` for one call: the earliest gives the call time | Never seen in the data; deterministic | Latest |
| Exact timestamp ties between dispositions: later arrival wins | Deterministic | Lexical `event_id` order |
| An override with an unknown code is ignored, so the effective disposition falls back to the next override or the set | Follows "count it and ignore it" | Letting it blank the disposition |
| Events of a call with no `call.initiated` are linked (not orphans) but form no call | The spec separates "linked" from "call counted" | Counting them as orphans |
| Events with an unknown `event_type` are stored and linked but affect nothing | Not in the spec or the data | Malformed |
| Field warnings are also counted on duplicate rows | They are not rejected rows; the spec says count warnings for non-rejected rows | Only counting on the first row |
| Defaults are applied after each merge | Equivalent to "after merging": defaults are non-null, and nulls never overwrite | Applying defaults only at report time |
| Decimal commas are stripped without validating the grouping (`1,23,456` OK) | Indian grouping differs from Western; the spec only says commas are separators | Strict grouping validation |
| Decimal rounding is half-up (`10.005 → 10.01`) | Money convention | Banker's rounding |
| `connectivity_rate` formatted with Python `f"{x:.4f}"` | Standard formatting; exact ties at the 5th decimal are rare | `Decimal` half-up |
| `lead_report` ties on `external_ref` are broken by `batch_id`, then `phone_masked` | Deterministic output | — |
| `last_call_at` uses whole seconds | All inputs are whole seconds | Microseconds |
| Whitespace-only event lines are not counted in `events_total` | "Non-empty line" | Counting them as malformed |
| Duplicate `event_id` keeps the first-seen copy even if later content differs | The spec defines duplicates by `event_id` | Last wins |
| SQLite | Zero setup; single writer is enough for a batch pipeline | Postgres (see the production note) |

## Failure modes considered
- **Re-running ingest** (same drop, or overlapping drops): file-level skip by name + hash,
  plus the event primary key. Tested.
- **Crash mid-file:** one transaction per file, so the file is rolled back and re-ingested in
  full next time. No partial counters.
- **Events before their lead, overrides without a phone:** linking happens at report time,
  and overrides link through `call_id`. Tested.
- **Duplicate events within and across drops; truncated JSON lines:** counted and skipped.
  Tested.
- **Dirty values:** six phone formats, four date formats, currency prefixes, enum casing,
  renamed/re-ordered/extra columns, blank rows. All unit-tested.
- **Tampering with the ledger:** triggers block `UPDATE`/`DELETE`. Tested.
- **Plaintext PII leak:** a test scans the raw database bytes for phones and names.

**Not handled:**
- Changing or losing the secret key makes existing hashes and encrypted data unusable. There
  is no key rotation.
- No concurrent ingest processes. SQLite's `BEGIN IMMEDIATE` serialises them, but this is
  untested.
- Corrected re-uploads of a file under the same name are refused rather than reconciled.

## Testing
- `pytest`: 89 tests.
  - **Unit:** phone, date, decimal, enum, header and timestamp rules (including IST date
    vs UTC date near midnight), rejection order, warnings vs defaults, effective disposition
    (override beats a later set; latest override by time, not by arrival), linking (orphans,
    other workflow, overrides via `call_id`, unknown codes), and every bucket rule.
  - **End-to-end:** a hand-made two-drop dataset whose five reports were computed by hand
    from the spec and are asserted line by line. Plus: orphan counts after drop 1 alone;
    re-ingest and drop-by-drop equal all-at-once; changed-file refusal; no plaintext PII;
    append-only enforcement; generated seeds 42 and 7 with consistency checks (rejections
    add up, leads = rows − rejected − merged).
- **Extra verification (outside the repo):** a second, independent in-memory implementation
  of the spec agreed byte-for-byte with this pipeline on seeds 42, 7, 123, 2026 and 999 and on
  the large dataset. Two values in the assignment README (connectivity `0.5625` on
  2026-10-01, and `last_call_at` `2026-10-14T17:55:24+05:30` for `MOT/2026/100000`) match our
  seed-42 output exactly.
- **Not tested:** concurrent runs, Postgres, key rotation.

## Results

Seed 42, small: 2,057 lead rows → 1,892 leads, 108 rejected, 57 merged, 122 field warnings.
8,745 event lines: 8 malformed, 237 duplicates, 462 orphans, 18 unknown dispositions. Each
step takes about 0.2s.

**Performance stretch (seed 42, large: 143,990 lead rows, about 600k event lines), 2 vCPU
cloud VM:**

| Step | Time |
|---|---|
| ingest drop_1 | 24.7 s |
| ingest drop_2 | 30.9 s |
| report | 11.5 s |
| **total** | **about 67 s** (target < 120 s) |

On my Windows laptop (Python 3.12), small dataset: each ingest and report step took about 0.3 s, and pytest ran 89 tests in 7.7 s.

Surprises in the data:
- About 25% of leads are never called.
- Rejected rows still receive calls, which become orphans: a lead that "exists" for the
  client doesn't exist for us.
- Cross-drop duplicate events are common: 154 of 237 duplicates are in drop 2.
- Calls just before midnight IST fall on a different date in UTC, which matters for
  `daily_overview`.
- Some overrides land days after the call, in the next drop.

## Production note (100k leads/day)
- **Postgres** instead of SQLite. Same tables. Partition `events` by month. Unique index on
  `event_id`. Keep the hash and decrypt keys in a KMS, not on disk.
- **Scheduling:** run ingest per drop when files arrive (object-store event → queue → worker),
  using the same per-file transaction and file registry. Reports run on a schedule after
  ingest, or as SQL views / materialised tables refreshed incrementally per affected lead.
- **Failures:** a failed file stays unregistered and is retried automatically. Poison files go
  to a dead-letter folder with an alert. Counters per file make anomalies visible (e.g.
  rejection rate spikes).
- **Backfills / late data:** already handled by design, since linking is computed from the
  full ledger. At scale, recompute only the leads touched by new events instead of all of
  them.

## If I had another day
1. Move report aggregation into SQL, so the report step doesn't load all events into memory.
2. Only recompute leads affected by a new drop (store derived per-lead facts, invalidate on
   new events).
3. Key rotation for the PII secret (versioned keys, re-hash in the background).
4. Postgres backend behind the same interface, with a concurrency test.
5. A `status` command that prints per-file counters and the last ingested drop.
