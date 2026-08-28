# -*- coding: utf-8 -*-
"""Task 8 실행 계약: 근거 결속 마이크로 스토리보드 생성.

네트워크 없음 — 모델 호출은 전부 ``model=`` 시임으로 가짜 응답을 주입한다.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import video_contracts as vc
import video_storyboard as vs


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

KR_QUOTE_1 = "줄이 없는 무소음 볼 방식이라 층간소음 걱정이 적습니다"
KR_QUOTE_2 = "층간소음 매트가 기본 구성에 포함되어 있습니다"
KR_QUOTE_3 = "실내 전용으로 설계된 제품입니다"

US_QUOTE_1 = "The label lists 400 IU of vitamin D3 per drop"
US_QUOTE_2 = "The other listed ingredient is fractionated coconut oil"


def _evidence(market="KR", quotes=None):
    quotes = quotes or ([KR_QUOTE_1, KR_QUOTE_2, KR_QUOTE_3] if market == "KR"
                        else [US_QUOTE_1, US_QUOTE_2])
    return vc.ProductEvidence(
        product_id="p-001",
        market=market,
        source_urls=["https://example.com/product/1"],
        source_sha256=["a" * 64],
        rights={"basis": "official page", "holder": "brand",
                "source_url": "https://example.com/product/1",
                "captured_at": "2026-08-28T00:00:00+09:00"},
        provenance=[{"quote": q, "source_url": "https://example.com/product/1",
                     "original_location": f"spec table row {i + 1}"}
                    for i, q in enumerate(quotes)],
        captured_at="2026-08-28T00:00:00+09:00",
    ).validate()


def _kr_cut(index, claim, evidence_id, action, benefit):
    return {
        "index": index,
        "duration_seconds": 5,
        "action": action,
        "benefit": benefit,
        "claim": claim,
        "evidence_id": evidence_id,
        "voice_line": f"{claim}. 그래서 {benefit}",
        "first_frame_prompt": "세로 9:16 화면, 거실 바닥에 놓인 제품 한 개를 정면에서 담은 한 장면",
        "motion_prompt": "카메라가 천천히 아래로 기울며 손이 제품을 잡는 동작",
    }


def _kr_response(n=2):
    quotes = [KR_QUOTE_1, KR_QUOTE_2, KR_QUOTE_3]
    benefits = ["소음 걱정이 줄어듭니다", "따로 살 것이 줄어듭니다", "실내에서 쓸 수 있습니다"]
    actions = ["손으로 제품을 집어 바닥에 놓는다", "매트를 펼친다", "제품을 거실에 둔다"]
    return {"cuts": [_kr_cut(i + 1, quotes[i], f"ev{i + 1}", actions[i], benefits[i])
                     for i in range(n)]}


def _us_response(n=2):
    quotes = [US_QUOTE_1, US_QUOTE_2]
    cuts = []
    for i in range(n):
        cuts.append({
            "index": i + 1,
            "duration_seconds": 5,
            "action": "a hand lifts the bottle into frame",
            "benefit": "you can read the label yourself",
            "claim": quotes[i],
            "evidence_id": f"ev{i + 1}",
            "voice_line": f"{quotes[i]}. So {['you can read the label yourself'][0]}",
            "first_frame_prompt": "vertical 9:16 frame, one bottle on a kitchen counter, single subject",
            "motion_prompt": "the camera pushes in slowly while the hand rotates the bottle",
        })
    return {"cuts": cuts}


def _model(response):
    """호출 인자를 기록하는 가짜 모델 시임."""
    calls = []

    def _call(system_prompt, payload):
        calls.append({"system": system_prompt, "payload": payload})
        return response

    _call.calls = calls
    return _call


CFG = {"openrouter": {"model": "google/gemini-3.1-pro-preview", "api_key": "x"}}


def _generate(response, market="KR", evidence=None, **kw):
    return vs.generate_storyboard(
        CFG,
        evidence=evidence if evidence is not None else _evidence(market),
        market=market,
        run_id="run-1",
        content_draft_id="draft-1",
        viral_pattern_ids=["vp-1"],
        model=_model(response),
        **kw,
    )


# ---------------------------------------------------------------------------
# 정상 경로 — 5 / 10 / 15초
# ---------------------------------------------------------------------------


class TestCutMath(unittest.TestCase):

    def test_simple_is_5_seconds_one_cut(self):
        sb = _generate(_kr_response(1), complexity="simple")
        self.assertEqual(len(sb.cuts), 1)
        self.assertEqual(sb.total_duration_seconds(), 5)

    def test_default_is_10_seconds_two_cuts(self):
        sb = _generate(_kr_response(2))
        self.assertEqual(len(sb.cuts), 2)
        self.assertEqual(sb.total_duration_seconds(), 10)

    def test_complex_is_15_seconds_three_cuts(self):
        sb = _generate(_kr_response(3), complexity="complex")
        self.assertEqual(len(sb.cuts), 3)
        self.assertEqual(sb.total_duration_seconds(), 15)

    def test_every_total_is_an_allowed_duration(self):
        for complexity, expected in (("simple", 5), ("standard", 10), ("complex", 15)):
            sb = _generate(_kr_response(expected // 5), complexity=complexity)
            self.assertIn(sb.total_duration_seconds(), vc.ALLOWED_TOTAL_DURATIONS)

    def test_unknown_complexity_rejected(self):
        with self.assertRaises(vs.StoryboardError):
            _generate(_kr_response(2), complexity="epic")

    def test_cut_count_mismatch_with_requested_complexity_rejected(self):
        # 2컷을 요청했는데 모델이 3컷을 돌려주면 조용히 자르지 않고 죽는다.
        with self.assertRaises(vs.ModelOutputError):
            _generate(_kr_response(3), complexity="standard")


# ---------------------------------------------------------------------------
# 신뢰할 수 없는 모델 출력 — 거부 경로
# ---------------------------------------------------------------------------


class TestUntrustedModelOutput(unittest.TestCase):

    def test_four_cuts_rejected(self):
        resp = _kr_response(3)
        resp["cuts"].append(_kr_cut(4, KR_QUOTE_1, "ev1", "제품을 든다", "편합니다"))
        with self.assertRaises(vc.DurationError):
            _generate(resp, complexity="complex")

    def test_seven_second_cut_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][1]["duration_seconds"] = 7
        with self.assertRaises(vc.DurationError):
            _generate(resp)

    def test_missing_evidence_id_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0].pop("evidence_id")
        with self.assertRaises(vs.EvidenceError):
            _generate(resp)

    def test_unknown_evidence_id_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["evidence_id"] = "ev99"
        with self.assertRaises(vs.EvidenceError):
            _generate(resp)

    def test_claim_not_supported_by_cited_quote_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["claim"] = "키가 3cm 더 큽니다"
        with self.assertRaises(vs.EvidenceError):
            _generate(resp)

    def test_malformed_json_string_rejected(self):
        with self.assertRaises(vs.ModelOutputError):
            _generate("{not json at all")

    def test_non_dict_response_rejected(self):
        with self.assertRaises(vs.ModelOutputError):
            _generate(["cuts"])

    def test_missing_cuts_key_rejected(self):
        with self.assertRaises(vs.ModelOutputError):
            _generate({"storyboard": []})

    def test_empty_cuts_rejected(self):
        with self.assertRaises(vc.DurationError):
            _generate({"cuts": []}, complexity="simple")

    def test_non_sequential_index_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][1]["index"] = 5
        with self.assertRaises(vc.ContractError):
            _generate(resp)

    def test_over_length_voice_line_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["voice_line"] = "가" * (vs.VOICE_LINE_MAX_CHARS + 1)
        with self.assertRaises(vs.ModelOutputError):
            _generate(resp)

    def test_json_string_response_is_accepted(self):
        sb = _generate(json.dumps(_kr_response(2), ensure_ascii=False))
        self.assertEqual(len(sb.cuts), 2)


# ---------------------------------------------------------------------------
# 컷 1개 = 동작 1개 = 효용 1개
# ---------------------------------------------------------------------------


class TestOneActionOneBenefit(unittest.TestCase):

    def test_two_actions_in_one_cut_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["action"] = "제품을 집고 그리고 매트를 펼친다"
        with self.assertRaises(vs.OneIdeaError):
            _generate(resp)

    def test_two_benefits_in_one_cut_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["benefit"] = "소음이 줄고, 돈도 아낍니다"
        with self.assertRaises(vs.OneIdeaError):
            _generate(resp)

    def test_montage_first_frame_prompt_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["first_frame_prompt"] = "분할 화면 몽타주로 여러 컷을 한 번에"
        with self.assertRaises(vs.OneIdeaError):
            _generate(resp)

    def test_scene_change_in_motion_prompt_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["motion_prompt"] = "손이 제품을 잡은 뒤 다른 장면으로 컷 전환"
        with self.assertRaises(vs.OneIdeaError):
            _generate(resp)

    def test_empty_action_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["action"] = "   "
        with self.assertRaises(vs.ModelOutputError):
            _generate(resp)


# ---------------------------------------------------------------------------
# 시장 격리 · 언어 게이트
# ---------------------------------------------------------------------------


class TestMarketIsolation(unittest.TestCase):

    def test_us_market_english_copy_accepted(self):
        sb = _generate(_us_response(2), market="US")
        self.assertEqual(sb.market, "US")

    def test_english_copy_in_kr_market_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["voice_line"] = "This one is quiet enough for apartments"
        with self.assertRaises(vs.MarketLanguageError):
            _generate(resp)

    def test_korean_copy_in_us_market_rejected(self):
        resp = _us_response(2)
        resp["cuts"][0]["voice_line"] = "층간소음 걱정이 적습니다"
        with self.assertRaises(vs.MarketLanguageError):
            _generate(resp, market="US", evidence=_evidence("US"))

    def test_market_mismatch_with_evidence_rejected(self):
        with self.assertRaises(vc.LineageError):
            _generate(_kr_response(2), market="US", evidence=_evidence("KR"))

    def test_unknown_market_rejected(self):
        with self.assertRaises(vc.LineageError):
            _generate(_kr_response(2), market="JP", evidence=_evidence("KR"))


# ---------------------------------------------------------------------------
# 금지 표현 — 가짜 체험담 · 효능 암시
# ---------------------------------------------------------------------------


class TestForbiddenClaims(unittest.TestCase):

    def test_efficacy_implication_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["voice_line"] = f"{KR_QUOTE_1}. 키 크는 데 효과가 있습니다"
        with self.assertRaises(vs.ForbiddenClaimError):
            _generate(resp)

    def test_fake_experience_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["voice_line"] = f"{KR_QUOTE_1}. 우리 아이가 먹어보니 좋았어요"
        with self.assertRaises(vs.ForbiddenClaimError):
            _generate(resp)

    def test_us_medical_claim_rejected(self):
        resp = _us_response(2)
        resp["cuts"][0]["voice_line"] = f"{US_QUOTE_1}. It helps kids grow taller"
        with self.assertRaises(vs.ForbiddenClaimError):
            _generate(resp, market="US", evidence=_evidence("US"))


# ---------------------------------------------------------------------------
# 근거 부재는 조용히 넘어가지 않는다
# ---------------------------------------------------------------------------


class TestEvidenceRequired(unittest.TestCase):

    def test_missing_evidence_object_fails_loudly(self):
        with self.assertRaises(vs.EvidenceError):
            vs.generate_storyboard(CFG, evidence=None, market="KR", run_id="run-1",
                                   content_draft_id="draft-1",
                                   viral_pattern_ids=["vp-1"],
                                   model=_model(_kr_response(2)))

    def test_evidence_without_provenance_fails_loudly(self):
        bad = _evidence("KR")
        bad.provenance = []
        with self.assertRaises(vc.RightsError):
            _generate(_kr_response(2), evidence=bad)

    def test_evidence_index_ids_are_stable(self):
        idx = vs.evidence_index(_evidence("KR"))
        self.assertEqual(sorted(idx), ["ev1", "ev2", "ev3"])
        self.assertEqual(idx["ev1"]["quote"], KR_QUOTE_1)

    def test_missing_viral_pattern_ids_fails_loudly(self):
        with self.assertRaises(vc.LineageError):
            vs.generate_storyboard(CFG, evidence=_evidence("KR"), market="KR",
                                   run_id="run-1", content_draft_id="draft-1",
                                   viral_pattern_ids=[],
                                   model=_model(_kr_response(2)))


# ---------------------------------------------------------------------------
# 고지 의무 유지
# ---------------------------------------------------------------------------


class TestDisclosureSurvives(unittest.TestCase):

    def test_kr_disclosure_carried(self):
        sb = _generate(_kr_response(2))
        self.assertEqual(sb.disclosure["market"], "KR")
        self.assertIn("쿠팡 파트너스", sb.disclosure["text"])
        self.assertTrue(sb.disclosure["required"])

    def test_us_disclosure_carried(self):
        sb = _generate(_us_response(2), market="US", evidence=_evidence("US"))
        self.assertIn("Amazon Associate", sb.disclosure["text"])

    def test_disclosure_survives_serialisation(self):
        sb = _generate(_kr_response(2))
        data = sb.to_dict()
        self.assertIn("쿠팡 파트너스", data["disclosure"]["text"])
        self.assertTrue(data["disclosure"]["required"])

    def test_contract_storyboard_view_validates(self):
        sb = _generate(_kr_response(2))
        sb.as_contract_storyboard().validate()


# ---------------------------------------------------------------------------
# 기준선 출처 — 실측 metrics.jsonl 에서만
# ---------------------------------------------------------------------------


class TestBaselineProvenance(unittest.TestCase):

    def _metrics(self, rows):
        fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_baseline_computed_from_real_metrics(self):
        path = self._metrics([
            {"country": "KR", "insights": {"views": 100, "likes": 10}},
            {"country": "KR", "insights": {"views": 300, "likes": 20}},
            {"country": "US", "insights": {"views": 999, "likes": 99}},
        ])
        baseline = vs.compute_baseline_from_metrics(path, market="KR", metric="views")
        self.assertEqual(baseline["baseline_value"], 200.0)
        self.assertEqual(baseline["sample_size"], 2)
        self.assertEqual(baseline["source"], path)

    def test_baseline_fails_loudly_when_no_records(self):
        path = self._metrics([{"country": "US", "insights": {"views": 5}}])
        with self.assertRaises(vs.BaselineError):
            vs.compute_baseline_from_metrics(path, market="KR", metric="views")

    def test_baseline_fails_loudly_when_file_missing(self):
        with self.assertRaises(vs.BaselineError):
            vs.compute_baseline_from_metrics("/nonexistent/metrics.jsonl",
                                             market="KR", metric="views")

    def test_hand_typed_baseline_rejected(self):
        with self.assertRaises(vs.BaselineError):
            vs.assert_measured_baseline({"metric": "views", "baseline_value": 1000,
                                         "pattern_value": 2000, "sample_size": 3,
                                         "source": "운영자 추정", "compared_at": "2026-08-28"})

    def test_measured_baseline_accepted(self):
        path = self._metrics([{"country": "KR", "insights": {"views": 100}}])
        vs.assert_measured_baseline(
            vs.compute_baseline_from_metrics(path, market="KR", metric="views"))


# ---------------------------------------------------------------------------
# 프롬프트 페이로드 — 모델에게 근거 외의 것을 주지 않는다
# ---------------------------------------------------------------------------


class TestPayloadGrounding(unittest.TestCase):

    def test_payload_carries_evidence_ids_and_market(self):
        model = _model(_kr_response(2))
        vs.generate_storyboard(CFG, evidence=_evidence("KR"), market="KR",
                               run_id="run-1", content_draft_id="draft-1",
                               viral_pattern_ids=["vp-1"], model=model)
        payload = model.calls[0]["payload"]
        self.assertEqual(payload["market"], "KR")
        self.assertEqual(payload["cut_count"], 2)
        self.assertEqual(payload["cut_duration_seconds"], 5)
        self.assertEqual(sorted(payload["evidence"]), ["ev1", "ev2", "ev3"])
        self.assertIn("쿠팡 파트너스", payload["disclosure"]["text"])

    def test_offline_by_default_requires_model_seam(self):
        # 시임 없이 부르면 실제 네트워크 경로를 타므로, 테스트는 항상 시임을 준다.
        self.assertTrue(callable(vs._default_model))


if __name__ == "__main__":
    unittest.main(verbosity=2)
