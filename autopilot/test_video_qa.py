#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video_qa 회귀 — 발행 직전 마지막 게이트.

이 테스트는 **네트워크를 타지 않고 ffmpeg/whisper 도 부르지 않는다.**
프레임 샘플러·전사기·오디오 프로브는 전부 주입 시임이며, mp4 와 PNG 는
이 파일에서 바이트로 직접 조립한다 — 그래야 게이트가 '선언된 값을 믿는'
게 아니라 실제 파일을 읽는다는 걸 증명할 수 있다.

중점은 **막아야 하는 케이스**다. 통과 케이스 하나보다 거절 케이스 열이 중요하다.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import video_contracts as vc  # noqa: E402
import video_qa as vq  # noqa: E402
from test_video_compose import build_mp4  # noqa: E402
from video_storyboard import DISCLOSURE_TEXT  # noqa: E402


# ---------------------------------------------------------------------------
# 진짜 PNG 조립기 — 디코더가 실제로 zlib 를 풀어야 통과한다
# ---------------------------------------------------------------------------


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def build_png(rows) -> bytes:
    """rows: list[list[(r,g,b)]] → 8bit RGB non-interlaced PNG 바이트."""
    height = len(rows)
    width = len(rows[0])
    raw = b""
    for row in rows:
        raw += b"\x00"  # filter type 0 (None)
        for (r, g, b) in row:
            raw += bytes((r, g, b))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b""))


