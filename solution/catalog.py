"""Loads the per-workflow lead schemas and the disposition catalogue."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalise_header

DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "assignment-a-data" / "schemas"


@dataclass
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    values: list[str] = field(default_factory=list)
    default: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class LeadSchema:
    workflow_id: str
    fields: list[Field]  # in schema order: rejection reasons depend on this order

    @property
    def reference_field(self) -> str:
        return next(f.name for f in self.fields if f.reference)

    def header_lookup(self) -> dict[str, str]:
        """normalised header text -> field name (field name and every alias)."""
        lookup = {}
        for f in self.fields:
            for h in [f.name, *f.aliases]:
                lookup.setdefault(normalise_header(h), f.name)
        return lookup


@dataclass(frozen=True)
class Disposition:
    is_terminal: bool
    is_success: bool


class Catalog:
    def __init__(self, schema_dir: Path = DEFAULT_SCHEMA_DIR):
        self.schemas: dict[str, LeadSchema] = {}
        for p in sorted(schema_dir.glob("*.json")):
            raw = json.loads(p.read_text(encoding="utf-8"))
            fields = [Field(**{k: v for k, v in f.items() if k in Field.__dataclass_fields__})
                      for f in raw["fields"]]
            self.schemas[raw["workflow_id"]] = LeadSchema(raw["workflow_id"], fields)

        # (workflow_id, code) -> Disposition
        self.dispositions: dict[tuple[str, str], Disposition] = {}
        with open(schema_dir / "dispositions.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.dispositions[(row["workflow_id"].strip(), row["code"].strip())] = Disposition(
                    is_terminal=row["is_terminal"].strip().lower() == "true",
                    is_success=row["is_success"].strip().lower() == "true",
                )
