#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video_compose 회귀 — Remotion 전용 합성, 고지 생존, 승인 카피 무변형.

이 테스트는 **절대 렌더하지 않는다.** `npx remotion render` 도, ffmpeg 도,
네트워크도 호출하지 않는다. 런타임 탐지와 렌더러는 전부 주입 시임이며
mp4 는 이 파일에서 바이트로 직접 조립한다 — 그래야 `measure_mp4` 가
'선언된 헤더를 믿는' 게 아니라 실제 파일을 읽는다는 걸 증명할 수 있다.
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import video_compose as vcm  # noqa: E402
from video_storyboard import DISCLOSURE_TEXT  # noqa: E402


# ---------------------------------------------------------------------------
# 합성 mp4 조립기 — 진짜 박스 구조를 만든다 (파서가 실제로 읽어야 통과)
# ---------------------------------------------------------------------------


def _box(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + tag + payload


def _mvhd(timescale: int, duration_units: int) -> bytes:
    payload = (b"\x00\x00\x00\x00"                    # version 0 + flags
               + struct.pack(">I", 0)                 # creation
               + struct.pack(">I", 0)                 # modification
               + struct.pack(">I", timescale)
               + struct.pack(">I", duration_units))
    payload += b"\x00" * (100 - len(payload))         # rate..next_track_id
    return _box(b"mvhd", payload)


def _tkhd(track_id: int, width: int, height: int) -> bytes:
    payload = bytearray(84)
    payload[0:4] = b"\x00\x00\x00\x07"                # version 0, enabled
    payload[12:16] = struct.pack(">I", track_id)
    struct.pack_into(">I", payload, 76, width << 16)  # 16.16 고정소수점
    struct.pack_into(">I", payload, 80, height << 16)
    return _box(b"tkhd", bytes(payload))


def _stsd(fourcc: bytes) -> bytes:
    entry = _box(fourcc, b"\x00" * 70)
    return _box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry)


def _trak(track_id: int, width: int, height: int, fourcc: bytes,
          handler: bytes) -> bytes:
    hdlr = _box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 12)
    stbl = _box(b"stbl", _stsd(fourcc))
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", hdlr + minf)
    return _box(b"trak", _tkhd(track_id, width, height) + mdia)


def build_mp4(width: int = vcm.COMPOSITION_WIDTH,
              height: int = vcm.COMPOSITION_HEIGHT,
              duration_seconds: float = 10.0,
              video_codec: bytes = b"avc1",
              audio_codec: bytes | None = b"mp4a",
              timescale: int = 1000) -> bytes:
    """진짜 moov/mvhd/tkhd/stsd 를 가진 최소 mp4 바이트."""
    traks = _trak(1, width, height, video_codec, b"vide")
    if audio_codec is not None:
        traks += _trak(2, 0, 0, audio_codec, b"soun")
    moov = _box(b"moov", _mvhd(timescale, int(round(duration_seconds * timescale))) + traks)
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    mdat = _box(b"mdat", b"\x00" * 64)
    return ftyp + moov + mdat


# ---------------------------------------------------------------------------
# 시임 스파이
# ---------------------------------------------------------------------------


class RendererSpy:
    """주입 렌더러. 어떤 런타임이 실제로 호출됐는지 전부 기록한다."""

    def __init__(self, *, runtime: str = vcm.RENDER_RUNTIME,
                 version: str = "4.0.360", mp4: bytes | None = None,
                 text_layers=None, drop_disclosure: bool = False,
                 mutate_caption=None):
        self.runtime = runtime
        self.version = version
        self.mp4 = mp4
        self.text_layers = text_layers
        self.drop_disclosure = drop_disclosure
        self.mutate_caption = mutate_caption
        self.calls = []
        self.runtimes_invoked = []

    def __call__(self, request):
        self.calls.append(request)
        self.runtimes_invoked.append(request.get("render_runtime"))
        out = request["output_path"]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(self.mp4 if self.mp4 is not None else build_mp4())
        layers = self.text_layers
        if layers is None:
            layers = [t["text"] for t in request["overlay_plan"]["text_layers"]]
            if self.drop_disclosure:
                disc = request["overlay_plan"]["disclosure"]["text"]
                layers = [t for t in layers if t != disc]
            if self.mutate_caption is not None:
                layers = [self.mutate_caption(t) for t in layers]
        return {"output_path": out, "runtime": self.runtime,
                "runtime_version": self.version, "text_layers": list(layers)}


