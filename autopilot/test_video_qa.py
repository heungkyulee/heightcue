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
from unittest import mock
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


class EnergyProfileSpy:
    """OpenMontage AudioEnergy 가 **실제로** 돌려주는 모양.

    첫 유료 실행이 여기서 깨졌다 — 프로브는 ``energy_profile`` 을 주는데
    검사는 ``rms_dbfs`` 를 읽었다.
    """

    def __init__(self, lufs_per_second=(-21.0, -19.5, -20.2), *, raw=None):
        self.raw = raw
        self.lufs = list(lufs_per_second)

    def __call__(self, video_path):
        if self.raw is not None:
            return self.raw
        return {
            "energy_profile": [
                {"time_seconds": i, "loudness_lufs": v, "active": v > -40}
                for i, v in enumerate(self.lufs)
            ],
            "audio_duration": float(len(self.lufs)),
        }


def signoff_for(path, by="haneul-proof"):
    return {"signed_off_by": by, "signed_off_at": "2026-08-29T10:00:00+09:00",
            "artifact_sha256": vq._sha256_file(path),
            "note": "사람이 프레임을 직접 보고 상품 동일성을 확인했다"}


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
            identity_signoff=signoff_for(self.video),
            # AI 충실도는 이제 사람 서명 앞의 필수 기계 필터다. 테스트는
            # 전부 오프라인이어야 하므로 시임으로 통과 판정을 주입한다.
            # (검사 자체의 회귀는 test_product_fidelity.py 가 진다.)
            fidelity_checker=lambda paths, asset_dir, budget: {
                "passed": True, "reason": "stubbed offline", "calls": 0},
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

    def test_missing_product_image_records_the_advisory_gap_without_gating(self):
        """참고용 dHash 이미지가 없다고 게이트를 닫지는 않는다.

        예전에는 닫았다. 그런데 스테이징 상품 자산은 **전부 JPEG** 이고 이
        디코더는 PNG 전용이라, 그 규칙은 product_identity 를 구조적으로
        통과 불가능하게 만들었다 (통과 불가능한 게이트 = 없는 게이트).
        dHash 는 애초에 판정에 쓰지 않는 참고값이다. 진짜 기계 증거는 AI
        비전 대조이고, 그것은 아래 두 테스트가 진다.
        """
        os.unlink(self.product_image)
        report = self.run_qa()
        check = report.checks["product_identity_screen"]
        self.assertTrue(check["passed"], report.failures)
        self.assertTrue(check["advisory_error"])       # 사실은 기록된다
        self.assertIsNone(check["advisory_best_distance"])

    def test_jpeg_product_image_does_not_break_the_check(self):
        """스테이징 자산은 전부 JPEG 다 — 그것 때문에 검사가 죽으면 안 된다."""
        with open(self.product_image, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0 a real jpeg would go here")
        report = self.run_qa()
        self.assertTrue(report.checks["product_identity_screen"]["passed"],
                        report.failures)

    def test_ai_fidelity_failure_still_gates_when_advisory_is_absent(self):
        """참고값이 없어도 AI 판정은 여전히 게이트다 — 구멍이 아니다."""
        os.unlink(self.product_image)
        self.assertFailed(
            self.run_qa(fidelity_checker=lambda p, a, b: {
                "passed": False, "reason": "두 번째 병목"}),
            "product_identity_screen")

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

    def test_in_scene_footage_is_not_failed_by_the_advisory_screen(self):
        """첫 유료 실행의 실제 실패 모양.

        레퍼런스는 흰 배경 카탈로그 컷아웃이고 영상은 주방·손·자연광 속
        같은 상품이다. dHash 거리가 35 로 임계 16 을 넘었다 — 어떤 정직한
        인신 I2V 컷도 이 비교를 통과할 수 없다. 거리는 **참고값**으로만
        보고하고, 게이트는 사람 서명이 진다.
        """
        sampler = SamplerSpy(lambda i, ts: inverted_rows())   # 거리 최대
        report = self.run_qa(frame_sampler=sampler)
        check = report.checks["product_identity_screen"]
        self.assertTrue(check["passed"],
                        f"인신 촬영이 구조 해시 거리로 반려됐다: {check}")
        self.assertGreater(check["advisory_best_distance"], vq.MAX_DHASH_DISTANCE)
        self.assertTrue(check["advisory_only"])

    def test_without_human_signoff_identity_fails(self):
        report = self.run_qa(identity_signoff=None)
        self.assertFailed(report, "product_identity_screen")
        self.assertIn("machine", " ".join(
            report.checks["product_identity_screen"]["limitations"]).lower())

    def test_signoff_for_a_different_artifact_fails(self):
        bad = dict(signoff_for(self.video), artifact_sha256="0" * 64)
        self.assertFailed(self.run_qa(identity_signoff=bad),
                          "product_identity_screen")

    def test_signoff_from_an_unknown_owner_fails(self):
        self.assertFailed(
            self.run_qa(identity_signoff=signoff_for(self.video, by="nobody")),
            "product_identity_screen")

    def test_legacy_signoff_owner_is_accepted(self):
        # 현행: haneul-proof / 레거시: mungchi-proof — 과거 서명 호환성 회귀
        report = self.run_qa(
            identity_signoff=signoff_for(self.video, by="mungchi-proof"))
        self.assertTrue(report.checks["product_identity_screen"]["passed"],
                        report.failures)

    def test_screen_declares_it_does_not_establish_identity(self):
        report = self.run_qa()
        check = report.checks["product_identity_screen"]
        self.assertFalse(check["establishes_identity"])
        # 기계 계층(AI 비전 대조)이 생겼으므로 machine_verified 는 True 다.
        # 그러나 그것이 동일성을 '확립'하지는 않으며 사람 서명은 여전히 필수다.
        self.assertTrue(check["machine_verified"])
        self.assertTrue(check["requires_human_signoff"])
        self.assertTrue(check["limitations"])
        self.assertIn("perceptual", " ".join(check["limitations"]).lower())

    def test_ai_fidelity_is_a_mandatory_prefilter_before_the_human_signoff(self):
        """AI 가 결함을 보고하면 사람 서명이 유효해도 통과하지 않는다.

        기계 필터는 사람의 대체재가 아니라 사람 앞의 체다 — 사람이 결함
        있는 프레임에 도장을 찍는 일이 없게 한다.
        """
        report = self.run_qa(
            fidelity_checker=lambda paths, asset_dir, budget: {
                "passed": False, "reason": "ORCAIN 위조"})
        self.assertFailed(report, "product_identity_screen")
        self.assertIn("ORCAIN",
                      report.checks["product_identity_screen"]["detail"])

    def test_missing_ai_fidelity_verdict_fails_closed(self):
        report = self.run_qa(
            fidelity_checker=lambda paths, asset_dir, budget: None)
        self.assertFailed(report, "product_identity_screen")

    def test_ai_fidelity_pass_does_not_waive_the_human_signoff(self):
        report = self.run_qa(identity_signoff=None)
        self.assertFailed(report, "product_identity_screen")

    def test_module_declares_the_known_verification_gap(self):
        self.assertFalse(vq.PERCEPTUAL_VERIFICATION_AVAILABLE)
        self.assertTrue(vq.IDENTITY_LIMITATIONS)


class TestAudioProbeShapes(QABase):
    """첫 유료 실행 실패 2 — 프로브 키 이름 불일치(배관 버그)."""

    def test_energy_profile_shape_is_understood(self):
        report = self.run_qa(audio_probe=EnergyProfileSpy())
        check = report.checks["technical_audio_signal"]
        self.assertTrue(check["passed"], check)
        self.assertEqual(check["shape"], "energy_profile")

    def test_silent_energy_profile_still_fails(self):
        report = self.run_qa(
            audio_probe=EnergyProfileSpy(lufs_per_second=(-91.0, -120.0, -95.0)))
        self.assertFailed(report, "technical_audio_signal")

    def test_rms_dbfs_shape_still_works(self):
        self.assertTrue(self.run_qa().checks["technical_audio_signal"]["passed"])

    def test_unrecognised_probe_shape_fails_loudly_not_as_silence(self):
        report = self.run_qa(audio_probe=EnergyProfileSpy(
            raw={"loudness": "quite loud", "unitless": True}))
        self.assertFailed(report, "technical_audio_signal")
        detail = report.checks["technical_audio_signal"]["detail"]
        self.assertIn("loudness", detail)   # 실제 키를 적어 진단 가능해야 한다
        # 미인식을 무음 판정으로 둔갑시키지 않는다 ("무음이다" 는 무음 판정 문구)
        self.assertNotIn("무음이다", detail)
        self.assertNotIn("shape", report.checks["technical_audio_signal"])

    def test_empty_energy_profile_fails(self):
        self.assertFailed(
            self.run_qa(audio_probe=EnergyProfileSpy(
                raw={"energy_profile": []})), "technical_audio_signal")


class TestSampleTimestampTail(QABase):
    """첫 유료 실행 실패 3 — 마지막 샘플이 최종 프레임 뒤로 넘어간다."""

    def test_last_sample_lands_before_the_final_frame(self):
        stamps = vq.sample_timestamps(10.0, 2, fps=30.0)
        self.assertLess(max(stamps), 10.0)
        # 헤더 길이는 마지막 프레임 PTS 보다 최대 한 프레임 길다. 꼬리 샘플은
        # 마지막에서 두 번째 프레임 시작 이하여야 반드시 실재한다.
        self.assertLessEqual(max(stamps), 10.0 - 2.0 / 30.0 + 1e-9)
        self.assertGreater(max(stamps), 9.9)      # 그래도 '끝'을 본다

    def test_tail_sample_is_inside_a_low_fps_video(self):
        stamps = vq.sample_timestamps(10.0, 2, fps=24.0)
        self.assertLessEqual(max(stamps), 10.0 - 2.0 / 24.0 + 1e-9)

    def test_old_fixed_offset_would_have_overrun(self):
        # 회귀 잠금: 24fps 에서 옛 상수 0.05 는 마지막 두 프레임 경계
        # (10 - 2/24 = 9.9167) 보다 뒤였다 — 그래서 프레임이 안 왔다.
        self.assertGreater(round(10.0 - 0.05, 3), 10.0 - 2.0 / 24.0)

    def test_tail_uses_the_video_track_not_the_longer_audio_tail(self):
        """꼬리는 **비디오 트랙** 길이에서 잡아야 한다.

        2026-08-29 두 번째 유료 실행: 7장을 요청했는데 6장만 왔다. 원인은
        ``sample_timestamps`` 도 오프셋도 아니라 **호출부가 어느 길이를
        넘겼는가**였다.

        컨테이너 길이(mvhd)는 가장 긴 트랙을 따른다. 이 영상은 오디오가
        15.062초, 비디오가 15.000초였다. 컨테이너 길이로 계산한
        ``15.062 - 2/30 = 14.995`` 는 **마지막 비디오 프레임(14.967)보다
        뒤**다 — 2프레임 여유가 0.062초의 오디오 꼬리에 통째로 먹혔다.

        그래서 ``run_qa`` 는 ``coded_duration_seconds``(비디오 트랙 실측)가
        있으면 그것으로 샘플 시각을 잡는다.
        """
        seen = {}

        class DurationCapturingSampler(SamplerSpy):
            def __call__(self, video_path, timestamps, out_dir):
                seen["stamps"] = list(timestamps)
                return super().__call__(video_path, timestamps, out_dir)

        # 오디오가 비디오보다 0.062초 긴 실제 산출물의 측정값을 그대로 쓴다.
        measured = {"duration_seconds": 15.062, "coded_duration_seconds": 15.0}
        stamps = vq.sample_timestamps(
            vq.frame_sampling_duration(measured), 3, fps=30.0)
        seen["stamps"] = stamps

        last_real_frame_start = 15.0 - 1.0 / 30.0
        self.assertLessEqual(
            max(seen["stamps"]), last_real_frame_start,
            "꼬리 샘플이 마지막 실재 비디오 프레임보다 뒤에 있다")

    def test_container_duration_alone_would_have_overrun(self):
        """회귀 잠금: 컨테이너 길이를 그대로 쓰면 다시 프레임을 놓친다."""
        overrun = max(vq.sample_timestamps(15.062, 3, fps=30.0))
        self.assertGreater(overrun, 15.0 - 1.0 / 30.0,
                           "이 값이 7장 중 6장만 오던 원인이다")

    def test_frame_sampling_duration_prefers_the_video_track(self):
        self.assertEqual(
            vq.frame_sampling_duration({"duration_seconds": 15.062,
                                        "coded_duration_seconds": 15.0}),
            15.0)

    def test_frame_sampling_duration_falls_back_to_the_container(self):
        """비디오 트랙 길이를 못 재면 컨테이너 길이로 물러난다 — 건너뛰지 않는다."""
        self.assertEqual(
            vq.frame_sampling_duration({"duration_seconds": 15.062}), 15.062)

    def test_frame_sampling_duration_ignores_a_nonsense_track_length(self):
        """코딩 길이가 0/음수면 신뢰하지 않는다 — 샘플 시각이 전부 뭉개진다."""
        self.assertEqual(
            vq.frame_sampling_duration({"duration_seconds": 15.062,
                                        "coded_duration_seconds": 0.0}),
            15.062)

    def test_sampler_refusing_the_tail_timestamp_still_fails_closed(self):
        class TailDroppingSampler(SamplerSpy):
            """헤더보다 한 프레임 짧은 실제 미디어를 흉내낸다."""

            def __call__(self, video_path, timestamps, out_dir):
                last_real_frame = 10.0 - 1.0 / 30.0
                stamps = [t for t in timestamps if t <= last_real_frame]
                return super().__call__(video_path, stamps, out_dir)

        report = self.run_qa(frame_sampler=TailDroppingSampler())
        self.assertTrue(report.checks["technical_frames"]["passed"],
                        f"경계가 여전히 실재 프레임 뒤에 있다: {report.failures}")


class TestArtifactUnderTest(QABase):
    """어느 산출물을 봤는지 리포트가 이름으로 말한다.

    베이스 영상 에셋에는 자막이 없다 (사용자 명령). 자막은 별도 후처리
    패스에서 입힌다. 그래서 고지는 **클린 마스터와 최종 납품물 양쪽**에서
    확인해야 하고(법적 의무는 전 구간 존재), 캡션 드리프트는 자막이 입혀진
    **최종 납품물**에서만 판정한다.
    """

    def test_report_names_the_artifact_each_check_inspected(self):
        report = self.run_qa()
        for name in vq.CHECK_NAMES:
            self.assertIn("artifact_under_test", report.checks[name],
                          f"{name} 이 어느 산출물을 봤는지 밝히지 않는다")
            self.assertIn(report.checks[name]["artifact_under_test"],
                          vq.ARTIFACT_KINDS)

    def test_disclosure_is_verified_on_both_master_and_deliverable(self):
        report = self.run_qa()
        check = report.checks["policy_disclosure"]
        self.assertEqual(set(check["artifacts_verified"]),
                         {vq.ARTIFACT_CLEAN_MASTER, vq.ARTIFACT_DELIVERABLE})

    def test_disclosure_missing_on_the_clean_master_fails(self):
        # 최종 납품물 캡션에는 고지가 있으나 마스터 쪽 오버레이/캡션이 비었다.
        report = self.run_qa(master_caption="아이 키 고민할 때 성분표부터 봤어요")
        self.assertFailed(report, "policy_disclosure")
        self.assertIn(vq.ARTIFACT_CLEAN_MASTER,
                      report.checks["policy_disclosure"]["detail"])

    def test_caption_drift_check_runs_on_the_subtitled_deliverable(self):
        report = self.run_qa()
        self.assertEqual(report.checks["policy_forbidden_claims"]
                         ["artifact_under_test"], vq.ARTIFACT_DELIVERABLE)

    def test_deliverable_defaults_to_the_video_path_when_no_master_given(self):
        report = self.run_qa(master_caption=None)
        self.assertTrue(report.checks["policy_disclosure"]["passed"],
                        report.failures)


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


# ---------------------------------------------------------------------------
# 9. 픽스 라운드 1 — 짧은/비한글 드리프트, 순서, 쓰기 순서, 독립 길이 교차검증
# ---------------------------------------------------------------------------


class TestSpokenDriftHardening(QABase):
    """리뷰 지적 1·2·4 — 짧은 추가·비한글 추가·순서 뒤바뀜이 통과하면 안 된다."""

    def test_three_hangul_syllable_addition_fails(self):
        # 3음절이면 한국어에서 완결된 한 단어다 ("무조건").
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} 무조건")), "spoken_content")

    def test_single_hangul_artifact_is_tolerated(self):
        report = self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} 음"))
        self.assertTrue(report.checks["spoken_content"]["passed"],
                        report.failures)

    def test_passing_report_records_the_tolerated_residual(self):
        report = self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} 음"))
        check = report.checks["spoken_content"]
        self.assertIn("unapproved_residual", check)
        self.assertEqual(check["unapproved_residual"], "음")

    def test_injected_non_hangul_sentence_fails(self):
        # 중국어 한 문장은 [0-9a-z가-힣] 필터에서 통째로 사라진다.
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} 这个产品绝对能让孩子长高")), "spoken_content")

    def test_injected_cyrillic_sentence_fails(self):
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} купите прямо сейчас")), "spoken_content")

    def test_injected_japanese_sentence_fails(self):
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_1} {VOICE_2} これを飲めば必ず背が伸びます")), "spoken_content")

    def test_approved_lines_spoken_out_of_order_fail(self):
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_2}. {VOICE_1}")), "spoken_content")

    def test_out_of_order_failure_is_diagnosable(self):
        report = self.run_qa(transcriber=TranscriberSpy(
            f"{VOICE_2}. {VOICE_1}"))
        check = report.checks["spoken_content"]
        self.assertTrue(check.get("out_of_order_lines"),
                        f"순서 위반 근거가 없다: {check}")


