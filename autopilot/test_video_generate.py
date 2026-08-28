#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video_generate 테스트 — 컷당 첫 프레임 1장.

네트워크·유료 호출 절대 금지. Codex 브리지는 ``bridge=`` 시드로,
Hermes 프리플라이트는 ``preflight_runner=`` 시드로 주입한다.
PNG 바이트는 로컬에서 합성한다.
"""

import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import video_generate as vg
import video_storyboard as vs


# ---------------------------------------------------------------------------
# 로컬 PNG 합성 (네트워크 없음)
# ---------------------------------------------------------------------------


def make_png(width=1024, height=1536):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b"".join(b"\x00" + b"\x7f\x7f\x7f" * width for _ in range(height))
    return sig + ihdr + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


OK_AUTH = ("provider        status\n"
           "openai-codex    authorized (oauth)\n"
           "anthropic       authorized\n")
OK_IMAGE_CFG = ('{"provider": "openai-codex", "model": "gpt-image-2-medium"}\n')


def preflight_ok(cmd, timeout=None):
    joined = " ".join(cmd)
    if "auth" in joined:
        return {"returncode": 0, "stdout": OK_AUTH, "stderr": ""}
    return {"returncode": 0, "stdout": OK_IMAGE_CFG, "stderr": ""}


def make_preflight(auth=OK_AUTH, image=OK_IMAGE_CFG, auth_rc=0, image_rc=0):
    def runner(cmd, timeout=None):
        joined = " ".join(cmd)
        if "auth" in joined:
            return {"returncode": auth_rc, "stdout": auth, "stderr": ""}
        return {"returncode": image_rc, "stdout": image, "stderr": ""}
    return runner


# ---------------------------------------------------------------------------
# 가짜 Codex 브리지 — 실제 지출 없음
# ---------------------------------------------------------------------------


class FakeBridge:
    """codex_image_bridge.edit_image 대체. 호출 인자를 전부 기록한다."""

    def __init__(self, width=1024, height=1536, fail=None, partial_bytes=None):
        self.width = width
        self.height = height
        self.fail = fail
        #: 쓰다 만 바이트를 남기고 죽는 브리지 (트런케이트 다운로드·디스크 오류).
        self.partial_bytes = partial_bytes
        self.calls = []

    def __call__(self, prompt, source_images, output_path, **kwargs):
        self.calls.append({"prompt": prompt,
                           "source_images": list(source_images),
                           "output_path": output_path})
        if self.partial_bytes is not None:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as fh:
                fh.write(self.partial_bytes)
            raise self.fail or RuntimeError("bridge died mid-write")
        if self.fail:
            raise self.fail
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(make_png(self.width, self.height))
        return {
            "model_alias": vg.MODEL_ALIAS,
            "hermes_provider": vg.HERMES_PROVIDER,
            "hermes_model": vg.HERMES_MODEL,
            "provider_model": vg.PROVIDER_MODEL,
            # 플러그인이 요청값을 에코할 뿐 실제 측정이 아니다 — 일부러 항상 portrait.
            "observed_aspect_ratio": "portrait",
            "observed_pixel_size": None,
            "output_path": output_path,
            "prompt": prompt,
        }


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


def make_cut(index, action="컵을 집어 든다", benefit="아침에 챙기기 쉬움"):
    return vs.StoryboardCut(
        index=index,
        duration_seconds=5,
        action=action,
        benefit=benefit,
        claim="하루 1포",
        evidence_id=f"ev{index}",
        evidence_quote="하루 1포 섭취",
        evidence_source_url="https://example.com/p",
        voice_line="아침마다 한 포씩.",
        first_frame_prompt=f"세로 9:16 정지 프레임 {index}: 손이 스틱을 든다",
        motion_prompt="손이 천천히 올라간다",
    )


def make_storyboard(n_cuts=2, run_id="run-001"):
    return vs.GroundedStoryboard(
        storyboard_id="sb-001",
        run_id=run_id,
        product_id="prod-kr-1",
        market="KR",
        content_draft_id="draft-1",
        viral_pattern_ids=["vp-1"],
        complexity="simple",
        cuts=[make_cut(i) for i in range(1, n_cuts + 1)],
        disclosure={"text": "쿠팡 파트너스 활동으로 수수료를 받습니다."},
        evidence_ids=["ev1"],
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vg-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.projects_root = os.path.join(self.tmp, "projects")
        self.src = os.path.join(self.tmp, "assets", "prod-kr-1.png")
        os.makedirs(os.path.dirname(self.src), exist_ok=True)
        with open(self.src, "wb") as fh:
            fh.write(make_png(800, 800))
        self.asset_manifest = {
            "product_id": "prod-kr-1",
            "market": "KR",
            "assets": [{
                "local_path": self.src,
                "sha256": vg.sha256_file(self.src),
                "source_sha256": vg.sha256_file(self.src),
                "rights_basis": "official_product_page",
                "source_url": "https://example.com/img.png",
            }],
        }

    def run_generate(self, **kw):
        kw.setdefault("bridge", FakeBridge())
        kw.setdefault("preflight_runner", preflight_ok)
        kw.setdefault("projects_root", self.projects_root)
        kw.setdefault("storyboard", make_storyboard())
        kw.setdefault("asset_manifest", self.asset_manifest)
        sb = kw.pop("storyboard")
        am = kw.pop("asset_manifest")
        return vg.generate_first_frames(sb, am, **kw)


# ---------------------------------------------------------------------------
# 정상 경로
# ---------------------------------------------------------------------------


class TestHappyPath(Base):
    def test_one_frame_per_cut_in_run_frames_dir(self):
        bridge = FakeBridge()
        result = self.run_generate(bridge=bridge)
        self.assertEqual(len(result["frames"]), 2)
        self.assertEqual(len(bridge.calls), 2)
        expected_dir = os.path.join(self.projects_root, "heightcue_run-001",
                                    "assets", "frames")
        for frame in result["frames"]:
            self.assertEqual(os.path.dirname(frame["output_path"]), expected_dir)
            self.assertTrue(os.path.isfile(frame["output_path"]))

    def test_each_frame_uses_only_its_own_cut_prompt(self):
        bridge = FakeBridge()
        self.run_generate(bridge=bridge)
        self.assertIn("프레임 1", bridge.calls[0]["prompt"])
        self.assertNotIn("프레임 2", bridge.calls[0]["prompt"])
        self.assertIn("프레임 2", bridge.calls[1]["prompt"])
        self.assertNotIn("프레임 1", bridge.calls[1]["prompt"])

    def test_source_image_is_the_official_product_asset(self):
        bridge = FakeBridge()
        self.run_generate(bridge=bridge)
        for call in bridge.calls:
            self.assertEqual(call["source_images"], [self.src])

    def test_full_lineage_recorded(self):
        result = self.run_generate()
        src_hash = vg.sha256_file(self.src)
        for i, frame in enumerate(result["frames"], start=1):
            self.assertEqual(frame["image_model_alias"], "gpt-image-gen-2")
            self.assertEqual(frame["image_hermes_provider"], "openai-codex")
            self.assertEqual(frame["image_hermes_model"], "gpt-image-2-medium")
            self.assertEqual(frame["image_provider_model"], "gpt-image-2")
            self.assertEqual(frame["source_sha256"], src_hash)
            self.assertEqual(frame["product_id"], "prod-kr-1")
            self.assertEqual(frame["market"], "KR")
            self.assertEqual(frame["run_id"], "run-001")
            self.assertEqual(frame["cut_index"], i)
            self.assertTrue(frame["prompt"].strip())
            self.assertEqual(frame["output_sha256"],
                             vg.sha256_file(frame["output_path"]))
            self.assertEqual((frame["measured_width"], frame["measured_height"]),
                             (1024, 1536))

    def test_product_truth_preserved_in_prompt_instructions(self):
        bridge = FakeBridge()
        self.run_generate(bridge=bridge)
        sent = bridge.calls[0]["prompt"]
        self.assertIn(vg.PRODUCT_FIDELITY_CLAUSE, sent)


# ---------------------------------------------------------------------------
# 프리플라이트 — 대체 금지, 크게 실패
# ---------------------------------------------------------------------------


class TestPreflight(Base):
    def test_missing_codex_oauth_stops_job_without_calling_bridge(self):
        bridge = FakeBridge()
        with self.assertRaises(vg.PreflightError) as ctx:
            self.run_generate(bridge=bridge,
                              preflight_runner=make_preflight(
                                  auth="anthropic  authorized\n"))
        self.assertIn("openai-codex", str(ctx.exception))
        self.assertEqual(bridge.calls, [])

    def test_wrong_image_tier_stops_job_without_calling_bridge(self):
        """provider 는 맞지만 티어가 다르면 — 대체 없이 중단."""
        bridge = FakeBridge()
        with self.assertRaises(vg.PreflightError) as ctx:
            self.run_generate(bridge=bridge,
                              preflight_runner=make_preflight(
                                  image='{"provider": "openai-codex", '
                                        '"model": "gpt-image-2-low"}'))
        self.assertIn("gpt-image-2-medium", str(ctx.exception))
        self.assertEqual(bridge.calls, [])

    def test_wrong_image_provider_stops_job_without_calling_bridge(self):
        bridge = FakeBridge()
        with self.assertRaises(vg.PreflightError) as ctx:
            self.run_generate(bridge=bridge,
                              preflight_runner=make_preflight(
                                  image='{"provider": "openai", '
                                        '"model": "gpt-image-1"}'))
        self.assertIn("openai-codex", str(ctx.exception))
        self.assertEqual(bridge.calls, [])

    def test_preflight_command_failure_stops_job(self):
        bridge = FakeBridge()
        with self.assertRaises(vg.PreflightError):
            self.run_generate(bridge=bridge,
                              preflight_runner=make_preflight(auth_rc=1))
        self.assertEqual(bridge.calls, [])

    def test_no_fallback_provider_is_ever_used(self):
        """프리플라이트 실패 시 어떤 대체 이미지 경로도 열리지 않는다."""
        with self.assertRaises(vg.PreflightError):
            self.run_generate(preflight_runner=make_preflight(auth_rc=1))
        frames_dir = os.path.join(self.projects_root, "heightcue_run-001",
                                  "assets", "frames")
        self.assertFalse(os.path.isdir(frames_dir) and os.listdir(frames_dir))

    def test_preflight_result_reports_pinned_identifiers(self):
        info = vg.preflight_codex(runner=preflight_ok)
        self.assertEqual(info["provider"], "openai-codex")
        self.assertEqual(info["model"], "gpt-image-2-medium")


# ---------------------------------------------------------------------------
# 측정된 세로비 — 에코를 믿지 않는다
# ---------------------------------------------------------------------------


class TestMeasuredPortrait(Base):
    def test_landscape_output_rejected_despite_portrait_echo(self):
        bridge = FakeBridge(width=1536, height=1024)  # 에코는 여전히 portrait
        with self.assertRaises(vg.PortraitError) as ctx:
            self.run_generate(bridge=bridge)
        self.assertIn("1536x1024", str(ctx.exception))

    def test_square_output_rejected(self):
        with self.assertRaises(vg.PortraitError):
            self.run_generate(bridge=FakeBridge(width=1024, height=1024))

    def test_wrong_portrait_size_rejected(self):
        with self.assertRaises(vg.PortraitError):
            self.run_generate(bridge=FakeBridge(width=768, height=1344))

    def test_measure_reads_ihdr_directly(self):
        path = os.path.join(self.tmp, "m.png")
        with open(path, "wb") as fh:
            fh.write(make_png(1024, 1536))
        self.assertEqual(vg.measure_png(path), (1024, 1536))

    def test_non_png_output_rejected(self):
        path = os.path.join(self.tmp, "n.png")
        with open(path, "wb") as fh:
            fh.write(b"<!DOCTYPE html><html>nope</html>")
        with self.assertRaises(vg.PortraitError):
            vg.measure_png(path)

    def test_rejected_frame_is_not_left_on_disk(self):
        with self.assertRaises(vg.PortraitError):
            self.run_generate(bridge=FakeBridge(width=1024, height=1024))
        frames_dir = os.path.join(self.projects_root, "heightcue_run-001",
                                  "assets", "frames")
        self.assertEqual(os.listdir(frames_dir) if os.path.isdir(frames_dir)
                         else [], [])


# ---------------------------------------------------------------------------
# 후보 상한
# ---------------------------------------------------------------------------


class TestCandidateCap(Base):
    def test_cap_constant_is_three(self):
        self.assertEqual(vg.MAX_FIRST_FRAME_CANDIDATES, 3)

    def test_exceeding_cap_rejected_before_any_spend(self):
        bridge = FakeBridge()
        sb = make_storyboard(n_cuts=2)
        sb.cuts = [make_cut(i) for i in range(1, 5)]  # 4컷 → 4장 요구
        with self.assertRaises(vg.CandidateCapError) as ctx:
            self.run_generate(bridge=bridge, storyboard=sb)
        self.assertIn("3", str(ctx.exception))
        self.assertEqual(bridge.calls, [])

    def test_cap_boundary_three_allowed(self):
        result = self.run_generate(storyboard=make_storyboard(n_cuts=3))
        self.assertEqual(len(result["frames"]), 3)


# ---------------------------------------------------------------------------
# 소스 이미지 출처
# ---------------------------------------------------------------------------


class TestSourceOrigin(Base):
    def test_source_outside_product_assets_rejected(self):
        stray = os.path.join(self.tmp, "stray.png")
        with open(stray, "wb") as fh:
            fh.write(make_png(800, 800))
        manifest = dict(self.asset_manifest)
        manifest["assets"] = [{"local_path": stray, "sha256": "0" * 64}]
        bridge = FakeBridge()
        with self.assertRaises(vg.SourceAssetError):
            self.run_generate(bridge=bridge, asset_manifest=manifest)
        self.assertEqual(bridge.calls, [])

    def test_non_official_rights_basis_rejected(self):
        manifest = dict(self.asset_manifest)
        asset = dict(self.asset_manifest["assets"][0])
        asset["rights_basis"] = "creator_photo"
        manifest["assets"] = [asset]
        bridge = FakeBridge()
        with self.assertRaises(vg.SourceAssetError):
            self.run_generate(asset_manifest=manifest, bridge=bridge)
        self.assertEqual(bridge.calls, [])

    def test_empty_asset_manifest_rejected(self):
        manifest = dict(self.asset_manifest)
        manifest["assets"] = []
        bridge = FakeBridge()
        with self.assertRaises(vg.SourceAssetError):
            self.run_generate(asset_manifest=manifest, bridge=bridge)
        self.assertEqual(bridge.calls, [])

    def test_product_id_mismatch_rejected(self):
        manifest = dict(self.asset_manifest)
        manifest["product_id"] = "other-product"
        bridge = FakeBridge()
        with self.assertRaises(vg.SourceAssetError):
            self.run_generate(asset_manifest=manifest, bridge=bridge)
        self.assertEqual(bridge.calls, [])


# ---------------------------------------------------------------------------
# 프리플라이트 — 인증 문자열 토큰 판정 / 부분 기록 정리 / PNG 무결성
# ---------------------------------------------------------------------------


class _Cut:
    """계약 검증을 우회해 거부 경로만 겨누는 최소 컷 스텁."""

    def __init__(self, index, first_frame_prompt="세로 프레임"):
        self.index = index
        self.first_frame_prompt = first_frame_prompt
        self.motion_prompt = "손이 올라간다"


class _Storyboard:
    def __init__(self, cuts):
        self.storyboard_id = "sb-001"
        self.run_id = "run-001"
        self.product_id = "prod-kr-1"
        self.market = "KR"
        self.cuts = cuts


class TestAuthStatusToken(Base):
    """`authorized` 는 `unauthorized` 의 부분문자열이다 — 토큰으로 봐야 한다."""

    def test_unauthorized_provider_stops_job_without_calling_bridge(self):
        bridge = FakeBridge()
        with self.assertRaises(vg.PreflightError) as ctx:
            self.run_generate(bridge=bridge, preflight_runner=make_preflight(
                auth="provider     status\nopenai-codex  unauthorized\n"))
        self.assertIn("openai-codex", str(ctx.exception))
        self.assertEqual(bridge.calls, [])

    def test_not_authorized_provider_stops_job_without_calling_bridge(self):
        bridge = FakeBridge()
        with self.assertRaises(vg.PreflightError):
            self.run_generate(bridge=bridge, preflight_runner=make_preflight(
                auth="provider     status\nopenai-codex  not authorized\n"))
        self.assertEqual(bridge.calls, [])

    def test_expired_was_authorized_stops_job(self):
        bridge = FakeBridge()
        with self.assertRaises(vg.PreflightError):
            self.run_generate(bridge=bridge, preflight_runner=make_preflight(
                auth="openai-codex  expired (was authorized)\n"))
        self.assertEqual(bridge.calls, [])

    def test_config_without_provider_key_says_so(self):
        """JSON 은 파싱되지만 provider 키가 없는 경우 — 오류 문구가 구분돼야 한다."""
        with self.assertRaises(vg.PreflightError) as ctx:
            self.run_generate(preflight_runner=make_preflight(
                image='{"model": "gpt-image-2-medium"}'))
        self.assertIn("provider", str(ctx.exception))
        self.assertIn("없다", str(ctx.exception))


class TestPartialWriteCleanup(Base):
    def test_bridge_raising_mid_write_leaves_no_file(self):
        bridge = FakeBridge(partial_bytes=make_png(1024, 1536)[:40])
        with self.assertRaises(RuntimeError):
            self.run_generate(bridge=bridge)
        frames_dir = os.path.join(self.projects_root, "heightcue_run-001",
                                  "assets", "frames")
        leftovers = (os.listdir(frames_dir) if os.path.isdir(frames_dir) else [])
        self.assertEqual(leftovers, [], f"부분 산출물이 남았다: {leftovers}")

    def test_bridge_raising_before_write_leaves_no_file(self):
        bridge = FakeBridge(fail=RuntimeError("bridge exploded"))
        with self.assertRaises(RuntimeError):
            self.run_generate(bridge=bridge)
        frames_dir = os.path.join(self.projects_root, "heightcue_run-001",
                                  "assets", "frames")
        leftovers = (os.listdir(frames_dir) if os.path.isdir(frames_dir) else [])
        self.assertEqual(leftovers, [])


class TestPngIntegrity(Base):
    def _write(self, data):
        path = os.path.join(self.tmp, "probe.png")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_truncated_png_with_intact_header_is_rejected(self):
        """앞 33바이트만 멀쩡한 잘린 응답을 1024x1536 으로 받아들이면 안 된다."""
        path = self._write(make_png(1024, 1536)[:33])
        with self.assertRaises(vg.PortraitError):
            vg.measure_png(path)

    def test_corrupt_ihdr_crc_is_rejected(self):
        good = make_png(1024, 1536)
        bad = good[:29] + bytes([good[29] ^ 0xFF]) + good[30:]
        with self.assertRaises(vg.PortraitError):
            vg.measure_png(self._write(bad))

    def test_missing_iend_is_rejected(self):
        good = make_png(1024, 1536)
        with self.assertRaises(vg.PortraitError):
            vg.measure_png(self._write(good[:-12]))

    def test_missing_idat_is_rejected(self):
        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
        sig = b"\x89PNG\r\n\x1a\x0a"[:8]
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 1536, 8, 2, 0, 0, 0))
        with self.assertRaises(vg.PortraitError):
            vg.measure_png(self._write(sig + ihdr + chunk(b"IEND", b"")))

    def test_valid_png_still_measures(self):
        self.assertEqual(vg.measure_png(self._write(make_png(1024, 1536))),
                         (1024, 1536))


class TestPromptAndIndexRefusals(Base):
    def test_empty_first_frame_prompt_rejected_without_calling_bridge(self):
        bridge = FakeBridge()
        sb = _Storyboard([_Cut(1), _Cut(2, first_frame_prompt="   ")])
        with self.assertRaises(vg.FirstFrameError):
            self.run_generate(storyboard=sb, bridge=bridge)
        self.assertEqual(bridge.calls, [])

    def test_duplicate_cut_indices_rejected_without_calling_bridge(self):
        bridge = FakeBridge()
        sb = _Storyboard([_Cut(1), _Cut(1)])
        with self.assertRaises(vg.FirstFrameError) as ctx:
            self.run_generate(storyboard=sb, bridge=bridge)
        self.assertIn("중복", str(ctx.exception))
        self.assertEqual(bridge.calls, [])


# ---------------------------------------------------------------------------
# 매니페스트 식별자 강제
# ---------------------------------------------------------------------------


class TestManifestIdentifiers(Base):
    def _valid(self):
        return self.run_generate()["frames"][0]

    def test_missing_each_identifier_is_rejected(self):
        for key in ("image_model_alias", "image_hermes_provider",
                    "image_hermes_model", "image_provider_model"):
            frame = dict(self._valid())
            frame.pop(key)
            with self.assertRaises(vg.ManifestLineageError,
                                   msg=f"{key} 누락이 통과됐다"):
                vg.assert_frame_lineage(frame)

    def test_wrong_identifier_value_is_rejected(self):
        frame = dict(self._valid())
        frame["image_hermes_model"] = "gpt-image-1"
        with self.assertRaises(vg.ManifestLineageError):
            vg.assert_frame_lineage(frame)

    def test_valid_frame_passes(self):
        vg.assert_frame_lineage(self._valid())


# ===========================================================================
# 컷 생성 (MiniMax H3 Max image-to-video) — 실제 지출 없음
# ===========================================================================


def make_mp4(nbytes=2048):
    """로컬 합성 mp4 바이트 (ftyp 박스 포함). 네트워크 없음."""
    box = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    return box + b"\x00" * max(0, nbytes - len(box))


class FakeFalClient:
    """fal.ai I2V 클라이언트 대체. 모든 요청을 기록한다 — 실제 지출 없음."""

    def __init__(self, fail_with=None, fail_times=None, cost_usd=None,
                 bad_bytes=False):
        #: fail_with 가 있으면 fail_times 회(기본 무한) 실패시킨다.
        self.fail_with = fail_with
        self.fail_times = fail_times
        self.cost_usd = cost_usd
        self.bad_bytes = bad_bytes
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        n = len(self.requests)
        if self.fail_with is not None and (self.fail_times is None
                                           or n <= self.fail_times):
            raise self.fail_with
        path = request["output_path"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"not-an-mp4" if self.bad_bytes else make_mp4())
        out = {"request_id": f"fal-req-{n:03d}", "output_path": path}
        if self.cost_usd is not None:
            out["cost_usd"] = self.cost_usd
        return out


class CutBase(Base):
    def setUp(self):
        super().setUp()
        self.frames = self.run_generate()
        self.ledger = os.path.join(self.tmp, "spend_ledger.json")

    def run_cuts(self, **kw):
        kw.setdefault("client", FakeFalClient())
        kw.setdefault("storyboard", make_storyboard())
        kw.setdefault("frames_manifest", self.frames)
        kw.setdefault("projects_root", self.projects_root)
        kw.setdefault("ledger_path", self.ledger)
        kw.setdefault("job_id", "job-001")
        kw.setdefault("sleep", lambda _s: None)
        kw.setdefault("today", "2026-08-28")
        sb = kw.pop("storyboard")
        fm = kw.pop("frames_manifest")
        return vg.generate_cuts(sb, fm, **kw)


class TestPinnedRequestShape(CutBase):
    def test_two_cuts_make_exactly_two_requests(self):
        client = FakeFalClient()
        result = self.run_cuts(client=client)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(result["manifest"]["cuts"]), 2)
        self.assertEqual(result["state"], "ready_to_publish")

    def test_every_request_is_pinned_i2v_5s_768p_9x16(self):
        client = FakeFalClient()
        self.run_cuts(client=client)
        for req in client.requests:
            self.assertEqual(req["url"], vg.FAL_I2V_URL)
            self.assertEqual(req["endpoint"], "minimax/h3-max/image-to-video")
            self.assertEqual(req["operation"], "image_to_video")
            self.assertEqual(req["payload"]["duration"], 5)
            self.assertEqual(req["payload"]["resolution"], "768P")
            self.assertEqual(req["payload"]["aspect_ratio"], "9:16")
            self.assertTrue(req["payload"]["image_url"])

    def test_each_cut_uses_its_own_first_frame(self):
        client = FakeFalClient()
        self.run_cuts(client=client)
        hashes = [req["first_frame_sha256"] for req in client.requests]
        expected = [f["output_sha256"] for f in self.frames["frames"]]
        self.assertEqual(hashes, expected)

    def test_non_five_second_request_is_impossible(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(frame, motion_prompt="x", output_path="/t/a.mp4",
                                 duration_seconds=7)

    def test_non_768p_request_is_impossible(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(frame, motion_prompt="x", output_path="/t/a.mp4",
                                 resolution="480P")

    def test_text_to_video_operation_is_impossible(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(frame, motion_prompt="x", output_path="/t/a.mp4",
                                 operation="text_to_video")


class TestCostGate(CutBase):
    def test_pricing_constants(self):
        self.assertEqual(vg.RATE_USD_PER_SECOND_768P, 0.04)
        self.assertEqual(vg.estimate_cut_cost_usd(), 0.20)
        self.assertGreater(vg.MAX_RUN_SPEND_USD, 0)
        self.assertGreater(vg.MAX_DAILY_SPEND_USD, 0)

    def test_run_cap_refuses_before_any_request(self):
        client = FakeFalClient()
        with self.assertRaises(vg.CostGateError) as ctx:
            self.run_cuts(client=client, run_cap_usd=0.10)
        self.assertIn("run", str(ctx.exception).lower())
        self.assertEqual(client.requests, [])

    def test_daily_cap_refuses_before_any_request(self):
        client = FakeFalClient()
        vg.reserve_spend(self.ledger, "2026-08-28", vg.MAX_DAILY_SPEND_USD,
                         run_id="other", cut_index=1,
                         daily_cap_usd=vg.MAX_DAILY_SPEND_USD)
        with self.assertRaises(vg.CostGateError) as ctx:
            self.run_cuts(client=client)
        self.assertIn("day", str(ctx.exception).lower())
        self.assertEqual(client.requests, [])

    def test_caps_cannot_be_raised_at_runtime(self):
        client = FakeFalClient()
        with self.assertRaises(vg.CostGateError):
            self.run_cuts(client=client,
                          run_cap_usd=vg.MAX_RUN_SPEND_USD + 10)
        self.assertEqual(client.requests, [])

    def test_cost_gate_logged_before_the_call(self):
        client = FakeFalClient(fail_with=RuntimeError("HTTP 503 upstream"))
        self.run_cuts(client=client)
        log = os.path.join(vg.cuts_dir_for("run-001", self.projects_root),
                           "cut_generation_events.jsonl")
        rows = [__import__("json").loads(ln)
                for ln in open(log, encoding="utf-8") if ln.strip()]
        gates = [r for r in rows if r["event"] == "cost_gate"]
        self.assertTrue(gates)
        g = gates[0]
        self.assertEqual(g["tool"], vg.VIDEO_TOOL)
        self.assertEqual(g["provider"], vg.VIDEO_PROVIDER)
        self.assertEqual(g["gateway"], vg.VIDEO_GATEWAY)
        self.assertEqual(g["endpoint"], vg.VIDEO_ENDPOINT)
        self.assertEqual(g["model"], vg.VIDEO_MODEL)
        self.assertEqual(g["estimated_cost_usd"], 0.20)
        self.assertEqual(g["approval_policy"], vg.APPROVAL_POLICY)

    def test_reservation_reconciled_to_actual_cost(self):
        self.run_cuts(client=FakeFalClient(cost_usd=0.17))
        day = vg.load_spend(self.ledger)["days"]["2026-08-28"]
        self.assertAlmostEqual(day["actual_usd"], 0.34, places=6)
        self.assertAlmostEqual(day["reserved_usd"], 0.34, places=6)

    def test_failed_cut_releases_its_reservation(self):
        self.run_cuts(client=FakeFalClient(fail_with=RuntimeError("HTTP 503")))
        day = vg.load_spend(self.ledger)["days"]["2026-08-28"]
        self.assertEqual(day["reserved_usd"], 0.0)
        self.assertEqual(day["actual_usd"], 0.0)

    def test_estimate_never_replaces_actual_in_records(self):
        result = self.run_cuts(client=FakeFalClient(cost_usd=0.17))
        for cut in result["cut_lineage"]:
            self.assertEqual(cut["estimated_cost_usd"], 0.20)
            self.assertEqual(cut["actual_cost_usd"], 0.17)
            self.assertTrue(cut["actual_cost_is_provider_reported"])


class TestNoFallback(CutBase):
    def test_failure_never_calls_another_model(self):
        client = FakeFalClient(fail_with=RuntimeError("HTTP 500 boom"))
        result = self.run_cuts(client=client)
        self.assertEqual(result["state"], "retryable_failed")
        self.assertTrue(client.requests)
        for req in client.requests:
            self.assertEqual(req["url"], vg.FAL_I2V_URL)
            self.assertEqual(req["endpoint"], vg.VIDEO_ENDPOINT)
            blob = repr(req).lower()
            self.assertNotIn("hailuo", blob)
            self.assertNotIn("text-to-video", blob)
            self.assertNotIn("text_to_video", blob)

    def test_module_declares_no_fallback_endpoint(self):
        source = open(vg.__file__, encoding="utf-8").read().lower()
        self.assertNotIn("hailuo", source)
        self.assertNotIn("fallback_tools", source)

    def test_no_partial_video_left_on_disk_after_failure(self):
        self.run_cuts(client=FakeFalClient(fail_with=RuntimeError("timeout")))
        cuts_dir = vg.cuts_dir_for("run-001", self.projects_root)
        self.assertEqual([f for f in (os.listdir(cuts_dir)
                                      if os.path.isdir(cuts_dir) else [])
                          if f.endswith(".mp4")], [])


class TestRetryPolicy(CutBase):
    def test_retryable_error_retries_within_limit_then_stops(self):
        client = FakeFalClient(fail_with=RuntimeError("HTTP 503 unavailable"))
        result = self.run_cuts(client=client)
        self.assertEqual(len(client.requests), vg.MAX_CUT_ATTEMPTS)
        self.assertEqual(result["state"], "retryable_failed")
        self.assertEqual(result["attempts"][0], vg.MAX_CUT_ATTEMPTS)

    def test_retryable_error_recovers_within_limit(self):
        client = FakeFalClient(fail_with=RuntimeError("rate limit exceeded"),
                               fail_times=1)
        sb1 = make_storyboard(1)
        frames1 = self.run_generate(storyboard=sb1)
        result = self.run_cuts(client=client, storyboard=sb1,
                               frames_manifest=frames1)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(result["state"], "ready_to_publish")

    def test_content_failure_goes_to_qa_failed_without_retry(self):
        client = FakeFalClient(
            fail_with=RuntimeError("content_policy_violation: blocked"))
        result = self.run_cuts(client=client)
        self.assertEqual(result["state"], "qa_failed")
        self.assertEqual(len(client.requests), 1)

    def test_terminal_states_are_contract_transitions(self):
        import video_contracts as vc
        for state in ("qa_failed", "retryable_failed", "ready_to_publish"):
            vc.assert_transition("generating", state)

    def test_classifier(self):
        self.assertTrue(vg.is_retryable_provider_error(RuntimeError("timeout")))
        self.assertTrue(vg.is_retryable_provider_error(RuntimeError("HTTP 502")))
        self.assertTrue(vg.is_retryable_provider_error(RuntimeError("429 rate limit")))
        self.assertFalse(vg.is_retryable_provider_error(
            RuntimeError("content_policy_violation")))
        self.assertFalse(vg.is_retryable_provider_error(RuntimeError("HTTP 400 bad")))


class TestCutLineage(CutBase):
    def test_full_lineage_recorded(self):
        result = self.run_cuts(client=FakeFalClient())
        for i, cut in enumerate(result["cut_lineage"], start=1):
            self.assertEqual(cut["cut_index"], i)
            self.assertEqual(cut["first_frame_sha256"],
                             self.frames["frames"][i - 1]["output_sha256"])
            self.assertEqual(cut["endpoint"], vg.VIDEO_ENDPOINT)
            self.assertEqual(cut["model"], vg.VIDEO_MODEL)
            self.assertEqual(cut["resolution"], "768P")
            self.assertEqual(cut["aspect_ratio"], "9:16")
            self.assertEqual(cut["duration_seconds"], 5)
            self.assertEqual(cut["provider_request_id"], f"fal-req-{i:03d}")
            self.assertEqual(cut["output_sha256"],
                             vg.sha256_file(cut["output_path"]))
            self.assertEqual(cut["estimated_cost_usd"], 0.20)
            self.assertIn("actual_cost_usd", cut)

    def test_manifest_validates_against_contract(self):
        import video_contracts as vc
        result = self.run_cuts(client=FakeFalClient())
        vc.GenerationManifest.from_dict(result["manifest"]).validate()
        self.assertEqual(result["manifest"]["video_endpoint"], vg.VIDEO_ENDPOINT)
        self.assertEqual(result["manifest"]["resolution"], "768P")

    def test_non_mp4_output_is_a_content_failure(self):
        result = self.run_cuts(client=FakeFalClient(bad_bytes=True))
        self.assertEqual(result["state"], "qa_failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
