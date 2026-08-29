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
    cuts = [_kr_cut(i + 1, quotes[i], f"ev{i + 1}", actions[i], benefits[i])
            for i in range(n)]
    # task 28 — 3컷 아크의 1번은 Ken-Burns 정지 컷이다. 정지 사진에는 네이티브
    # 오디오가 없으므로 발화를 배정하지 않는다 (스토리보드가 강제한다).
    for cut, kind in zip(cuts, vs.cut_kinds_for(n)):
        if kind == vs.CUT_KIND_STILL:
            cut["voice_line"] = ""
    return {"cuts": cuts}



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

    # --- 유도 재계산: 손으로 적은 숫자는 실존 파일을 가리켜도 통과 못 한다 ---

    def test_hand_typed_numbers_with_real_source_file_rejected(self):
        """실존 metrics 파일을 source 로 적어도 숫자가 그 파일에서 안 나오면 거부.

        모든 필수 키를 채운 완전한 dict 다 — 빈 필드 검사로 우연히 죽지 않고,
        오직 재집계 불일치로만 죽어야 한다 (비-공허 테스트).
        """
        path = self._metrics([{"country": "KR", "insights": {"views": 100}}])
        with self.assertRaises(vs.BaselineError) as ctx:
            vs.assert_measured_baseline({
                "metric": "views", "baseline_value": 9999, "sample_size": 3,
                "pattern_value": 12345.0,
                "source": path, "compared_at": "2026-08-28",
                "derivation": {"method": "mean", "market": "KR",
                               "metric": "views"},
            })
        self.assertIn("재집계", str(ctx.exception),
                      "빈 필드/경로 검사로 우연히 죽었을 뿐, 재집계 검증이 없다")

    def test_baseline_without_derivation_rejected(self):
        path = self._metrics([{"country": "KR", "insights": {"views": 100}}])
        with self.assertRaises(vs.BaselineError):
            vs.assert_measured_baseline({
                "metric": "views", "baseline_value": 100.0, "sample_size": 1,
                "pattern_value": None,
                "source": path, "compared_at": "2026-08-28",
            })

    def test_tampered_baseline_value_rejected(self):
        path = self._metrics([{"country": "KR", "insights": {"views": 100}},
                              {"country": "KR", "insights": {"views": 300}}])
        baseline = vs.compute_baseline_from_metrics(path, market="KR")
        baseline["baseline_value"] = 250.0
        with self.assertRaises(vs.BaselineError):
            vs.assert_measured_baseline(baseline)

    def test_tampered_sample_size_rejected(self):
        path = self._metrics([{"country": "KR", "insights": {"views": 100}},
                              {"country": "KR", "insights": {"views": 300}}])
        baseline = vs.compute_baseline_from_metrics(path, market="KR")
        baseline["sample_size"] = 50
        with self.assertRaises(vs.BaselineError):
            vs.assert_measured_baseline(baseline)

    def test_derivation_market_swap_rejected(self):
        """derivation.market 을 바꾸면 재집계 결과가 달라져 거부된다."""
        path = self._metrics([{"country": "KR", "insights": {"views": 100}},
                              {"country": "US", "insights": {"views": 900}}])
        baseline = vs.compute_baseline_from_metrics(path, market="KR")
        baseline["derivation"] = dict(baseline["derivation"], market="US")
        with self.assertRaises(vs.BaselineError):
            vs.assert_measured_baseline(baseline)

    # --- pattern_value 를 지어내지 않는다 ---

    def test_pattern_value_is_not_synthesised(self):
        path = self._metrics([{"country": "KR", "insights": {"views": 100}},
                              {"country": "KR", "insights": {"views": 300}}])
        baseline = vs.compute_baseline_from_metrics(path, market="KR")
        self.assertIsNone(baseline["pattern_value"],
                          "측정되지 않은 pattern_value 를 기준선 평균으로 지어냈다")

    def test_supplied_pattern_value_is_kept(self):
        path = self._metrics([{"country": "KR", "insights": {"views": 100}}])
        baseline = vs.compute_baseline_from_metrics(path, market="KR",
                                                    pattern_value=777.0)
        self.assertEqual(baseline["pattern_value"], 777.0)
        vs.assert_measured_baseline(baseline)

    def test_non_numeric_pattern_value_rejected(self):
        path = self._metrics([{"country": "KR", "insights": {"views": 100}}])
        baseline = vs.compute_baseline_from_metrics(path, market="KR")
        baseline["pattern_value"] = "많이"
        with self.assertRaises(vs.BaselineError):
            vs.assert_measured_baseline(baseline)


