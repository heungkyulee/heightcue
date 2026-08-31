import json
from unittest.mock import patch

import analytics
import companyos
import generation_ssot
import generation_worker
import run
import sourcing
from test_supabase_products import FakeTransport


def _queue_product_without_request_defaults():
    from test_fix_round3 import _queue_product
    product = _queue_product()
    product.pop("country", None)
    product.pop("formfactor_id", None)
    product.pop("ux_grade", None)
    return product


def test_kr_queue_request_defaults_are_part_of_shared_canonical_identity(tmp_path):
    state = tmp_path / "autopilot/state"
    queue = state / "browser-queue"
    queue.mkdir(parents=True)
    product = _queue_product_without_request_defaults()
    request = {"id": "req-1", "status": "filled", "formfactor_id": "front-open",
               "ux_grade": "observed"}
    (queue / "results.json").write_text(json.dumps([product], ensure_ascii=False), encoding="utf-8")
    (queue / "requests.json").write_text(json.dumps([request], ensure_ascii=False), encoding="utf-8")
    cfg = {"mode": {}, "paths": {"state_dir": str(state), "browser_queue": str(queue)}}

    with patch.object(sourcing, "is_audit_approved", return_value=True), \
         patch.object(sourcing, "score_candidate", return_value={"eligible": True}), \
         patch.object(sourcing, "_mark_sourced"):
        selected = sourcing.pick(cfg)

    assert selected["country"] == "KR"
    assert selected["formfactor_id"] == "front-open"
    assert selected["ux_grade"] == "observed"
    resolved = generation_ssot.resolve_inputs(
        tmp_path, "sales_post", [selected["_generation_input_id"]])
    assert resolved == [{**product, "country": "KR", "formfactor_id": "front-open",
                         "ux_grade": "observed"}]

    rows = json.loads((queue / "results.json").read_text(encoding="utf-8"))
    rows[0]["price_info"] = "99,000원"
    (queue / "results.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    try:
        generation_ssot.resolve_inputs(tmp_path, "sales_post", [selected["_generation_input_id"]])
    except ValueError as exc:
        assert "digest" in str(exc).lower()
    else:
        raise AssertionError("post-selection packet mutation must fail closed")


def _companyos_us_claim():
    return {
        "product_key": "us-priced-product", "product_name": "Priced Product",
        "category": "storage", "friction_id": "fr-us-storage",
        "source_pointers": ["companyos:evidence:3"], "mechanism": "front_open",
        "failure_mode": "weak_latch", "skip_if": "your shelf is too shallow",
        "evidence_revision": 3, "product_id": "p1", "offer_id": "o1",
        "workflow_id": "w1", "claim_token": "claim-1",
        "landing_url": "https://heightcue.test/us/priced", "sub_id": "priced",
        "price_info": {"amount": 19.99, "currency": "USD"},
    }


def test_us_companyos_usd_price_reaches_verdict_publication_and_analytics(tmp_path):
    product = companyos.claim_us_product(
        {"mode": {}}, owner="test-owner", transport=FakeTransport([_companyos_us_claim()]))
    assert product["price_info"] == {"amount": 19.99, "currency": "USD"}
    assert product["price_band"] == "US_15_30"

    verdict = generation_worker.bind_friction_contract(
        "sales_post", "US", [product], {"text": "#ad priced product"})
    assert verdict["price_band"] == "US_15_30"

    captured = {}
    cfg = {"mode": {"publish": False}, "paths": {"state_dir": str(tmp_path)}}
    with patch.object(run.sourcing, "pick_us", return_value=product), \
         patch.object(run.generate, "make_master", return_value={}), \
         patch.object(run.generate, "make_sales_post", return_value=verdict), \
         patch.object(run, "_publish_with_retry",
                      side_effect=lambda *_a, **kw: (captured.update(kw["meta_extra"]) or "PREVIEW-1", "published")), \
         patch.object(companyos, "release_product_claim", return_value={"ok": True}):
        run._us_sales(cfg, "", False)
    assert captured["price_band"] == "US_15_30"
    metric = {"friction_id": product["friction_id"], "stage": "verdict", "market": "US",
              "source_pointers": product["source_pointers"], "mechanism": product["mechanism"],
              "price_band": captured["price_band"],
              "affiliate_destination": captured["affiliate_destination"]}
    assert analytics.attribution_gaps(metric) == []


def test_verdict_binding_rejects_empty_price_band():
    product = _companyos_us_claim()
    product["link"] = product["landing_url"]
    try:
        generation_worker.bind_friction_contract(
            "sales_post", "US", [product], {"text": "#ad priced product"})
    except RuntimeError as exc:
        assert "verdict" in str(exc).lower()
    else:
        raise AssertionError("verdict without price_band must fail closed")
