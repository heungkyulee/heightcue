#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video_generate 테스트 — 컷당 첫 프레임 1장.

네트워크·유료 호출 절대 금지. Codex 브리지는 ``bridge=`` 시드로,
Hermes 프리플라이트는 ``preflight_runner=`` 시드로 주입한다.
PNG 바이트는 로컬에서 합성한다.
"""

import inspect
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


#: 이 맥에서 실제로 나오는 `hermes auth list` 출력. 'authorized' 라는 단어는
#: 어디에도 없다 — provider 헤더 + 들여쓴 자격증명 행이 전부다.
REAL_AUTH = ("anthropic (1 credentials):\n"
             "  #1  claude_code          oauth   claude_code ←\n"
             "\n"
             "copilot (1 credentials):\n"
             "  #1  gh auth token        api_key gh_cli ←\n"
             "\n"
             "openai-codex (1 credentials):\n"
             "  #1  device_code          oauth   device_code ←\n"
             "\n"
             "openrouter (1 credentials):\n"
             "  #1  OPENROUTER_API_KEY   api_key env:OPENROUTER_API_KEY ←\n")

OK_AUTH = REAL_AUTH
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

    def __init__(self, width=941, height=1672, fail=None, partial_bytes=None):
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
    voice_line = "아침마다 한 포씩."
    return vs.StoryboardCut(
        index=index,
        duration_seconds=5,
        action=action,
        benefit=benefit,
        claim="하루 1포",
        evidence_id=f"ev{index}",
        evidence_quote="하루 1포 섭취",
        evidence_source_url="https://example.com/p",
        voice_line=voice_line,
        first_frame_prompt=f"세로 9:16 정지 프레임 {index}: 손이 스틱을 든다",
        motion_prompt="손이 천천히 올라간다",
        # 실제 파이프라인이 fal 로 보내는 값. 픽스처가 이걸 비워두면 테스트가
        # 무음 영상을 만드는 배선을 초록으로 통과시킨다.
        generation_prompt=(
            f"integrated_multimodal_description: [Shot {index}] one parent "
            f"(S1) {action}, then speaks exactly these words and no others: "
            f"<d>[Korean] {voice_line}</d> No on-screen text of any kind."
            "\n\noverall_soundscape: quiet indoor room tone, close-mic voice."
            "\n\nnon_diegetic_music: N/A"),
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
                             (941, 1672))

    def test_product_truth_preserved_in_prompt_instructions(self):
        bridge = FakeBridge()
        self.run_generate(bridge=bridge)
        sent = bridge.calls[0]["prompt"]
        self.assertIn(vg.PRODUCT_FIDELITY_CLAUSE, sent)

    # -- 2026-08-29 실사고: 생성된 병에 목(neck)이 두 개였다. 위에 흰 캡이
    # 달린 채 아래쪽에 나사산 달린 두 번째 구멍이 생겨 거기서 방울이 떨어졌다.
    # 업라이트 병이 아래로 방울을 흘리라고 시키면 모델은 없는 구멍을 만든다.
    def test_physical_plausibility_clause_present(self):
        clause = vg.PRODUCT_FIDELITY_CLAUSE
        self.assertIn(vg.PHYSICAL_PLAUSIBILITY_CLAUSE, clause)
        low = vg.PHYSICAL_PLAUSIBILITY_CLAUSE.lower()
        # 구멍은 정확히 하나
        self.assertIn("exactly one opening", low)
        # 캡은 그 구멍 위에만
        self.assertIn("cap", low)
        # 방울이 떨어지면 병은 뒤집히거나 기울어져 있어야 한다
        self.assertIn("inverted", low)
        self.assertIn("tilted", low)
        # 부품을 지어내지 말 것
        for banned in ("second", "invent"):
            self.assertIn(banned, low)

    # -- 같은 런에서 컷1의 녹색 배지가 `ORGANIC` 대신 `ORCAIN` 으로 렌더됐다.
    # 패키지 글자는 정확히 복제하거나 아예 읽을 수 없어야 한다.
    def test_label_text_clause_forbids_invented_lettering(self):
        clause = vg.PRODUCT_FIDELITY_CLAUSE
        self.assertIn(vg.LABEL_TEXT_CLAUSE, clause)
        low = vg.LABEL_TEXT_CLAUSE.lower()
        self.assertIn("never invent", low)
        self.assertIn("too small", low)
        self.assertIn("exactly", low)

    # -- task 26 실측: 실패는 파라미터가 아니라 **프레임 안 글자 크기**를 따라간다.
    # 모든 암에서 큰 글자(`Ddrops`, `600 IU`)는 살아남고 작은 글자(`ORGANIC`,
    # `Booster`, 아래첨자 `3`, 잔글씨)는 위조됐다. 그래서 제품 히어로 컷은
    # 라벨을 **크게** 잡고 잔글씨는 **프레임 밖**으로 뺀다.
    def test_tight_framing_clause_demands_large_label_and_no_fine_print(self):
        clause = vg.TIGHT_FRAMING_CLAUSE
        self.assertIn(clause, vg.PRODUCT_FIDELITY_CLAUSE)
        low = clause.lower()
        # 1차 라벨은 프레임 폭의 약 1/3
        self.assertIn("one third", low)
        # 잔글씨/성분표는 프레임 밖
        self.assertIn("out of frame", low)
        for banned in ("supplement facts", "fine print", "ingredient"):
            self.assertIn(banned, low)

    def test_tight_framing_clause_reaches_the_bridge(self):
        bridge = FakeBridge()
        self.run_generate(bridge=bridge)
        for call in bridge.calls:
            self.assertIn(vg.TIGHT_FRAMING_CLAUSE, call["prompt"])

    def test_fidelity_clauses_reach_the_bridge(self):
        bridge = FakeBridge()
        self.run_generate(bridge=bridge)
        for call in bridge.calls:
            self.assertIn(vg.PHYSICAL_PLAUSIBILITY_CLAUSE, call["prompt"])
            self.assertIn(vg.LABEL_TEXT_CLAUSE, call["prompt"])


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

    def test_real_hermes_auth_list_shape_is_accepted(self):
        """이 맥의 실제 `hermes auth list` 출력. 'authorized' 토큰이 없다."""
        info = vg.preflight_codex(runner=make_preflight(auth=REAL_AUTH))
        self.assertEqual(info["provider"], "openai-codex")

    def test_provider_with_zero_credentials_rejected(self):
        auth = ("openai-codex (0 credentials):\n"
                "\nanthropic (1 credentials):\n"
                "  #1  claude_code          oauth   claude_code ←\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))

    def test_provider_header_with_no_credential_rows_rejected(self):
        auth = ("openai-codex (1 credentials):\n"
                "\nanthropic (1 credentials):\n"
                "  #1  claude_code          oauth   claude_code ←\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))

    def test_explicitly_unauthorized_provider_rejected(self):
        auth = ("openai-codex (1 credentials):\n"
                "  #1  device_code          oauth   unauthorized\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))

    def test_expired_credential_rejected(self):
        auth = ("openai-codex (1 credentials):\n"
                "  #1  device_code          oauth   expired (was authorized)\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))

    def test_not_authorized_credential_rejected(self):
        auth = ("openai-codex (1 credentials):\n"
                "  #1  device_code          oauth   not authorized\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))

    def test_revoked_credential_rejected(self):
        auth = ("openai-codex (1 credentials):\n"
                "  #1  device_code          oauth   revoked\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))

    def test_absent_provider_rejected(self):
        auth = ("anthropic (1 credentials):\n"
                "  #1  claude_code          oauth   claude_code ←\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))

    def test_empty_auth_listing_rejected(self):
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=""))

    def test_other_provider_credentials_do_not_vouch_for_codex(self):
        """다른 provider 의 정상 자격증명이 codex 를 대신 인증해선 안 된다."""
        auth = ("openai-codex (1 credentials):\n"
                "\nanthropic (1 credentials):\n"
                "  #1  claude_code          oauth   claude_code ←\n"
                "\ncopilot (1 credentials):\n"
                "  #1  gh auth token        api_key gh_cli ←\n")
        with self.assertRaises(vg.PreflightError):
            vg.preflight_codex(runner=make_preflight(auth=auth))


# ---------------------------------------------------------------------------
# 측정된 세로비 — 에코를 믿지 않는다
# ---------------------------------------------------------------------------


class TestMeasuredPortrait(Base):
    def test_real_941x1672_first_frame_accepted(self):
        """실 provider 출력 941x1672(9:16) 는 통과해야 한다 — 이게 파이프라인 형상이다."""
        bridge = FakeBridge(width=941, height=1672)
        result = self.run_generate(bridge=bridge)
        self.assertTrue(result)

    def test_2x3_1024x1536_rejected(self):
        """1024x1536 은 2:3 이라 9:16 영상에 크롭 없이 못 들어간다 — 거부."""
        with self.assertRaises(vg.PortraitError) as ctx:
            self.run_generate(bridge=FakeBridge(width=1024, height=1536))
        self.assertIn("1024x1536", str(ctx.exception))

    def test_tiny_9x16_rejected(self):
        with self.assertRaises(vg.PortraitError):
            self.run_generate(bridge=FakeBridge(width=90, height=160))

    def test_geometry_rule_is_shared_with_bridge(self):
        import codex_image_bridge as cib_mod
        import video_contracts as vc_mod
        self.assertIs(vg.assert_first_frame_geometry,
                      vc_mod.assert_first_frame_geometry)
        self.assertIs(cib_mod.assert_first_frame_geometry,
                      vc_mod.assert_first_frame_geometry)

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
                 bad_bytes=False, billed_cost_usd=None):
        #: fail_with 가 있으면 fail_times 회(기본 무한) 실패시킨다.
        self.fail_with = fail_with
        self.fail_times = fail_times
        #: provider 가 돌려주는 `cost_usd` — OpenMontage 도구는 여기에 자기
        #: **추정치**를 넣는다(청구액이 아니다).
        self.cost_usd = cost_usd
        #: 실제 청구액을 아는 provider 만 채우는 필드.
        self.billed_cost_usd = billed_cost_usd
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
        if self.billed_cost_usd is not None:
            out["billed_cost_usd"] = self.billed_cost_usd
        return out


def fake_uploaded_url(frame):
    """업로드 어댑터 대체 — 실제 업로드 없이 https URL 만 흉내낸다."""
    return f"https://cdn.example.test/frames/{frame['output_sha256']}.png"


class CutBase(Base):
    def setUp(self):
        super().setUp()
        self.frames = self.run_generate()
        self.ledger = os.path.join(self.tmp, "spend_ledger.json")

    def run_cuts(self, **kw):
        kw.setdefault("client", FakeFalClient())
        kw.setdefault("image_url_for", fake_uploaded_url)
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
            vg.build_cut_request(frame, generation_prompt="x", output_path="/t/a.mp4",
                                 duration_seconds=7)

    def test_non_768p_request_is_impossible(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(frame, generation_prompt="x", output_path="/t/a.mp4",
                                 resolution="480P")

    def test_text_to_video_operation_is_impossible(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(frame, generation_prompt="x", output_path="/t/a.mp4",
                                 operation="text_to_video")

    def test_ken_burns_cut_can_never_become_a_paid_request(self):
        """task 28 — 정지 컷은 **요청 자체를 만들 수 없다.**

        이것이 구조적 보장의 전부다: 요청 생성 지점이 이 함수 하나뿐이므로,
        여기서 막히면 정지 컷이 fal 로 나가는 코드 경로가 존재하지 않는다.
        """
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError) as ctx:
            vg.build_cut_request(
                frame, generation_prompt="S1 speaks.",
                output_path="/t/a.mp4",
                image_url="https://example.com/a.png",
                cut_kind=vs.CUT_KIND_STILL)
        self.assertIn("ken_burns", str(ctx.exception))

    def test_unknown_cut_kind_is_rejected(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(
                frame, generation_prompt="S1 speaks.",
                output_path="/t/a.mp4",
                image_url="https://example.com/a.png",
                cut_kind="slideshow")



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

    def test_failed_attempt_stays_on_the_books_as_possibly_billed(self):
        """제출된 요청은 실패해도 청구될 수 있다 — 원장에서 사라지면 안 된다."""
        client = FakeFalClient(fail_with=RuntimeError("HTTP 503"))
        result = self.run_cuts(client=client)
        day = vg.load_spend(self.ledger)["days"]["2026-08-28"]
        n = len(client.requests)
        self.assertEqual(n, vg.MAX_CUT_ATTEMPTS)
        self.assertAlmostEqual(day["possibly_billed_usd"], 0.20 * n, places=6)
        # 예약이 0 으로 사라지지 않는다 — 상한 압력을 유지한다.
        self.assertAlmostEqual(day["reserved_usd"], 0.20 * n, places=6)
        self.assertEqual(day["actual_usd"], 0.0)
        self.assertAlmostEqual(result["possibly_billed_usd"], 0.20 * n, places=6)

    def test_possibly_billed_failures_count_against_the_run_cap(self):
        """재시도가 실행 상한을 소모해야 한다 — 무료가 아니다."""
        client = FakeFalClient(fail_with=RuntimeError("HTTP 503"))
        result = self.run_cuts(client=client, run_cap_usd=0.50)
        self.assertEqual(len(client.requests), 2)  # 0.20*2=0.40, 3번째는 상한
        self.assertEqual(result["state"], "retryable_failed")

    def test_estimate_never_replaces_actual_in_records(self):
        result = self.run_cuts(client=FakeFalClient(billed_cost_usd=0.17))
        for cut in result["cut_lineage"]:
            self.assertEqual(cut["estimated_cost_usd"], 0.20)
            self.assertEqual(cut["actual_cost_usd"], 0.17)
            self.assertEqual(cut["cost_source"], "provider_billed")


class TestCostSource(CutBase):
    """`actual_` 필드에 추정치가 앉는 것을 구조적으로 막는다."""

    def test_provider_estimate_is_not_recorded_as_actual(self):
        # OpenMontage 도구는 cost_usd 에 자기 추정치를 넣는다.
        result = self.run_cuts(client=FakeFalClient(cost_usd=0.17))
        for cut in result["cut_lineage"]:
            self.assertEqual(cut["cost_source"], "provider_estimate")
            self.assertIsNone(cut["actual_cost_usd"])
            self.assertEqual(cut["provider_reported_cost_usd"], 0.17)
            self.assertEqual(cut["charged_cost_usd"], 0.17)

    def test_local_estimate_when_provider_reports_nothing(self):
        result = self.run_cuts(client=FakeFalClient())
        for cut in result["cut_lineage"]:
            self.assertEqual(cut["cost_source"], "local_estimate")
            self.assertIsNone(cut["actual_cost_usd"])
            self.assertIsNone(cut["provider_reported_cost_usd"])
            self.assertEqual(cut["charged_cost_usd"], 0.20)

    def test_cost_source_reaches_the_contract_manifest(self):
        for client, expected in ((FakeFalClient(billed_cost_usd=0.17),
                                  "provider_billed"),
                                 (FakeFalClient(cost_usd=0.17),
                                  "provider_estimate"),
                                 (FakeFalClient(), "local_estimate")):
            with self.subTest(expected=expected):
                self.setUp()
                result = self.run_cuts(client=client)
                cuts = result["manifest"]["cuts"]
                self.assertTrue(cuts)
                for mcut in cuts:
                    self.assertEqual(mcut["cost_source"], expected)

    def test_cost_source_values_are_the_only_three(self):
        self.assertEqual(set(vg.COST_SOURCES),
                         {"provider_billed", "provider_estimate",
                          "local_estimate"})


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

    def test_module_declares_exactly_one_https_literal(self):
        """구조적 판정: 모듈 전체에 `https://` 리터럴이 정확히 1개다."""
        import re as _re
        source = open(vg.__file__, encoding="utf-8").read()
        hits = _re.findall(r"https://", source)
        self.assertEqual(len(hits), 1, f"https:// 리터럴 {len(hits)}개 — "
                                       "두 번째 엔드포인트가 생겼다")
        self.assertTrue(vg.FAL_I2V_URL.startswith("https://"))
        self.assertIn(vg.VIDEO_ENDPOINT, vg.FAL_I2V_URL)

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


class TestErrorClassification(CutBase):
    """분류는 예외 타입·HTTP 상태코드로 한다 — 문자열 줍기가 아니다."""

    def test_status_code_attribute_beats_message_text(self):
        exc = RuntimeError("request 1500ms elapsed, id=req-500-abc")
        exc.status_code = 400
        self.assertEqual(vg.classify_provider_error(exc), "content")

    def test_status_5xx_is_retryable_by_code(self):
        exc = RuntimeError("upstream boom")
        exc.status_code = 503
        self.assertEqual(vg.classify_provider_error(exc), "retryable")

    def test_status_429_is_retryable_by_code(self):
        exc = RuntimeError("slow down")
        exc.status_code = 429
        self.assertEqual(vg.classify_provider_error(exc), "retryable")

    def test_digit_run_in_request_id_is_not_a_5xx(self):
        """`500` 이 요청 id 나 `1500ms` 안에 있다고 재시도하지 않는다."""
        exc = RuntimeError("rejected by moderation, request_id=abc500def")
        self.assertEqual(vg.classify_provider_error(exc), "content")
        self.assertFalse(vg.is_retryable_provider_error(exc))

    def test_timeout_exception_type_is_retryable(self):
        self.assertEqual(vg.classify_provider_error(TimeoutError("x")),
                         "retryable")

    def test_connection_error_type_is_retryable(self):
        self.assertEqual(vg.classify_provider_error(ConnectionError("x")),
                         "retryable")

    def test_unknown_infrastructure_error_defaults_to_retryable(self):
        """알 수 없는 오류는 qa_failed 가 아니라 상한이 걸린 재시도로 간다."""
        exc = ConnectionError("Network is unreachable")
        self.assertEqual(vg.classify_provider_error(exc), "retryable")
        client = FakeFalClient(fail_with=exc)
        result = self.run_cuts(client=client)
        self.assertEqual(result["state"], "retryable_failed")

    def test_totally_unknown_exception_defaults_to_retryable(self):
        class Weird(Exception):
            pass
        self.assertEqual(vg.classify_provider_error(Weird("???")), "retryable")

    def test_keyboard_interrupt_is_not_swallowed_into_qa_failed(self):
        client = FakeFalClient(fail_with=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_cuts(client=client)

    def test_system_exit_is_not_swallowed(self):
        client = FakeFalClient(fail_with=SystemExit(1))
        with self.assertRaises(SystemExit):
            self.run_cuts(client=client)


class TestImageUrlGate(CutBase):
    """file:// 는 지출 **전에** 크게 거부한다 — 조용한 4xx 루프 금지."""

    def test_file_scheme_image_url_is_rejected_before_spending(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError) as ctx:
            vg.build_cut_request(frame, generation_prompt="x",
                                 output_path="/t/a.mp4",
                                 image_url="file:///tmp/a.png")
        self.assertIn("http", str(ctx.exception).lower())

    def test_local_path_default_is_rejected(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(frame, generation_prompt="x",
                                 output_path="/t/a.mp4")

    def test_https_image_url_is_accepted(self):
        frame = self.frames["frames"][0]
        req = vg.build_cut_request(
            frame, generation_prompt="x", output_path="/t/a.mp4",
            image_url="https://cdn.example.test/a.png")
        self.assertEqual(req["payload"]["image_url"],
                         "https://cdn.example.test/a.png")

    def test_generate_cuts_refuses_without_uploader_before_any_request(self):
        """어댑터를 주지 않아도 지출은 0건이다.

        이제 기본값은 프로덕션 fal 업로더(무료 스토리지)다. 자격증명이 없으면
        업로드 단계에서 죽는데, 그 시점은 여전히 fal 생성 요청 **이전**이므로
        지출 0건이라는 성질은 그대로다 — 게이트를 약화하지 않았다.
        """
        import fal_upload
        from unittest import mock
        client = FakeFalClient()
        with mock.patch.dict(os.environ, {"FAL_KEY": ""}, clear=False):
            with self.assertRaises((vg.CutRequestError,
                                    fal_upload.FalUploadError)):
                self.run_cuts(client=client, image_url_for=None)
        self.assertEqual(client.requests, [])

    def test_every_dispatched_request_carries_an_https_image_url(self):
        client = FakeFalClient()
        self.run_cuts(client=client)
        for req in client.requests:
            self.assertTrue(req["payload"]["image_url"].startswith("https://"))
            self.assertNotIn("file://", req["payload"]["image_url"])


class TestLedgerHygiene(CutBase):
    def test_negative_reconcile_is_warned_not_silently_clamped(self):
        vg.reserve_spend(self.ledger, "2026-08-28", 0.20, run_id="r",
                         cut_index=1)
        with self.assertLogs(vg.LOGGER, level="WARNING") as cap:
            vg.reconcile_spend(self.ledger, "2026-08-28", 5.00, 0.0,
                               run_id="r", cut_index=1)
        self.assertTrue(any("음수" in m or "negative" in m.lower()
                            for m in cap.output))
        day = vg.load_spend(self.ledger)["days"]["2026-08-28"]
        self.assertEqual(day["reserved_usd"], 0.0)

    def test_negative_release_is_warned(self):
        with self.assertLogs(vg.LOGGER, level="WARNING"):
            vg.release_spend(self.ledger, "2026-08-28", 5.00, run_id="r",
                             cut_index=1)

    def test_module_points_at_the_proven_lock_for_the_ledger(self):
        source = open(vg.__file__, encoding="utf-8").read()
        self.assertIn("video_queue.py", source)


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


class TestPermanentContentMarkers(unittest.TestCase):
    """영구 콘텐츠 실패는 재시도하면 안 된다 — possibly_billed 회계에서 돈만 탄다."""

    def test_invalid_prompt_is_content(self):
        self.assertEqual(
            vg.classify_provider_error(RuntimeError("invalid prompt")), "content")

    def test_unsupported_image_format_is_content(self):
        self.assertEqual(
            vg.classify_provider_error(
                RuntimeError("unsupported image format: bmp")), "content")

    def test_infra_failures_still_retry(self):
        """마커 추가가 인프라 버킷을 잠식하지 않았는지 확인한다."""
        for exc in (TimeoutError("timed out"),
                    ConnectionError("Network is unreachable"),
                    RuntimeError("HTTP 503 service unavailable")):
            self.assertEqual(vg.classify_provider_error(exc), "retryable")


class TestResolveCostSignature(unittest.TestCase):
    """`_resolve_cost` 는 응답만 본다 — 쓰지 않는 인자를 받으면 안 된다."""

    def test_takes_only_response(self):
        params = list(inspect.signature(vg._resolve_cost).parameters)
        self.assertEqual(params, ["response"])

    def test_still_resolves_all_three_sources(self):
        self.assertEqual(vg._resolve_cost({"billed_cost_usd": 0.2}),
                         (0.2, vg.COST_SOURCE_PROVIDER_BILLED, 0.2))
        self.assertEqual(vg._resolve_cost({"cost_usd": 0.2}),
                         (None, vg.COST_SOURCE_PROVIDER_ESTIMATE, 0.2))
        self.assertEqual(vg._resolve_cost(None),
                         (None, vg.COST_SOURCE_LOCAL_ESTIMATE, None))


class TestExplicitSourceAssetSelection(unittest.TestCase):
    """자산이 여러 장일 때 '첫 번째'를 조용히 집지 않는다.

    us-ddrops-kids-600iu 는 공식 자산 3장 중 1장만 깨끗한 히어로 컷이고
    나머지는 A+ 마케팅 합성물과 성분표 뒷면이다. 순서에 기대면 I2V 레퍼런스로
    성분표가 들어갈 수 있다. 운영자가 sha256 으로 명시하게 만든다.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.paths, self.digests = [], []
        for i in range(3):
            p = os.path.join(self.tmp, f"a{i}.png")
            with open(p, "wb") as fh:
                fh.write(b"asset-bytes-%d" % i)
            self.paths.append(p)
            self.digests.append(vg.sha256_file(p))

    def manifest(self):
        return {"product_id": "p-1", "market": "US",
                "assets": [{"local_path": p, "sha256": d,
                            "rights_basis": "official_product_page"}
                           for p, d in zip(self.paths, self.digests)]}

    def test_explicit_sha256_selects_that_asset_not_the_first(self):
        got = vg.select_source_asset(self.manifest(), product_id="p-1",
                                     market="US",
                                     asset_sha256=self.digests[2])
        self.assertEqual(got["path"], self.paths[2])
        self.assertEqual(got["sha256"], self.digests[2])
        self.assertEqual(got["selection_basis"], "operator_explicit_sha256")

    def test_unknown_sha256_is_rejected_not_silently_defaulted(self):
        with self.assertRaises(vg.SourceAssetError) as ctx:
            vg.select_source_asset(self.manifest(), product_id="p-1",
                                   market="US", asset_sha256="deadbeef")
        self.assertIn("deadbeef", str(ctx.exception))

    def test_ambiguous_multi_asset_manifest_refuses_to_guess(self):
        with self.assertRaises(vg.SourceAssetError) as ctx:
            vg.select_source_asset(self.manifest(), product_id="p-1",
                                   market="US")
        self.assertIn("3", str(ctx.exception))

    def test_single_asset_manifest_still_works_without_explicit_choice(self):
        m = self.manifest()
        m["assets"] = m["assets"][:1]
        got = vg.select_source_asset(m, product_id="p-1", market="US")
        self.assertEqual(got["path"], self.paths[0])
        self.assertEqual(got["selection_basis"], "sole_asset")


class TestFalUploadAdapterFitsTheSeam(unittest.TestCase):
    """fal_upload.make_image_url_for 가 generate_cuts 시드와 실제로 맞물리는가."""

    def test_frame_manifest_keys_match_the_uploader_contract(self):
        import fal_upload
        src = inspect.getsource(fal_upload.make_image_url_for)
        for key in ("output_path", "output_sha256", "cut_index"):
            self.assertIn(key, src)

    def test_default_uploader_is_the_fal_adapter(self):
        self.assertIsNotNone(getattr(vg, "default_image_url_for", None))
        import fal_upload
        self.assertIs(vg.default_image_url_for, fal_upload.make_image_url_for)


class TestGenerationPromptReachesFal(CutBase):
    """스토리보드의 ``generation_prompt`` 가 실제로 fal payload 에 실려야 한다.

    이전 배선은 ``motion_prompt`` 를 보냈다. 그 값에는 화자도, 대사도,
    사운드 지시도 없어서 **모델이 말을 할 근거가 없었다** — 첫 유료 영상이
    -91.0 dB 무음으로 나온 원인이다. 여기서 막지 못하면 돈을 쓰고 또
    무음 영상을 받는다.
    """

    @staticmethod
    def _speaking_storyboard():
        sb = make_storyboard()
        for cut in sb.cuts:
            cut.generation_prompt = (
                "integrated_multimodal_description: [Shot %d] one parent (S1) "
                "speaks: <d>[Korean] 아침마다 한 포씩.</d> No on-screen text.\n\n"
                "overall_soundscape: quiet room tone.\n\n"
                "non_diegetic_music: N/A" % cut.index)
        return sb

    def test_generation_prompt_is_the_prompt_sent_to_fal(self):
        client = FakeFalClient()
        sb = self._speaking_storyboard()
        self.run_cuts(client=client, storyboard=sb)
        sent = [req["payload"]["prompt"] for req in client.requests]
        self.assertEqual(sent, [c.generation_prompt for c in sb.cuts])

    def test_payload_carries_speaker_dialogue_and_soundscape(self):
        client = FakeFalClient()
        self.run_cuts(client=client, storyboard=self._speaking_storyboard())
        for req in client.requests:
            prompt = req["payload"]["prompt"]
            self.assertIn("(S1)", prompt)
            self.assertIn("<d>", prompt)
            self.assertIn("아침마다 한 포씩.", prompt)
            self.assertIn("overall_soundscape:", prompt)

    def test_motion_prompt_alone_is_never_what_gets_sent(self):
        client = FakeFalClient()
        self.run_cuts(client=client, storyboard=self._speaking_storyboard())
        for req in client.requests:
            self.assertNotEqual(req["payload"]["prompt"], "손이 천천히 올라간다")

    def test_missing_generation_prompt_refuses_before_spending(self):
        """대사 없는 컷으로는 한 푼도 쓰지 않는다."""
        client = FakeFalClient()
        sb = make_storyboard()
        for cut in sb.cuts:
            cut.generation_prompt = ""
        with self.assertRaises(vg.CutRequestError):
            self.run_cuts(client=client, storyboard=sb)
        self.assertEqual(client.requests, [])

    def test_build_cut_request_takes_generation_prompt(self):
        frame = self.frames["frames"][0]
        req = vg.build_cut_request(
            frame, generation_prompt="S1 speaks: <d>[Korean] 안녕.</d>",
            output_path="/t/a.mp4",
            image_url="https://cdn.example.test/a.png")
        self.assertEqual(req["payload"]["prompt"],
                         "S1 speaks: <d>[Korean] 안녕.</d>")

    # -- FIX 1 (task 26): fal 의 프롬프트 확장이 우리 반위조 조항을 **삭제**하고
    # "라벨이 잘 읽히도록 병을 회전한다"를 지어냈다. 확장은 기본으로 끈다.
    def test_request_disables_prompt_expansion_by_default(self):
        frame = self.frames["frames"][0]
        req = vg.build_cut_request(
            frame, generation_prompt="S1 speaks.", output_path="/t/a.mp4",
            image_url="https://cdn.example.test/a.png")
        self.assertEqual(req["payload"]["prompt_expansion_mode"],
                         vg.PROMPT_EXPANSION_MODE)
        self.assertEqual(vg.PROMPT_EXPANSION_MODE, "disabled")

    def test_request_pins_a_seed_for_reproducibility(self):
        frame = self.frames["frames"][0]
        req = vg.build_cut_request(
            frame, generation_prompt="S1 speaks.", output_path="/t/a.mp4",
            image_url="https://cdn.example.test/a.png", seed=260826)
        self.assertEqual(req["payload"]["seed"], 260826)

    def test_unknown_expansion_mode_is_rejected_before_spending(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(
                frame, generation_prompt="S1 speaks.", output_path="/t/a.mp4",
                image_url="https://cdn.example.test/a.png",
                prompt_expansion_mode="turbo")

    def test_empty_generation_prompt_is_rejected(self):
        frame = self.frames["frames"][0]
        with self.assertRaises(vg.CutRequestError):
            vg.build_cut_request(frame, generation_prompt="   ",
                                 output_path="/t/a.mp4",
                                 image_url="https://cdn.example.test/a.png")

    def test_pinned_gates_still_hold_with_generation_prompt(self):
        frame = self.frames["frames"][0]
        url = "https://cdn.example.test/a.png"
        for kw in ({"duration_seconds": 7}, {"resolution": "480P"},
                   {"aspect_ratio": "16:9"}, {"operation": "text_to_video"}):
            with self.assertRaises(vg.CutRequestError):
                vg.build_cut_request(frame, generation_prompt="S1 speaks.",
                                     output_path="/t/a.mp4", image_url=url,
                                     **kw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