# ---------------------------------------------------------------------------
# 언어 게이트는 시장에 노출되는 모든 텍스트 필드를 덮는다
# ---------------------------------------------------------------------------


class TestLanguageGateCoversEveryField(unittest.TestCase):

    def _reject(self, field, value):
        resp = _kr_response(2)
        resp["cuts"][0][field] = value
        with self.assertRaises(vs.MarketLanguageError):
            _generate(resp)

    def test_english_action_in_kr_market_rejected(self):
        self._reject("action", "a hand places the product on the floor")

    def test_english_benefit_in_kr_market_rejected(self):
        self._reject("benefit", "quieter evenings for the neighbours")

    def test_english_first_frame_prompt_in_kr_market_rejected(self):
        self._reject("first_frame_prompt",
                     "vertical 9:16 frame of one product on a wooden floor")

    def test_english_motion_prompt_in_kr_market_rejected(self):
        self._reject("motion_prompt", "the camera tilts down slowly")

    def test_korean_action_in_us_market_rejected(self):
        resp = _us_response(2)
        resp["cuts"][0]["action"] = "손이 병을 집는다"
        with self.assertRaises(vs.MarketLanguageError):
            _generate(resp, market="US", evidence=_evidence("US"))

    def test_korean_first_frame_prompt_in_us_market_rejected(self):
        resp = _us_response(2)
        resp["cuts"][0]["first_frame_prompt"] = "세로 9:16 화면, 병 하나"
        with self.assertRaises(vs.MarketLanguageError):
            _generate(resp, market="US", evidence=_evidence("US"))


# ---------------------------------------------------------------------------
# 금지 표현 스캔은 이미지·영상 모델에 도달하는 필드까지 덮는다
# ---------------------------------------------------------------------------


class TestForbiddenClaimCoversEveryField(unittest.TestCase):

    def _reject(self, field, value):
        resp = _kr_response(2)
        resp["cuts"][0][field] = value
        with self.assertRaises(vs.ForbiddenClaimError):
            _generate(resp)

    def test_efficacy_in_action_rejected(self):
        self._reject("action", "성장 촉진 자세를 취한다")

    def test_efficacy_in_first_frame_prompt_rejected(self):
        """효능 암시를 그림으로 렌더링하는 경로도 막힌다."""
        self._reject("first_frame_prompt",
                     "세로 9:16 화면, 키 크는 성장 그래프가 벽에 걸린 한 장면")

    def test_efficacy_in_motion_prompt_rejected(self):
        self._reject("motion_prompt", "카메라가 올라가며 키가 커지는 모습을 담는다")

    def test_medical_framing_in_us_prompt_rejected(self):
        resp = _us_response(2)
        resp["cuts"][0]["first_frame_prompt"] = (
            "vertical 9:16 frame of a clinically proven growth chart")
        with self.assertRaises(vs.ForbiddenClaimError):
            _generate(resp, market="US", evidence=_evidence("US"))


# ---------------------------------------------------------------------------
# 근거 항목 형태 · 설정 배선
# ---------------------------------------------------------------------------


class TestEvidenceEntryShape(unittest.TestCase):

    def test_provenance_entry_without_source_url_fails_loudly(self):
        ev = _evidence("KR")
        ev.provenance[0] = {"quote": KR_QUOTE_1, "original_location": "spec row 1"}
        with self.assertRaises(vc.ContractError):
            _generate(_kr_response(2), evidence=ev)


