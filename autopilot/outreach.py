#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic external Threads outreach policy and append-only ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import journey_policy


_REQUIRED = (
    "market", "source_post_id", "source_post_url", "source_author",
    "source_text", "friction_id", "reply_text",
)
_MEDICAL = re.compile(
    r"(?:검사\s*수치|복용량|처방|진단|병력|혈액\s*검사|dosage|dose|diagnos|lab\s*result|medical\s*history)",
    re.IGNORECASE,
)
_COMMERCIAL = re.compile(
    r"(?:https?://|www\.|프로필\s*(?:링크|에서)|링크\s*인\s*바이오|link\s+in\s+bio|"
    r"#ad|제휴|파트너스|amazon|coupang|구매하세요|사세요|buy\s+this|shop\s+now)",
    re.IGNORECASE,
)
_UNTRUSTED_SOURCE = re.compile(
    r"(?:사주|시주|원국|운세|타로|점성술|궁합|astrology|horoscope|zodiac|birth\s*chart|psychic)",
    re.IGNORECASE,
)
_SELF = {"heightcue", "heightcue_us"}


class OutreachError(RuntimeError):
    pass


def _threads_post_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() in {"www.threads.com", "threads.com", "www.threads.net", "threads.net"}
        and "/post/" in parsed.path
    )


def validate_candidate(row: dict) -> dict:
    """Fail closed before any external reply reservation or publication."""
    reasons = []
    for field in _REQUIRED:
        if not str(row.get(field, "")).strip():
            reasons.append(f"missing_{field}")
    market = str(row.get("market", "")).upper()
    if market not in {"KR", "US"}:
        reasons.append("unsupported_market")
    if row.get("source_post_url") and not _threads_post_url(str(row["source_post_url"])):
        reasons.append("invalid_threads_post_url")
    author = str(row.get("source_author", "")).lstrip("@").lower()
    if author in _SELF:
        reasons.append("self_reply")
    source_text = str(row.get("source_text", ""))
    reply_text = str(row.get("reply_text", ""))
    if _MEDICAL.search(source_text) or _MEDICAL.search(reply_text):
        reasons.append("medical_or_personal_health_context")
    if _UNTRUSTED_SOURCE.search(source_text):
        reasons.append("unsupported_occult_claim_context")
    if _COMMERCIAL.search(reply_text):
        reasons.append("commercial_connection")
    if any(row.get(field) for field in ("product_key", "offer_id", "affiliate_url", "brand")):
        reasons.append("commercial_metadata")
    reasons.extend(f"reader_{reason}" for reason in journey_policy.caregiver_shaming_reasons(reply_text, market))
    reasons.extend(f"reader_{reason}" for reason in journey_policy.retired_persona_reasons(reply_text))
    return {"eligible": not reasons, "reasons": sorted(set(reasons))}


def _key(row: dict) -> str:
    source = f"{str(row.get('market', '')).upper()}:{row.get('source_post_id', '')}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reserve(ledger_path, row: dict, now: str) -> dict:
    """Atomically reserve one source post before the browser performs a write."""
    verdict = validate_candidate(row)
    if not verdict["eligible"]:
        raise OutreachError("ineligible:" + ",".join(verdict["reasons"]))
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    key = _key(row)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        existing = next((item for item in reversed(_rows(path)) if item.get("idempotency_key") == key), None)
        if existing:
            return {**existing, "reserved": False}
        record = {
            **row,
            "idempotency_key": key,
            "status": "reserved",
            "reserved_at": now,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {**record, "reserved": True}


def record_readback(ledger_path, idempotency_key: str, readback: dict, now: str) -> dict:
    """Append a verified transition only when the live reply exactly matches its reservation."""
    path = Path(ledger_path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        rows = _rows(path)
        history = [row for row in rows if row.get("idempotency_key") == idempotency_key]
        if not history:
            raise OutreachError("reservation_not_found")
        if history[-1].get("status") == "verified":
            return history[-1]
        reservation = next((row for row in history if row.get("status") == "reserved"), None)
        if not reservation:
            raise OutreachError("reservation_not_found")
        expected_author = "heightcue_us" if reservation.get("market") == "US" else "heightcue"
        reasons = []
        checks = {
            "reply_id": bool(str(readback.get("reply_id", "")).strip()),
            "reply_url": _threads_post_url(str(readback.get("reply_url", ""))),
            "reply_author": str(readback.get("reply_author", "")).lstrip("@").lower() == expected_author,
            "parent_source_post_id": str(readback.get("parent_source_post_id", "")) == str(reservation["source_post_id"]),
            "reply_text": str(readback.get("reply_text", "")) == str(reservation["reply_text"]),
        }
        reasons.extend(f"readback_{name}_mismatch" for name, ok in checks.items() if not ok)
        record = {
            "idempotency_key": idempotency_key,
            "market": reservation.get("market"),
            "friction_id": reservation.get("friction_id"),
            "friction_category": reservation.get("friction_category"),
            "mechanism_id": reservation.get("mechanism_id"),
            "reply_model": reservation.get("reply_model"),
            "source_post_id": reservation.get("source_post_id"),
            "source_post_url": reservation.get("source_post_url"),
            "status": "verified" if not reasons else "verification_pending",
            "verified_at": now if not reasons else None,
            "checked_at": now,
            "verification_reasons": reasons,
            "reply_id": readback.get("reply_id"),
            "reply_url": readback.get("reply_url"),
            "reply_author": readback.get("reply_author"),
            "parent_source_post_id": readback.get("parent_source_post_id"),
            "reply_text": readback.get("reply_text"),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record
