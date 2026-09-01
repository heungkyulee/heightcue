#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scheduled outreach orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from pathlib import Path

from common import load_config, state_path
import journey_policy
import outreach
import outreach_aside
import outreach_reply


class OutreachWorkerError(RuntimeError):
    pass


def _record_probe(path, market, queries, status, source_count=0, now="", error=None):
    """Persist read-only discovery provenance, including a genuine empty result."""
    record = {"observed_at": now, "market": market, "queries": list(queries),
              "query_count": len(queries), "source_count": source_count, "status": status}
    if error:
        record["error"] = error
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def run_once(
    cfg: dict,
    slot: int,
    ledger_path,
    now: str,
    discover_fn=outreach_aside.discover,
    generate_fn=outreach_reply.generate,
    publish_fn=outreach_aside.publish_and_verify,
):
    settings = cfg.get("outreach") or {}
    if not settings.get("enabled"):
        return {"verified": 0, "held": 0, "skipped": "outreach_disabled", "records": []}
    if not cfg.get("mode", {}).get("publish") or not settings.get("publish"):
        return {"verified": 0, "held": 0, "skipped": "outreach_publish_disabled", "records": []}
    query_count = int(settings.get("queries_per_market_per_run", 2))
    source_limit = int(settings.get("max_sources_per_query", 2))
    if query_count < 1 or source_limit < 1:
        raise OutreachWorkerError("outreach_limits_must_be_positive")

    result = {"verified": 0, "held": 0, "records": []}
    markets = tuple(settings.get("markets") or ("KR", "US"))
    probe_path = Path(ledger_path).with_name("outreach_probe.jsonl")
    for market in markets:
        if market not in journey_policy.OUTREACH_QUERY_PACKS:
            result["held"] += 1
            result["records"].append({"market": market, "status": "held", "reason": "unsupported_market"})
            continue
        pack = journey_policy.OUTREACH_QUERY_PACKS[market]
        start = (int(slot) * query_count) % len(pack)
        selected = [pack[(start + offset) % len(pack)] for offset in range(query_count)]
        query_category = {row["query"]: row["category"] for row in selected}
        try:
            sources = discover_fn(market, [row["query"] for row in selected], source_limit)
        except Exception as exc:
            _record_probe(probe_path, market, [row["query"] for row in selected], "error",
                          now=now, error=f"{type(exc).__name__}: {exc}")
            result["held"] += query_count
            result["records"].append({"market": market, "status": "held", "reason": f"discovery:{type(exc).__name__}"})
            continue
        _record_probe(probe_path, market, [row["query"] for row in selected], "ok",
                      source_count=len(sources), now=now)
        for source in sources:
            try:
                query = source.get("discovery_query")
                category = query_category.get(query)
                if not category:
                    raise OutreachWorkerError("source_query_not_selected")
                candidate = generate_fn(source, category, cfg)
                reservation = outreach.reserve(ledger_path, candidate, now)
                if not reservation.get("reserved"):
                    result["records"].append({"market": market, "status": "duplicate", "idempotency_key": reservation["idempotency_key"]})
                    continue
                verified = publish_fn(ledger_path, reservation, now=now)
                status = verified.get("status")
                if status == "verified":
                    result["verified"] += 1
                else:
                    result["held"] += 1
                result["records"].append({"market": market, "status": status, "idempotency_key": reservation["idempotency_key"]})
            except Exception as exc:
                result["held"] += 1
                result["records"].append({"market": market, "status": "held", "reason": type(exc).__name__})
    return result


def cli(argv=None, cfg_loader=load_config, run_fn=run_once, now_fn=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "run":
        print(json.dumps({"error": "usage: outreach_worker.py run SLOT"}))
        return 2
    try:
        slot = int(args[1])
    except ValueError:
        print(json.dumps({"error": "slot_must_be_integer"}))
        return 2
    cfg = cfg_loader()
    now = now_fn() if now_fn else datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    result = run_fn(cfg, slot=slot, ledger_path=state_path(cfg, "outreach.jsonl"), now=now)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not result.get("held") else 1


if __name__ == "__main__":
    raise SystemExit(cli())
