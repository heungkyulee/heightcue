#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""product_assets 계약 테스트 — 네트워크 절대 금지.

이 모듈은 영상 파이프라인의 `truth layer`(실제 공식 상품 사진)를 지킨다.
여기서 상품의 실제 생김새가 틀리면 하류의 모든 프레임·영상이 틀린다.
따라서 테스트는 "성공 경로"보다 "거부 경로"를 더 촘촘하게 증명한다.

fetcher 는 전부 주입 시드(`fetcher=`)로 대체되고, 바이트는 테스트 안에서
직접 합성한다 (Pillow 등 신규 의존성 없음).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import tempfile
import unittest
import zlib

import product_assets as pa
import sourcing
import video_storyboard as vs


# ---------------------------------------------------------------------------
# 바이트 합성 헬퍼 — 네트워크 없이 진짜 이미지/가짜 이미지를 만든다
# ---------------------------------------------------------------------------


def make_png(width=600, height=800):
    """Pillow 없이 유효한 최소 PNG 바이트를 만든다."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b"".join(b"\x00" + b"\x7f\x7f\x7f" * width for _ in range(height))
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def make_jpeg(width=600, height=800):
    """SOF0 프레임 헤더를 가진 최소 JPEG 바이트."""
    sof = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
           + struct.pack(">HH", height, width) + b"\x03"
           + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" \
        + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof \
        + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x11\x22\x33" \
        + b"\xff\xd9"


def png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_ihdr(width=600, height=800):
    return png_chunk(b"IHDR",
                     struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))


PNG_IEND = png_chunk(b"IEND", b"")


def make_webp_vp8(width=600, height=800):
    """최소 VP8(손실) WebP 바이트 — 헤더 파서용."""
    body = (b"\x00\x00\x00" + b"\x9d\x01\x2a"
            + struct.pack("<HH", width, height))
    chunk = b"VP8 " + struct.pack("<I", len(body)) + body
    payload = b"WEBP" + chunk
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def make_webp_vp8l(width=600, height=800):
    """최소 VP8L(무손실) WebP 바이트."""
    bits = (width - 1) | ((height - 1) << 14)
    body = b"\x2f" + struct.pack("<I", bits)[:4]
    chunk = b"VP8L" + struct.pack("<I", len(body)) + body
    payload = b"WEBP" + chunk
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def make_webp_vp8x(width=600, height=800):
    """VP8X(확장) 캔버스 헤더를 가진 WebP."""
    body = (b"\x00\x00\x00\x00"
            + (width - 1).to_bytes(3, "little")
            + (height - 1).to_bytes(3, "little"))
    chunk = b"VP8X" + struct.pack("<I", len(body)) + body
    payload = b"WEBP" + chunk
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


HTML_ERROR = (b"<!DOCTYPE html>\n<html><head><title>404 Not Found</title>"
              b"</head><body><h1>Not Found</h1></body></html>\n")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 주입용 fetcher
# ---------------------------------------------------------------------------


class FakeFetcher:
    """URL -> 응답 매핑. 실제 네트워크를 절대 타지 않는다."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if url not in self.mapping:
            raise AssertionError(f"테스트가 준비하지 않은 URL 호출: {url}")
        resp = dict(self.mapping[url])
        resp.setdefault("final_url", url)
        resp.setdefault("redirect_chain", [])
        resp.setdefault("status", 200)
        resp.setdefault("content_type", "image/png")
        return resp


OFFICIAL = "https://www.coupang.com/vp/products/1234567"
IMG1 = "https://image.coupangcdn.com/official/1234567/main.png"
IMG2 = "https://image.coupangcdn.com/official/1234567/side.jpg"


def image_spec(**over):
    spec = {
        "source_url": IMG1,
        "market": "KR",
        "product_id": "cp-1234567",
        "option": "30정 1박스",
        "official_page_url": OFFICIAL,
        "rights_basis": "official_product_page",
        "rights_holder": "브랜드코리아(주)",
        "captured_at": "2026-08-28T09:00:00+09:00",
    }
    spec.update(over)
    return spec


def product(**over):
    p = {
        "product_key": "cp-1234567",
        "country": "KR",
        "product_name": "테스트 상품",
        "product_url": OFFICIAL,
        "marketed_option": "30정 1박스",
        "official_image_provenance": [image_spec()],
    }
    p.update(over)
    return p


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="pa-test-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.png = make_png()
        self.jpg = make_jpeg()

    def fetcher_ok(self, extra=None):
        mapping = {IMG1: {"bytes": self.png, "content_type": "image/png"}}
        if extra:
            mapping.update(extra)
        return FakeFetcher(mapping)


# ---------------------------------------------------------------------------
# 1. 매직 바이트 스니핑
# ---------------------------------------------------------------------------


class TestSniff(BaseCase):
    def test_png_dimensions_from_magic_bytes(self):
        fmt, w, h = pa.sniff_image(make_png(640, 480))
        self.assertEqual((fmt, w, h), ("png", 640, 480))

    def test_jpeg_dimensions_from_magic_bytes(self):
        fmt, w, h = pa.sniff_image(make_jpeg(320, 240))
        self.assertEqual((fmt, w, h), ("jpeg", 320, 240))

    def test_html_error_page_is_not_an_image(self):
        with self.assertRaises(pa.NotAnImageError):
            pa.sniff_image(HTML_ERROR)

    def test_extension_and_content_type_are_never_trusted(self):
        """.png 확장자 + image/png 헤더라도 바이트가 HTML이면 거부된다."""
        f = FakeFetcher({IMG1: {"bytes": HTML_ERROR, "content_type": "image/png"}})
        with self.assertRaises(pa.NotAnImageError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)
        self.assertEqual(os.listdir(self.ws), [],
                         "거부된 자산이 워크스페이스에 남으면 안 된다")

    def test_truncated_png_is_rejected(self):
        truncated = make_png()[:-20]
        f = FakeFetcher({IMG1: {"bytes": truncated}})
        with self.assertRaises(pa.TruncatedAssetError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)

    def test_truncated_jpeg_is_rejected(self):
        f = FakeFetcher({IMG1: {"bytes": self.jpg[:-2],
                                "content_type": "image/jpeg"}})
        with self.assertRaises(pa.TruncatedAssetError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)


