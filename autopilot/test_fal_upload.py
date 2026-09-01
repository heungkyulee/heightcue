#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fal 스토리지 업로드 어댑터 회귀 — 네트워크 0건.

`build_cut_request()` 는 http(s) 가 아닌 `image_url` 을 네트워크 호출 **전에**
거부한다. 첫 프레임은 로컬 PNG 로 만들어지므로 그 사이를 메우는 것이 이
어댑터다. 전송은 전부 `session=` 주입 시드로만 들어오고, 이 스위트는 단
한 번도 소켓을 열지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fal_upload as fu  # noqa: E402


# ---------------------------------------------------------------------------
# 픽스처 — 진짜 최소 PNG (CRC 정상, IDAT 존재)
# ---------------------------------------------------------------------------


def _chunk(tag: bytes, body: bytes) -> bytes:
    import struct
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))


def tiny_png() -> bytes:
    import struct
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


FAKE_KEY = "test-key-id:test-key-secret-not-real"
FILE_URL = "https://v3.fal.media/files/penguin/abc123_frame.png"
UPLOAD_URL = "https://storage.example.fal/upload/signed"


class Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """`session=` 주입 시드. 호출을 전부 기록한다."""

    def __init__(self, post=None, put=None):
        self.posts = []
        self.puts = []
        self._post = post or [Resp(200, {"upload_url": UPLOAD_URL,
                                         "file_url": FILE_URL})]
        self._put = put or [Resp(200)]

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        item = self._post[min(len(self.posts) - 1, len(self._post) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    def put(self, url, **kwargs):
        self.puts.append((url, kwargs))
        item = self._put[min(len(self.puts) - 1, len(self._put) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="falup-")
        self.png = os.path.join(self.tmp, "frame.png")
        self.data = tiny_png()
        with open(self.png, "wb") as fh:
            fh.write(self.data)
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.slept = []

    def sleeper(self, seconds):
        self.slept.append(seconds)

    def up(self, session, **kw):
        kw.setdefault("api_key", FAKE_KEY)
        kw.setdefault("sleeper", self.sleeper)
        return fu.upload_file(self.png, session=session, **kw)


# ---------------------------------------------------------------------------
# 성공 경로
# ---------------------------------------------------------------------------


class TestHappyPath(Base):
    def test_returns_https_url_and_local_sha256(self):
        s = FakeSession()
        out = self.up(s)
        self.assertEqual(out["url"], FILE_URL)
        self.assertTrue(out["url"].startswith("https://"))
        self.assertEqual(out["sha256"], self.sha)
        self.assertEqual(out["bytes"], len(self.data))
        self.assertEqual(out["local_path"], self.png)
        self.assertTrue(out["uploaded_at"])

    def test_initiate_hits_documented_endpoint_with_key_auth(self):
        s = FakeSession()
        self.up(s)
        url, kwargs = s.posts[0]
        self.assertTrue(url.startswith(fu.FAL_REST_URL + "/storage/upload/initiate"))
        self.assertIn("storage_type=" + fu.FAL_STORAGE_TYPE, url)
        self.assertEqual(kwargs["headers"]["Authorization"], "Key " + FAKE_KEY)
        self.assertEqual(kwargs["json"]["content_type"], "image/png")
        self.assertEqual(kwargs["json"]["file_name"], "frame.png")
        self.assertEqual(kwargs["timeout"], fu.DEFAULT_TIMEOUT)

    def test_bytes_are_put_to_the_signed_upload_url(self):
        s = FakeSession()
        self.up(s)
        url, kwargs = s.puts[0]
        self.assertEqual(url, UPLOAD_URL)
        self.assertEqual(kwargs["data"], self.data)
        self.assertEqual(kwargs["headers"]["Content-Type"], "image/png")
        # 서명된 URL 에 우리 키를 덧붙이지 않는다.
        self.assertNotIn("Authorization", kwargs["headers"])

    def test_no_retry_when_first_attempt_succeeds(self):
        s = FakeSession()
        self.up(s)
        self.assertEqual(len(s.posts), 1)
        self.assertEqual(len(s.puts), 1)
        self.assertEqual(self.slept, [])


# ---------------------------------------------------------------------------
# https 강제
# ---------------------------------------------------------------------------


class TestHttpsEnforced(Base):
    def test_http_file_url_is_an_error_not_a_warning(self):
        s = FakeSession(post=[Resp(200, {"upload_url": UPLOAD_URL,
                                         "file_url": "http://v3.fal.media/x.png"})])
        with self.assertRaises(fu.InsecureUploadUrlError):
            self.up(s)

    def test_file_url_scheme_relative_or_junk_rejected(self):
        for bad in ("//v3.fal.media/x.png", "ftp://h/x.png", "x.png", ""):
            s = FakeSession(post=[Resp(200, {"upload_url": UPLOAD_URL,
                                            "file_url": bad})])
            with self.assertRaises(fu.FalUploadError):
                self.up(s)

    def test_http_upload_url_rejected_before_bytes_leave(self):
        s = FakeSession(post=[Resp(200, {"upload_url": "http://insecure/put",
                                         "file_url": FILE_URL})])
        with self.assertRaises(fu.InsecureUploadUrlError):
            self.up(s)
        self.assertEqual(s.puts, [])


# ---------------------------------------------------------------------------
# 인증 — 값은 절대 새지 않는다
# ---------------------------------------------------------------------------


class TestAuth(Base):
    def test_missing_env_key_raises_before_any_network(self):
        s = FakeSession()
        with self.assertRaises(fu.FalAuthError):
            fu.upload_file(self.png, session=s, api_key=None, env={},
                           sleeper=self.sleeper)
        self.assertEqual(s.posts, [])

    def test_key_read_from_env(self):
        s = FakeSession()
        out = fu.upload_file(self.png, session=s, env={"FAL_KEY": FAKE_KEY},
                             sleeper=self.sleeper)
        self.assertEqual(out["url"], FILE_URL)

    def test_key_never_appears_in_error_text(self):
        s = FakeSession(post=[Resp(401, None, "denied for key " + FAKE_KEY)])
        with self.assertRaises(fu.FalAuthError) as ctx:
            self.up(s)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertIn(fu.REDACTED, str(ctx.exception))

    def test_redact_scrubs_key_anywhere_in_text(self):
        text = f"a {FAKE_KEY} b {FAKE_KEY}"
        out = fu.redact(text, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, out)
        # 키의 secret 부분만 남는 부분 유출도 없다.
        self.assertNotIn(FAKE_KEY.split(":")[-1], out)

    def test_auth_failure_is_never_retried(self):
        s = FakeSession(post=[Resp(403, None, "forbidden")])
        with self.assertRaises(fu.FalAuthError):
            self.up(s)
        self.assertEqual(len(s.posts), 1)
        self.assertEqual(self.slept, [])


# ---------------------------------------------------------------------------
# 한계
# ---------------------------------------------------------------------------


class TestLimits(Base):
    def test_oversize_file_rejected_before_network(self):
        s = FakeSession()
        with self.assertRaises(fu.FalUploadSizeError):
            self.up(s, max_bytes=len(self.data) - 1)
        self.assertEqual(s.posts, [])

    def test_empty_file_rejected(self):
        empty = os.path.join(self.tmp, "empty.png")
        open(empty, "wb").close()
        s = FakeSession()
        with self.assertRaises(fu.FalUploadError):
            fu.upload_file(empty, session=s, api_key=FAKE_KEY,
                           sleeper=self.sleeper)
        self.assertEqual(s.posts, [])

    def test_missing_file_rejected_before_network(self):
        s = FakeSession()
        with self.assertRaises(fu.FalUploadError):
            fu.upload_file(os.path.join(self.tmp, "nope.png"), session=s,
                           api_key=FAKE_KEY, sleeper=self.sleeper)
        self.assertEqual(s.posts, [])

    def test_named_constants_exist_and_are_sane(self):
        self.assertGreater(fu.MAX_UPLOAD_BYTES, 0)
        self.assertGreater(fu.DEFAULT_TIMEOUT, 0)
        self.assertGreaterEqual(fu.MAX_ATTEMPTS, 2)
        self.assertEqual(len(fu.RETRY_BACKOFF_SECONDS), fu.MAX_ATTEMPTS - 1)


# ---------------------------------------------------------------------------
# 재시도 — 일시적 장애만
# ---------------------------------------------------------------------------


class TestRetry(Base):
    def test_transient_connection_error_is_retried_then_succeeds(self):
        s = FakeSession(post=[ConnectionError("connection reset"),
                              Resp(200, {"upload_url": UPLOAD_URL,
                                         "file_url": FILE_URL})])
        out = self.up(s)
        self.assertEqual(out["url"], FILE_URL)
        self.assertEqual(len(s.posts), 2)
        self.assertEqual(self.slept, [fu.RETRY_BACKOFF_SECONDS[0]])

    def test_503_is_retried(self):
        s = FakeSession(post=[Resp(503, None, "service unavailable"),
                              Resp(200, {"upload_url": UPLOAD_URL,
                                         "file_url": FILE_URL})])
        self.assertEqual(self.up(s)["url"], FILE_URL)

    def test_429_is_retried(self):
        s = FakeSession(post=[Resp(429, None, "rate limit"),
                              Resp(200, {"upload_url": UPLOAD_URL,
                                         "file_url": FILE_URL})])
        self.assertEqual(self.up(s)["url"], FILE_URL)

    def test_400_is_not_retried(self):
        s = FakeSession(post=[Resp(400, None, "bad request")])
        with self.assertRaises(fu.FalUploadResponseError):
            self.up(s)
        self.assertEqual(len(s.posts), 1)

    def test_404_is_not_retried(self):
        s = FakeSession(post=[Resp(404, None, "not found")])
        with self.assertRaises(fu.FalUploadResponseError):
            self.up(s)
        self.assertEqual(len(s.posts), 1)

    def test_attempts_are_bounded(self):
        s = FakeSession(post=[ConnectionError("timeout")] * 20)
        with self.assertRaises(fu.FalUploadTransportError):
            self.up(s)
        self.assertEqual(len(s.posts), fu.MAX_ATTEMPTS)
        self.assertEqual(self.slept, list(fu.RETRY_BACKOFF_SECONDS))

    def test_put_transient_failure_is_retried(self):
        s = FakeSession(put=[Resp(502, None, "bad gateway"), Resp(200)])
        self.assertEqual(self.up(s)["url"], FILE_URL)
        self.assertEqual(len(s.puts), 2)

    def test_put_auth_failure_not_retried(self):
        s = FakeSession(put=[Resp(403, None, "forbidden")])
        with self.assertRaises(fu.FalAuthError):
            self.up(s)
        self.assertEqual(len(s.puts), 1)


# ---------------------------------------------------------------------------
# 응답 형태 방어 — provider 응답은 신뢰하지 않는 입력이다
# ---------------------------------------------------------------------------


class TestMalformedResponse(Base):
    def test_non_json_initiate_body(self):
        s = FakeSession(post=[Resp(200, None, "<html>oops</html>")])
        with self.assertRaises(fu.FalUploadResponseError):
            self.up(s)

    def test_missing_upload_url(self):
        s = FakeSession(post=[Resp(200, {"file_url": FILE_URL})])
        with self.assertRaises(fu.FalUploadResponseError):
            self.up(s)

    def test_non_dict_initiate_body(self):
        s = FakeSession(post=[Resp(200, ["nope"])])
        with self.assertRaises(fu.FalUploadResponseError):
            self.up(s)


# ---------------------------------------------------------------------------
# generate_cuts 접합 — `image_url_for` 시드로 바로 꽂힌다
# ---------------------------------------------------------------------------


class TestFrameAdapter(Base):
    def _frame(self):
        return {"output_path": self.png, "output_sha256": self.sha,
                "cut_index": 0}

    def test_adapter_returns_https_url_for_a_frame(self):
        s = FakeSession()
        seed = fu.make_image_url_for(session=s, api_key=FAKE_KEY,
                                     sleeper=self.sleeper)
        self.assertEqual(seed(self._frame()), FILE_URL)

    def test_adapter_output_satisfies_build_cut_request(self):
        import video_generate as vg
        s = FakeSession()
        seed = fu.make_image_url_for(session=s, api_key=FAKE_KEY,
                                     sleeper=self.sleeper)
        req = vg.build_cut_request(self._frame(), generation_prompt="a hand lifts it",
                                   output_path=os.path.join(self.tmp, "c0.mp4"),
                                   image_url=seed(self._frame()))
        self.assertEqual(req["payload"]["image_url"], FILE_URL)

    def test_adapter_verifies_declared_frame_hash(self):
        s = FakeSession()
        seed = fu.make_image_url_for(session=s, api_key=FAKE_KEY,
                                     sleeper=self.sleeper)
        bad = dict(self._frame(), output_sha256="0" * 64)
        with self.assertRaises(fu.FalUploadError):
            seed(bad)
        self.assertEqual(s.posts, [])

    def test_adapter_records_lineage(self):
        s = FakeSession()
        records = []
        seed = fu.make_image_url_for(session=s, api_key=FAKE_KEY,
                                     sleeper=self.sleeper, record=records.append)
        seed(self._frame())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["sha256"], self.sha)
        self.assertEqual(records[0]["url"], FILE_URL)

    def test_adapter_uploads_each_frame_once(self):
        s = FakeSession()
        seed = fu.make_image_url_for(session=s, api_key=FAKE_KEY,
                                     sleeper=self.sleeper)
        seed(self._frame())
        seed(self._frame())
        self.assertEqual(len(s.posts), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
