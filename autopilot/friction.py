"""Inspectable friction-demand ledger for child-household commerce."""
from __future__ import annotations

import json
from pathlib import Path

LIFECYCLES = {"candidate", "validated", "active", "retired"}
SOURCE_TYPES = {"internal_engagement", "external_complaint", "marketplace_reviews", "conversion_pattern"}
REQUIRED = ("friction_id", "market", "domain", "source_type", "source_pointer", "verbatim",
            "recurrence", "intensity", "mechanisms", "lifecycle")


def validate_signal(record):
    row = dict(record or {})
    missing = [key for key in REQUIRED if row.get(key) in (None, "", [])]
    if missing:
        raise ValueError("missing friction fields: " + ",".join(missing))
    if row["source_type"] not in SOURCE_TYPES:
        raise ValueError("unapproved friction source")
    if row["lifecycle"] not in LIFECYCLES:
        raise ValueError("invalid lifecycle")
    if not isinstance(row["mechanisms"], list):
        raise ValueError("mechanisms must be a list")
    for key in ("recurrence", "intensity"):
        if isinstance(row[key], bool) or not isinstance(row[key], int) or row[key] < 1 or row[key] > 5:
            raise ValueError(f"{key} must be 1..5")
    return row


def load_signals(path):
    target = Path(path)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(validate_signal(json.loads(line)))
    return rows


def append_signal(path, record):
    row = validate_signal(record)
    target = Path(path)
    existing = load_signals(target)
    if any(item["friction_id"] == row["friction_id"] for item in existing):
        raise ValueError("duplicate friction_id")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def pick_signal(path, market):
    eligible = [row for row in load_signals(path)
                if row["market"] == market and row["lifecycle"] in {"validated", "active"}]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row["recurrence"], row["intensity"], row["friction_id"]))
