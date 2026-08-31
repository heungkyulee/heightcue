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


def test_account_memory_rotates_friction_not_personality():
    records = [{"country": "KR", "text": "scene", "media_id": "m1", "meta": {"publish_status": "verified", "friction_id": "fr-1", "stage": "discovery", "mechanism": "front_open"}}]
    packet = digest.build_account_memory(records)["KR"]
    assert packet["recent_friction_ids"] == ["fr-1"]
    assert packet["funnel_bottleneck"] in {"orders", "clicks", "progression", "qualified_engagement", "insufficient_data"}
    assert "persona" not in str(packet).lower()
    assert "story" not in str(packet).lower()
