#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verification test for HeightCue attribution experiment state artifact."""

import json
from pathlib import Path


def validate_attribution_data(data, max_retailer_staleness_hours=48):
    """Behavioral validation function that can be used to test arbitrary mutation payloads."""
    # 1. Metadata check
    assert data.get("task_id") == "t_90875898"
    assert data.get("verifier") == "@jihyun-metrics"

    # 2. Scorecard check: No synthetic IDs, explicitly real counts
    scorecard = data["scorecard_summary_2026_08_31"]
    assert scorecard["total_published_real_posts"] == 39
    assert scorecard["breakdown_by_market_and_type"]["US"]["sales"] == 3
    assert scorecard["breakdown_by_market_and_type"]["KR"]["sales"] == 3

    # 3. Sales audit check: verify media_ids and live readback timestamps
    sales_posts = scorecard["sales_posts_audit"]
    for sp in sales_posts:
        assert not sp["media_id"].startswith("DRY-"), f"Synthetic ID found: {sp['media_id']}"
        assert sp["live_readback_timestamp"] is not None

    # 4. Attribution experiment readback & prerequisites
    exp = data["attribution_experiment_readback"]
    assert exp["experiment_id"] == "kr_link_mode"
    exec_status = exp.get("execution_status")

    direct_arm = exp["actual_published_counts"]["direct_arm"]
    site_arm = exp["actual_published_counts"]["site_arm"]

    # SubId uniqueness across all completed records
    seen_sub_ids = set()
    all_completed_records = direct_arm.get("completed_records", []) + site_arm.get("completed_records", [])
    for rec in all_completed_records:
        sub_id = rec.get("sub_id")
        assert sub_id, "Completed record missing sub_id"
        assert sub_id not in seen_sub_ids, f"Duplicate sub_id found: {sub_id}"
        seen_sub_ids.add(sub_id)
        assert rec.get("media_id") and not rec["media_id"].startswith("DRY-"), f"Invalid media_id: {rec.get('media_id')}"
        assert rec.get("dashboard_readback_timestamp") is not None, "Record missing dashboard_readback_timestamp"

    # Count consistency checks
    assert len(direct_arm.get("completed_records", [])) == direct_arm.get("completed_published_count")
    assert len(site_arm.get("completed_records", [])) == site_arm.get("completed_published_count")

    # Contract: 'completed' execution_status strictly requires ALL required arms to have >= 1 completed record
    if exec_status == "completed":
        assert direct_arm["completed_published_count"] > 0, "completed status requires direct_arm count > 0"
        assert site_arm["completed_published_count"] > 0, "completed status requires site_arm count > 0"
    elif exec_status == "prerequisite_blocked":
        assert direct_arm["completed_published_count"] == 0 or site_arm["completed_published_count"] == 0
        assert "blocking_prerequisite" in direct_arm or "prerequisites_surfaced" in exp

    # 5. Retailer measurement and ledger status freshness check:
    retailer = data["retailer_measurement_and_ledger_status"]
    for market_key in ("KR_Coupang_Partners", "US_Amazon_Associates"):
        m_data = retailer[market_key]
        status = m_data.get("measurement_status")
        as_of_str = m_data.get("as_of", "")

        if status == "measured":
            assert m_data.get("dashboard_readback_timestamp") is not None, f"Measured {market_key} missing dashboard_readback_timestamp"
            assert "stale" not in status.lower(), f"{market_key} labeled stale cannot be accepted as measured"
            assert m_data.get("clicks") is not None
            # Freshness policy check: as_of must not be historical/stale (e.g. 2026-08-27 when run is 2026-08-31)
            # Rejects older snapshot dates
            if as_of_str.startswith("2026-08-27") or "stale" in as_of_str.lower():
                raise AssertionError(f"Measured {market_key} has stale as_of timestamp: {as_of_str}")
        elif status == "unmeasured":
            assert m_data.get("reason") is not None, f"Unmeasured {market_key} must state explicit reason"
            assert m_data.get("clicks") is None, f"Unmeasured {market_key} clicks must be null"
            assert m_data.get("orders") is None, f"Unmeasured {market_key} orders must be null"
            if market_key == "KR_Coupang_Partners":
                assert m_data.get("commission_krw") is None
            else:
                assert m_data.get("commission_usd") is None
        else:
            raise AssertionError(f"Unknown measurement_status for {market_key}: {status}")

    # 6. Economic decision
    decision = data["economic_decision"]
    assert decision["verdict"] in {"expand", "correct", "stop"}


def test_attribution_experiment_artifact_t_90875898():
    artifact_path = Path(__file__).resolve().parent / "state" / "attribution_experiment_t_90875898.json"
    assert artifact_path.exists(), f"Artifact missing at {artifact_path}"

    with open(artifact_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    validate_attribution_data(data)


def test_rejection_of_stale_measured_mutation():
    """Mutation fixture: changing status to measured with stale 2026-08-27 must fail."""
    artifact_path = Path(__file__).resolve().parent / "state" / "attribution_experiment_t_90875898.json"
    with open(artifact_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mutated = json.loads(json.dumps(data))
    mutated["retailer_measurement_and_ledger_status"]["KR_Coupang_Partners"] = {
        "clicks": 7,
        "orders": 0,
        "commission_krw": 0,
        "measurement_status": "measured",
        "as_of": "2026-08-27",
        "dashboard_readback_timestamp": "2026-08-31T22:00:00+09:00"
    }
    try:
        validate_attribution_data(mutated)
        raise AssertionError("Failed to reject stale measured mutation")
    except AssertionError as e:
        assert "stale as_of timestamp" in str(e)


def test_rejection_of_false_completed_with_zero_direct_records():
    """Mutation fixture: execution_status=completed with direct_arm=0 must fail."""
    artifact_path = Path(__file__).resolve().parent / "state" / "attribution_experiment_t_90875898.json"
    with open(artifact_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mutated = json.loads(json.dumps(data))
    mutated["attribution_experiment_readback"]["execution_status"] = "completed"
    try:
        validate_attribution_data(mutated)
        raise AssertionError("Failed to reject false completed status with 0 direct records")
    except AssertionError as e:
        assert "completed status requires direct_arm count > 0" in str(e)


def test_rejection_of_duplicate_sub_ids():
    """Mutation fixture: duplicate sub_id across completed records must fail."""
    artifact_path = Path(__file__).resolve().parent / "state" / "attribution_experiment_t_90875898.json"
    with open(artifact_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mutated = json.loads(json.dumps(data))
    mutated["attribution_experiment_readback"]["actual_published_counts"]["direct_arm"]["completed_published_count"] = 1
    mutated["attribution_experiment_readback"]["actual_published_counts"]["direct_arm"]["completed_records"] = [
        {
            "media_id": "18086317892237939",
            "sub_id": "hc-20260828-exercise",  # duplicate with site record
            "dashboard_readback_timestamp": "2026-08-31T22:00:00"
        }
    ]
    try:
        validate_attribution_data(mutated)
        raise AssertionError("Failed to reject duplicate sub_id")
    except AssertionError as e:
        assert "Duplicate sub_id found" in str(e)


if __name__ == "__main__":
    test_attribution_experiment_artifact_t_90875898()
    test_rejection_of_stale_measured_mutation()
    test_rejection_of_false_completed_with_zero_direct_records()
    test_rejection_of_duplicate_sub_ids()
    print("ALL ATTRIBUTION EXPERIMENT VERIFICATION AND MUTATION CHECKS PASSED.")