def gradient_rows(w=16, h=16, seed=0):
    """좌→우로 밝아지는 결정적 패턴. seed 로 형태를 바꾼다."""
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            v = (x * 255 // max(1, w - 1) + seed * 7 + y) % 256
            row.append((v, v, v))
        out.append(row)
    return out


def flat_rows(value, w=16, h=16):
    return [[(value, value, value)] * w for _ in range(h)]


def inverted_rows(w=16, h=16):
    """gradient 와 명암이 정반대 — 거친 스크리닝에서 크게 어긋난다."""
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            v = 255 - (x * 255 // max(1, w - 1))
            row.append((v, v, v))
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 주입 시임 스파이
# ---------------------------------------------------------------------------


class SamplerSpy:
    """요청된 타임스탬프마다 PNG 를 하나씩 써 주는 가짜 프레임 샘플러."""

    def __init__(self, rows_for=None, *, fail=None, short=False):
        self.rows_for = rows_for or (lambda i, ts: gradient_rows(seed=i + 1))
        self.fail = fail
        self.short = short
        self.calls = []

    def __call__(self, video_path, timestamps, out_dir):
        self.calls.append((video_path, tuple(timestamps), out_dir))
        if self.fail:
            raise self.fail
        os.makedirs(out_dir, exist_ok=True)
        stamps = list(timestamps)[:-1] if self.short else list(timestamps)
        frames = []
        for i, ts in enumerate(stamps):
            path = os.path.join(out_dir, f"f{i:03d}.png")
            with open(path, "wb") as fh:
                fh.write(build_png(self.rows_for(i, ts)))
            frames.append({"timestamp": ts, "path": path})
        return frames


class TranscriberSpy:
    def __init__(self, text="", *, fail=None, language="ko"):
        self.text = text
        self.fail = fail
        self.language = language
        self.calls = []

    def __call__(self, video_path):
        self.calls.append(video_path)
        if self.fail:
            raise self.fail
        return {"text": self.text, "language": self.language}


class AudioSpy:
    def __init__(self, rms_dbfs=-18.0, peak_dbfs=-3.0, *, fail=None):
        self.rms_dbfs = rms_dbfs
        self.peak_dbfs = peak_dbfs
        self.fail = fail
        self.calls = []

    def __call__(self, video_path):
        self.calls.append(video_path)
        if self.fail:
            raise self.fail
        return {"rms_dbfs": self.rms_dbfs, "peak_dbfs": self.peak_dbfs}


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

VOICE_1 = "밤에 잘 자는 습관부터 챙겨주세요"
VOICE_2 = "성분표는 이렇게 확인하면 됩니다"

CAPTION_KR = (
    "아이 키 고민할 때 제가 먼저 본 건 성분표였어요\n"
    + DISCLOSURE_TEXT["KR"]
)


def storyboard_dict(market="KR", voice_lines=(VOICE_1, VOICE_2)):
    return {
        "storyboard_id": "sb-1",
        "run_id": "run-1",
        "product_id": "prd-1",
        "market": market,
        "content_draft_id": "draft-1",
        "viral_pattern_ids": ["vp-1"],
        "complexity": "standard",
        "disclosure": {"market": market, "required": True,
                       "text": DISCLOSURE_TEXT[market],
                       "placement": "on_screen_and_caption"},
        "cuts": [
            {"index": i + 1, "duration_seconds": 5, "voice_line": line,
             "action": "a", "benefit": "b", "claim": "c",
             "evidence_id": "e1", "evidence_quote": "q",
             "evidence_source_url": "https://x/y",
             "first_frame_prompt": "p", "motion_prompt": "m"}
            for i, line in enumerate(voice_lines)
        ],
    }


class QABase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.video = os.path.join(self.dir, "out.mp4")
        self.write_video()
        self.product_image = os.path.join(self.dir, "product.png")
        with open(self.product_image, "wb") as fh:
            fh.write(build_png(gradient_rows(seed=1)))

    def write_video(self, **kw):
        kw.setdefault("duration_seconds", 10.0)
        with open(self.video, "wb") as fh:
            fh.write(build_mp4(**kw))

    def run_qa(self, **over):
        kw = dict(
            job_id="job-1", run_id="run-1",
            video_path=self.video,
            storyboard=storyboard_dict(),
            caption=CAPTION_KR,
            overlay_texts=[DISCLOSURE_TEXT["KR"]],
            product_image_path=self.product_image,
            frame_sampler=SamplerSpy(),
            transcriber=TranscriberSpy(VOICE_1 + " " + VOICE_2),
            audio_probe=AudioSpy(),
            workdir=os.path.join(self.dir, "qa"),
        )
        kw.update(over)
        return vq.run_qa(**kw)

    def assertFailed(self, report, check):
        self.assertFalse(report.passed, f"통과하면 안 된다: {report.checks}")
        self.assertIn(check, report.checks)
        self.assertFalse(report.checks[check]["passed"],
                         f"{check} 가 통과로 표시됐다: {report.checks[check]}")
        self.assertTrue(any(check in f for f in report.failures),
                        f"failures 에 {check} 근거가 없다: {report.failures}")
        report.validate()


# ---------------------------------------------------------------------------
# 1. 통과 케이스 — 게이트가 정상 산출물을 막지 않는다
# ---------------------------------------------------------------------------


class TestHappyPath(QABase):

    def test_known_good_passes_every_gate(self):
        report = self.run_qa()
        self.assertTrue(report.passed, report.failures)
        self.assertEqual(report.failures, [])
        report.validate()
        for name in vq.CHECK_NAMES:
            self.assertIn(name, report.checks)
            self.assertTrue(report.checks[name]["passed"], name)

    def test_report_is_a_contract_qareport(self):
        self.assertIsInstance(self.run_qa(), vc.QAReport)

    def test_samples_first_middle_transition_and_final_frames(self):
        sampler = SamplerSpy()
        self.run_qa(frame_sampler=sampler)
        stamps = sampler.calls[0][1]
        self.assertIn(0.0, stamps)                    # first
        self.assertTrue(any(abs(s - 5.0) < 1e-6 for s in stamps))   # transition
        self.assertTrue(any(4.5 <= s <= 5.5 for s in stamps))       # middle
        self.assertTrue(max(stamps) > 9.0)            # final

    def test_measures_the_real_file_not_the_declared_duration(self):
        # 선언은 10초인데 실제 파일은 7초 — 실측이 이긴다.
        self.write_video(duration_seconds=7.0)
        self.assertFailed(self.run_qa(), "technical_container")


# ---------------------------------------------------------------------------
# 2. 기술 검사 거절 경로
# ---------------------------------------------------------------------------


class TestTechnicalRefusals(QABase):

    def test_wrong_duration_fails(self):
        self.write_video(duration_seconds=12.0)
        self.assertFailed(self.run_qa(), "technical_container")

    def test_landscape_fails(self):
        self.write_video(width=1366, height=768)
        self.assertFailed(self.run_qa(), "technical_container")

    def test_non_768p_class_fails(self):
        self.write_video(width=1080, height=1920)
        self.assertFailed(self.run_qa(), "technical_container")

    def test_non_h264_video_codec_fails(self):
        self.write_video(video_codec=b"hvc1")
        self.assertFailed(self.run_qa(), "technical_container")

    def test_missing_audio_track_fails(self):
        self.write_video(audio_codec=None)
        self.assertFailed(self.run_qa(), "technical_container")

    def test_non_aac_audio_fails(self):
        self.write_video(audio_codec=b"opus")
        self.assertFailed(self.run_qa(), "technical_container")

    def test_silent_audio_fails(self):
        self.assertFailed(self.run_qa(audio_probe=AudioSpy(rms_dbfs=-90.0,
                                                           peak_dbfs=-88.0)),
                          "technical_audio_signal")

    def test_all_black_frames_fail(self):
        sampler = SamplerSpy(lambda i, ts: flat_rows(0))
        self.assertFailed(self.run_qa(frame_sampler=sampler), "technical_frames")

    def test_frozen_duplicate_frames_fail(self):
        sampler = SamplerSpy(lambda i, ts: gradient_rows(seed=3))
        self.assertFailed(self.run_qa(frame_sampler=sampler), "technical_frames")

    def test_missing_video_file_fails(self):
        os.unlink(self.video)
        self.assertFailed(self.run_qa(), "technical_container")


# ---------------------------------------------------------------------------
# 3. 검사 자체가 돌 수 없으면 실패한다 (FAIL CLOSED)
# ---------------------------------------------------------------------------


class TestFailClosed(QABase):

    def test_frame_sampler_unavailable_fails_not_passes(self):
        sampler = SamplerSpy(fail=FileNotFoundError("ffmpeg 없음"))
        report = self.run_qa(frame_sampler=sampler)
        self.assertFailed(report, "technical_frames")
        self.assertFalse(report.checks["product_identity_screen"]["passed"])

    def test_transcriber_unavailable_fails_not_passes(self):
        tr = TranscriberSpy(fail=RuntimeError("faster_whisper 미설치"))
        self.assertFailed(self.run_qa(transcriber=tr), "spoken_content")

    def test_empty_transcript_fails(self):
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy("")),
                          "spoken_content")

    def test_audio_probe_unavailable_fails_not_passes(self):
        self.assertFailed(self.run_qa(audio_probe=AudioSpy(fail=OSError("no ffmpeg"))),
                          "technical_audio_signal")

    def test_sampler_returning_fewer_frames_fails(self):
        self.assertFailed(self.run_qa(frame_sampler=SamplerSpy(short=True)),
                          "technical_frames")

    def test_missing_product_image_fails_identity_screen(self):
        os.unlink(self.product_image)
        self.assertFailed(self.run_qa(), "product_identity_screen")

    def test_undecodable_product_image_fails_rather_than_skipping(self):
        with open(self.product_image, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0 not a png")
        self.assertFailed(self.run_qa(), "product_identity_screen")

    def test_no_seams_supplied_still_fails_closed_offline(self):
        # 시임을 주지 않으면 기본 구현이 OpenMontage 를 찾는다. 이 환경에
        # 없으면 통과가 아니라 실패여야 한다. 있으면 통과해도 된다 —
        # 어느 쪽이든 '조용한 통과'는 없어야 한다.
        report = self.run_qa(frame_sampler=None, transcriber=None,
                             audio_probe=None)
        self.assertIsInstance(report, vc.QAReport)
        report.validate()
        for name in ("technical_frames", "spoken_content",
                     "technical_audio_signal"):
            self.assertIn("passed", report.checks[name])


# ---------------------------------------------------------------------------
# 4. 제품 동일성 — 정직하게 부분 검사임을 밝힌다
# ---------------------------------------------------------------------------


class TestProductIdentity(QABase):

    def test_grossly_divergent_frames_fail_the_screen(self):
        sampler = SamplerSpy(lambda i, ts: (inverted_rows() if i % 2 else
                                            flat_rows(200 - i)))
        self.assertFailed(self.run_qa(frame_sampler=sampler),
                          "product_identity_screen")

    def test_screen_declares_it_does_not_establish_identity(self):
        report = self.run_qa()
        check = report.checks["product_identity_screen"]
        self.assertFalse(check["establishes_identity"])
        self.assertTrue(check["limitations"])
        self.assertIn("perceptual", " ".join(check["limitations"]).lower())

    def test_module_declares_the_known_verification_gap(self):
        self.assertFalse(vq.PERCEPTUAL_VERIFICATION_AVAILABLE)
        self.assertTrue(vq.IDENTITY_LIMITATIONS)


# ---------------------------------------------------------------------------
# 5. 발화 내용 — 승인 카피에서 벗어나면 실패
# ---------------------------------------------------------------------------


class TestSpokenContent(QABase):

    def test_transcript_matching_approved_lines_passes(self):
        report = self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1}. {VOICE_2}!"))
        self.assertTrue(report.checks["spoken_content"]["passed"],
                        report.failures)

    def test_drifted_transcript_fails(self):
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            "밤에 잘 자는 습관부터 챙겨주세요 그리고 이거 먹으면 무조건 큽니다")),
            "spoken_content")

    def test_dropped_approved_line_fails(self):
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(VOICE_1)),
                          "spoken_content")

    def test_unapproved_extra_sentence_fails(self):
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} 지금 주문하면 반값입니다 서두르세요")),
            "spoken_content")

    def test_forbidden_claim_in_transcript_fails_policy(self):
        report = self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} 우리 아이가 먹어보니 키 크는 효과가 있었어요"))
        self.assertFailed(report, "policy_forbidden_claims")


