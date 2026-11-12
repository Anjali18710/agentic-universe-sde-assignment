"""Command line entry point.

    python -m solution ingest data/drop_1 [data/drop_2 ...]   # or: ingest data  (all drops)
    python -m solution report --out reports/

Options (both commands): --db DIR (default .ledger/), --schemas DIR.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .catalog import DEFAULT_SCHEMA_DIR, Catalog
from .crypto import Vault
from .db import connect
from .pipeline import expand_drops, ingest_drop
from .report import build_reports


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m solution")
    ap.add_argument("--db", type=Path, default=Path(".ledger"),
                    help="folder for ledger.db and the secret key (default: .ledger)")
    ap.add_argument("--schemas", type=Path, default=DEFAULT_SCHEMA_DIR)
    sub = ap.add_subparsers(dest="command", required=True)
    p_ing = sub.add_parser("ingest", help="ingest one or more drops, in the order given")
    p_ing.add_argument("drops", nargs="+", type=Path)
    p_rep = sub.add_parser("report", help="write the five report files")
    p_rep.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    catalog = Catalog(args.schemas)
    conn = connect(args.db)
    vault = Vault.load(args.db / "secret.key")
    started = time.perf_counter()
    try:
        if args.command == "ingest":
            for drop in expand_drops(args.drops):
                print(f"ingest {drop}")
                ingest_drop(conn, vault, catalog, drop)
        else:
            build_reports(conn, vault, catalog, args.out)
            print(f"reports written to {args.out}")
    finally:
        conn.close()
    print(f"done in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
