#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""product_fidelity 회귀 — 전부 오프라인. 네트워크를 절대 건드리지 않는다.

원칙은 video_qa 와 같다: **답하지 못한 검사는 통과가 아니다.**
그래서 이 파일의 대부분은 '막아야 하는 케이스'다.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import product_fidelity as pf


# --- 픽스처 --------------------------------------------------------------

PNG_1x1 = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    b"IQAAAABJRU5ErkJggg==")
JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64 + b"\xff\xd9"


def _good_payload(**over):
    base = {
        "on_pack_text": [
            {"read": "ORGANIC", "expected": "ORGANIC", "status": "faithful"}],
        "geometry_findings": [],
        "colour_findings": [],
        "missing_or_invented": [],
        "legibility": "legible",
        "verdict": "pass",
        "confidence": 0.92,
        "notes": "matches reference",
    }
    base.update(over)
    return json.dumps(base)


class _Client:
    """주입 시임 — 호출을 기록하고 미리 정한 문자열을 돌려준다."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, prompt, images):
        self.calls.append({"prompt": prompt, "images": list(images)})
        reply = self.replies.pop(0) if self.replies else self.replies
        if isinstance(reply, Exception):
            raise reply
        return reply


class _TempImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.frame = os.path.join(self.tmp.name, "frame.png")
        with open(self.frame, "wb") as fh:
            fh.write(PNG_1x1)
        self.ref_jpeg = os.path.join(self.tmp.name, "ref.jpeg")
        with open(self.ref_jpeg, "wb") as fh:
            fh.write(JPEG_MAGIC)

    def tearDown(self):
        self.tmp.cleanup()


# --- 이미지 인코딩: JPEG 를 반드시 받아야 한다 (기존 PNG 전용 버그) ------

class TestEncoding(_TempImages):
    def test_jpeg_reference_is_accepted(self):
        """스테이징된 상품 사진은 전부 JPEG 다. 거부하면 검사 자체가 못 돈다."""
        url = pf.encode_image_data_url(self.ref_jpeg)
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))

    def test_png_frame_is_accepted(self):
        url = pf.encode_image_data_url(self.frame)
        self.assertTrue(url.startswith("data:image/png;base64,"))

    def test_unknown_format_fails_closed(self):
        bogus = os.path.join(self.tmp.name, "x.bin")
        with open(bogus, "wb") as fh:
            fh.write(b"NOTANIMAGE")
        with self.assertRaises(pf.FidelityUnavailable):
            pf.encode_image_data_url(bogus)

    def test_missing_file_fails_closed(self):
        with self.assertRaises(pf.FidelityUnavailable):
            pf.encode_image_data_url(os.path.join(self.tmp.name, "nope.png"))

    def test_empty_file_fails_closed(self):
        empty = os.path.join(self.tmp.name, "empty.png")
        open(empty, "wb").close()
        with self.assertRaises(pf.FidelityUnavailable):
            pf.encode_image_data_url(empty)


# --- 응답 파싱: 못 읽으면 실패 ------------------------------------------

class TestParsing(unittest.TestCase):
    def test_parses_plain_json(self):
        v = pf.parse_verdict(_good_payload())
        self.assertEqual(v["verdict"], "pass")
        self.assertAlmostEqual(v["confidence"], 0.92)

    def test_parses_fenced_json(self):
        v = pf.parse_verdict("```json\n" + _good_payload() + "\n```")
        self.assertEqual(v["verdict"], "pass")

    def test_unparseable_raises(self):
        with self.assertRaises(pf.FidelityUnavailable):
            pf.parse_verdict("I think the bottle looks fine!")

    def test_missing_required_key_raises(self):
        payload = json.loads(_good_payload())
        del payload["verdict"]
        with self.assertRaises(pf.FidelityUnavailable):
            pf.parse_verdict(json.dumps(payload))

    def test_non_numeric_confidence_raises(self):
        with self.assertRaises(pf.FidelityUnavailable):
            pf.parse_verdict(_good_payload(confidence="high"))

    def test_out_of_range_confidence_raises(self):
        with self.assertRaises(pf.FidelityUnavailable):
            pf.parse_verdict(_good_payload(confidence=4.0))

    def test_unknown_verdict_value_raises(self):
        with self.assertRaises(pf.FidelityUnavailable):
            pf.parse_verdict(_good_payload(verdict="maybe"))

    def test_empty_response_raises(self):
        with self.assertRaises(pf.FidelityUnavailable):
            pf.parse_verdict("")


# --- 단일 프레임 판정 ----------------------------------------------------

class TestCheckFrame(_TempImages):
    def test_clean_frame_passes(self):
        client = _Client(_good_payload())
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertTrue(r["passed"])
        self.assertEqual(len(client.calls), 1)

    def test_prompt_carries_known_wording_and_inversion_allowance(self):
        """모델이 무엇과 대조해야 하는지 모르면 'ORCAIN' 을 못 잡는다.
        그리고 정당한 뒤집힘을 결함으로 신고하면 안 된다."""
        client = _Client(_good_payload())
        pf.check_frame(self.frame, [self.ref_jpeg], client=client,
                       known_wording=["ORGANIC", "Ddrops"])
        prompt = client.calls[0]["prompt"]
        self.assertIn("ORGANIC", prompt)
        self.assertIn("Ddrops", prompt)
        self.assertIn("invert", prompt.lower())

    def test_frame_and_every_reference_are_sent(self):
        client = _Client(_good_payload())
        pf.check_frame(self.frame, [self.ref_jpeg, self.ref_jpeg], client=client)
        self.assertEqual(len(client.calls[0]["images"]), 3)

    def test_forged_text_fails(self):
        client = _Client(_good_payload(
            verdict="fail",
            on_pack_text=[{"read": "ORCAIN", "expected": "ORGANIC",
                           "status": "forged"}]))
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertFalse(r["passed"])
        self.assertIn("ORCAIN", json.dumps(r, ensure_ascii=False))

    def test_forged_finding_overrides_a_pass_verdict(self):
        """모델이 결함을 나열하고도 pass 라고 말하면 결함을 믿는다."""
        client = _Client(_good_payload(
            verdict="pass", confidence=0.95,
            on_pack_text=[{"read": "ORCAIN", "expected": "ORGANIC",
                           "status": "forged"}]))
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertFalse(r["passed"])

    def test_geometry_finding_fails(self):
        client = _Client(_good_payload(
            verdict="fail",
            geometry_findings=["two necks: a second threaded orifice at bottom"]))
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertFalse(r["passed"])

    def test_low_confidence_fails_closed(self):
        client = _Client(_good_payload(confidence=0.2))
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertFalse(r["passed"])
        self.assertIn("confidence", r["reason"].lower())

    def test_unreachable_model_fails_closed(self):
        client = _Client(RuntimeError("connection refused"))
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertFalse(r["passed"])
        self.assertIn("connection refused", r["reason"])

    def test_unparseable_response_fails_closed(self):
        client = _Client("the bottle looks great to me")
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertFalse(r["passed"])

    def test_no_references_fails_closed(self):
        client = _Client(_good_payload())
        r = pf.check_frame(self.frame, [], client=client)
        self.assertFalse(r["passed"])
        self.assertEqual(client.calls, [])

    def test_illegible_label_reported_separately_not_as_a_defect(self):
        """초점이 나간 라벨은 위조가 아니다 — 통과시키되 별도로 신고한다."""
        client = _Client(_good_payload(legibility="unreadable"))
        r = pf.check_frame(self.frame, [self.ref_jpeg], client=client)
        self.assertTrue(r["passed"])
        self.assertTrue(r["brand_illegible"])


# --- 다중 프레임 / 호출 예산 --------------------------------------------

class TestCheckFrames(_TempImages):
    def test_any_failing_frame_fails_the_set(self):
        client = _Client(_good_payload(), _good_payload(verdict="fail"))
        r = pf.check_frames([self.frame, self.frame], [self.ref_jpeg],
                            client=client)
        self.assertFalse(r["passed"])

    def test_call_budget_is_bounded_and_configurable(self):
        client = _Client(*[_good_payload()] * 10)
        r = pf.check_frames([self.frame] * 8, [self.ref_jpeg], client=client,
                            max_calls=3)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(r["calls"], 3)

    def test_zero_frames_fails_closed(self):
        client = _Client(_good_payload())
        r = pf.check_frames([], [self.ref_jpeg], client=client)
        self.assertFalse(r["passed"])

    def test_reports_per_frame_verdicts(self):
        client = _Client(_good_payload(), _good_payload())
        r = pf.check_frames([self.frame, self.frame], [self.ref_jpeg],
                            client=client)
        self.assertTrue(r["passed"])
        self.assertEqual(len(r["frames"]), 2)


# --- 레퍼런스 자산 선택: JPEG 를 반드시 집어야 한다 ---------------------

class TestReferenceSelection(_TempImages):
    def test_picks_jpeg_assets_from_a_staged_dir(self):
        d = os.path.join(self.tmp.name, "assets")
        os.makedirs(d)
        for name in ("a-5a20031e25c5ea4c.jpeg", "b-db5257e0250b0e4b.jpeg"):
            with open(os.path.join(d, name), "wb") as fh:
                fh.write(JPEG_MAGIC)
        with open(os.path.join(d, "product_assets.json"), "w") as fh:
            fh.write("{}")
        picked = pf.reference_photos(d)
        self.assertEqual(len(picked), 2)
        self.assertTrue(all(p.endswith(".jpeg") for p in picked))

    def test_missing_dir_returns_empty_not_raise(self):
        self.assertEqual(pf.reference_photos(
            os.path.join(self.tmp.name, "nope")), [])

    def test_reference_count_is_bounded(self):
        d = os.path.join(self.tmp.name, "many")
        os.makedirs(d)
        for i in range(9):
            with open(os.path.join(d, f"r{i}.jpeg"), "wb") as fh:
                fh.write(JPEG_MAGIC)
        self.assertLessEqual(len(pf.reference_photos(d, limit=3)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
