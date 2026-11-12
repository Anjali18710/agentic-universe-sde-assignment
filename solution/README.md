# Assignment A: Interaction Ledger & Reporting Pipeline

Python 3.11+, SQLite (standard library), and `cryptography` for PII encryption.

## Setup (from a clean clone)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

## Run

All commands run from the repository root.

```bash
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2
python -m solution report --out reports/
```

- `ingest` takes one or more drop folders and processes them in the order given.
  `python -m solution ingest data` ingests every drop under `data/` in name order.
  Re-ingesting a drop is a no-op.
- The database and secret key live in `.ledger/` by default. Use `--db <dir>` to change it.
  The folder gets its own `.gitignore`, so neither file can be committed by accident.
- PII secret: set `LEDGER_SECRET` in the environment. If it isn't set, a random key is
  created at `.ledger/secret.key` on first run. Keep the same secret for the whole life of a
  database: phone matching and decryption depend on it.
- To start over: `rm -rf .ledger reports`.

## Test

```bash
pytest
```

89 tests, about 3 seconds. They cover phone, date, decimal and timestamp rules; row
validation; effective disposition; linking; buckets; and end-to-end runs with hand-computed
expected reports, idempotency and incremental-vs-all-at-once.

## Code map

| File | Responsibility |
|---|---|
| `normalize.py` | Pure value rules: headers, phone, date, decimal, enum, timestamps |
| `catalog.py` | Loads lead schemas and the disposition catalogue |
| `schema.sql` | Database schema (ledger, leads, rejections, ingested files) |
| `db.py` | Connection, file-level idempotency guard |
| `crypto.py` | PII protection: keyed phone hash for matching, encryption for storage |
| `leads.py` | Lead CSVs: header mapping, validation, rejection, duplicate merge |
| `events.py` | Event JSONL into the append-only ledger: malformed and duplicate detection |
| `derive.py` | Pure derived facts: linking, orphans, calls, effective disposition, buckets |
| `report.py` | The five output files |
| `pipeline.py` | Walks drops in order; one transaction per file |
| `__main__.py` | Command line |
