import json

import analytics
import digest


def metric(**overrides):
    base = {"media_id": "m1", "friction_id": "fr-1", "stage": "verdict", "mechanism": "front_open", "product_id": "p1", "price_band": "KR_20_40K", "hook_family": "scene", "affiliate_destination": "coupang", "views": 100, "qualified_engagement": 2, "progression": 1, "clicks": 1, "orders": 0, "commission": 0}
    base.update(overrides)
    return base


def test_friction_summary_tracks_dimensions_and_revenue_winner():
    rows = [metric(media_id="viral", friction_id="fr-viral", views=999999, clicks=0), metric(media_id="sale", friction_id="fr-sale", views=10, clicks=1, orders=1, commission=4.2)]
    summary = analytics.friction_summary(rows)
    assert summary["revenue_winner"]["friction_id"] == "fr-sale"
    assert "friction_id" in summary["dimensions"]
    assert summary["observed"]["rows"] == 2
    assert "hypotheses" in summary


def test_commission_per_1000_requires_verified_impression_provenance_commission_and_minimum_sample():
    rows = [{
        "impressions": 4000, "impressions_provenance": "threads_insights:post-1",
        "commission": 20.0, "commission_status": "verified",
    }]
    result = analytics.commission_per_1000_verified_impressions(rows, minimum_impressions=3000)
    assert result == {"value": 5.0, "verified_impressions": 4000, "verified_commission": 20.0, "eligible": True}
    assert analytics.commission_per_1000_verified_impressions(rows, minimum_impressions=5000)["value"] is None
    unknown = [{**rows[0], "commission": None}]
    result = analytics.commission_per_1000_verified_impressions(unknown, minimum_impressions=3000)
    assert result["value"] is None and result["eligible"] is False


def test_outreach_summary_counts_only_verified_readback_and_keeps_profile_change_account_level():
    ledger = [
        {"idempotency_key": "kr-1", "status": "reserved", "market": "KR", "friction_category": "sleep_morning", "source_author": "parent_one"},
        {"idempotency_key": "kr-1", "status": "verified", "market": "KR", "reply_id": "reply-1"},
        {"idempotency_key": "us-1", "status": "reserved", "market": "US", "friction_category": "storage_space", "source_author": "parent_two"},
        {"idempotency_key": "us-1", "status": "verification_pending", "market": "US"},
    ]
    profiles = [{"market": "KR", "profile_visits": 7, "follows": 1, "provenance": "threads_insights:account:2026-08-31"}]
    summary = analytics.outreach_summary(ledger, profile_observations=profiles)
    assert summary["verified_replies"] == 1
    assert summary["by_market"] == {"KR": 1}
    assert summary["by_category"] == {"sleep_morning": 1}
    assert summary["account_level_observations"] == profiles
    assert summary["causal_reply_to_profile_attribution"] == "not_observed"


def test_weekly_summary_exposes_normalized_efficiency_and_outreach_without_reply_level_causality(tmp_path):
    cfg = {"paths": {"state_dir": str(tmp_path)}}
    metrics = {
        **metric(), "insights": {"views": 4000}, "impressions": 4000,
        "impressions_provenance": "threads_insights:m1", "commission": 20.0,
        "commission_status": "verified", "source_pointers": ["src"], "market": "KR",
    }
    (tmp_path / "metrics.jsonl").write_text(json.dumps(metrics, ensure_ascii=False) + "\n", encoding="utf-8")
    outreach_rows = [
        {"idempotency_key": "k", "status": "reserved", "market": "KR", "friction_category": "storage_space"},
        {"idempotency_key": "k", "status": "verified", "market": "KR", "reply_id": "r"},
    ]
    (tmp_path / "outreach.jsonl").write_text("".join(json.dumps(row) + "\n" for row in outreach_rows), encoding="utf-8")
    profile = {"market": "KR", "profile_visits": 3, "provenance": "threads_insights:account"}
    (tmp_path / "profile_metrics.jsonl").write_text(json.dumps(profile) + "\n", encoding="utf-8")
    summary = analytics.weekly_summary(cfg)
    assert summary["normalized_efficiency"]["value"] == 5.0
    assert summary["outreach"]["verified_replies"] == 1
    assert summary["outreach"]["causal_reply_to_profile_attribution"] == "not_observed"


def test_account_memory_rotates_friction_not_personality():
    records = [{"country": "KR", "text": "scene", "media_id": "m1", "meta": {"publish_status": "verified", "friction_id": "fr-1", "stage": "discovery", "mechanism": "front_open"}}]
    packet = digest.build_account_memory(records)["KR"]
    assert packet["recent_friction_ids"] == ["fr-1"]
    assert packet["funnel_bottleneck"] in {"orders", "clicks", "progression", "qualified_engagement", "insufficient_data"}
    assert "persona" not in str(packet).lower()
    assert "story" not in str(packet).lower()
