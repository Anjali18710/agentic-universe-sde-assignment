-- Interaction ledger schema (SQLite).
-- Rule of thumb: store FACTS at ingest time, derive REPORTS at report time.
-- Nothing here holds a name or phone number in plaintext.

-- One row per input file we have fully ingested. Doubles as the idempotency guard:
-- a file is ingested inside one transaction together with its row here, so a file is
-- either completely in or completely out, and re-ingesting it is a no-op.
CREATE TABLE IF NOT EXISTS ingested_files (
    file_id          INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
    workflow_id      TEXT NOT NULL DEFAULT '',   -- '' for event files
    file_name        TEXT NOT NULL,              -- e.g. batch_001.csv / events_0001.jsonl
    sha256           TEXT NOT NULL,
    source_path      TEXT NOT NULL,
    ingested_at      TEXT NOT NULL,
    -- lead-file counters
    lead_rows_total     INTEGER NOT NULL DEFAULT 0,
    lead_rows_rejected  INTEGER NOT NULL DEFAULT 0,
    duplicates_merged   INTEGER NOT NULL DEFAULT 0,
    field_warnings      INTEGER NOT NULL DEFAULT 0,
    -- event-file counters
    event_lines_total   INTEGER NOT NULL DEFAULT 0,
    malformed_events    INTEGER NOT NULL DEFAULT 0,
    duplicate_events    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, workflow_id, file_name)
);

-- One row per lead identity (workflow_id, canonical phone).
-- The phone is represented by a keyed hash (for matching) and is encrypted inside pii_enc.
CREATE TABLE IF NOT EXISTS leads (
    workflow_id   TEXT NOT NULL,
    phone_hash    TEXT NOT NULL,              -- HMAC-SHA256 of the canonical phone
    batch_id      TEXT NOT NULL,              -- stem of the file that first created the lead; never changes
    external_ref  TEXT NOT NULL,              -- the schema field marked "reference": true
    attributes    TEXT NOT NULL,              -- JSON of the non-PII fields (after merge + defaults)
    pii_enc       BLOB NOT NULL,              -- encrypted JSON of the PII fields (name, phone)
    created_file  INTEGER NOT NULL REFERENCES ingested_files(file_id),
    updated_file  INTEGER NOT NULL REFERENCES ingested_files(file_id),
    PRIMARY KEY (workflow_id, phone_hash)
);

-- Rejected lead rows. Deliberately carries no PII, only where the row was and why.
CREATE TABLE IF NOT EXISTS rejections (
    workflow_id  TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    row_number   INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    file_id      INTEGER NOT NULL REFERENCES ingested_files(file_id),
    PRIMARY KEY (workflow_id, batch_id, row_number)
);

-- The interaction ledger: every well-formed, first-seen event, exactly as received.
-- Append-only (see triggers). Linking to leads is NOT stored here: it is computed at
-- report time, so an event that arrives before its lead links automatically later.
CREATE TABLE IF NOT EXISTS events (
    event_id          TEXT PRIMARY KEY,           -- dedupe key across all drops
    seq               INTEGER NOT NULL UNIQUE,    -- global arrival order
    file_id           INTEGER NOT NULL REFERENCES ingested_files(file_id),
    event_type        TEXT,
    workflow_id       TEXT,
    call_id           TEXT,
    occurred_at_utc   TEXT NOT NULL,              -- fixed-width UTC text, sortable
    phone_hash        TEXT,                       -- NULL when absent or not a valid mobile
    phone_enc         BLOB,                       -- encrypted raw phone string, kept for audit
    disposition_code  TEXT,                       -- payload.disposition_code, for disposition.* events
    payload           TEXT NOT NULL               -- payload JSON (no PII in it)
);

CREATE INDEX IF NOT EXISTS events_by_phone ON events (workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS events_by_call  ON events (workflow_id, call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;