# ---------------------------------------------------------------------------
# 6. 정책 — 고지·금지 표현
# ---------------------------------------------------------------------------


class TestPolicy(QABase):

    def test_missing_disclosure_in_caption_fails(self):
        self.assertFailed(self.run_qa(caption="아이 키 고민할 때 성분표부터 봤어요"),
                          "policy_disclosure")

    def test_altered_disclosure_wording_fails(self):
        bad = "아이 키 고민\n쿠팡 파트너스로 수수료를 좀 받습니다"
        self.assertFailed(self.run_qa(caption=bad), "policy_disclosure")

    def test_us_market_requires_associates_line(self):
        sb = storyboard_dict("US", ("Check the label first",
                                    "This is the exact serving size"))
        report = self.run_qa(
            storyboard=sb, caption="How I read the label #ad",
            overlay_texts=[], transcriber=TranscriberSpy(
                "Check the label first. This is the exact serving size"))
        self.assertFailed(report, "policy_disclosure")

    def test_us_market_passes_with_associates_line(self):
        sb = storyboard_dict("US", ("Check the label first",
                                    "This is the exact serving size"))
        report = self.run_qa(
            storyboard=sb,
            caption="How I read the label #ad\n" + DISCLOSURE_TEXT["US"],
            overlay_texts=[DISCLOSURE_TEXT["US"]],
            transcriber=TranscriberSpy(
                "Check the label first. This is the exact serving size"))
        self.assertTrue(report.checks["policy_disclosure"]["passed"],
                        report.failures)

    def test_forbidden_claim_in_caption_fails(self):
        bad = "이거 먹으면 키 크는 효과 확실합니다\n" + DISCLOSURE_TEXT["KR"]
        self.assertFailed(self.run_qa(caption=bad), "policy_forbidden_claims")

    def test_forbidden_claim_in_overlay_fails(self):
        report = self.run_qa(overlay_texts=[DISCLOSURE_TEXT["KR"],
                                            "우리 아이가 먹어보니 달라졌어요"])
        self.assertFailed(report, "policy_forbidden_claims")

    def test_forbidden_claim_scan_covers_all_three_surfaces(self):
        self.assertEqual(set(vq.CLAIM_SCAN_SURFACES),
                         {"caption", "transcript", "overlay"})


