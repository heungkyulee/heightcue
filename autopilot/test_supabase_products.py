# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from unittest import mock


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, function, payload):
        self.calls.append((function, payload))
        if not self.responses:
            raise AssertionError("unexpected RPC")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CompanyOSProductTests(unittest.TestCase):
    def test_claim_maps_database_payload_to_generator_product(self):
        import companyos
        transport = FakeTransport([{
            "product_key": "us-renzos-iron",
            "product_name": "Renzo's Iron Strong",
            "category": "nutrition",
            "evidence_revision": 3,
            "product_id": "p1",
            "offer_id": "o1",
            "workflow_id": "w1",
            "claim_token": "claim-1",
            "lease_expires_at": "2026-08-30T10:00:00Z",
            "landing_url": "https://heightcue.lifoli.co.kr/us/renzos-iron.html",
            "affiliate_url": "https://amazon.com/dp/B000000000?tag=heightcue-20",
            "sub_id": "us-renzos-iron",
            "price_info": {"amount": 19.99, "currency": "USD"},
            "review_quotes": ["easy to take"],
            "spec_facts": ["tablet format"],
            "approved_claims": [],
            "claim_boundary": {"allowed": ["tablet format"]},
        }])
        product = companyos.claim_us_product({"mode": {}}, owner="test-owner", transport=transport)
        self.assertEqual(product["product_key"], "us-renzos-iron")
        self.assertEqual(product["link"], "https://heightcue.lifoli.co.kr/us/renzos-iron.html")
        self.assertEqual(product["_workflow"]["claim_token"], "claim-1")
        self.assertEqual(transport.calls[0][0], "hc_claim_active_product")

    def test_approved_generation_packet_maps_price_observation_to_price_info(self):
        import companyos
        observed = {"amount": 25.9, "currency": "USD", "observed_at": "2026-08-31T08:00:00Z"}
        transport = FakeTransport([{
            "product_key": "us-val-magnesium-cream-kids",
            "product_name": "VAL Magnesium Cream for Kids",
            "price_observation": observed,
            "affiliate_url": "https://www.amazon.com/dp/B0C1XX99RJ?tag=heightcue-20",
            "workflow_id": "w-val",
            "evidence_revision": 1,
        }])
        product = companyos.get_product("us-val-magnesium-cream-kids", transport=transport)
        self.assertEqual(product["price_info"], observed)
        self.assertEqual(product["price_observation"], observed)
        self.assertEqual(product["price_band"], "US_15_30")
        self.assertEqual(product["link"], "https://www.amazon.com/dp/B0C1XX99RJ?tag=heightcue-20")

    def test_supabase_failure_fails_closed_without_json_fallback(self):
        import companyos
        transport = FakeTransport([companyos.CompanyOSError("offline")])
        with self.assertRaises(companyos.CompanyOSError):
            companyos.claim_us_product({"mode": {}}, owner="test-owner", transport=transport)

    def test_release_requires_claim_identity(self):
        import companyos
        with self.assertRaises(companyos.CompanyOSError):
            companyos.release_product_claim({}, "generation_failed", {}, transport=FakeTransport())

    def test_verified_publication_records_exact_remote_identity(self):
        import companyos
        transport = FakeTransport([{"ok": True, "state": "published"}])
        product = {"product_key": "us-ddrops-kids-600iu", "_workflow": {
            "claim_token": "claim-2", "workflow_id": "w2", "evidence_revision": 4,
            "offer_id": "o2", "product_id": "p2"}}
        result = companyos.record_product_publication(
            product, media_id="123", publication_url="https://threads.net/@x/post/123",
            text="exact text", tracking_key="amazon-us-ddrops-kids-600iu",
            sub_id="us-guide-ddrops", readback_verified=True, transport=transport)
        self.assertTrue(result["ok"])
        fn, payload = transport.calls[0]
        self.assertEqual(fn, "hc_record_product_publication")
        self.assertEqual(payload["p_publication"]["external_media_id"], "123")
        self.assertTrue(payload["p_publication"]["readback_verified"])

    def test_rehearsal_uses_non_claiming_fixture(self):
        import sourcing
        with mock.patch("companyos.claim_us_product") as claim:
            product = sourcing.pick_us({"mode": {"_rehearsal": True, "publish": False}})
        claim.assert_not_called()
        self.assertEqual(product["product_key"], "us-front-open-storage")

    def test_rehearsal_fixture_uses_companyos_approved_product_identity(self):
        import generation_ssot
        import sourcing
        product = sourcing.pick_us({"mode": {"_rehearsal": True, "publish": False}})
        approved_key = "us-front-open-storage"
        self.assertEqual(product["product_key"], approved_key)
        self.assertIn(approved_key, generation_ssot.REHEARSAL_PRODUCTS)

    def test_pick_us_delegates_to_companyos_and_never_reads_json(self):
        import sourcing
        expected = {"product_key": "us-db-product"}
        with mock.patch("companyos.claim_us_product", return_value=expected) as claim, \
                mock.patch.object(sourcing, "read_json", side_effect=AssertionError("JSON fallback used")):
            self.assertIs(sourcing.pick_us({"mode": {}}, dry_run=False), expected)
        claim.assert_called_once()

    def test_us_sales_records_verified_publication_and_releases_failures(self):
        import run
        product = {"product_key": "us-db", "country": "US", "product_name": "DB Product",
                   "link": "https://heightcue.test/us/db", "sub_id": "sub-db",
                   "_workflow": {"claim_token": "token", "tracking_key": "track-db"}}
        cfg = {"mode": {}, "paths": {}}
        generated = {"text": "verified sales text"}
        with mock.patch.object(run.sourcing, "pick_us", return_value=product), \
             mock.patch.object(run.generate, "make_master", return_value={}), \
             mock.patch.object(run.generate, "make_sales_post", return_value=generated), \
             mock.patch.object(run, "_publish_with_retry", side_effect=lambda _cfg, build, *_a, **_k: (build() and "media-1", "published")), \
             mock.patch.object(run.publish, "verified_publication_url", return_value="https://www.threads.net/@heightcue_us/post/code") as permalink, \
             mock.patch("companyos.record_product_publication", return_value={"ok": True}) as record:
            run._us_sales(cfg, "", False)
        self.assertEqual(record.call_args.kwargs["media_id"], "media-1")
        self.assertEqual(record.call_args.kwargs["text"], "verified sales text")
        self.assertEqual(record.call_args.kwargs["publication_url"], "https://www.threads.net/@heightcue_us/post/code")
        permalink.assert_called_once_with(cfg, "media-1")

        with mock.patch.object(run.sourcing, "pick_us", return_value=product), \
             mock.patch.object(run.generate, "make_master", side_effect=RuntimeError("boom")), \
             mock.patch("companyos.release_product_claim", return_value={"ok": True}) as release:
            with self.assertRaises(RuntimeError):
                run._us_sales(cfg, "", False)
        self.assertEqual(release.call_args.args[1], "generation_or_publish_failed")

    def test_us_sales_preview_media_never_uses_threads_readback(self):
        import run
        product = {"product_key": "us-db", "country": "US", "product_name": "DB Product",
                   "link": "https://heightcue.test/us/db", "sub_id": "sub-db",
                   "_workflow": {"claim_token": "token", "tracking_key": "track-db"}}
        cfg = {"mode": {"publish": False}, "paths": {}}
        generated = {"text": "verified preview text"}
        with mock.patch.object(run.sourcing, "pick_us", return_value=product), \
             mock.patch.object(run.generate, "make_master", return_value={}), \
             mock.patch.object(run.generate, "make_sales_post", return_value=generated), \
             mock.patch.object(run, "_publish_with_retry", side_effect=lambda _cfg, build, *_a, **_k: (build() and "PREVIEW-123", "published")), \
             mock.patch.object(run.publish, "verified_publication_url") as permalink, \
             mock.patch("companyos.record_product_publication") as record, \
             mock.patch("companyos.release_product_claim", return_value={"ok": True}) as release:
            media, reason = run._us_sales(cfg, "", False)
        self.assertEqual((media, reason), ("PREVIEW-123", "published"))
        permalink.assert_not_called()
        record.assert_not_called()
        self.assertEqual(release.call_args.args[1], "preview_only")

    def test_credentials_fall_back_to_companyos_env_file(self):
        import companyos
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "companyos.env")
            with open(p, "w", encoding="utf-8") as f:
                f.write("SUPABASE_URL=https://example.supabase.co\nSUPABASE_SERVICE_KEY=service-secret\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(companyos._load_credentials(p), ("https://example.supabase.co", "service-secret"))

    def test_publication_requires_verified_permalink(self):
        import companyos
        product = {"product_key": "us-x", "_workflow": {"claim_token": "claim"}}
        with self.assertRaises(companyos.CompanyOSError):
            companyos.record_product_publication(
                product, media_id="123", publication_url="", text="x",
                tracking_key="amazon-x", sub_id="sub", readback_verified=True)

    def test_generation_product_input_resolves_from_companyos_not_legacy_json(self):
        import generation_ssot
        expected = {"product_key": "us-db", "approved_claims": ["fact"]}
        with mock.patch("companyos.get_product", return_value=expected) as get_product:
            resolved = generation_ssot.resolve_inputs("/tmp/nonexistent", "sales_post", ["product:us-db"])
        self.assertEqual(resolved, [expected])
        get_product.assert_called_once_with("us-db")

    def test_unverified_publication_is_rejected_client_side(self):
        import companyos
        product = {"_workflow": {"claim_token": "claim"}}
        with self.assertRaises(companyos.CompanyOSError):
            companyos.record_product_publication(product, media_id="123", publication_url="",
                                                  text="x", tracking_key="x", sub_id="x",
                                                  readback_verified=False, transport=FakeTransport())


if __name__ == "__main__":
    unittest.main()