# ---------------------------------------------------------------------------
# 2. 성공 경로 — provenance 가 전부 채워진다
# ---------------------------------------------------------------------------


class TestAcquireSuccess(BaseCase):
    def test_manifest_carries_full_provenance(self):
        f = self.fetcher_ok()
        m = pa.acquire_product_assets(product(), self.ws, fetcher=f)

        self.assertEqual(m["product_id"], "cp-1234567")
        self.assertEqual(m["market"], "KR")
        self.assertEqual(len(m["assets"]), 1)
        a = m["assets"][0]

        self.assertEqual(a["source_url"], IMG1)
        self.assertEqual(a["market"], "KR")
        self.assertEqual(a["product_id"], "cp-1234567")
        self.assertEqual(a["sha256"], sha256(self.png))
        self.assertEqual(a["format"], "png")
        self.assertEqual((a["width"], a["height"]), (600, 800))
        self.assertEqual(a["bytes"], len(self.png))
        self.assertEqual(a["option"], "30정 1박스")
        self.assertEqual(a["official_page_url"], OFFICIAL)
        self.assertTrue(a["fetched_at"])
        self.assertIn("rights", a)

        self.assertTrue(os.path.isfile(a["local_path"]))
        with open(a["local_path"], "rb") as fh:
            self.assertEqual(fh.read(), self.png)

    def test_local_filename_is_hash_based_and_safe(self):
        """원격이 준 파일명을 절대 쓰지 않는다 (경로 탈출 방지)."""
        evil = "https://image.coupangcdn.com/x/../../etc/passwd.png"
        f = FakeFetcher({evil: {"bytes": self.png}})
        p = product(official_image_provenance=[image_spec(source_url=evil)])
        m = pa.acquire_product_assets(p, self.ws, fetcher=f)
        name = os.path.basename(m["assets"][0]["local_path"])
        self.assertNotIn("..", name)
        self.assertNotIn("/", name)
        self.assertIn(sha256(self.png)[:16], name)
        self.assertEqual(
            os.path.dirname(os.path.abspath(m["assets"][0]["local_path"])),
            os.path.abspath(os.path.join(self.ws, "cp-1234567")))

    def test_redirects_and_final_url_are_recorded(self):
        f = FakeFetcher({IMG1: {"bytes": self.png,
                                "final_url": IMG1 + "?v=2",
                                "redirect_chain": [IMG1, IMG1 + "?v=2"]}})
        m = pa.acquire_product_assets(product(), self.ws, fetcher=f)
        a = m["assets"][0]
        self.assertEqual(a["final_url"], IMG1 + "?v=2")
        self.assertEqual(a["redirect_chain"], [IMG1, IMG1 + "?v=2"])

    def test_hashes_are_stable_across_runs(self):
        ws2 = tempfile.mkdtemp(prefix="pa-test2-")
        self.addCleanup(shutil.rmtree, ws2, ignore_errors=True)
        a = pa.acquire_product_assets(product(), self.ws,
                                      fetcher=self.fetcher_ok())["assets"][0]
        b = pa.acquire_product_assets(product(), ws2,
                                      fetcher=self.fetcher_ok())["assets"][0]
        self.assertEqual(a["sha256"], b["sha256"])
        self.assertEqual(a["source_sha256"], b["source_sha256"])

    def test_two_assets_both_stored(self):
        p = product(official_image_provenance=[
            image_spec(), image_spec(source_url=IMG2)])
        f = FakeFetcher({IMG1: {"bytes": self.png},
                         IMG2: {"bytes": self.jpg, "content_type": "image/jpeg"}})
        m = pa.acquire_product_assets(p, self.ws, fetcher=f)
        self.assertEqual([a["format"] for a in m["assets"]], ["png", "jpeg"])


# ---------------------------------------------------------------------------
# 3. 거부 경로 — provenance 결손
# ---------------------------------------------------------------------------


class TestProvenanceRejection(BaseCase):
    def _reject(self, spec_over, needle):
        p = product(official_image_provenance=[image_spec(**spec_over)])
        with self.assertRaises(pa.AssetProvenanceError) as cm:
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())
        self.assertIn(needle, str(cm.exception))
        self.assertEqual(os.listdir(self.ws), [])

    def test_missing_source_url_rejected(self):
        self._reject({"source_url": ""}, "source_url")

    def test_missing_market_rejected(self):
        self._reject({"market": ""}, "market")

    def test_missing_product_id_rejected(self):
        self._reject({"product_id": ""}, "product_id")

    def test_missing_captured_at_rejected(self):
        self._reject({"captured_at": ""}, "captured_at")

    def test_missing_rights_basis_rejected(self):
        self._reject({"rights_basis": ""}, "rights_basis")

    def test_missing_official_page_url_rejected(self):
        self._reject({"official_page_url": ""}, "official_page_url")

    def test_non_http_source_url_rejected(self):
        self._reject({"source_url": "file:///etc/passwd"}, "source_url")

    def test_no_image_provenance_at_all_fails_loudly(self):
        p = product(official_image_provenance=[])
        with self.assertRaises(pa.AssetProvenanceError):
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())

    def test_non_official_rights_basis_rejected(self):
        """크리에이터 사진·스톡·생성 이미지는 truth layer 에 들어올 수 없다."""
        for bad in ("creator_photo", "stock_photo", "ai_generated",
                    "competitor_page"):
            with self.subTest(basis=bad):
                p = product(official_image_provenance=[
                    image_spec(rights_basis=bad)])
                with self.assertRaises(pa.AssetProvenanceError):
                    pa.acquire_product_assets(p, self.ws,
                                              fetcher=self.fetcher_ok())


