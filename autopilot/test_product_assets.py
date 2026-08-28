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

        removed = pa.takedown(m["manifest_path"], source_url=IMG1,
                              reason="rights holder request")
        self.assertEqual(len(removed), 1)
        self.assertFalse(os.path.isfile(local))

        reloaded = pa.load_manifest(m["manifest_path"])
        self.assertEqual(reloaded["assets"], [])
        self.assertTrue(reloaded["takedowns"])
        self.assertEqual(reloaded["takedowns"][0]["source_url"], IMG1)
        self.assertEqual(reloaded["takedowns"][0]["sha256"], sha256(self.png))

    def test_takedown_by_unknown_url_is_a_noop_not_a_silent_success(self):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
