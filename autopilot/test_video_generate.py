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

    def __init__(self, width=1024, height=1536, fail=None):
        self.width = width
        self.height = height
        self.fail = fail
        self.calls = []

    def __call__(self, prompt, source_images, output_path, **kwargs):
        self.calls.append({"prompt": prompt,
                           "source_images": list(source_images),
                           "output_path": output_path})
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
        with self.assertRaises(vg.SourceAssetError):
            self.run_generate(asset_manifest=manifest)

    def test_empty_asset_manifest_rejected(self):
        manifest = dict(self.asset_manifest)
        manifest["assets"] = []
        with self.assertRaises(vg.SourceAssetError):
            self.run_generate(asset_manifest=manifest)

    def test_product_id_mismatch_rejected(self):
        manifest = dict(self.asset_manifest)
        manifest["product_id"] = "other-product"
        with self.assertRaises(vg.SourceAssetError):
            self.run_generate(asset_manifest=manifest)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