# ---------------------------------------------------------------------------
# 4. 거부 경로 — 계보 불일치 (시장·상품 id·옵션)
# ---------------------------------------------------------------------------


class TestLineageRejection(BaseCase):
    def test_wrong_market_rejected(self):
        p = product(official_image_provenance=[image_spec(market="US")])
        with self.assertRaises(pa.AssetLineageError) as cm:
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())
        self.assertIn("market", str(cm.exception))

    def test_unknown_market_rejected(self):
        p = product(official_image_provenance=[image_spec(market="JP")])
        with self.assertRaises(pa.AssetLineageError):
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())

    def test_unknown_product_id_rejected(self):
        p = product(official_image_provenance=[
            image_spec(product_id="cp-9999999")])
        with self.assertRaises(pa.AssetLineageError) as cm:
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())
        self.assertIn("product_id", str(cm.exception))

    def test_option_mismatch_rejected(self):
        p = product(official_image_provenance=[image_spec(option="90정 3박스")])
        with self.assertRaises(pa.OptionMismatchError):
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())

    def test_product_without_marketed_option_fails_loudly(self):
        p = product(marketed_option="")
        with self.assertRaises(pa.OptionMismatchError):
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())

    def test_official_page_url_must_match_product_url(self):
        p = product(official_image_provenance=[
            image_spec(official_page_url="https://competitor.example/p/1")])
        with self.assertRaises(pa.AssetLineageError):
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())


# ---------------------------------------------------------------------------
# 5. 거부 경로 — 크기·개수 한계
# ---------------------------------------------------------------------------


class TestLimits(BaseCase):
    def test_constants_exist_and_are_sane(self):
        self.assertIsInstance(pa.MAX_ASSET_BYTES, int)
        self.assertIsInstance(pa.MAX_ASSETS_PER_PRODUCT, int)
        self.assertGreater(pa.MAX_ASSET_BYTES, 0)
        self.assertGreater(pa.MAX_ASSETS_PER_PRODUCT, 0)
        self.assertLessEqual(pa.MAX_ASSETS_PER_PRODUCT, 12)

    def test_oversized_asset_rejected(self):
        big = self.png + b"\x00" * (pa.MAX_ASSET_BYTES + 1)
        f = FakeFetcher({IMG1: {"bytes": big}})
        with self.assertRaises(pa.AssetSizeError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)
        self.assertEqual(os.listdir(self.ws), [])

    def test_too_many_assets_rejected_before_any_fetch(self):
        specs = [image_spec(source_url=f"{IMG1}?i={i}")
                 for i in range(pa.MAX_ASSETS_PER_PRODUCT + 1)]
        f = FakeFetcher({})
        with self.assertRaises(pa.AssetCountError):
            pa.acquire_product_assets(product(official_image_provenance=specs),
                                      self.ws, fetcher=f)
        self.assertEqual(f.calls, [], "한계 초과는 fetch 전에 막아야 한다")

    def test_empty_payload_rejected(self):
        f = FakeFetcher({IMG1: {"bytes": b""}})
        with self.assertRaises(pa.NotAnImageError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)

    def test_too_small_dimensions_rejected(self):
        f = FakeFetcher({IMG1: {"bytes": make_png(4, 4)}})
        with self.assertRaises(pa.AssetDimensionError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)

    def test_http_error_status_fails_loudly(self):
        f = FakeFetcher({IMG1: {"bytes": HTML_ERROR, "status": 404}})
        with self.assertRaises(pa.AssetFetchError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)

    def test_fetcher_exception_fails_loudly_never_silently_skips(self):
        def boom(url, timeout=None):
            raise OSError("connection reset")
        with self.assertRaises(pa.AssetFetchError):
            pa.acquire_product_assets(product(), self.ws, fetcher=boom)


# ---------------------------------------------------------------------------
# 6. 권리/삭제 요청 — 계보 역추적
# ---------------------------------------------------------------------------


