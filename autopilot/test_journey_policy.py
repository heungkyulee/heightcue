#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue active journey policy regression tests."""

import unittest

import journey_policy as policy


class JourneyPolicyTests(unittest.TestCase):
    def test_public_positioning_is_persona_free_and_friction_led(self):
        self.assertEqual(policy.POSITIONING["KR"], "아이 키우는 집의 반복되는 귀찮음을 줄이는 제품 판정")
        self.assertEqual(policy.POSITIONING["US"], "Product verdicts for recurring parenting friction")
        combined = " ".join(policy.POSITIONING.values()).lower()
        for token in ("167cm", "5'6\"", "uncle", "팩트폭격기"):
            self.assertNotIn(token.lower(), combined)

    def test_measurement_products_and_growth_claim_products_are_never_commerce_eligible(self):
        blocked = (
            "휴비딕 초음파 무선 신장계 HUK-2",
            "seca 213 portable stadiometer",
            "벽걸이 키재기 스티커",
            "키 성장 영양제",
        )
        for name in blocked:
            with self.subTest(name=name):
                result = policy.product_eligibility({"product_name": name, "category": "sleep"})
                self.assertFalse(result["eligible"])
                self.assertTrue(result["reasons"])

    def test_recurring_parenting_friction_categories_are_publicly_mapped(self):
        expected = {"sleep_morning", "meals_lunch", "play_movement", "study_routine", "storage_cleanup"}
        self.assertEqual(set(policy.PUBLIC_CATEGORIES), expected)
        self.assertEqual(policy.map_category("sleep"), "sleep_morning")
        self.assertEqual(policy.map_category("nutrition"), "meals_lunch")
        self.assertEqual(policy.map_category("exercise"), "play_movement")
        self.assertEqual(policy.map_category("posture"), "study_routine")
        self.assertEqual(policy.map_category("storage"), "storage_cleanup")

    def test_caregiver_shaming_is_rejected_but_specific_claim_criticism_passes(self):
        must_reject = (
            ("KR", "이걸 또 사는 부모는 호구입니다."),
            ("KR", "귀찮아서 젤리로 때우는 부모님들 많죠."),
            ("US", "Lazy parents keep buying these gummies."),
            ("US", "If you buy this, you clearly did not read the label."),
        )
        for market, text in must_reject:
            with self.subTest(text=text):
                self.assertTrue(policy.caregiver_shaming_reasons(text, market))

        must_pass = (
            ("KR", "성장기 맞춤은 효능이 아닙니다. 1회 섭취량부터 보세요."),
            ("KR", "잠금이 약해서 작은 부품이 다시 쏟아진다는 후기가 반복됩니다."),
            ("US", "The front label makes a promise the nutrition panel does not support."),
            ("US", "Skip this bin if the shelf is shallower than 12 inches."),
        )
        for market, text in must_pass:
            with self.subTest(text=text):
                self.assertEqual(policy.caregiver_shaming_reasons(text, market), [])

    def test_retired_persona_is_rejected_only_on_active_reader_facing_copy(self):
        self.assertTrue(policy.retired_persona_reasons("167cm 팩트폭격기가 골랐습니다"))
        self.assertTrue(policy.retired_persona_reasons("The 5'6\" Uncle verdict"))
        self.assertEqual(policy.retired_persona_reasons("작은 부품이 바닥에 다시 쏟아집니다."), [])

    def test_disclosures_and_distribution_defaults_are_exact(self):
        self.assertEqual(
            policy.AFFILIATE_DISCLOSURES["KR"],
            "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
        )
        self.assertEqual(policy.AFFILIATE_DISCLOSURES["US_LINK"], "#ad")
        self.assertEqual(
            policy.AFFILIATE_DISCLOSURES["US_ACCOUNT"],
            "As an Amazon Associate I earn from qualifying purchases.",
        )
        self.assertEqual(policy.CADENCE["original_posts_per_market_per_day"], 2)
        self.assertEqual(policy.CADENCE["outreach_replies_per_market_per_day"], (10, 15))

    def test_each_public_category_has_noncommercial_generic_reply_mechanisms(self):
        self.assertEqual(set(policy.GENERIC_REPLY_MECHANISMS), set(policy.PUBLIC_CATEGORIES))
        for category, mechanisms in policy.GENERIC_REPLY_MECHANISMS.items():
            self.assertGreaterEqual(len(mechanisms), 2, category)
            for mechanism in mechanisms:
                self.assertEqual(set(mechanism), {"id", "KR", "US"})
                self.assertNotRegex(mechanism["US"].lower(), r"amazon|product|buy|doctor|diagnos")
                self.assertNotRegex(mechanism["KR"], r"쿠팡|제품|구매|의사|진단")

    def test_query_packs_cover_each_market_and_public_category_without_commerce_terms(self):
        self.assertEqual(set(policy.OUTREACH_QUERY_PACKS), {"KR", "US"})
        for market, rows in policy.OUTREACH_QUERY_PACKS.items():
            self.assertEqual({row["category"] for row in rows}, set(policy.PUBLIC_CATEGORIES))
            self.assertGreaterEqual(len(rows), 10)
            for row in rows:
                self.assertEqual(set(row), {"category", "query"})
                self.assertNotRegex(row["query"].lower(), r"amazon|coupang|product|제품|구매|height")


if __name__ == "__main__":
    unittest.main(verbosity=2)
