# -*- coding: utf-8 -*-
"""Retailer revenue read-back parser/merge regression tests."""
import json

import pytest

import revenue_readback


def test_extract_readback_ignores_ansi_and_progress_noise():
    payload = {
        "market": "KR",
        "period": "month_to_date",
        "clicks": 15,
        "orders": 0,
        "commission": 0,
        "currency": "KRW",
        "as_of": "2026-09-01",
        "dashboard_readback_timestamp": "2026-09-01T12:05:01+09:00",
        "source": "aside:u0",
    }
    raw = "\x1b[32m[tool] openTab\x1b[0m\nprogress {not-json}\n" + json.dumps(payload)
    assert revenue_readback.extract_readback(raw, "KR") == payload


def test_extract_readback_rejects_unmeasured_or_negative_values():
    base = {
        "market": "KR", "period": "month_to_date", "clicks": 15,
        "orders": 0, "commission": 0, "currency": "KRW",
        "as_of": "2026-09-01",
        "dashboard_readback_timestamp": "2026-09-01T12:05:01+09:00",
        "source": "aside:u0",
    }
    for mutation in (
        {"orders": -1},
        {"clicks": None},
        {"market": "US"},
        {"source": "estimated"},
    ):
        row = {**base, **mutation}
        with pytest.raises(ValueError):
            revenue_readback.extract_readback(json.dumps(row), "KR")


def test_merge_readback_preserves_other_market_and_projects_legacy_totals():
    current = {
        "month_krw": 0,
        "markets": {"US": {"measurement_status": "unmeasured"}},
    }
    observation = {
        "market": "KR", "period": "month_to_date", "clicks": 15,
        "orders": 0, "commission": 0, "currency": "KRW",
        "as_of": "2026-09-01",
        "dashboard_readback_timestamp": "2026-09-01T12:05:01+09:00",
        "source": "aside:u0",
    }
    merged = revenue_readback.merge_readback(current, observation)
    assert merged["markets"]["US"]["measurement_status"] == "unmeasured"
    assert merged["markets"]["KR"]["clicks"] == 15
    assert merged["markets"]["KR"]["measurement_status"] == "measured"
    assert merged["month_krw"] == 0
    assert merged["month_clicks"] == 15


def test_merge_attribution_projects_market_measurement():
    artifact = {
        "retailer_measurement_and_ledger_status": {
            "KR_Coupang_Partners": {"measurement_status": "unmeasured"},
            "US_Amazon_Associates": {"measurement_status": "unmeasured"},
        }
    }
    observation = {
        "market": "US", "period": "month_to_date", "clicks": 2,
        "orders": 0, "commission": 0, "currency": "USD",
        "as_of": "2026-09-01",
        "dashboard_readback_timestamp": "2026-09-01T12:35:50+09:00",
        "source": "aside:u0",
    }
    merged = revenue_readback.merge_attribution(artifact, observation)
    us = merged["retailer_measurement_and_ledger_status"]["US_Amazon_Associates"]
    assert us == {
        "measurement_status": "measured",
        "as_of": "2026-09-01",
        "dashboard_readback_timestamp": "2026-09-01T12:35:50+09:00",
        "source": "aside:u0",
        "clicks": 2,
        "orders": 0,
        "commission_usd": 0,
    }
    assert merged["retailer_measurement_and_ledger_status"]["KR_Coupang_Partners"]["measurement_status"] == "unmeasured"