class TestTakedownLineage(BaseCase):
    def test_manifest_persists_and_reloads(self):
        m = pa.acquire_product_assets(product(), self.ws,
                                      fetcher=self.fetcher_ok())
        path = m["manifest_path"]
        self.assertTrue(os.path.isfile(path))
        again = pa.load_manifest(path)
        self.assertEqual(again["assets"][0]["sha256"], m["assets"][0]["sha256"])

    def test_takedown_removes_asset_and_records_event(self):
        m = pa.acquire_product_assets(product(), self.ws,
                                      fetcher=self.fetcher_ok())
        local = m["assets"][0]["local_path"]
        self.assertTrue(os.path.isfile(local))

        result = pa.takedown(m["manifest_path"], source_url=IMG1,
                             reason="rights holder request")
        self.assertEqual(len(result["removed"]), 1)
        self.assertFalse(os.path.isfile(local))

        reloaded = pa.load_manifest(m["manifest_path"])
        self.assertEqual(reloaded["assets"], [])
        self.assertTrue(reloaded["takedowns"])
        self.assertEqual(reloaded["takedowns"][0]["source_url"], IMG1)
        self.assertEqual(reloaded["takedowns"][0]["sha256"], sha256(self.png))

    def test_takedown_by_unknown_url_raises_instead_of_silent_success(self):
        m = pa.acquire_product_assets(product(), self.ws,
                                      fetcher=self.fetcher_ok())
        with self.assertRaises(pa.AssetLineageError):
            pa.takedown(m["manifest_path"], source_url="https://nope.example/x.png",
                        reason="test")

    def test_evidence_bridges_to_video_contracts(self):
        """ProductEvidence 로 변환돼 하류 계약 검증을 통과해야 한다."""
        m = pa.acquire_product_assets(product(), self.ws,
                                      fetcher=self.fetcher_ok())
        ev = pa.to_product_evidence(m)
        ev.validate()
        self.assertEqual(ev.product_id, "cp-1234567")
        self.assertEqual(ev.market, "KR")
        self.assertIn(IMG1, ev.source_urls)
        self.assertIn(sha256(self.png), ev.source_sha256)


# ---------------------------------------------------------------------------
# 6-1. 증거 다리는 시장 언어를 따른다 (US 스토리보드 차단 사고 재발 방지)
#
# 2026-08-28 실사고: to_product_evidence 가 모든 시장에 **한국어** quote 를
# 만들어서, video_storyboard 의 시장 언어 게이트가 US 상품 카피를 전부 거부했다.
# US 스토리보드가 구조적으로 생성 불가능했다. 언어 게이트는 옳다 — 고친 곳은
# 증거 다리다. 그래서 이 테스트는 문자열이 영어처럼 보이는지가 아니라
# **진짜 게이트**(video_storyboard._assert_language / generate_storyboard)를
# 통과하는지로 판정한다.
# ---------------------------------------------------------------------------


US_OFFICIAL = "https://www.amazon.com/dp/B00843E5NS"
US_IMG = "https://m.media-amazon.com/images/I/81VnUASlMEL._AC_SL1500_.jpg"
US_OPTION = "600 IU, 100 drops (0.09 fl oz)"
US_SPEC_FACTS = ["600 IU vitamin D3 per labeled drop",
                 "single tasteless oil drop format"]


def us_image_spec(**over):
    spec = {
        "source_url": US_IMG,
        "market": "US",
        "product_id": "us-ddrops-kids-600iu",
        "option": US_OPTION,
        "official_page_url": US_OFFICIAL,
        "rights_basis": "official_product_page",
        "rights_holder": "Ddrops Company",
        "captured_at": "2026-08-28T09:00:00+09:00",
    }
    spec.update(over)
    return spec


def us_product(**over):
    p = {
        "product_key": "us-ddrops-kids-600iu",
        "country": "US",
        "product_name": "Ddrops Kids Booster Vitamin D3 600 IU",
        "product_url": US_OFFICIAL,
        "marketed_option": US_OPTION,
        "spec_facts": list(US_SPEC_FACTS),
        "official_image_provenance": [us_image_spec()],
    }
    p.update(over)
    return p


class TestEvidenceIsMarketAware(BaseCase):
    def _us_manifest(self, **over):
        f = FakeFetcher({US_IMG: {"bytes": self.jpg, "content_type": "image/jpeg"}})
        return pa.acquire_product_assets(us_product(**over), self.ws, fetcher=f)

    def test_us_evidence_quotes_pass_the_real_language_gate(self):
        """US 증거 원문은 실제 video_storyboard 언어 게이트를 통과해야 한다."""
        ev = pa.to_product_evidence(self._us_manifest())
        ev.validate()
        for i, entry in enumerate(ev.provenance):
            vs._assert_language(entry["quote"], "US", f"provenance[{i}].quote")

    def test_kr_evidence_quotes_pass_the_real_language_gate(self):
        m = pa.acquire_product_assets(product(), self.ws, fetcher=self.fetcher_ok())
        ev = pa.to_product_evidence(m)
        ev.validate()
        for i, entry in enumerate(ev.provenance):
            vs._assert_language(entry["quote"], "KR", f"provenance[{i}].quote")

    def test_us_spec_facts_are_carried_verbatim(self):
        """진실 계층 — 문구를 지어내지 않고 기록된 스펙 원문을 그대로 옮긴다."""
        quotes = [e["quote"] for e in pa.to_product_evidence(self._us_manifest()).provenance]
        for fact in US_SPEC_FACTS:
            self.assertIn(fact, quotes)

    def test_us_storyboard_is_actually_producible(self):
        """진짜 최종 판정: 이 증거로 US 스토리보드가 실제로 생성된다.

        스토리보드가 재설계되면서 컷은 이제 **사람이 제품을 쓰며 말하는 장면**을
        요구한다(제품 클로즈업 전용 컷은 `SilentCutError`). 픽스처를 그 계약에
        맞춰 올렸을 뿐 검증은 오히려 강화했다 — 실증거에서 나온 스토리보드가
        fal 로 나갈 `generation_prompt` 까지 실제로 만들어내는지 본다.
        """
        ev = pa.to_product_evidence(self._us_manifest())
        quote = ev.provenance[0]["quote"]
        voice_line = f"The label says {quote}."

        def model(system_prompt, payload):
            return {"cuts": [{
                "index": 1,
                "duration_seconds": 5,
                "action": "A parent holds the amber bottle up to the camera",
                "benefit": "One drop is the whole serving",
                "claim": quote,
                "evidence_id": "ev1",
                "voice_line": voice_line,
                "first_frame_prompt": (
                    "Vertical 9:16 still photo of a parent standing in a "
                    "kitchen holding one amber dropper bottle"),
                "motion_prompt": (
                    "The parent turns the bottle toward the lens and speaks "
                    "to the camera"),
            }]}

        board = vs.generate_storyboard(
            {}, ev, "US", run_id="run-us-1", content_draft_id="draft-us-1",
            viral_pattern_ids=["vp-1"], complexity="simple", model=model)
        self.assertEqual(board.market, "US")
        self.assertEqual(board.cuts[0].evidence_quote, quote)
        # 증거 → 스토리보드 → **실제로 발송될 프롬프트**까지 이어지는지 확인.
        prompt = board.cuts[0].generation_prompt
        self.assertTrue(prompt)
        self.assertIn("(S1)", prompt)
        self.assertIn(voice_line, prompt)
        self.assertEqual(vs.spoken_segments(prompt), [voice_line])

    def test_unknown_market_fails_loudly_instead_of_defaulting(self):
        """모르는 시장에 어느 한쪽 언어를 기본값으로 주지 않는다 — 그게 이 버그의 원인."""
        m = self._us_manifest()
        for bad in ("JP", "", None):
            broken = dict(m, market=bad)
            with self.assertRaises(pa.AssetLineageError):
                pa.to_product_evidence(broken)

    def test_spec_fact_in_wrong_language_is_rejected(self):
        """시장과 언어가 어긋난 스펙 원문은 조용히 흘려보내지 않는다."""
        with self.assertRaises(pa.EvidenceLanguageError):
            pa.to_product_evidence(self._us_manifest(spec_facts=["방울당 600 IU"]))