# ---------------------------------------------------------------------------
# 7. 실패한 QA 는 잡을 qa_failed 로 보낸다 (계약 전이표만 사용)
# ---------------------------------------------------------------------------


def _job(state=vc.STATE_GENERATING):
    evidence = vc.ProductEvidence(
        product_id="prd-1", market="KR",
        source_urls=["https://www.coupang.com/vp/products/1"],
        source_sha256=["a" * 64],
        rights={"basis": "official_product_page", "holder": "brand",
                "source_url": "https://www.coupang.com/vp/products/1",
                "captured_at": "2026-08-28T00:00:00+09:00"},
        provenance=[{"quote": "q", "source_url": "https://www.coupang.com/vp/products/1",
                     "original_location": "https://www.coupang.com/vp/products/1"}],
        captured_at="2026-08-28T00:00:00+09:00")
    storyboard = vc.Storyboard(
        storyboard_id="sb-1", run_id="run-1", product_id="prd-1", market="KR",
        viral_pattern_ids=["vp-1"], content_draft_id="draft-1",
        cuts=[vc.CutPrompt(index=1, prompt="p1"), vc.CutPrompt(index=2, prompt="p2")])
    return vc.VideoJob(job_id="job-1", run_id="run-1", product_id="prd-1",
                       market="KR", state=state, evidence=evidence,
                       storyboard=storyboard)