class ProbeSpy:
    def __init__(self, available: bool = True, version: str = "4.0.360"):
        self.available = available
        self.version = version
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"available": self.available, "version": self.version}


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

RUN_ID = "run-2026-08-28-01"
JOB_ID = "job-abc123"
SB_ID = "sb-xyz789"
PRODUCT_ID = "kr-vitd-001"
MARKET = "KR"
DRAFT_ID = "draft-77"

CAPTIONS = ["아이 저녁 루틴에 한 스푼 더하는 법",
            "설명서 그대로, 하루 한 번이면 끝"]
CTA = "프로필 링크에서 성분표 확인하세요"


def make_storyboard(n_cuts: int = 2, *, disclosure=None, captions=None):
    caps = captions if captions is not None else CAPTIONS
    return {
        "storyboard_id": SB_ID, "run_id": RUN_ID, "product_id": PRODUCT_ID,
        "market": MARKET, "content_draft_id": DRAFT_ID,
        "viral_pattern_ids": ["vp-1"], "complexity": "standard",
        "total_duration_seconds": n_cuts * 5,
        "cuts": [{"index": i + 1, "duration_seconds": 5,
                  "action": "a", "benefit": "b", "claim": "c",
                  "evidence_id": "ev1", "evidence_quote": "q",
                  "evidence_source_url": "https://x/y",
                  "voice_line": caps[i % len(caps)],
                  "first_frame_prompt": "ffp", "motion_prompt": "mp"}
                 for i in range(n_cuts)],
        "disclosure": ({"market": MARKET, "required": True,
                        "text": DISCLOSURE_TEXT[MARKET],
                        "placement": "on_screen_and_caption"}
                       if disclosure is None else disclosure),
        "evidence_ids": ["ev1"], "baseline": None,
    }


EDIT_DECISIONS = {"render_runtime": "remotion", "composition_mode": "atelier",
                  "aspect_ratio": "9:16", "resolution": "768P"}


def write_cuts(tmp: str, n_cuts: int):
    """실제 파일 + 실제 sha256 을 가진 컷 계보를 만든다."""
    lineage = []
    for i in range(n_cuts):
        path = os.path.join(tmp, f"cut{i + 1:02d}.mp4")
        data = build_mp4(duration_seconds=5.0) + bytes([i])
        with open(path, "wb") as fh:
            fh.write(data)
        lineage.append({
            "cut_index": i + 1, "output_path": path,
            "output_sha256": hashlib.sha256(data).hexdigest(),
            "duration_seconds": 5,
        })
    return lineage


class ComposeTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.out = os.path.join(self.tmp, "out", "final.mp4")
        self.addCleanup(self._tmp.cleanup)

    def compose(self, *, n_cuts=2, storyboard=None, edit_decisions=None,
                renderer=None, probe=None, cut_lineage=None, **kw):
        sb = storyboard if storyboard is not None else make_storyboard(n_cuts)
        lineage = cut_lineage if cut_lineage is not None else write_cuts(self.tmp, n_cuts)
        return vcm.compose_video(
            storyboard=sb,
            cut_lineage=lineage,
            edit_decisions=edit_decisions if edit_decisions is not None else dict(EDIT_DECISIONS),
            job_id=kw.pop("job_id", JOB_ID),
            output_path=kw.pop("output_path", self.out),
            cta_text=kw.pop("cta_text", CTA),
            renderer=renderer if renderer is not None else RendererSpy(),
            runtime_probe=probe if probe is not None else ProbeSpy(),
            **kw)


# ---------------------------------------------------------------------------
# 1. 정상 경로
# ---------------------------------------------------------------------------