# ---------------------------------------------------------------------------
# 7. sourcing.py 노출 계층 — 기존 게이트를 약화시키지 않는다
# ---------------------------------------------------------------------------


class TestSourcingExposure(unittest.TestCase):
    def test_approved_image_sources_exposed(self):
        specs = [image_spec()]
        self.assertEqual(
            sourcing.approved_image_sources(product(official_image_provenance=specs)),
            specs)

    def test_approved_image_sources_empty_when_absent(self):
        self.assertEqual(sourcing.approved_image_sources({}), [])

    def test_image_provenance_reasons_reports_gaps(self):
        reasons = sourcing.image_provenance_reasons({})
        self.assertIn("official_image_provenance_missing", reasons)

    def test_image_provenance_reasons_clean_for_good_result(self):
        self.assertEqual(sourcing.image_provenance_reasons(product()), [])

    def test_existing_audit_gate_not_weakened(self):
        """이미지 provenance 는 추가 노출일 뿐, 기존 감사 게이트를 바꾸지 않는다."""
        empty_reasons = set(sourcing.audit_readiness_reasons({}))
        for expected in ("audit_status_not_approved", "product_url_missing",
                         "collected_at_missing", "sub_id_missing",
                         "official_provenance_missing",
                         "review_provenance_missing"):
            self.assertIn(expected, empty_reasons)
        self.assertFalse(sourcing.is_audit_approved({}))
        # 이미지가 잘 갖춰진 결과라도 감사 미승인이면 여전히 승인되지 않는다.
        self.assertFalse(sourcing.is_audit_approved(product()))


# ---------------------------------------------------------------------------
# 8. PNG 청크 검증 — 래퍼가 아니라 실제 이미지 바이트를 본다
# ---------------------------------------------------------------------------