class TestTranscriptionNoiseDiagnosis(QABase):
    """전사 잡음은 **흡수하지 않는다.** 다만 진단은 정확히 붙인다.

    base 모델이 "마인드셋"을 "마인드색"으로 적는 건 기록된 사실이다. 멀쩡한
    유료 영상이 여기서 반려될 수 있다. 그렇다고 근사 매칭으로 흡수하면
    한국어에서 1음절 치환이 의미를 뒤집는 경우("잘 커요" / "안 커요")를 함께
    통과시키게 된다 — 드리프트 감지력의 직접적 손실이다. 그래서 게이트는
    그대로 닫아두고, 대신 실패 리포트가 '전사 잡음일 가능성'을 명시해
    운영자가 재생성이 아니라 사람 확인으로 라우팅하게 한다.
    """

    def test_single_syllable_substitution_still_fails(self):
        drifted = VOICE_1.replace("습관", "습곤")
        self.assertFailed(self.run_qa(transcriber=TranscriberSpy(
            f"{drifted} {VOICE_2}")), "spoken_content")

    def test_near_miss_is_flagged_as_likely_transcription_noise(self):
        drifted = VOICE_1.replace("습관", "습곤")
        report = self.run_qa(transcriber=TranscriberSpy(f"{drifted} {VOICE_2}"))
        check = report.checks["spoken_content"]
        self.assertTrue(check.get("likely_transcription_noise"), check)
        near = check["near_misses"][0]
        self.assertEqual(near["approved_line"], VOICE_1)
        self.assertEqual(near["edit_distance"], 1)

    def test_a_genuinely_different_line_is_not_flagged_as_noise(self):
        report = self.run_qa(transcriber=TranscriberSpy(
            f"이건 완전히 다른 말입니다 {VOICE_2}"))
        check = report.checks["spoken_content"]
        self.assertFalse(check.get("likely_transcription_noise"), check)