class TestHappyPath(ComposeTestBase):

    def test_two_cuts_compose_to_ten_seconds(self):
        renderer, probe = RendererSpy(), ProbeSpy()
        result = self.compose(renderer=renderer, probe=probe)

        self.assertEqual(result["render_runtime"], "remotion")
        self.assertEqual(result["runtime_version"], "4.0.360")
        self.assertEqual(result["composition_mode"], "atelier")
        self.assertEqual(result["expected_duration_seconds"], 10)
        self.assertAlmostEqual(result["measured_duration_seconds"], 10.0, places=3)
        self.assertEqual(result["measured_width"], vcm.COMPOSITION_WIDTH)
        self.assertEqual(result["measured_height"], vcm.COMPOSITION_HEIGHT)
        self.assertEqual(result["video_codec_fourcc"], "avc1")
        self.assertEqual(result["audio_codec_fourcc"], "mp4a")
        self.assertEqual(renderer.runtimes_invoked, ["remotion"])
        self.assertEqual(probe.calls, 1)

    def test_lineage_is_complete(self):
        lineage = write_cuts(self.tmp, 2)
        result = self.compose(cut_lineage=lineage)
        self.assertEqual(result["job_id"], JOB_ID)
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["storyboard_id"], SB_ID)
        self.assertEqual(result["product_id"], PRODUCT_ID)
        self.assertEqual(result["market"], MARKET)
        self.assertEqual([c["output_sha256"] for c in result["input_cuts"]],
                         [c["output_sha256"] for c in lineage])
        with open(self.out, "rb") as fh:
            self.assertEqual(result["output_sha256"],
                             hashlib.sha256(fh.read()).hexdigest())
        self.assertEqual(result["render_settings"]["aspect_ratio"], "9:16")
        self.assertEqual(result["render_settings"]["resolution"], "768P")

    def test_disclosure_and_captions_are_in_the_overlay_plan(self):
        renderer = RendererSpy()
        result = self.compose(renderer=renderer)
        plan = renderer.calls[0]["overlay_plan"]
        self.assertEqual(plan["disclosure"]["text"], DISCLOSURE_TEXT["KR"])
        texts = [t["text"] for t in plan["text_layers"]]
        for cap in CAPTIONS:
            self.assertIn(cap, texts)
        self.assertIn(CTA, texts)
        self.assertTrue(result["disclosure_included"])
        self.assertEqual(result["captions"], CAPTIONS)

    def test_props_written_to_disk_carry_the_disclosure(self):
        result = self.compose()
        import json
        with open(result["props_path"], encoding="utf-8") as fh:
            props = json.load(fh)
        self.assertEqual(props["disclosure"]["text"], DISCLOSURE_TEXT["KR"])

    def test_single_cut_and_three_cuts_are_allowed(self):
        for n, secs in ((1, 5), (3, 15)):
            with self.subTest(n=n):
                out = os.path.join(self.tmp, f"o{n}.mp4")
                r = self.compose(n_cuts=n, output_path=out,
                                 renderer=RendererSpy(
                                     mp4=build_mp4(duration_seconds=float(secs))))
                self.assertEqual(r["measured_duration_seconds"], float(secs))


# ---------------------------------------------------------------------------
# 2. Remotion 전용 — 대체 금지
# ---------------------------------------------------------------------------