class TestPngChunkVerification(BaseCase):
    def test_png_without_idat_is_rejected(self):
        """시그니처+IHDR+IEND 만으로는 이미지가 아니다 — 픽셀 데이터가 없다."""
        payload = PNG_SIG + png_ihdr() + PNG_IEND
        with self.assertRaises(pa.NotAnImageError):
            pa.sniff_image(payload)

    def test_png_wrapping_html_body_is_rejected(self):
        """PNG 헤더로 감싼 HTML 오류 페이지는 공식 사진이 아니다."""
        payload = PNG_SIG + png_ihdr() + b"<html>" * 100 + PNG_IEND
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(payload)

    def test_png_lying_about_dimensions_is_rejected(self):
        """IHDR 이 99999x99999 라 주장해도 실제 데이터가 없으면 거부."""
        payload = PNG_SIG + png_ihdr(99999, 99999) + PNG_IEND
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(payload)

    def test_png_with_corrupt_ihdr_crc_is_rejected(self):
        good = make_png()
        # IHDR CRC 는 시그니처(8)+길이(4)+태그(4)+본문(13) 뒤 4바이트.
        bad = bytearray(good)
        bad[29] ^= 0xFF
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(bytes(bad))

    def test_png_with_trailing_garbage_is_rejected(self):
        """청크 길이가 페이로드를 정확히 소비해야 한다."""
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(make_png() + b"GARBAGE")

    def test_png_with_lying_chunk_length_is_rejected(self):
        """청크 길이가 남은 바이트를 넘어서면 잘린 다운로드다."""
        good = bytearray(make_png())
        struct.pack_into(">I", good, 8, 0x7FFFFFF0)  # IHDR 길이 위조
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(bytes(good))

    def test_valid_png_still_accepted_with_observed_dimensions(self):
        self.assertEqual(pa.sniff_image(make_png(640, 480)), ("png", 640, 480))

    def test_hostile_payloads_never_hang_or_crash_uncaught(self):
        """적대적 입력은 전부 ProductAssetError 로만 나온다 (크래시·무한루프 금지)."""
        hostile = [
            PNG_SIG,
            PNG_SIG + b"\x00" * 25,
            PNG_SIG + png_ihdr() + png_chunk(b"IDAT", b""),
            PNG_SIG + png_ihdr() + b"\xff\xff\xff\xffIDAT",
            b"RIFF\x00\x00\x00\x00WEBP",
            b"RIFF" + struct.pack("<I", 4) + b"WEBP",
            b"\xff\xd8\xff" + b"\xff" * 200,
        ]
        for i, payload in enumerate(hostile):
            with self.subTest(i=i):
                with self.assertRaises(pa.ProductAssetError):
                    pa.sniff_image(payload)

    def test_sniff_rejects_zero_dimension_jpeg_itself(self):
        """0x0 은 MIN_ASSET_DIMENSION 하류가 아니라 스니퍼가 직접 거부한다."""
        zero = (b"\xff\xd8\xff\xc0" + struct.pack(">H", 17) + b"\x08"
                + struct.pack(">HH", 0, 0) + b"\x03"
                + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01" + b"\xff\xd9")
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(zero)


# ---------------------------------------------------------------------------
# 9. WebP 는 지원하지 않는다 (선언만으로 위조 가능) — 섹션 13 참조
# ---------------------------------------------------------------------------


class TestWebpVariantsAllRejected(BaseCase):
    """VP8 / VP8L / VP8X 어느 형태로도 truth layer 에 들어올 수 없다."""

    def test_all_webp_flavours_rejected(self):
        for maker in (make_webp_vp8, make_webp_vp8l, make_webp_vp8x):
            with self.subTest(maker=maker.__name__):
                with self.assertRaises(pa.NotAnImageError):
                    pa.sniff_image(maker(600, 800))

    def test_truncated_webp_rejected(self):
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(make_webp_vp8()[:-4])

    def test_accept_header_still_pins_the_two_supported_formats(self):
        self.assertIn("image/png", pa.IMAGE_ACCEPT_HEADER)
        self.assertIn("image/jpeg", pa.IMAGE_ACCEPT_HEADER)


# ---------------------------------------------------------------------------
# 10. 공식 CDN 호스트 강제
# ---------------------------------------------------------------------------


class TestOfficialCdnHosts(BaseCase):
    def test_allowlist_constants_exist_per_market(self):
        self.assertIn("KR", pa.OFFICIAL_IMAGE_HOSTS)
        self.assertIn("US", pa.OFFICIAL_IMAGE_HOSTS)
        self.assertTrue(any("coupangcdn.com" in h
                            for h in pa.OFFICIAL_IMAGE_HOSTS["KR"]))
        self.assertTrue(any("media-amazon.com" in h
                            for h in pa.OFFICIAL_IMAGE_HOSTS["US"]))

    def test_ddrops_first_party_manufacturer_image_host_is_allowed(self):
        self.assertTrue(pa.is_official_image_host(
            "https://vitaminddrops.com/us-en/wp-content/uploads/product.png", "US"))

    def test_off_allowlist_image_host_rejected(self):
        """rights_basis 문자열만 맞다고 임의 호스트를 신뢰하지 않는다."""
        evil = "https://scontent.cdninstagram.com/creator/photo.png"
        p = product(official_image_provenance=[image_spec(source_url=evil)])
        f = FakeFetcher({evil: {"bytes": self.png}})
        with self.assertRaises(pa.AssetProvenanceError) as cm:
            pa.acquire_product_assets(p, self.ws, fetcher=f)
        self.assertIn("호스트", str(cm.exception))
        self.assertEqual(f.calls, [], "허용되지 않은 호스트는 fetch 전에 막는다")
        self.assertEqual(os.listdir(self.ws), [])

    def test_lookalike_suffix_host_rejected(self):
        evil = "https://evil-coupangcdn.com.attacker.example/main.png"
        p = product(official_image_provenance=[image_spec(source_url=evil)])
        f = FakeFetcher({evil: {"bytes": self.png}})
        with self.assertRaises(pa.AssetProvenanceError):
            pa.acquire_product_assets(p, self.ws, fetcher=f)

    def test_redirect_target_off_allowlist_rejected(self):
        """선언된 source_url 만이 아니라 최종 URL 도 검증한다."""
        f = FakeFetcher({IMG1: {
            "bytes": self.png,
            "final_url": "https://attacker.example/x.png",
            "redirect_chain": [IMG1, "https://attacker.example/x.png"]}})
        with self.assertRaises(pa.AssetProvenanceError) as cm:
            pa.acquire_product_assets(product(), self.ws, fetcher=f)
        self.assertIn("리다이렉트", str(cm.exception))
        self.assertEqual(os.listdir(self.ws), [])

    def test_us_market_amazon_cdn_accepted(self):
        us_page = "https://www.amazon.com/dp/B00TEST"
        us_img = "https://m.media-amazon.com/images/I/main.png"
        p = {
            "product_key": "az-B00TEST", "country": "US",
            "product_url": us_page, "marketed_option": "60 count",
            "official_image_provenance": [image_spec(
                source_url=us_img, market="US", product_id="az-B00TEST",
                option="60 count", official_page_url=us_page)],
        }
        f = FakeFetcher({us_img: {"bytes": self.png}})
        m = pa.acquire_product_assets(p, self.ws, fetcher=f)
        self.assertEqual(m["assets"][0]["source_url"], us_img)

    def test_kr_image_on_us_cdn_rejected(self):
        """시장별 allowlist — KR 상품이 아마존 CDN 을 쓰면 거부."""
        img = "https://m.media-amazon.com/images/I/main.png"
        p = product(official_image_provenance=[image_spec(source_url=img)])
        f = FakeFetcher({img: {"bytes": self.png}})
        with self.assertRaises(pa.AssetProvenanceError):
            pa.acquire_product_assets(p, self.ws, fetcher=f)


# ---------------------------------------------------------------------------
# 11. takedown 은 내구성 있고 denylist 로 작동한다
# ---------------------------------------------------------------------------


class TestTakedownDurability(BaseCase):
    def _acquire(self):
        return pa.acquire_product_assets(product(), self.ws,
                                         fetcher=self.fetcher_ok())

    def test_takedown_ledger_survives_reacquisition(self):
        m = self._acquire()
        pa.takedown(m["manifest_path"], source_url=IMG1, reason="rights holder")

        p = product(official_image_provenance=[image_spec(source_url=IMG2)])
        f = FakeFetcher({IMG2: {"bytes": self.jpg, "content_type": "image/jpeg"}})
        again = pa.acquire_product_assets(p, self.ws, fetcher=f)

        self.assertTrue(again["takedowns"],
                        "재수집이 법적 삭제 기록을 지워서는 안 된다")
        self.assertEqual(again["takedowns"][0]["source_url"], IMG1)
        reloaded = pa.load_manifest(again["manifest_path"])
        self.assertTrue(reloaded["takedowns"])

    def test_taken_down_url_is_never_reacquired(self):
        m = self._acquire()
        pa.takedown(m["manifest_path"], source_url=IMG1, reason="rights holder")

        f = self.fetcher_ok()
        with self.assertRaises(pa.AssetTakedownError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)
        self.assertEqual(f.calls, [], "삭제된 URL 은 다시 내려받지 않는다")

    def test_taken_down_sha256_is_never_reacquired_under_a_new_url(self):
        """URL 을 바꿔 우회해도 동일 바이트면 거부된다."""
        m = self._acquire()
        pa.takedown(m["manifest_path"], source_url=IMG1, reason="rights holder")

        p = product(official_image_provenance=[image_spec(source_url=IMG2)])
        f = FakeFetcher({IMG2: {"bytes": self.png}})
        with self.assertRaises(pa.AssetTakedownError):
            pa.acquire_product_assets(p, self.ws, fetcher=f)

    def test_takedown_reports_derivatives_not_swept(self):
        """파생물까지 지웠다고 조용히 주장하지 않는다."""
        m = self._acquire()
        result = pa.takedown(m["manifest_path"], source_url=IMG1,
                             reason="rights holder")
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result["removed"]), 1)
        self.assertFalse(result["derivatives_swept"])
        self.assertEqual(result["derivative_sweep"], "not_swept")
        self.assertIn(sha256(self.png), result["sweep_sha256"])