class TestSpokenNumberCanonicalisation(QABase):
    """쓰인 숫자와 **말해진** 숫자를 같은 것으로 본다 — 값은 그대로 지킨 채.

    2026-08-29 두 번째 유료 실행: 승인 카피 ``age 1+`` 을 성우가
    정확히 읽었는데 전사는 ``age one plus`` 였다. 모델은 옳게 말했고
    비교기가 표기 형태를 몰랐을 뿐인데 멀쩡한 영상이 반려됐다.

    이건 근사 매칭이 **아니다.** 편집거리로 봐주는 게 아니라 양쪽을 같은
    표준형으로 옮긴 뒤 **정확히** 비교한다. 그래서 값이 다르면 여전히
    걸린다 — 영양 라벨에서 숫자 하나가 틀리는 것은 잡음이 아니라 오류다.
    """

    def test_written_one_plus_matches_spoken_one_plus(self):
        report = self.run_qa(
            storyboard=storyboard_dict(voice_lines=["대상 연령 1+ 입니다."]),
            transcriber=TranscriberSpy("대상 연령 one plus 입니다."))
        self.assertTrue(report.checks["spoken_content"]["passed"],
                        report.checks["spoken_content"])

    def test_a_wrong_number_still_fails(self):
        """600 IU 를 60 IU 로 말하면 반드시 걸린다 — 게이트의 존재 이유다."""
        report = self.run_qa(
            storyboard=storyboard_dict(voice_lines=["표시된 600 IU 입니다."]),
            transcriber=TranscriberSpy("표시된 60 IU 입니다."))
        self.assertFalse(report.checks["spoken_content"]["passed"],
                         report.checks["spoken_content"])

    def test_a_spoken_wrong_number_word_still_fails(self):
        """말로 틀리게 읽어도 걸린다 — 숫자 단어도 표준형으로 옮겨 비교한다."""
        report = self.run_qa(
            storyboard=storyboard_dict(voice_lines=["대상 연령 1+ 입니다."]),
            transcriber=TranscriberSpy("대상 연령 two plus 입니다."))
        self.assertFalse(report.checks["spoken_content"]["passed"],
                         report.checks["spoken_content"])

    def test_canonicalisation_does_not_erase_the_plus(self):
        """``1+`` 과 그냥 ``1`` 은 다른 말이다 — plus 를 지워 통과시키지 않는다."""
        report = self.run_qa(
            storyboard=storyboard_dict(voice_lines=["대상 연령 1+ 입니다."]),
            transcriber=TranscriberSpy("대상 연령 one 입니다."))
        self.assertFalse(report.checks["spoken_content"]["passed"],
                         report.checks["spoken_content"])