class TestRemotionOnly(ComposeTestBase):

    def test_unavailable_remotion_fails_and_renders_nothing(self):
        renderer = RendererSpy()
        with self.assertRaises(vcm.RuntimeUnavailableError):
            self.compose(renderer=renderer, probe=ProbeSpy(available=False))
        self.assertEqual(renderer.calls, [], "Remotion 없으면 렌더는 0회여야 한다")
        self.assertEqual(renderer.runtimes_invoked, [],
                         "다른 런타임으로 대체 호출이 있어선 안 된다")
        self.assertFalse(os.path.exists(self.out))

    def test_edit_decisions_naming_another_runtime_is_rejected(self):
        for bad in ("ffmpeg", "hyperframes", "moviepy", "", None):
            with self.subTest(runtime=bad):
                renderer, probe = RendererSpy(), ProbeSpy()
                ed = dict(EDIT_DECISIONS, render_runtime=bad)
                with self.assertRaises(vcm.RuntimeSwapError):
                    self.compose(edit_decisions=ed, renderer=renderer, probe=probe)
                self.assertEqual(renderer.calls, [])
                self.assertEqual(probe.calls, 0,
                                 "런타임 락 위반은 프리플라이트보다 먼저 거부해야 한다")

    def test_renderer_reporting_another_runtime_is_rejected(self):
        for bad in ("hyperframes", "ffmpeg", "unknown"):
            with self.subTest(runtime=bad):
                out = os.path.join(self.tmp, f"swap_{bad}.mp4")
                with self.assertRaises(vcm.RuntimeSwapError):
                    self.compose(renderer=RendererSpy(runtime=bad), output_path=out)
                self.assertFalse(os.path.exists(out),
                                 "런타임 스왑 산출물은 남기지 않는다")

    def test_composition_mode_must_be_atelier(self):
        renderer = RendererSpy()
        ed = dict(EDIT_DECISIONS, composition_mode="template")
        with self.assertRaises(vcm.RuntimeSwapError):
            self.compose(edit_decisions=ed, renderer=renderer)
        self.assertEqual(renderer.calls, [])

    def test_only_remotion_is_an_allowed_runtime(self):
        self.assertEqual(vcm.ALLOWED_RENDER_RUNTIMES, ("remotion",))
        self.assertEqual(vcm.RENDER_RUNTIME, "remotion")

    def test_module_exposes_no_alternate_runtime_entrypoint(self):
        names = [n.lower() for n in dir(vcm)]
        for banned in ("ffmpeg", "hyperframes", "moviepy", "fallback"):
            hits = [n for n in names if banned in n]
            self.assertEqual(hits, [],
                             f"대체 런타임 진입점이 존재해선 안 된다: {hits}")

    def test_missing_runtime_version_is_rejected(self):
        with self.assertRaises(vcm.RuntimeUnavailableError):
            self.compose(probe=ProbeSpy(version=""))


# ---------------------------------------------------------------------------
# 3. 고지 생존 (SSOT 불변 규칙 2)
# ---------------------------------------------------------------------------


class TestDisclosureSurvives(ComposeTestBase):

    def test_missing_disclosure_block_is_rejected_before_render(self):
        renderer, probe = RendererSpy(), ProbeSpy()
        sb = make_storyboard(2)
        sb.pop("disclosure")
        with self.assertRaises(vcm.DisclosureError):
            self.compose(storyboard=sb, renderer=renderer, probe=probe)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(probe.calls, 0)

    def test_empty_disclosure_text_is_rejected(self):
        sb = make_storyboard(2, disclosure={"market": "KR", "required": True,
                                            "text": "   ",
                                            "placement": "on_screen_and_caption"})
        renderer = RendererSpy()
        with self.assertRaises(vcm.DisclosureError):
            self.compose(storyboard=sb, renderer=renderer)
        self.assertEqual(renderer.calls, [])

    def test_paraphrased_disclosure_is_rejected(self):
        sb = make_storyboard(2, disclosure={
            "market": "KR", "required": True,
            "text": "쿠팡 파트너스로 수수료를 받을 수도 있어요",
            "placement": "on_screen_and_caption"})
        with self.assertRaises(vcm.DisclosureError):
            self.compose(storyboard=sb, renderer=RendererSpy())

    def test_us_market_requires_the_associates_line(self):
        sb = make_storyboard(2)
        sb["market"] = "US"
        sb["disclosure"] = {"market": "US", "required": True,
                            "text": DISCLOSURE_TEXT["US"],
                            "placement": "on_screen_and_caption"}
        r = self.compose(storyboard=sb, renderer=RendererSpy())
        self.assertEqual(r["disclosure_text"], DISCLOSURE_TEXT["US"])

    def test_render_that_drops_the_disclosure_is_rejected_not_shipped(self):
        renderer = RendererSpy(drop_disclosure=True)
        with self.assertRaises(vcm.DisclosureError):
            self.compose(renderer=renderer)
        self.assertTrue(renderer.calls, "이 케이스는 렌더 후 검증이어야 한다")
        self.assertFalse(os.path.exists(self.out),
                         "고지 없는 산출물은 폐기돼야 한다")

    def test_disclosure_must_cover_the_whole_video(self):
        renderer = RendererSpy()
        self.compose(renderer=renderer)
        disc = renderer.calls[0]["overlay_plan"]["disclosure"]
        self.assertEqual(disc["start_seconds"], 0)
        self.assertEqual(disc["end_seconds"], 10)

    def test_disclosure_style_is_legible(self):
        renderer = RendererSpy()
        self.compose(renderer=renderer)
        style = renderer.calls[0]["overlay_plan"]["disclosure"]["style"]
        self.assertGreaterEqual(style["font_size_px"], vcm.MIN_DISCLOSURE_FONT_PX)
        self.assertGreaterEqual(style["safe_area_margin_px"],
                                vcm.MIN_SAFE_AREA_MARGIN_PX)
        self.assertTrue(style["background_scrim"])