# ---------------------------------------------------------------------------
# 12. provenance 필수 키 — 나머지 2개
# ---------------------------------------------------------------------------


class TestRemainingProvenanceKeys(BaseCase):
    def test_missing_option_rejected(self):
        p = product(official_image_provenance=[image_spec(option="")])
        with self.assertRaises(pa.AssetProvenanceError) as cm:
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())
        self.assertIn("option", str(cm.exception))
        self.assertEqual(os.listdir(self.ws), [])

    def test_missing_rights_holder_rejected(self):
        p = product(official_image_provenance=[image_spec(rights_holder="")])
        with self.assertRaises(pa.AssetProvenanceError) as cm:
            pa.acquire_product_assets(p, self.ws, fetcher=self.fetcher_ok())
        self.assertIn("rights_holder", str(cm.exception))
        self.assertEqual(os.listdir(self.ws), [])


# ---------------------------------------------------------------------------
# 13. 라운드 2 — 모든 청크 CRC / WebP 위조 / takedown URL 정규화 / 정직한 크기
# ---------------------------------------------------------------------------


def png_chunk_bad_crc(tag, data, crc=0xDEADBEEF):
    """CRC 를 일부러 틀리게 넣은 청크."""
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


class TestEveryPngChunkCrcIsVerified(BaseCase):
    def test_ancillary_text_chunk_with_wrong_crc_is_rejected(self):
        """tEXt 에 숨긴 임의 바이트도 CRC 로 걸러야 한다.

        CRC 를 IHDR/IDAT/IEND 세 태그에만 걸면, 공격자가 통제하는 바이트가
        유효해 보이는 PNG 안에 그대로 실려 truth layer 로 들어온다 —
        원래의 'PNG 헤더로 감싼 HTML' 버그가 보조 청크로 자리만 옮긴 것이다.
        """
        good = make_png(600, 800)
        # sig(8) + IHDR(25) 뒤에 위조 CRC 를 단 tEXt 를 끼워 넣는다.
        head, tail = good[:33], good[33:]
        payload = head + png_chunk_bad_crc(b"tEXt", b"<html>" * 34) + tail
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(payload)

    def test_ancillary_chunk_with_correct_crc_still_parses(self):
        """정상 CRC 를 가진 보조 청크는 정상 PNG 를 깨뜨리지 않는다."""
        good = make_png(600, 800)
        head, tail = good[:33], good[33:]
        payload = head + png_chunk(b"tEXt", b"Comment\x00hi") + tail
        self.assertEqual(pa.sniff_image(payload), ("png", 600, 800))

    def test_every_chunk_crc_is_checked_not_just_three_tags(self):
        """어떤 태그든 CRC 가 틀리면 거부된다."""
        good = make_png(600, 800)
        head, tail = good[:33], good[33:]
        for tag in (b"tEXt", b"pHYs", b"zTXt", b"iTXt", b"bKGD"):
            with self.subTest(tag=tag):
                payload = head + png_chunk_bad_crc(tag, b"x" * 40) + tail
                with self.assertRaises(pa.ProductAssetError):
                    pa.sniff_image(payload)