class TestJobRouting(QABase):

    def test_failing_qa_lands_the_job_in_qa_failed(self):
        job = _job()
        report = self.run_qa(transcriber=TranscriberSpy("완전히 다른 말"))
        vq.apply_qa_result(job, report)
        self.assertEqual(job.state, vc.STATE_QA_FAILED)
        self.assertIs(job.qa_report, report)
        job.validate()

    def test_qa_failure_cannot_enter_ready_to_publish(self):
        job = _job()
        report = self.run_qa(transcriber=TranscriberSpy("완전히 다른 말"))
        vq.apply_qa_result(job, report)
        with self.assertRaises(vc.StateError):
            job.transition(vc.STATE_READY_TO_PUBLISH)

    def test_passing_qa_attaches_report_without_publishing_itself(self):
        job = _job()
        report = self.run_qa()
        vq.apply_qa_result(job, report)
        self.assertTrue(report.passed)
        self.assertEqual(job.state, vc.STATE_GENERATING)
        self.assertIs(job.qa_report, report)

    def test_only_contract_edges_are_used(self):
        job = _job(vc.STATE_QUEUED)
        report = self.run_qa(transcriber=TranscriberSpy("다른 말"))
        with self.assertRaises(vc.StateError):
            vq.apply_qa_result(job, report)   # queued -> qa_failed 는 없는 간선
        self.assertEqual(job.state, vc.STATE_QUEUED)

    def test_retry_budget_exhausted_dead_letters(self):
        job = _job()
        report = self.run_qa(transcriber=TranscriberSpy("다른 말"))
        vq.apply_qa_result(job, report)
        self.assertEqual(vq.route_after_failure(job, attempt=1), vc.STATE_QUEUED)
        job2 = _job()
        vq.apply_qa_result(job2, report)
        self.assertEqual(vq.route_after_failure(job2, attempt=vq.MAX_REGEN_ATTEMPTS),
                         vc.STATE_DEAD_LETTER)


# ---------------------------------------------------------------------------
# 8. 보고서 진단 가능성
# ---------------------------------------------------------------------------


class TestReportDetail(QABase):

    def test_every_failure_carries_a_diagnosable_detail(self):
        self.write_video(duration_seconds=12.0)
        report = self.run_qa(transcriber=TranscriberSpy(""),
                             audio_probe=AudioSpy(rms_dbfs=-99.0, peak_dbfs=-99.0))
        self.assertFalse(report.passed)
        self.assertGreaterEqual(len(report.failures), 3)
        for name, check in report.checks.items():
            self.assertIn("passed", check)
            if not check["passed"]:
                self.assertTrue(str(check.get("detail") or "").strip(),
                                f"{name} 실패에 진단 정보가 없다")

    def test_report_round_trips_through_dict(self):
        report = self.run_qa()
        again = vc.QAReport.from_dict(report.to_dict())
        self.assertEqual(again.to_dict(), report.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
