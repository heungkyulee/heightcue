import json
from pathlib import Path
from unittest.mock import patch

import analytics
import generation_ssot
import publish
import sourcing


def _queue_product():
    return {
        "status": "done", "product_key": "kr-audited-1", "request_id": "req-1",
        "country": "KR", "product_name": "앞으로 여는 수납함", "category": "storage",
        "friction_id": "fr-kr-storage", "source_pointers": ["review:r1", "official:o1"],
        "scores": {name: value for name, value in {
            "friction_frequency": 4, "friction_intensity": 4, "mechanism_clarity": 5,
            "mobile_demo_clarity": 5, "consideration_cost": 1, "price_resistance": 1,
            "review_evidence_strength": 4, "failure_mode_severity": 2, "compliance_cost": 1,
            "expected_commission_value": 3, "attribution_readiness": 5,
        }.items()},
        "wrong_purchase_reversible": True, "mechanism": "front_open",
        "failure_mode": "weak_latch", "skip_if": "선반이 얕은 집",
        "price_info": "20,000원", "price_band": "KR_10_30K",
        "link": "https://example.test/kr-audited-1", "sub_id": "hc-fr-kr-storage",
        "audit_status": "approved", "audited_by": "haneul-proof",
    }


def test_kr_queue_selection_and_authoritative_resolution_share_exact_packet(tmp_path):
    state = tmp_path / "autopilot/state"
    queue = state / "browser-queue"
    queue.mkdir(parents=True)
    product = _queue_product()
    (queue / "results.json").write_text(json.dumps([product], ensure_ascii=False), encoding="utf-8")
    (queue / "requests.json").write_text("[]", encoding="utf-8")
    cfg = {"mode": {}, "paths": {"state_dir": str(state), "browser_queue": str(queue)}}

    with patch.object(sourcing, "is_audit_approved", return_value=True), \
         patch.object(sourcing, "score_candidate", return_value={"eligible": True}), \
         patch.object(sourcing, "_mark_sourced"):
        selected = sourcing.pick(cfg)

    input_id = selected["_generation_input_id"]
    assert input_id.startswith("queue_product:kr-audited-1:")
    with patch("companyos.get_product", side_effect=AssertionError("KR must not use Company OS")):
        resolved = generation_ssot.resolve_inputs(tmp_path, "sales_post", [input_id])
    assert resolved == [product]

    rows = json.loads((queue / "results.json").read_text(encoding="utf-8"))
    rows[0]["price_info"] = "99,000원"
    (queue / "results.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    try:
        generation_ssot.resolve_inputs(tmp_path, "sales_post", [input_id])
    except ValueError as exc:
        assert "digest" in str(exc).lower()
    else:
        raise AssertionError("mutated audited queue packet must fail closed")


def test_active_static_contract_scans_worker_for_retired_persona():
    text = Path(__file__).with_name("generation_worker.py").read_text(encoding="utf-8").lower()
    for token in ("167cm", "5'6", "26-year-old"):
        assert token not in text


def test_stage_aware_attribution_completeness():
    common = {"friction_id": "fr-1", "market": "KR", "source_pointers": ["review:r1"]}
    assert analytics.attribution_gaps({**common, "stage": "discovery"}) == []
    assert analytics.attribution_gaps({**common, "stage": "bridge", "mechanism": "front_open"}) == []
    verdict = {**common, "stage": "verdict", "mechanism": "front_open",
               "price_band": "KR_10_30K", "affiliate_destination": "https://example.test/p"}
    assert analytics.attribution_gaps(verdict) == []
    assert "mechanism" in analytics.attribution_gaps({**common, "stage": "bridge"})
    assert {"mechanism", "price_band", "affiliate_destination"}.issubset(
        analytics.attribution_gaps({**common, "stage": "verdict"}))


def test_collector_maps_attributable_route_to_affiliate_destination(tmp_path):
    cfg = {"paths": {"state_dir": str(tmp_path)}}
    published = {
        "media_id": "m1", "country": "KR", "text": "수납 불편",
        "meta": {"post_type": "sales", "friction_id": "fr-1", "stage": "verdict",
                 "market": "KR", "source_pointers": ["review:r1"], "mechanism": "front_open",
                 "price_band": "KR_10_30K", "attributable_route": "https://example.test/p"},
    }
    (tmp_path / "published.jsonl").write_text(json.dumps(published, ensure_ascii=False) + "\n", encoding="utf-8")
    with patch.object(publish, "fetch_link_clicks", return_value={}), \
         patch.object(publish, "fetch_insights", return_value={"views": 1}):
        assert analytics.collect(cfg) == 1
    row = json.loads((tmp_path / "metrics.jsonl").read_text(encoding="utf-8"))
    assert row["affiliate_destination"] == "https://example.test/p"
    assert row["attribution_complete"] is True


def test_verdict_publication_metadata_carries_price_and_route():
    product = generation_ssot.REHEARSAL_PRODUCTS["kr-front-open-storage"]
    cfg = {"mode": {"_rehearsal": True, "publish": False}}
    captured = {}
    def capture(*args, **kwargs):
        captured.update(kwargs["meta_extra"])
        return "PREVIEW-1", "published"
    with patch.object(sourcing, "pick", return_value=product), \
         patch("run.generate.make_master", return_value={}), \
         patch("run._publish_with_retry", side_effect=capture):
        import run
        run._kr_sales(cfg, "", False)
    assert captured["price_band"] == product["price_band"]
    assert captured["affiliate_destination"] == captured["attributable_route"]
    assert captured["affiliate_destination"].startswith("https://")


def test_fixture_products_require_explicit_rehearsal_and_publish_false():
    assert sourcing.pick({"mode": {"publish": False}}, dry_run=True) is None
    assert sourcing.pick_us({"mode": {"publish": False}}, dry_run=True) is None
    assert sourcing.pick({"mode": {"_rehearsal": True, "publish": True}}) is None
    assert sourcing.pick_us({"mode": {"_rehearsal": True, "publish": True}}) is None
    assert sourcing.pick({"mode": {"_rehearsal": True, "publish": False}})["rehearsal_fixture"] is True
    assert sourcing.pick_us({"mode": {"_rehearsal": True, "publish": False}})["rehearsal_fixture"] is True


def test_dry_run_fixture_cannot_enter_published_ledger(tmp_path):
    cfg = {"mode": {"publish": False}, "paths": {"state_dir": str(tmp_path)}}
    media = publish.publish_text(
        cfg, "KR", "한국어 테스트", dry_run=True,
        meta={"post_type": "value", "rehearsal_fixture": True})
    assert media is None
    assert not (tmp_path / "published.jsonl").exists()
