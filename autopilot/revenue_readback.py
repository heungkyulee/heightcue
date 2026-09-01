#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read retailer dashboards through Aside and persist typed revenue observations.

The collector is intentionally fail-closed: an absent/partial/estimated read-back never
replaces the last measured snapshot.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone

from common import load_config, read_json, record_error, state_path, write_json

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
REQUIRED = (
    "market", "period", "clicks", "orders", "commission", "currency",
    "as_of", "dashboard_readback_timestamp", "source",
)

PROMPTS = {
    "KR": """읽기 전용 측정. 로그인된 Coupang Partners 대시보드에서 현재 월(MTD)의 실제 클릭 수, 구매 건수, 수익금을 읽어라. 설정 변경, 링크 생성, 광고 생성은 하지 마라. 추정하지 말고 화면에 보이는 값만 사용하라. 마지막 출력은 코드펜스 없이 정확히 JSON 객체 하나여야 한다: {\"market\":\"KR\",\"period\":\"month_to_date\",\"clicks\":정수,\"orders\":정수,\"commission\":숫자,\"currency\":\"KRW\",\"as_of\":\"YYYY-MM-DD\",\"dashboard_readback_timestamp\":\"ISO-8601 Asia/Seoul\",\"source\":\"aside:u0\"}. 값 하나라도 확인 못 하면 성공 JSON을 만들지 말고 오류만 반환하라.""",
    "US": """Read-only measurement. In the logged-in Amazon Associates dashboard, read the actual month-to-date clicks, ordered items, and commission/earnings. Do not change settings, create links, or estimate. The final output must be exactly one JSON object without a code fence: {\"market\":\"US\",\"period\":\"month_to_date\",\"clicks\":integer,\"orders\":integer,\"commission\":number,\"currency\":\"USD\",\"as_of\":\"YYYY-MM-DD\",\"dashboard_readback_timestamp\":\"ISO-8601 timestamp\",\"source\":\"aside:u0\"}. If any value cannot be observed, return an error instead of a success JSON.""",
}


def _balanced_objects(raw: str):
    spans = []
    stack = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in pairs:
            stack.append((char, index))
        elif char in ("}", "]") and stack:
            opening, start = stack[-1]
            if pairs[opening] == char:
                stack.pop()
                if not stack:
                    spans.append(raw[start:index + 1])
            else:
                stack.clear()
    return reversed(spans)


def _validate_observation(row, expected_market):
    if not isinstance(row, dict):
        raise ValueError("dashboard read-back must be a JSON object")
    missing = [key for key in REQUIRED if key not in row]
    if missing:
        raise ValueError("dashboard read-back missing: " + ", ".join(missing))
    if row["market"] != expected_market:
        raise ValueError("dashboard market mismatch")
    if row["period"] != "month_to_date":
        raise ValueError("dashboard period must be month_to_date")
    if row["source"] != "aside:u0":
        raise ValueError("dashboard source must be aside:u0")
    expected_currency = "KRW" if expected_market == "KR" else "USD"
    if row["currency"] != expected_currency:
        raise ValueError("dashboard currency mismatch")
    for key in ("clicks", "orders"):
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"dashboard {key} must be a non-negative integer")
    commission = row["commission"]
    if isinstance(commission, bool) or not isinstance(commission, (int, float)) or commission < 0:
        raise ValueError("dashboard commission must be a non-negative number")
    if not isinstance(row["as_of"], str) or not row["as_of"].strip():
        raise ValueError("dashboard as_of missing")
    if not isinstance(row["dashboard_readback_timestamp"], str) or not row["dashboard_readback_timestamp"].strip():
        raise ValueError("dashboard timestamp missing")
    return row


def extract_readback(raw, expected_market):
    clean = ANSI_RE.sub("", str(raw or ""))
    candidates = []
    try:
        candidates.append(json.loads(clean.strip()))
    except (json.JSONDecodeError, TypeError):
        pass
    for blob in _balanced_objects(clean):
        try:
            candidates.append(json.loads(blob))
        except json.JSONDecodeError:
            continue
    for candidate in candidates:
        if isinstance(candidate, list):
            candidates.extend(item for item in candidate if isinstance(item, dict))
            continue
        try:
            return _validate_observation(candidate, expected_market)
        except ValueError:
            continue
    raise ValueError(f"no valid {expected_market} dashboard read-back in Aside output")


def merge_readback(current, observation):
    merged = dict(current or {})
    markets = dict(merged.get("markets") or {})
    market = observation["market"]
    row = dict(observation)
    row["measurement_status"] = "measured"
    markets[market] = row
    merged["markets"] = markets
    merged["updated_at"] = observation["dashboard_readback_timestamp"]
    if market == "KR":
        merged["month_krw"] = observation["commission"]
        merged["month_clicks"] = observation["clicks"]
        merged["month_orders"] = observation["orders"]
    elif market == "US":
        merged["month_usd"] = observation["commission"]
        merged["us_month_clicks"] = observation["clicks"]
        merged["us_month_orders"] = observation["orders"]
    return merged


def merge_attribution(current, observation):
    merged = json.loads(json.dumps(current or {}))
    ledgers = merged.setdefault("retailer_measurement_and_ledger_status", {})
    market = observation["market"]
    key = "KR_Coupang_Partners" if market == "KR" else "US_Amazon_Associates"
    commission_key = "commission_krw" if market == "KR" else "commission_usd"
    ledgers[key] = {
        "measurement_status": "measured",
        "as_of": observation["as_of"],
        "dashboard_readback_timestamp": observation["dashboard_readback_timestamp"],
        "source": observation["source"],
        "clicks": observation["clicks"],
        "orders": observation["orders"],
        commission_key: observation["commission"],
    }
    return merged


def run_aside(market, account="u0", timeout=600):
    proc = subprocess.run(
        ["aside", "--account", account, "exec", PROMPTS[market]],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode:
        raise RuntimeError(f"Aside {market} read-back exit={proc.returncode}")
    return extract_readback(proc.stdout, market)


def collect(cfg, markets=("KR", "US"), account="u0", runner=run_aside):
    path = state_path(cfg, "revenue.json")
    attribution_path = state_path(cfg, "attribution_experiment_t_90875898.json")
    current = read_json(path, {})
    results = {}
    for market in markets:
        try:
            observation = runner(market, account=account)
            current = merge_readback(current, observation)
            write_json(path, current)
            attribution = read_json(attribution_path, None)
            if isinstance(attribution, dict):
                write_json(attribution_path, merge_attribution(attribution, observation))
            results[market] = {"status": "measured", "clicks": observation["clicks"],
                               "orders": observation["orders"], "commission": observation["commission"],
                               "currency": observation["currency"]}
        except Exception as exc:
            record_error(cfg, f"revenue_readback_{market.lower()}", exc)
            results[market] = {"status": "failed", "error": type(exc).__name__}
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aside retailer revenue read-back")
    parser.add_argument("--market", choices=("KR", "US", "all"), default="all")
    parser.add_argument("--account", default="u0")
    args = parser.parse_args(argv)
    markets = ("KR", "US") if args.market == "all" else (args.market,)
    results = collect(load_config(), markets=markets, account=args.account)
    print(json.dumps({"observed_at": datetime.now(timezone.utc).isoformat(),
                      "results": results}, ensure_ascii=False))
    return 1 if any(row["status"] == "failed" for row in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