class TestContractProjectionIsValidationOnly(unittest.TestCase):

    def test_projection_docstring_warns_it_is_not_a_handoff_type(self):
        doc = vs.GroundedStoryboard.as_contract_storyboard.__doc__ or ""
        self.assertIn("검증 전용", doc)


class TestConfigIsHonoured(unittest.TestCase):

    def test_default_complexity_from_config_is_used(self):
        cfg = dict(CFG, video_storyboard={"default_complexity": "complex"})
        sb = vs.generate_storyboard(cfg, evidence=_evidence("KR"), market="KR",
                                    run_id="run-1", content_draft_id="draft-1",
                                    viral_pattern_ids=["vp-1"],
                                    model=_model(_kr_response(3)))
        self.assertEqual(len(sb.cuts), 3)
        self.assertEqual(sb.total_duration_seconds(), 15)

    def test_explicit_complexity_overrides_config(self):
        cfg = dict(CFG, video_storyboard={"default_complexity": "complex"})
        sb = vs.generate_storyboard(cfg, evidence=_evidence("KR"), market="KR",
                                    run_id="run-1", content_draft_id="draft-1",
                                    viral_pattern_ids=["vp-1"],
                                    complexity="simple",
                                    model=_model(_kr_response(1)))
        self.assertEqual(len(sb.cuts), 1)

    def test_bad_config_complexity_fails_loudly(self):
        cfg = dict(CFG, video_storyboard={"default_complexity": "epic"})
        with self.assertRaises(vs.StoryboardError):
            vs.generate_storyboard(cfg, evidence=_evidence("KR"), market="KR",
                                   run_id="run-1", content_draft_id="draft-1",
                                   viral_pattern_ids=["vp-1"],
                                   model=_model(_kr_response(2)))

    def test_absent_config_block_keeps_standard_default(self):
        sb = _generate(_kr_response(2))
        self.assertEqual(sb.complexity, vs.DEFAULT_COMPLEXITY)
        self.assertEqual(len(sb.cuts), 2)

    def test_metrics_path_from_config_is_used(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8")
        fh.write(json.dumps({"country": "KR", "insights": {"views": 100}}) + "\n")
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        cfg = dict(CFG, video_storyboard={"metrics_path": fh.name})
        baseline = vs.compute_baseline_from_cfg(cfg, market="KR")
        self.assertEqual(baseline["source"], fh.name)
        vs.assert_measured_baseline(baseline)

    def test_missing_metrics_path_config_fails_loudly(self):
        with self.assertRaises(vs.StoryboardError):
            vs.compute_baseline_from_cfg(CFG, market="KR")


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
        # cfg 는 클로저로 묶인다 — 함수 속성에 전역 상태로 얹지 않는다 (재진입 안전).
        self.assertTrue(callable(vs._default_model))
        caller = vs._default_model(CFG)
        self.assertTrue(callable(caller))
        self.assertFalse(hasattr(vs._default_model, "cfg"),
                         "cfg 가 함수 속성(모듈 전역 가변 상태)으로 저장됐다")


# ---------------------------------------------------------------------------
# 스토리 · 발화 주도 생성 프롬프트 (H3 Max 네이티브 오디오)
#
# 2026-08-29 유료 1건 반려의 재발 방지선. 그때 motion_prompt 가 순수 카메라
# 지시("천천히 밀고 들어간다")뿐이어서 모델이 무음 클로즈업을 만들었고
# 측정 -91.0 dB, spoken_content 가 빈 전사로 실패했다. H3 Max 는 네이티브
# 오디오·립싱크를 생성하므로 **말하라고 시키지 않은 것**이 결함이었다.
# ---------------------------------------------------------------------------


class TestStoryDrivenGenerationPrompt(unittest.TestCase):

    def _cut(self, market="KR", n=1):
        resp = _kr_response(n) if market == "KR" else _us_response(n)
        return _generate(resp, market=market, complexity="simple"
                         if n == 1 else "standard").cuts[0]

    # -- 존재와 전달 --------------------------------------------------------

    def test_cut_exposes_generation_prompt(self):
        cut = self._cut()
        self.assertTrue(cut.generation_prompt.strip())

    def test_generation_prompt_is_in_handoff_dict(self):
        cut = self._cut()
        self.assertEqual(cut.to_dict()["generation_prompt"],
                         cut.generation_prompt)

    # -- H3 Max 문서화 구조 -------------------------------------------------

    def test_prompt_uses_documented_h3_field_order(self):
        p = self._cut().generation_prompt
        i = p.index("integrated_multimodal_description:")
        s = p.index("overall_soundscape:")
        m = p.index("non_diegetic_music:")
        self.assertLess(i, s)
        self.assertLess(s, m)

    def test_prompt_opens_shot_one_and_never_cuts(self):
        p = self._cut().generation_prompt
        self.assertIn("[Shot 1]", p)
        self.assertNotIn("[Shot 2]", p)

    def test_no_background_music_is_requested(self):
        self.assertIn("non_diegetic_music: N/A", self._cut().generation_prompt)

    # -- 발화가 실제로 실려 나가는가 ---------------------------------------

    def test_approved_voice_line_is_carried_verbatim(self):
        cut = self._cut()
        self.assertIn(cut.voice_line, cut.generation_prompt)

    def test_spoken_line_is_bounded_by_dialogue_delimiters(self):
        cut = self._cut()
        spoken = vs.spoken_segments(cut.generation_prompt)
        self.assertEqual(spoken, [cut.voice_line])

    def test_dialogue_language_tag_matches_market(self):
        self.assertIn("<d>[Korean]", self._cut("KR").generation_prompt)
        self.assertIn("<d>[English]", self._cut("US", 2).generation_prompt)

    def test_prompt_forbids_ad_libbing_beyond_the_approved_line(self):
        # MAX_UNAPPROVED_CHARS = 1 이므로 한 단어만 더 붙어도 QA 가 떨어진다.
        p = self._cut().generation_prompt
        self.assertIn("exactly these words and no others", p)

    def test_delivery_direction_sits_outside_the_dialogue_tag(self):
        cut = self._cut()
        head = cut.generation_prompt.split("<d>")[0]
        self.assertIn("voice", head)

    # -- 스토리·시연이지 정물 클로즈업이 아니다 -----------------------------

    def test_prompt_carries_the_demonstration_action(self):
        cut = self._cut()
        self.assertIn(cut.action, cut.generation_prompt)

    def test_prompt_names_a_speaking_person(self):
        self.assertIn("(S1)", self._cut().generation_prompt)

    def test_camera_only_motion_prompt_is_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["motion_prompt"] = "카메라가 제품을 향해 천천히 밀고 들어간다"
        with self.assertRaises(vs.SilentCutError):
            _generate(resp)

    def test_us_camera_only_motion_prompt_is_rejected(self):
        resp = _us_response(2)
        resp["cuts"][0]["motion_prompt"] = ("slow subtle handheld push-in on the "
                                            "carton, holding steady")
        with self.assertRaises(vs.SilentCutError):
            _generate(resp, market="US")

    # -- 자막 금지 (자막은 후반 작업 패스다) --------------------------------

    def test_generated_prompt_requests_no_on_screen_text(self):
        p = self._cut().generation_prompt
        self.assertIn("No on-screen text", p)
        for banned in ("subtitle", "caption", "lower third"):
            self.assertNotIn(f"add {banned}", p.lower())

    def test_model_requested_subtitles_are_rejected(self):
        resp = _kr_response(2)
        resp["cuts"][0]["motion_prompt"] = "손이 제품을 들고 화면에 자막이 떠오른다"
        with self.assertRaises(vs.OnScreenTextError):
            _generate(resp)

    def test_model_requested_caption_in_first_frame_is_rejected(self):
        resp = _us_response(2)
        resp["cuts"][0]["first_frame_prompt"] = (
            "vertical 9:16 frame, one bottle with a bold caption overlay")
        with self.assertRaises(vs.OnScreenTextError):
            _generate(resp, market="US")

    # -- 게이트는 파생 프롬프트에도 그대로 걸린다 ---------------------------

    def test_derived_prompt_is_covered_by_the_forbidden_scan(self):
        self.assertIn("generation_prompt", vs.FORBIDDEN_SCAN_TEXT_FIELDS)
        self.assertIn("generation_prompt", vs.MARKET_FACING_TEXT_FIELDS)

    def test_original_six_scanned_fields_are_still_scanned(self):
        for name in ("action", "benefit", "claim", "voice_line",
                     "first_frame_prompt", "motion_prompt"):
            self.assertIn(name, vs.FORBIDDEN_SCAN_TEXT_FIELDS)

    def test_kr_prompt_has_no_latin_only_body(self):
        # 필드명은 H3 규격이라 영문이지만, 대사와 동작은 한국어여야 한다.
        cut = self._cut("KR")
        self.assertRegex(cut.generation_prompt, r"[\uac00-\ud7a3]")

    def test_us_prompt_has_no_hangul(self):
        cut = self._cut("US", 2)
        self.assertNotRegex(cut.generation_prompt, r"[\uac00-\ud7a3]")

    # -- viral_ugc 패턴 원장에 근거한 컷 역할 -------------------------------

    def test_cut_roles_come_from_the_pattern_ledger_grammar(self):
        import viral_ugc
        for axis in vs.CUT_ROLE_GRAMMAR_AXES:
            self.assertIn(axis, viral_ugc.GRAMMAR_FIELDS)

    def test_three_cut_board_uses_hero_demo_proof_arc(self):
        """task 28 — 훅 자리를 Ken-Burns 제품 히어로가 가져갔다.

        제품 **식별**은 원본 사진(무료·위조 불가)이 하고, **시연**은 유료
        모션 컷 두 개가 한다.
        """
        sb = _generate(_kr_response(3), complexity="complex")
        roles = [c.story_role for c in sb.cuts]
        self.assertEqual(roles, ["product_hero", "demo_action", "proof_moment"])
        kinds = [c.cut_kind for c in sb.cuts]
        self.assertEqual(kinds, [vs.CUT_KIND_STILL, vs.CUT_KIND_MOTION,
                                 vs.CUT_KIND_MOTION])

    def test_single_cut_is_a_self_contained_demo(self):
        sb = _generate(_kr_response(1), complexity="simple")
        self.assertEqual(sb.cuts[0].story_role, "demo_action")


class TestVoiceLineIsJudgedByEvidenceNotSubstring(unittest.TestCase):
    """발화는 **근거에 어긋나지 않는가**로 판정한다 — 글자 포함이 아니라.

    옛 규칙은 ``claim`` 이 ``voice_line`` 의 리터럴 부분문자열일 것을 요구했다.
    그런데 ``claim`` 은 스펙시트 조각(``manufacturer audience: age 1+``)이라
    콜론과 ``+`` 때문에 **어떤 자연스러운 영어 문장도 통과할 수 없었다** —
    통과 가능한 발화가 존재하지 않는 게이트였다. 지켜야 할 성질은 글자
    일치가 아니라 "근거가 뒷받침하지 않는 것을 말하지 않는다"다.
    """

    Q_AGE = "manufacturer audience: age 1+"
    Q_IU = "600 IU vitamin D3 per labeled drop"

    def test_unsayable_spec_fragment_can_now_be_spoken_naturally(self):
        """(a) 실제로 막혔던 케이스 — 자연스러운 문장이 통과해야 한다."""
        vs._assert_voice_line_supported(
            "The manufacturer lists this for age 1 and up.",
            self.Q_AGE, "cuts[1].voice_line")

    def test_altered_dose_is_still_rejected(self):
        """(b) 600 IU 를 60 IU 로 바꾸면 반드시 죽는다 — 영양표시다."""
        with self.assertRaises(vs.EvidenceError):
            vs._assert_voice_line_supported(
                "This gives 60 IU vitamin D3 per labeled drop.",
                self.Q_IU, "cuts[1].voice_line")

    def test_invented_number_is_rejected(self):
        with self.assertRaises(vs.EvidenceError):
            vs._assert_voice_line_supported(
                "It is 600 IU vitamin D3 in each of the 250 drops.",
                self.Q_IU, "cuts[1].voice_line")

    def test_unevidenced_claim_is_rejected(self):
        """(c) 근거에 없는 새 사실을 얹으면 죽는다."""
        with self.assertRaises(vs.EvidenceError):
            vs._assert_voice_line_supported(
                "It is 600 IU vitamin D3 per labeled drop, certified organic "
                "by the USDA and clinically tested in Sweden.",
                self.Q_IU, "cuts[1].voice_line")

    def test_faithful_numbers_pass(self):
        vs._assert_voice_line_supported(
            "This provides 600 IU vitamin D3 per labeled drop, right here.",
            self.Q_IU, "cuts[1].voice_line")

    def test_unit_change_is_rejected(self):
        """단위를 바꾸는 것도 숫자를 바꾸는 것과 같다."""
        with self.assertRaises(vs.EvidenceError):
            vs._assert_voice_line_supported(
                "This is 600 mg vitamin D3 per labeled drop.",
                self.Q_IU, "cuts[1].voice_line")

    def test_empty_voice_line_is_rejected(self):
        with self.assertRaises(vs.EvidenceError):
            vs._assert_voice_line_supported("   ", self.Q_IU, "where")

    def test_generate_storyboard_accepts_natural_age_phrasing(self):
        """게이트 수정이 실제 생성 경로에서도 성립한다."""
        ev = vc.ProductEvidence(
            product_id="p-us", market="US",
            source_urls=["https://example.com/p"],
            source_sha256=["a" * 64],
            rights={"basis": "official page", "holder": "brand",
                    "source_url": "https://example.com/p",
                    "captured_at": "2026-08-28T00:00:00+09:00"},
            provenance=[{"quote": self.Q_AGE,
                         "source_url": "https://example.com/p",
                         "original_location": "spec table row 1"}],
            captured_at="2026-08-28T00:00:00+09:00").validate()

        def model(system_prompt, payload):
            eid = sorted(payload["evidence"])[0]
            return {"cuts": [{
                "index": 1, "duration_seconds": 5,
                "action": "A parent turns the carton to the lens",
                "benefit": "Clear age guidance on the pack",
                "claim": self.Q_AGE, "evidence_id": eid,
                "voice_line": "The manufacturer lists this for age 1 and up.",
                "first_frame_prompt": ("Vertical 9:16 still of a parent in a "
                                       "kitchen holding a small carton"),
                "motion_prompt": ("The parent turns the carton toward the lens "
                                  "and speaks to camera"),
            }]}

        sb = vs.generate_storyboard(
            {}, ev, "US", "run-1", "draft-1", ["vp-1"],
            complexity="simple", model=model)
        self.assertIn("age 1 and up", sb.cuts[0].voice_line)



# ---------------------------------------------------------------------------
# 타이트 프레이밍 (task 26 실측) — 큰 글자만 살아남는다
# ---------------------------------------------------------------------------


class TestTightFraming(unittest.TestCase):
    """task 28 — 타이트 프레이밍은 **버려졌다.** 27번이 반증했다.

    27번은 라벨을 크게 잡았고 1차 라벨은 좋아졌지만, 비어버린 라벨 면적을
    모델이 **날조된 Supplement Facts 패널**로 채웠다. 그래서 이제 유료
    컷에는 정반대를 지시한다: 라벨을 **읽을 수 없게** 하라. 브랜드 식별은
    생성되지 않는 Ken-Burns 정지 컷의 원본 픽셀이 맡는다.
    """

    def test_system_prompt_demands_illegible_label_on_paid_cuts(self):
        low = vs.SYSTEM_PROMPT.lower()
        self.assertIn("never be legible", low)
        self.assertIn("ken_burns", low)
        self.assertIn("supplement facts", low)
        # 낡은 지시가 남아 있으면 모델이 다시 라벨을 크게 그린다.
        self.assertNotIn("one third of the frame width", low)

    def test_proof_beat_no_longer_asks_for_printed_detail_to_be_read(self):
        beat = vs._ROLE_BEATS["proof_moment"].lower()
        self.assertNotIn("printed on-pack detail", beat)

    def test_generation_prompt_carries_the_illegibility_instruction(self):
        out = vs.build_generation_prompt(
            market="US", story_role="proof_moment",
            action="A parent holds the bottle up",
            voice_line="Each drop delivers 600 IU vitamin D3.",
            first_frame_prompt="A parent holds the amber bottle close to camera",
            motion_prompt="She steadies the bottle in front of her")
        low = out.lower()
        self.assertIn("never be legible", low)
        self.assertIn("out of focus", low)
        # 기존 반위조 조항은 그대로 살아 있어야 한다
        self.assertIn("no invented packaging wording", low)
        # 물리적 개연성 조항(84a2952)도 유지된다
        self.assertIn("cannot release a drop", low)
        # 낡은 "크게 잡아라" 지시가 살아 있으면 안 된다
        self.assertNotIn("one third of the frame width", low)


class TestKenBurnsStillCuts(unittest.TestCase):
    """정지 컷은 **생성되지 않는다** — 위조가 구조적으로 불가능하다."""

    def test_three_cut_arc_has_exactly_one_still_and_two_paid(self):
        kinds = vs.cut_kinds_for(3)
        self.assertEqual(kinds[0], vs.CUT_KIND_STILL)
        self.assertEqual(sum(1 for k in kinds if k == vs.CUT_KIND_MOTION), 2)
        self.assertLessEqual(
            sum(1 for k in kinds if k == vs.CUT_KIND_MOTION),
            vs.MAX_PAID_MOTION_CUTS)

    def test_still_role_cannot_produce_a_paid_prompt(self):
        """정지 전용 역할로는 fal 프롬프트를 **만들 수조차 없다**."""
        with self.assertRaises(vs.StoryboardError):
            vs.build_generation_prompt(
                market="US", story_role="product_hero",
                action="hold", voice_line="hi",
                first_frame_prompt="a bottle", motion_prompt="slow push in")

    def test_still_cut_gets_no_generation_prompt(self):
        sb = _generate(_kr_response(3), complexity="complex")
        still = [c for c in sb.cuts if c.cut_kind == vs.CUT_KIND_STILL]
        self.assertEqual(len(still), 1)
        self.assertEqual(still[0].generation_prompt, "")
        self.assertFalse(still[0].is_paid())
        self.assertEqual(still[0].voice_line, "")
        self.assertIsNotNone(still[0].still_plan)

    def test_voice_line_on_a_still_cut_is_rejected_not_dropped(self):
        """승인 대사를 조용히 버리지 않는다 — 배정 자체를 거부한다."""
        payload = _kr_response(3)
        payload["cuts"][0]["voice_line"] = "이건 절대 들리지 않는 대사입니다"
        with self.assertRaises(vs.ModelOutputError):
            _generate(payload, complexity="complex")

    def test_every_motion_cut_still_speaks(self):
        sb = _generate(_kr_response(3), complexity="complex")
        for cut in sb.cuts:
            if cut.is_paid():
                self.assertTrue(cut.voice_line)
                self.assertIn(cut.voice_line, cut.generation_prompt)




if __name__ == "__main__":
    unittest.main(verbosity=2)
