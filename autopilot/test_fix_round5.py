from unittest.mock import patch

import analytics
import companyos
import generation_ssot
import generation_worker
import run
from test_supabase_products import FakeTransport


def _approved_product(price_info=None):
    return {
        "product_key": "us-authoritative-priced-product",
        "product_name": "Authoritative Priced Product",
        "country": "US",
        "category": "storage",
        "friction_id": "fr-us-authoritative-storage",
        "source_pointers": ["companyos:evidence:5"],
        "mechanism": "front_open",
        "failure_mode": "weak_latch",
        "skip_if": "your shelf is too shallow",
        "link": "https://heightcue.test/us/authoritative-priced",
        "landing_url": "https://heightcue.test/us/authoritative-priced",
        "sub_id": "authoritative-priced",
        "workflow_id": "w5",
        "claim_token": "claim-5",
        "evidence_revision": 5,
        "product_id": "p5",
        "offer_id": "o5",
        "price_info": price_info if price_info is not None else {"amount": 19.99, "currency": "USD"},
        "_workflow": {"claim_token": "claim-5", "tracking_key": "authoritative-priced"},
    }


def test_authoritative_product_resolution_carries_derived_band_to_verdict_publication_and_analytics(tmp_path):
    authoritative = _approved_product()
    with patch.object(companyos, "rpc", FakeTransport([authoritative])):
        resolved = generation_ssot.resolve_inputs(
            tmp_path, "sales_post", ["product:us-authoritative-priced-product"]
        )

    assert resolved[0]["price_info"] == {"amount": 19.99, "currency": "USD"}
    assert resolved[0]["price_band"] == "US_15_30"
    verdict = generation_worker.bind_friction_contract(
        "sales_post", "US", resolved, {"text": "#ad authoritative priced product"}
    )
    assert verdict["price_band"] == "US_15_30"

    captured = {}
    cfg = {"mode": {"publish": False}, "paths": {"state_dir": str(tmp_path)}}
    with patch.object(run.sourcing, "pick_us", return_value=resolved[0]), \
         patch.object(run.generate, "make_master", return_value={}), \
         patch.object(run.generate, "make_sales_post", return_value=verdict), \
         patch.object(run, "_publish_with_retry",
                      side_effect=lambda *_a, **kw: (captured.update(kw["meta_extra"]) or "PREVIEW-5", "published")), \
         patch.object(companyos, "release_product_claim", return_value={"ok": True}):
        run._us_sales(cfg, "", False)

    assert captured["price_band"] == "US_15_30"
    metric = {
        "friction_id": verdict["friction_id"],
        "stage": verdict["stage"],
        "market": verdict["market"],
        "source_pointers": verdict["source_pointers"],
        "mechanism": verdict["mechanism"],
        "price_band": captured["price_band"],
        "affiliate_destination": captured["affiliate_destination"],
    }
    assert analytics.attribution_gaps(metric) == []


def test_authoritative_product_resolution_rejects_malformed_untyped_price(tmp_path):
    malformed = _approved_product({"amount": "19.99", "currency": "USD"})
    with patch.object(companyos, "rpc", FakeTransport([malformed])):
        try:
            generation_ssot.resolve_inputs(
                tmp_path, "sales_post", ["product:us-authoritative-priced-product"]
            )
        except companyos.CompanyOSError as exc:
            assert "typed USD price amount" in str(exc)
        else:
            raise AssertionError("malformed authoritative price must fail closed")