class TestWebpIsNotAcceptable(BaseCase):
    def test_thirty_byte_vp8x_declaring_huge_canvas_is_rejected(self):
        """30바이트짜리 VP8X 가 4000x4000 을 '선언' 한다고 통과하면 안 된다.

        선언만으로 MIN_ASSET_DIMENSION 을 만족시킬 수 있으면 최소 크기
        게이트 자체가 무의미해진다.
        """
        forged = make_webp_vp8x(4000, 4000)
        self.assertLess(len(forged), 40, "이 위조 페이로드는 30바이트대여야 한다")
        with self.assertRaises(pa.ProductAssetError):
            pa.sniff_image(forged)

    def test_webp_is_not_an_allowed_format(self):
        self.assertNotIn("webp", pa.ALLOWED_FORMATS)

    def test_accept_header_does_not_negotiate_webp(self):
        """파싱하지 못하는 포맷을 CDN 에 요구하지 않는다."""
        self.assertNotIn("webp", pa.IMAGE_ACCEPT_HEADER)

    def test_webp_asset_is_refused_at_acquisition(self):
        f = FakeFetcher({IMG1: {"bytes": make_webp_vp8(600, 800),
                                "content_type": "image/webp"}})
        with self.assertRaises(pa.NotAnImageError):
            pa.acquire_product_assets(product(), self.ws, fetcher=f)
        self.assertEqual(os.listdir(self.ws), [])


class TestTakedownUrlNormalization(BaseCase):
    """exact-string denylist 는 ?v=2 하나로 뚫린다."""

    VARIANTS = (
        IMG1 + "?v=2",
        IMG1.replace("https://", "http://"),
        IMG1 + "/",
        IMG1.replace("image.", "IMAGE."),
    )

    def _taken_down(self):
        m = pa.acquire_product_assets(product(), self.ws,
                                      fetcher=self.fetcher_ok())
        pa.takedown(m["manifest_path"], source_url=IMG1, reason="rights holder")

    def test_url_variants_do_not_escape_the_denylist(self):
        self._taken_down()
        for variant in self.VARIANTS:
            with self.subTest(variant=variant):
                p = product(
                    official_image_provenance=[image_spec(source_url=variant)])
                # 바이트를 바꿔 sha256 그물을 우회한다 (재압축 시나리오).
                f = FakeFetcher({variant: {"bytes": make_png(601, 801)}})
                with self.assertRaises(pa.AssetTakedownError):
                    pa.acquire_product_assets(p, self.ws, fetcher=f)
                self.assertEqual(f.calls, [],
                                 "denylist 는 fetch 이전에 물어야 한다")

    def test_normalization_is_symmetric_on_write(self):
        """대장에 변형 URL 로 기록돼도 정규 URL 이 막힌다."""
        self.assertEqual(pa.normalize_url(IMG1 + "?v=2"),
                         pa.normalize_url(IMG1.replace("image.", "IMAGE.")))

    def test_unrelated_url_still_allowed(self):
        self._taken_down()
        p = product(official_image_provenance=[image_spec(source_url=IMG2)])
        f = FakeFetcher({IMG2: {"bytes": self.jpg, "content_type": "image/jpeg"}})
        m = pa.acquire_product_assets(p, self.ws, fetcher=f)
        self.assertEqual(len(m["assets"]), 1)


class TestDimensionHonesty(BaseCase):
    def test_manifest_labels_dimensions_as_declared(self):
        """IHDR 값은 관측이 아니라 선언이다 — 매니페스트가 그렇게 말해야 한다."""
        m = pa.acquire_product_assets(product(), self.ws,
                                      fetcher=self.fetcher_ok())
        asset = m["assets"][0]
        self.assertEqual(asset["dimension_basis"], pa.DIMENSION_BASIS)
        self.assertEqual(asset["declared_width"], 600)
        self.assertEqual(asset["declared_height"], 800)

    def test_dimension_basis_does_not_claim_observation(self):
        self.assertIn("declared", pa.DIMENSION_BASIS)

    def test_sniff_docstring_does_not_claim_observed_pixels(self):
        self.assertNotIn("관측", pa._sniff_png.__doc__ or "")

    def test_header_declared_huge_png_with_real_idat_is_labelled_declared(self):
        """99999x99999 를 주장하는 PNG 는 통과하더라도 '선언' 으로만 기록된다."""
        payload = (PNG_SIG + png_ihdr(99999, 99999)
                   + png_chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + PNG_IEND)
        self.assertEqual(pa.sniff_image(payload), ("png", 99999, 99999))


class TestFetcherHeaders(BaseCase):
    def test_fetcher_actually_sends_the_pinned_accept_header(self):
        """상수 내용이 아니라 fetcher 가 실제로 보내는지를 본다."""
        from unittest import mock

        with mock.patch("requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.headers = {"Content-Type": "image/png"}
            get.return_value.url = IMG1
            get.return_value.history = []
            get.return_value.iter_content.return_value = [self.png]
            pa._requests_fetcher(IMG1)
        self.assertEqual(get.call_args.kwargs["headers"],
                         {"Accept": pa.IMAGE_ACCEPT_HEADER})


if __name__ == "__main__":
    unittest.main(verbosity=2)