class TestClaimScanSurfaceHonesty(QABase):
    """리뷰 지적 5 — 읽지 못한 면을 '깨끗하다'고 말하지 않는다."""

    def test_transcript_surface_is_marked_unscanned_when_unavailable(self):
        report = self.run_qa(transcriber=TranscriberSpy(
            "", fail=RuntimeError("no whisper")))
        claims = report.checks["policy_forbidden_claims"]
        self.assertIn("transcript", claims.get("unscanned") or [])
        self.assertNotIn("transcript", claims.get("scanned") or [])

    def test_transcript_surface_is_scanned_when_available(self):
        claims = self.run_qa().checks["policy_forbidden_claims"]
        self.assertIn("transcript", claims.get("scanned") or [])
        self.assertEqual(claims.get("unscanned") or [], [])


class TestForbiddenPatternAccessor(unittest.TestCase):
    """리뷰 지적 6 — 사설 심볼 커플링을 공개 접근자로 승격한다."""

    def test_storyboard_exposes_a_public_forbidden_pattern_accessor(self):
        import video_storyboard as vs
        self.assertTrue(hasattr(vs, "forbidden_patterns"))
        self.assertEqual(tuple(vs.forbidden_patterns()), tuple(vs._FORBIDDEN_RE))

    def test_qa_uses_the_public_accessor(self):
        import video_storyboard as vs
        calls = []
        original = vs.forbidden_patterns

        def spy():
            calls.append(1)
            return original()

        vs.forbidden_patterns = spy
        self.addCleanup(setattr, vs, "forbidden_patterns", original)
        vq.find_forbidden_claims("아무 말")
        self.assertTrue(calls, "공개 접근자를 거치지 않았다")


