#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue 바이럴 UGC 패턴 원장 테스트.

네트워크 호출 없음. 임시 디렉터리 + 로컬 픽스처만 사용한다.
실행: cd autopilot && ../.venv/bin/python -m unittest -v test_viral_ugc.py
"""

import os
import shutil
import tempfile
import unittest

import viral_ugc as vu


# ---------------------------------------------------------------------------
# fixtures — 전부 합성 데이터. 실제 크리에이터 핸들·실측 지표 아님.
# ---------------------------------------------------------------------------

NOW = "2026-08-28T09:00:00+09:00"

GRAMMAR = {
    "hook_0_2s": "아이 키 얘기로 0.5초 안에 훅",
    "product_reveal_seconds": 3.0,
    "shot_count": 3,
    "hand_face_product_ratio": "hand:0.5/face:0.2/product:0.3",
    "camera_movement": "handheld_push_in",
    "demo_action": "베개 높이 조절 시연",
    "proof_moment": "자세 전후 비교 컷",
    "caption_structure": "훅 → 문제 → 시연 → CTA",
    "voice_structure": "보이스오버 없음, 자막 위주",
    "disclosure": "쿠팡 파트너스 고지 2행 노출",
    "cta": "프로필 링크 확인",
}


def observation(**over):
    base = dict(
        observation_id="obs-kr-001",
        market="KR",
        platform="threads",
        source_url="https://www.threads.net/@fixture/post/FIXTURE001",
        observed_at="2026-08-20T10:00:00+09:00",
        product_id="kr-pillow-001",
        category="posture",
        engagement={
            "likes": 1200, "replies": 80, "reposts": 40, "shares": 25,
            "views": 90000, "observed_at": "2026-08-20T10:00:00+09:00",
        },
        notes="합성 픽스처 관측 기록",
    )
    base.update(over)
    return vu.Observation.from_dict(base)


def inference(**over):
    base = dict(
        inference_id="inf-kr-001",
        observation_id="obs-kr-001",
        market="KR",
        grammar=dict(GRAMMAR),
        analyst_notes="훅이 첫 컷에서 문제를 선언한다는 해석",
        confidence=0.7,
        created_at=NOW,
    )
    base.update(over)
    return vu.Inference.from_dict(base)


def pattern(**over):
    base = dict(
        pattern_id="vp-kr-hook-question",
        market="KR",
        name="질문형 훅",
        state=vu.STATE_CANDIDATE,
        observation_ids=[],
        inference_ids=[],
        baseline=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(over)
    return vu.Pattern.from_dict(base)


BASELINE = {
    "metric": "engagement_rate",
    "pattern_value": 0.041,
    "baseline_value": 0.019,
    "sample_size": 12,
    "source": "state/metrics.jsonl 주간 집계",
    "compared_at": NOW,
}


def _support(ledger, pattern_id, n_products, n_categories, market="KR",
             prefix="s"):
    """패턴에 관측 n개를 붙여 distinct 상품/카테고리 수를 만든다."""
    cats = list(vu.ALLOWED_CATEGORIES)
    obs_ids = []
    inf_ids = []
    for i in range(n_products):
        oid = "obs-%s-%s-%d" % (market.lower(), prefix, i)
        ledger.record_observation(observation(
            observation_id=oid,
            market=market,
            product_id="%s-prod-%d" % (market.lower(), i),
            category=cats[i % n_categories],
            source_url="https://example.invalid/fixture/%s/%d" % (prefix, i),
        ))
        iid = "inf-%s-%s-%d" % (market.lower(), prefix, i)
        ledger.record_inference(inference(
            inference_id=iid, observation_id=oid, market=market))
        obs_ids.append(oid)
        inf_ids.append(iid)
    ledger.upsert_pattern(pattern(
        pattern_id=pattern_id, market=market,
        observation_ids=obs_ids, inference_ids=inf_ids))
    return obs_ids, inf_ids


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="viral-ugc-test-")
        self.kr = vu.PatternLedger(self.tmp, "KR")
        self.us = vu.PatternLedger(self.tmp, "US")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. 관측(OBSERVATION) 검증
# ---------------------------------------------------------------------------


class TestObservation(unittest.TestCase):
    def test_valid_observation_roundtrips(self):
        obs = observation().validate()
        again = vu.Observation.from_dict(obs.to_dict())
        self.assertEqual(again.to_dict(), obs.to_dict())

    def test_missing_source_url_rejected(self):
        with self.assertRaises(vu.ObservationError):
            observation(source_url="").validate()

    def test_non_http_source_url_rejected(self):
        with self.assertRaises(vu.ObservationError):
            observation(source_url="threads://x").validate()

    def test_missing_observed_at_rejected(self):
        with self.assertRaises(vu.ObservationError):
            observation(observed_at="").validate()

    def test_engagement_requires_observed_at(self):
        eng = dict(observation().engagement.to_dict())
        eng["observed_at"] = ""
        with self.assertRaises(vu.ObservationError):
            observation(engagement=eng).validate()

    def test_negative_metric_rejected(self):
        eng = dict(observation().engagement.to_dict())
        eng["likes"] = -1
        with self.assertRaises(vu.ObservationError):
            observation(engagement=eng).validate()

    def test_unobserved_metric_stays_none_and_is_not_fabricated(self):
        eng = dict(observation().engagement.to_dict())
        eng.pop("views")
        obs = observation(engagement=eng).validate()
        self.assertIsNone(obs.engagement.views)
        self.assertNotIn("views", obs.engagement.observed_metrics())

    def test_unknown_market_rejected(self):
        with self.assertRaises(vu.MarketIsolationError):
            observation(market="JP").validate()

    def test_category_hard_lock(self):
        with self.assertRaises(vu.ObservationError):
            observation(category="gadget").validate()

    def test_media_fields_rejected(self):
        for key in vu.FORBIDDEN_MEDIA_KEYS:
            data = observation().to_dict()
            data[key] = "/tmp/stolen.mp4"
            with self.assertRaises(vu.MediaPolicyError):
                vu.Observation.from_dict(data)

    def test_observation_never_carries_inference_fields(self):
        data = observation().to_dict()
        for key in ("grammar", "analyst_notes", "confidence"):
            self.assertNotIn(key, data)


# ---------------------------------------------------------------------------
# 2. 해석(INFERENCE) 검증 — 관측과 구조적으로 분리
# ---------------------------------------------------------------------------


class TestInference(unittest.TestCase):
    def test_valid_inference_roundtrips(self):
        inf = inference().validate()
        self.assertEqual(vu.Inference.from_dict(inf.to_dict()).to_dict(),
                         inf.to_dict())

    def test_inference_carries_no_metrics(self):
        data = inference().to_dict()
        for key in ("engagement", "likes", "replies", "reposts", "shares"):
            self.assertNotIn(key, data)

    def test_grammar_requires_all_fields(self):
        for field in vu.GRAMMAR_FIELDS:
            g = dict(GRAMMAR)
            g.pop(field)
            with self.assertRaises(vu.InferenceError):
                inference(grammar=g).validate()

    def test_grammar_rejects_unknown_field(self):
        g = dict(GRAMMAR)
        g["made_up_axis"] = "x"
        with self.assertRaises(vu.InferenceError):
            inference(grammar=g).validate()

    def test_inference_must_reference_an_observation(self):
        with self.assertRaises(vu.InferenceError):
            inference(observation_id="").validate()

    def test_confidence_range(self):
        with self.assertRaises(vu.InferenceError):
            inference(confidence=1.4).validate()

    def test_missing_disclosure_is_flagged_not_silently_learned(self):
        g = dict(GRAMMAR)
        g["disclosure"] = "none"
        inf = inference(grammar=g).validate()
        self.assertIn(vu.FLAG_DISCLOSURE_MISSING, vu.policy_flags(inf))
        self.assertEqual([], vu.policy_flags(inference()))


# ---------------------------------------------------------------------------
# 3. KR/US 격리
# ---------------------------------------------------------------------------


class TestMarketIsolation(LedgerTestCase):
    def test_ledgers_use_separate_files(self):
        self.assertNotEqual(self.kr.observations_path, self.us.observations_path)
        self.assertNotEqual(self.kr.patterns_path, self.us.patterns_path)

    def test_cannot_record_us_observation_in_kr_ledger(self):
        with self.assertRaises(vu.MarketIsolationError):
            self.kr.record_observation(observation(market="US"))

    def test_cannot_record_us_inference_in_kr_ledger(self):
        self.kr.record_observation(observation())
        with self.assertRaises(vu.MarketIsolationError):
            self.kr.record_inference(inference(market="US"))

    def test_inference_must_reference_observation_in_same_ledger(self):
        self.us.record_observation(observation(
            observation_id="obs-us-001", market="US",
            source_url="https://example.invalid/us/1"))
        with self.assertRaises(vu.MarketIsolationError):
            self.kr.record_inference(inference(observation_id="obs-us-001"))

    def test_cannot_upsert_us_pattern_in_kr_ledger(self):
        with self.assertRaises(vu.MarketIsolationError):
            self.kr.upsert_pattern(pattern(market="US"))

    def test_kr_pattern_never_appears_in_us_selection(self):
        _support(self.kr, "vp-kr-only", 3, 2, market="KR", prefix="a")
        self.kr.promote("vp-kr-only", baseline=BASELINE)
        _support(self.us, "vp-us-only", 3, 2, market="US", prefix="b")
        self.us.promote("vp-us-only", baseline=BASELINE)

        kr_ids = [p.pattern_id for p in self.kr.select_patterns(now=NOW)]
        us_ids = [p.pattern_id for p in self.us.select_patterns(now=NOW)]
        self.assertEqual(["vp-kr-only"], kr_ids)
        self.assertEqual(["vp-us-only"], us_ids)

    def test_kr_observations_invisible_to_us_ledger(self):
        self.kr.record_observation(observation())
        self.assertEqual([], self.us.observations())
        self.assertEqual(1, len(self.kr.observations()))

    def test_pattern_support_only_counts_own_market(self):
        _support(self.kr, "vp-kr", 3, 2, market="KR", prefix="c")
        _support(self.us, "vp-us", 3, 2, market="US", prefix="d")
        # US 원장에서 KR 패턴 id 를 조회해도 존재하지 않아야 한다.
        self.assertIsNone(self.us.get_pattern("vp-kr"))


# ---------------------------------------------------------------------------
# 4. 라이프사이클 + 승격 임계값
# ---------------------------------------------------------------------------


class TestLifecycle(LedgerTestCase):
    def test_lifecycle_states_exact(self):
        self.assertEqual(
            ("candidate", "active", "fatigued", "retired"), vu.LIFECYCLE_STATES)

    def test_thresholds_are_named_constants(self):
        self.assertEqual(3, vu.PROMOTION_MIN_DISTINCT_PRODUCTS)
        self.assertEqual(2, vu.PROMOTION_MIN_DISTINCT_CATEGORIES)
        self.assertTrue(vu.PROMOTION_REQUIRES_BASELINE)

    def test_new_pattern_starts_as_candidate(self):
        self.kr.upsert_pattern(pattern())
        self.assertEqual(vu.STATE_CANDIDATE,
                         self.kr.get_pattern("vp-kr-hook-question").state)

    def test_promotion_succeeds_at_thresholds(self):
        _support(self.kr, "vp-ok", 3, 2, prefix="e")
        check = self.kr.evaluate_promotion("vp-ok", baseline=BASELINE)
        self.assertTrue(check.ok, check.reasons)
        self.assertEqual(3, check.distinct_products)
        self.assertEqual(2, check.distinct_categories)
        p = self.kr.promote("vp-ok", baseline=BASELINE)
        self.assertEqual(vu.STATE_ACTIVE, p.state)

    def test_three_products_but_one_category_is_not_promoted(self):
        _support(self.kr, "vp-onecat", 3, 1, prefix="f")
        check = self.kr.evaluate_promotion("vp-onecat", baseline=BASELINE)
        self.assertFalse(check.ok)
        self.assertEqual(3, check.distinct_products)
        self.assertEqual(1, check.distinct_categories)
        self.assertIn(vu.REASON_INSUFFICIENT_CATEGORIES, check.reasons)
        with self.assertRaises(vu.LifecycleError):
            self.kr.promote("vp-onecat", baseline=BASELINE)
        self.assertEqual(vu.STATE_CANDIDATE,
                         self.kr.get_pattern("vp-onecat").state)

    def test_two_products_two_categories_is_not_promoted(self):
        _support(self.kr, "vp-twoprod", 2, 2, prefix="g")
        check = self.kr.evaluate_promotion("vp-twoprod", baseline=BASELINE)
        self.assertFalse(check.ok)
        self.assertIn(vu.REASON_INSUFFICIENT_PRODUCTS, check.reasons)

    def test_missing_baseline_blocks_promotion(self):
        _support(self.kr, "vp-nobase", 3, 2, prefix="h")
        check = self.kr.evaluate_promotion("vp-nobase", baseline=None)
        self.assertFalse(check.ok)
        self.assertIn(vu.REASON_MISSING_BASELINE, check.reasons)
        with self.assertRaises(vu.LifecycleError):
            self.kr.promote("vp-nobase")

    def test_incomplete_baseline_blocks_promotion(self):
        _support(self.kr, "vp-badbase", 3, 2, prefix="i")
        bad = dict(BASELINE)
        bad.pop("baseline_value")
        check = self.kr.evaluate_promotion("vp-badbase", baseline=bad)
        self.assertFalse(check.ok)
        self.assertIn(vu.REASON_MISSING_BASELINE, check.reasons)

    def test_policy_flagged_pattern_is_not_promoted(self):
        obs_ids, inf_ids = _support(self.kr, "vp-flag", 3, 2, prefix="j")
        g = dict(GRAMMAR)
        g["disclosure"] = ""
        self.kr.record_inference(inference(
            inference_id="inf-flagged", observation_id=obs_ids[0], grammar=g))
        p = self.kr.get_pattern("vp-flag")
        p.inference_ids = list(p.inference_ids) + ["inf-flagged"]
        self.kr.upsert_pattern(p)
        check = self.kr.evaluate_promotion("vp-flag", baseline=BASELINE)
        self.assertFalse(check.ok)
        self.assertIn(vu.REASON_POLICY_FLAGGED, check.reasons)

    def test_allowed_transitions(self):
        _support(self.kr, "vp-t", 3, 2, prefix="k")
        self.kr.promote("vp-t", baseline=BASELINE)
        self.kr.transition("vp-t", vu.STATE_FATIGUED)
        self.assertEqual(vu.STATE_FATIGUED, self.kr.get_pattern("vp-t").state)
        self.kr.transition("vp-t", vu.STATE_ACTIVE)
        self.kr.transition("vp-t", vu.STATE_RETIRED)
        self.assertEqual(vu.STATE_RETIRED, self.kr.get_pattern("vp-t").state)

    def test_retired_is_terminal(self):
        _support(self.kr, "vp-r", 3, 2, prefix="l")
        self.kr.transition("vp-r", vu.STATE_RETIRED)
        with self.assertRaises(vu.LifecycleError):
            self.kr.transition("vp-r", vu.STATE_ACTIVE)

    def test_candidate_cannot_jump_to_fatigued(self):
        self.kr.upsert_pattern(pattern(pattern_id="vp-c"))
        with self.assertRaises(vu.LifecycleError):
            self.kr.transition("vp-c", vu.STATE_FATIGUED)

    def test_unknown_state_rejected(self):
        with self.assertRaises(vu.LifecycleError):
            pattern(state="hot").validate()


# ---------------------------------------------------------------------------
# 5. 점수·선택 결정성
# ---------------------------------------------------------------------------


class TestScoringAndSelection(LedgerTestCase):
    def test_only_active_patterns_are_selected(self):
        _support(self.kr, "vp-active", 3, 2, prefix="m")
        self.kr.promote("vp-active", baseline=BASELINE)
        _support(self.kr, "vp-cand", 3, 2, prefix="n")
        ids = [p.pattern_id for p in self.kr.select_patterns(now=NOW)]
        self.assertEqual(["vp-active"], ids)

    def test_score_components_present_and_bounded(self):
        _support(self.kr, "vp-s", 3, 2, prefix="o")
        self.kr.promote("vp-s", baseline=BASELINE)
        score = self.kr.score_pattern("vp-s", now=NOW)
        self.assertEqual(set(vu.SCORE_WEIGHTS), set(score.components))
        for name, value in score.components.items():
            self.assertGreaterEqual(value, 0.0, name)
            self.assertLessEqual(value, 1.0, name)
        self.assertGreaterEqual(score.total, 0.0)
        self.assertLessEqual(score.total, 1.0)

    def test_selection_is_deterministic(self):
        _support(self.kr, "vp-b", 3, 2, prefix="p")
        _support(self.kr, "vp-a", 3, 2, prefix="q")
        self.kr.promote("vp-b", baseline=BASELINE)
        self.kr.promote("vp-a", baseline=BASELINE)
        first = [p.pattern_id for p in self.kr.select_patterns(now=NOW)]
        second = [p.pattern_id for p in
                  vu.PatternLedger(self.tmp, "KR").select_patterns(now=NOW)]
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), first)  # 동점 시 id 오름차순

    def test_stale_observations_score_lower_recency(self):
        _support(self.kr, "vp-fresh", 3, 2, prefix="r")
        self.kr.promote("vp-fresh", baseline=BASELINE)
        fresh = self.kr.score_pattern("vp-fresh", now=NOW)
        stale = self.kr.score_pattern("vp-fresh", now="2027-08-28T09:00:00+09:00")
        self.assertLess(stale.components["recency"], fresh.components["recency"])


# ---------------------------------------------------------------------------
# 6. 지속성 (원자적 쓰기 / 추가 전용 이벤트)
# ---------------------------------------------------------------------------


class TestPersistence(LedgerTestCase):
    def test_observations_append_only_and_reload(self):
        self.kr.record_observation(observation())
        self.kr.record_observation(observation(
            observation_id="obs-kr-002",
            source_url="https://example.invalid/2"))
        reloaded = vu.PatternLedger(self.tmp, "KR").observations()
        self.assertEqual(["obs-kr-001", "obs-kr-002"],
                         [o.observation_id for o in reloaded])

    def test_duplicate_observation_id_rejected(self):
        self.kr.record_observation(observation())
        with self.assertRaises(vu.ObservationError):
            self.kr.record_observation(observation())

    def test_patterns_reload_across_instances(self):
        _support(self.kr, "vp-persist", 3, 2, prefix="s")
        self.kr.promote("vp-persist", baseline=BASELINE)
        p = vu.PatternLedger(self.tmp, "KR").get_pattern("vp-persist")
        self.assertEqual(vu.STATE_ACTIVE, p.state)
        self.assertEqual(BASELINE["metric"], p.baseline["metric"])

    def test_events_are_appended(self):
        _support(self.kr, "vp-ev", 3, 2, prefix="t")
        self.kr.promote("vp-ev", baseline=BASELINE)
        self.assertTrue(os.path.exists(self.kr.events_path))
        events = self.kr.events()
        kinds = [e.get("event") for e in events]
        self.assertIn("pattern_transition", kinds)
        self.assertTrue(all(e.get("market") == "KR" for e in events))


# ---------------------------------------------------------------------------
# 7. 픽스처 로딩 — 네트워크 없음
# ---------------------------------------------------------------------------


class TestFixture(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures", "viral_ugc_sample.jsonl")
        self.tmp = tempfile.mkdtemp(prefix="viral-ugc-fixture-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fixture_exists_and_loads(self):
        self.assertTrue(os.path.exists(self.path), self.path)
        obs = vu.load_observations(self.path)
        self.assertGreaterEqual(len(obs), 6)
        for o in obs:
            o.validate()

    def test_fixture_has_both_markets(self):
        markets = {o.market for o in vu.load_observations(self.path)}
        self.assertEqual({"KR", "US"}, markets)

    def test_fixture_carries_no_media(self):
        with open(self.path, encoding="utf-8") as fh:
            raw = fh.read()
        for key in vu.FORBIDDEN_MEDIA_KEYS:
            self.assertNotIn('"%s"' % key, raw)

    def test_fixture_is_marked_as_fixture_data(self):
        with open(self.path, encoding="utf-8") as fh:
            first = fh.readline()
        self.assertIn("fixture", first.lower())

    def test_fixture_ingest_is_market_isolated(self):
        kr = vu.PatternLedger(self.tmp, "KR")
        us = vu.PatternLedger(self.tmp, "US")
        loaded = vu.load_observations(self.path)
        for o in loaded:
            (kr if o.market == "KR" else us).record_observation(o)
        self.assertTrue(kr.observations())
        self.assertTrue(us.observations())
        self.assertTrue(all(o.market == "KR" for o in kr.observations()))
        self.assertTrue(all(o.market == "US" for o in us.observations()))
        self.assertEqual(len(loaded),
                         len(kr.observations()) + len(us.observations()))

    def test_loader_rejects_record_missing_url_or_date(self):
        bad = os.path.join(self.tmp, "bad.jsonl")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('{"observation_id":"x","market":"KR","platform":"threads",'
                     '"source_url":"","observed_at":"2026-08-20T10:00:00+09:00",'
                     '"product_id":"p","category":"posture",'
                     '"engagement":{"likes":1,'
                     '"observed_at":"2026-08-20T10:00:00+09:00"}}\n')
        with self.assertRaises(vu.ObservationError):
            vu.load_observations(bad)

    def test_module_makes_no_network_calls(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "viral_ugc.py"), encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("import requests", "urllib.request", "http.client",
                       "socket."):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