# ---------------------------------------------------------------------------
# 4. 승인 카피 무변형
# ---------------------------------------------------------------------------


class TestApprovedCaptions(ComposeTestBase):

    def test_rendered_caption_drift_is_rejected(self):
        renderer = RendererSpy(
            mutate_caption=lambda t: t.replace("한 스푼", "두 스푼"))
        with self.assertRaises(vcm.CaptionDriftError):
            self.compose(renderer=renderer)
        self.assertFalse(os.path.exists(self.out))

    def test_missing_caption_layer_is_rejected(self):
        renderer = RendererSpy(text_layers=[DISCLOSURE_TEXT["KR"], CTA])
        with self.assertRaises(vcm.CaptionDriftError):
            self.compose(renderer=renderer)

    def test_extra_unapproved_text_layer_is_rejected(self):
        base = CAPTIONS + [CTA, DISCLOSURE_TEXT["KR"]]
        renderer = RendererSpy(text_layers=base + ["지금 사면 키가 큽니다"])
        with self.assertRaises(vcm.CaptionDriftError):
            self.compose(renderer=renderer)

    def test_empty_approved_caption_is_rejected_before_render(self):
        renderer, probe = RendererSpy(), ProbeSpy()
        sb = make_storyboard(2)
        sb["cuts"][1]["voice_line"] = "  "
        with self.assertRaises(vcm.CaptionDriftError):
            self.compose(storyboard=sb, renderer=renderer, probe=probe)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(probe.calls, 0)

    def test_captions_are_verbatim_from_the_storyboard(self):
        renderer = RendererSpy()
        result = self.compose(renderer=renderer)
        self.assertEqual(result["captions"], CAPTIONS)
        self.assertEqual(result["caption_source"], "storyboard.cuts[].voice_line")

    def test_empty_cta_is_rejected(self):
        with self.assertRaises(vcm.CaptionDriftError):
            self.compose(cta_text="   ", renderer=RendererSpy())


# ---------------------------------------------------------------------------
# 5. 길이 / 형식 — 실제 파일에서 측정
# ---------------------------------------------------------------------------