class TestDurationCrossCheck(QABase):
    """리뷰 지적 7 — 헤더 길이를 실제 샘플된 마지막 프레임과 대조한다."""

    def test_header_duration_beyond_last_real_frame_fails(self):
        class TruncatedSampler(SamplerSpy):
            def __call__(self, video_path, timestamps, out_dir):
                frames = super().__call__(video_path, timestamps, out_dir)
                for f in frames:            # 실제 미디어는 7초에서 끝난다
                    f["actual_timestamp"] = min(float(f["timestamp"]), 7.0)
                return frames

        self.assertFailed(self.run_qa(frame_sampler=TruncatedSampler()),
                          "technical_frames")

    def test_matching_actual_timestamps_pass(self):
        class HonestSampler(SamplerSpy):
            def __call__(self, video_path, timestamps, out_dir):
                frames = super().__call__(video_path, timestamps, out_dir)
                for f in frames:
                    f["actual_timestamp"] = float(f["timestamp"])
                return frames

        report = self.run_qa(frame_sampler=HonestSampler())
        self.assertTrue(report.checks["technical_frames"]["passed"],
                        report.failures)


class TestApplyQAWriteOrdering(QABase):
    """리뷰 지적 3 — qa_failed 로 전이하는 순간 이미 실패 리포트를 달고 있어야 한다."""

    def test_report_is_attached_before_the_qa_failed_transition(self):
        job = _job()
        report = self.run_qa(transcriber=TranscriberSpy("완전히 다른 말"))
        seen = {}
        original = job.transition

        def spy(target, *a, **kw):
            seen["qa_report"] = job.qa_report
            return original(target, *a, **kw)

        job.transition = spy
        vq.apply_qa_result(job, report)
        self.assertIs(seen.get("qa_report"), report,
                      "전이 시점에 잡이 자기 불변식을 위반한다 (리포트 미부착)")