class TestDurationAndFormat(ComposeTestBase):

    def test_four_cuts_is_rejected_before_render(self):
        renderer, probe = RendererSpy(), ProbeSpy()
        with self.assertRaises(vcm.ComposeDurationError):
            self.compose(n_cuts=4, renderer=renderer, probe=probe)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(probe.calls, 0)

    def test_zero_cuts_is_rejected(self):
        with self.assertRaises(vcm.ComposeDurationError):
            self.compose(n_cuts=0, renderer=RendererSpy())

    def test_measured_twelve_seconds_for_a_ten_second_plan_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4(duration_seconds=12.0))
        with self.assertRaises(vcm.ComposeDurationError):
            self.compose(renderer=renderer)
        self.assertFalse(os.path.exists(self.out))

    def test_landscape_output_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4(width=vcm.COMPOSITION_HEIGHT,
                                             height=vcm.COMPOSITION_WIDTH))
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)

    def test_non_nine_sixteen_portrait_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4(width=768, height=1024))
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)

    def test_wrong_resolution_class_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4(width=540, height=960))
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)

    def test_non_h264_video_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4(video_codec=b"hev1"))
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)

    def test_missing_audio_track_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4(audio_codec=None))
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)

    def test_non_aac_audio_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4(audio_codec=b"ac-3"))
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)

    def test_truncated_output_is_rejected(self):
        renderer = RendererSpy(mp4=build_mp4()[:40])
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)

    def test_non_mp4_output_is_rejected(self):
        renderer = RendererSpy(mp4=b"not an mp4 at all" * 8)
        with self.assertRaises(vcm.ComposeFormatError):
            self.compose(renderer=renderer)


class TestMeasureMp4(ComposeTestBase):
    """측정은 선언이 아니라 파일 바이트에서 나온다."""

    def _write(self, data: bytes) -> str:
        path = os.path.join(self.tmp, "probe.mp4")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_reads_real_boxes(self):
        m = vcm.measure_mp4(self._write(build_mp4(duration_seconds=15.0)))
        self.assertEqual(m["width"], vcm.COMPOSITION_WIDTH)
        self.assertEqual(m["height"], vcm.COMPOSITION_HEIGHT)
        self.assertAlmostEqual(m["duration_seconds"], 15.0, places=3)
        self.assertEqual(m["video_codec_fourcc"], "avc1")
        self.assertEqual(m["audio_codec_fourcc"], "mp4a")

    def test_missing_moov_is_rejected(self):
        ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isom")
        with self.assertRaises(vcm.ComposeFormatError):
            vcm.measure_mp4(self._write(ftyp + _box(b"mdat", b"\x00" * 32)))

    def test_zero_duration_is_rejected(self):
        with self.assertRaises(vcm.ComposeFormatError):
            vcm.measure_mp4(self._write(build_mp4(duration_seconds=0.0)))


# ---------------------------------------------------------------------------
# 6. 입력 계보
# ---------------------------------------------------------------------------


class TestInputLineage(ComposeTestBase):

    def test_tampered_cut_hash_is_rejected_before_render(self):
        renderer, probe = RendererSpy(), ProbeSpy()
        lineage = write_cuts(self.tmp, 2)
        lineage[0]["output_sha256"] = "0" * 64
        with self.assertRaises(vcm.ComposeLineageError):
            self.compose(cut_lineage=lineage, renderer=renderer, probe=probe)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(probe.calls, 0)

    def test_missing_cut_file_is_rejected(self):
        lineage = write_cuts(self.tmp, 2)
        os.unlink(lineage[1]["output_path"])
        with self.assertRaises(vcm.ComposeLineageError):
            self.compose(cut_lineage=lineage, renderer=RendererSpy())

    def test_cut_count_must_match_the_storyboard(self):
        lineage = write_cuts(self.tmp, 2)[:1]
        with self.assertRaises(vcm.ComposeLineageError):
            self.compose(cut_lineage=lineage, renderer=RendererSpy())

    def test_out_of_order_cuts_are_rejected(self):
        lineage = write_cuts(self.tmp, 2)
        lineage[0]["cut_index"] = 3
        with self.assertRaises(vcm.ComposeLineageError):
            self.compose(cut_lineage=lineage, renderer=RendererSpy())

    def test_missing_job_id_is_rejected(self):
        with self.assertRaises(vcm.ComposeLineageError):
            self.compose(job_id="", renderer=RendererSpy())

    def test_unknown_market_is_rejected(self):
        sb = make_storyboard(2)
        sb["market"] = "JP"
        with self.assertRaises(vcm.ComposeLineageError):
            self.compose(storyboard=sb, renderer=RendererSpy())


if __name__ == "__main__":
    unittest.main(verbosity=2)