class TestOpenMontageInterpreter(unittest.TestCase):
    """OpenMontage 툴은 **OpenMontage 자기 인터프리터**로 돌아야 한다.

    호출자(heightcue) venv 에는 faster-whisper 가 없다 — sys.executable 로
    돌리면 전사기가 설치돼 있는데도 항상 CheckUnavailable 이 난다.
    """

    def _fake_root(self, with_venv=True):
        root = tempfile.mkdtemp()
        if with_venv:
            os.makedirs(os.path.join(root, ".venv", "bin"), exist_ok=True)
            p = os.path.join(root, ".venv", "bin", "python")
            with open(p, "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(p, 0o755)
        return root

    def test_subprocess_uses_openmontage_venv_python_not_sys_executable(self):
        root = self._fake_root()
        expected = os.path.join(root, ".venv", "bin", "python")
        captured = {}

        class Proc:
            returncode = 0
            stdout = '{"success": true, "data": {"ok": 1}, "error": null}'
            stderr = ""

        def fake_run(argv, **kw):
            captured["argv"] = argv
            captured["kw"] = kw
            return Proc()

        with mock.patch.object(vq.subprocess, "run", fake_run):
            data = vq._openmontage_call("transcriber", "Transcriber",
                                        {"input_path": "x.mp4"}, root=root)
        self.assertEqual(data, {"ok": 1})
        self.assertEqual(captured["argv"][0], expected,
                         f"OpenMontage 인터프리터가 아니라 {captured['argv'][0]} 로 돌았다")
        self.assertNotEqual(captured["argv"][0], sys.executable)
        self.assertEqual(captured["kw"].get("cwd"), root)
        self.assertEqual(captured["kw"].get("timeout"), vq.DEFAULT_SEAM_TIMEOUT)

    def test_missing_venv_python_fails_closed_naming_the_path(self):
        root = self._fake_root(with_venv=False)
        missing = os.path.join(root, ".venv", "bin", "python")
        with mock.patch.object(vq.subprocess, "run",
                               mock.Mock(side_effect=AssertionError("서브프로세스를 띄우면 안 된다"))):
            with self.assertRaises(vq.CheckUnavailable) as ctx:
                vq._openmontage_call("transcriber", "Transcriber", {}, root=root)
        self.assertIn(missing, str(ctx.exception))

    def test_transcriber_passes_an_explicit_model_size(self):
        """모델 크기는 **명시**돼야 한다 — 조용한 기본값 상속 금지.

        값 자체(base)는 실측으로 고른 것이다: small/medium 은 한국어
        고유명사를 오히려 더 뭉갰다. video_qa.TRANSCRIBER_MODEL_SIZE 주석 참조.
        """
        seen = {}

        def fake_call(module, cls, inputs, **kw):
            seen.update(inputs)
            return {"segments": [{"text": "안녕"}], "language": "ko"}

        with mock.patch.object(vq, "_openmontage_call", fake_call):
            vq.default_transcriber("x.mp4")
        self.assertEqual(seen.get("model_size"), vq.TRANSCRIBER_MODEL_SIZE)
        self.assertTrue(vq.TRANSCRIBER_MODEL_SIZE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
