#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V 파이프라인 — 로컬 첫 프레임을 fal 스토리지에 올린다.

`video_generate.build_cut_request()` 는 http(s) 가 아닌 `image_url` 을
**네트워크 호출 전에** 거부한다 (`file://` · 로컬 경로는 provider 가 읽지
못해 4xx 로 죽고, 그 실패는 잘못된 버킷으로 분류돼 다시 지출을 부른다).
그 가드는 옳고, 약화 대상이 아니다. 그런데 첫 프레임은
`video_generate.generate_first_frames()` 가 만든 **로컬 PNG** 다. 그래서
그 사이를 메우는 어댑터가 필요하다 — 이 모듈이다.

`generate_cuts(..., image_url_for=...)` 시드에 그대로 꽂히도록
`make_image_url_for()` 를 제공한다.

문서화된 흐름 (2026-08-28 fal 공식 클라이언트 소스로 확인):

1. ``POST {FAL_REST_URL}/storage/upload/initiate?storage_type=fal-cdn-v3``
   헤더 ``Authorization: Key $FAL_KEY`` → ``{upload_url, file_url}``
2. ``PUT <upload_url>`` 로 바이트 전송 (서명된 URL — 우리 키를 붙이지 않는다)
3. ``file_url`` 이 fetchable https URL

원 지시의 기억(`rest.alpha.fal.ai`)은 현행이 아니다. 현행 호스트는
``https://rest.fal.ai`` 이고 경로/응답 키(`upload_url`/`file_url`)와
``Authorization: Key`` 헤더는 그대로다.

**키 취급:** `FAL_KEY` 는 환경에서만 읽고 절대 로그·예외·매니페스트에
남기지 않는다. 모든 provider 응답 텍스트는 `redact()` 를 통과한 뒤에만
예외 메시지에 들어간다.

의존성 경량 원칙: 표준 라이브러리 + `requests` (이미 유일한 의존성).
네트워크는 `session=` 주입 시드로만 들어온다 (`product_assets.fetcher=` /
`video_generate.client=` 패턴과 동일). 테스트는 절대 소켓을 열지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# 명명 상수 — 한계는 흩어두지 않고 여기서만 정의한다
# ---------------------------------------------------------------------------

#: fal REST API 호스트 (fal-js `getRestApiUrl()` / fal-client-python `REST_URL`).
FAL_REST_URL = "https://rest.fal.ai"

#: 현행 스토리지 백엔드. 공식 클라이언트가 쿼리 파라미터로 붙인다.
FAL_STORAGE_TYPE = "fal-cdn-v3"

FAL_INITIATE_PATH = "/storage/upload/initiate"

#: 첫 프레임 1장의 최대 바이트 (8 MiB). 1024x1536 PNG 는 이보다 훨씬 작다.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

DEFAULT_TIMEOUT = 60

#: 시도 상한. 무한 재시도 금지 — 업로드가 안 되면 지출 0건으로 멈춘다.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2.0, 5.0)

#: 재시도할 가치가 있는 상태코드. 나머지 4xx 는 요청이 틀린 것이라
#: 다시 보내도 같은 답이 온다.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504,
                                    507, 509, 598, 599})

#: 인증 실패 — **절대** 재시도하지 않는다.
AUTH_STATUS_CODES = frozenset({401, 403})

#: 타입만으로 일시적 장애가 확정되는 예외들.
TRANSIENT_EXCEPTION_TYPES = (TimeoutError, ConnectionError, OSError)

#: 예외 메시지에서 키가 있던 자리.
REDACTED = "[FAL_KEY_REDACTED]"

#: 업로드하는 첫 프레임의 유일한 포맷.
UPLOAD_CONTENT_TYPE = "image/png"

#: fal 이 가져갈 수 있는 유일한 스킴 — https 만. 평문 http 는 경고가 아니라
#: 오류다: 첫 프레임 바이트가 중간자에게 노출되고, 하류 `build_cut_request`
#: 의 http(s) 검사는 http 도 통과시키므로 여기서 막지 않으면 아무도 못 막는다.
REQUIRED_SCHEME = "https"

#: 응답 본문을 예외에 실을 때의 상한.
MAX_ERROR_BODY_CHARS = 300


# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class FalUploadError(Exception):
    """fal 업로드 계약 위반 공통 베이스."""


class FalAuthError(FalUploadError):
    """FAL_KEY 없음 또는 401/403 — 재시도하지 않는다."""


class FalUploadSizeError(FalUploadError):
    """MAX_UPLOAD_BYTES 초과 — 전송 전에 거부."""


class FalUploadTransportError(FalUploadError):
    """시도 상한까지 일시적 장애가 계속됐다."""


class FalUploadResponseError(FalUploadError):
    """provider 응답이 계약을 벗어났다 (비 JSON·키 누락·4xx)."""


class InsecureUploadUrlError(FalUploadError):
    """https 가 아닌 URL — 경고가 아니라 오류다."""


# ---------------------------------------------------------------------------
# 키 취급 — 값은 어디에도 남지 않는다
# ---------------------------------------------------------------------------


def redact(text: Any, api_key: Optional[str]) -> str:
    """키 값(및 그 구성 조각)을 텍스트에서 지운다.

    fal 키는 ``<key_id>:<key_secret>`` 형태라 provider 가 일부만 되울릴 수
    있다. 전체 문자열만 지우면 secret 절반이 그대로 로그에 남으므로
    조각 단위로도 지운다.
    """
    out = str(text or "")
    if not api_key:
        return out
    parts = [api_key] + [p for p in str(api_key).split(":") if len(p) >= 8]
    for part in sorted(set(parts), key=len, reverse=True):
        out = out.replace(part, REDACTED)
    return out


def resolve_api_key(api_key: Optional[str] = None,
                    env: Optional[Dict[str, str]] = None) -> str:
    """`FAL_KEY` 를 환경에서 읽는다. 없으면 네트워크 전에 크게 실패한다."""
    if api_key:
        key = str(api_key).strip()
        if key:
            return key
    source = os.environ if env is None else env
    key = str(source.get("FAL_KEY") or "").strip()
    if not key:
        raise FalAuthError(
            "환경변수 FAL_KEY 가 비어 있다 — 업로드 자격증명 없이는 첫 프레임을 "
            "fal 스토리지에 올릴 수 없다. 키 값은 로그·매니페스트에 남기지 않는다.")
    return key


# ---------------------------------------------------------------------------
# URL 검증
# ---------------------------------------------------------------------------


def assert_https(url: Any, *, what: str) -> str:
    value = str(url or "").strip()
    if not value:
        raise FalUploadResponseError(f"{what} 이 비어 있다 — fal 응답 계약 위반")
    parts = urlsplit(value)
    if parts.scheme.lower() != REQUIRED_SCHEME or not parts.netloc:
        raise InsecureUploadUrlError(
            f"{what} 이 {REQUIRED_SCHEME} URL 이 아니다: {value!r} — "
            "평문/스킴 없는 URL 은 경고가 아니라 오류로 다룬다")
    return value


# ---------------------------------------------------------------------------
# 재시도 루프 — 일시적 장애만
# ---------------------------------------------------------------------------


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _status_of(resp: Any) -> int:
    try:
        return int(getattr(resp, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _body_of(resp: Any, api_key: str) -> str:
    return redact(str(getattr(resp, "text", "") or "")[:MAX_ERROR_BODY_CHARS],
                  api_key)


def _check_status(resp: Any, api_key: str, *, what: str) -> None:
    """4xx/5xx 를 분류한다. 인증 실패는 재시도 불가 예외로 확정한다."""
    status = _status_of(resp)
    if 200 <= status < 300:
        return
    body = _body_of(resp, api_key)
    if status in AUTH_STATUS_CODES:
        raise FalAuthError(
            f"{what} 이 HTTP {status} 로 거부됐다 (본문 {body!r}) — "
            "인증 실패는 재시도하지 않는다. FAL_KEY 를 확인하되 값은 출력하지 않는다.")
    if status in RETRYABLE_STATUS_CODES:
        raise FalUploadTransportError(
            f"{what} 이 HTTP {status} 로 실패했다 (본문 {body!r})")
    raise FalUploadResponseError(
        f"{what} 이 HTTP {status} 로 실패했다 (본문 {body!r}) — "
        "재시도해도 같은 답이 온다")


def _attempt(call: Callable[[], Any], *, api_key: str, what: str,
             sleeper: Callable[[float], None]) -> Any:
    """일시적 장애에 한해 MAX_ATTEMPTS 까지 재시도한다."""
    last: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = call()
            _check_status(resp, api_key, what=what)
            return resp
        except (FalAuthError, FalUploadResponseError, InsecureUploadUrlError):
            raise  # 영구 실패 — 돈도 시간도 더 쓰지 않는다
        except FalUploadTransportError as exc:
            last = exc
        except TRANSIENT_EXCEPTION_TYPES as exc:
            last = FalUploadTransportError(
                f"{what} 전송 실패: {redact(repr(exc), api_key)}")
        except Exception as exc:  # noqa: BLE001 — 미지의 예외는 재시도하지 않는다
            raise FalUploadError(
                f"{what} 중 예상 못한 오류: {redact(repr(exc), api_key)}") from exc
        if attempt < MAX_ATTEMPTS - 1:
            sleeper(RETRY_BACKOFF_SECONDS[attempt])
    raise last or FalUploadTransportError(f"{what} 이 실패했다")


def _json_of(resp: Any, api_key: str, *, what: str) -> Dict[str, Any]:
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise FalUploadResponseError(
            f"{what} 응답이 JSON 이 아니다 (본문 {_body_of(resp, api_key)!r})") from exc
    if not isinstance(payload, dict):
        raise FalUploadResponseError(
            f"{what} 응답이 dict 가 아니다: {type(payload).__name__}")
    return payload


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _default_session():
    import requests  # 지연 import — 테스트 경로는 여기 오지 않는다
    return requests.Session()


def upload_file(path: str, *, session: Any = None,
                api_key: Optional[str] = None,
                env: Optional[Dict[str, str]] = None,
                content_type: str = UPLOAD_CONTENT_TYPE,
                max_bytes: int = MAX_UPLOAD_BYTES,
                timeout: int = DEFAULT_TIMEOUT,
                sleeper: Optional[Callable[[float], None]] = None,
                ) -> Dict[str, Any]:
    """로컬 파일을 fal 스토리지에 올리고 fetchable https URL 을 돌려준다.

    반환 dict 는 계보 기록용이다 — 어떤 바이트가 나갔는지 sha256 으로
    증명할 수 있어야 하므로 **업로드 전에 로컬에서** 해시한다.
    """
    key = resolve_api_key(api_key, env)
    nap = sleeper or _sleep

    local = os.path.abspath(os.path.expanduser(str(path or "")))
    if not os.path.isfile(local):
        raise FalUploadError(f"업로드할 파일이 없다: {local!r}")
    size = os.path.getsize(local)
    if size <= 0:
        raise FalUploadError(f"빈 파일은 올리지 않는다: {local!r}")
    if size > max_bytes:
        raise FalUploadSizeError(
            f"{local!r} 가 {size} 바이트로 상한 {max_bytes} 를 초과한다 — "
            "전송 전에 거부한다")

    digest = sha256_file(local)
    with open(local, "rb") as fh:
        data = fh.read()

    sess = session or _default_session()
    initiate_url = (f"{FAL_REST_URL}{FAL_INITIATE_PATH}"
                    f"?storage_type={FAL_STORAGE_TYPE}")

    resp = _attempt(
        lambda: sess.post(initiate_url,
                          headers={"Authorization": f"Key {key}",
                                   "Content-Type": "application/json"},
                          json={"file_name": os.path.basename(local),
                                "content_type": content_type},
                          timeout=timeout),
        api_key=key, what="storage/upload/initiate", sleeper=nap)

    payload = _json_of(resp, key, what="storage/upload/initiate")
    for field in ("upload_url", "file_url"):
        if not payload.get(field):
            raise FalUploadResponseError(
                f"initiate 응답에 {field} 가 없다 (키 {sorted(payload)}) — "
                "fal 업로드 계약 위반")

    # 바이트가 나가기 **전에** 두 URL 을 모두 검증한다.
    upload_url = assert_https(payload["upload_url"], what="upload_url")
    file_url = assert_https(payload["file_url"], what="file_url")

    # 서명된 URL 에는 우리 키를 붙이지 않는다 — 서명 자체가 인증이고,
    # 키를 덧붙이면 제3자 스토리지 호스트로 자격증명이 새어 나간다.
    _attempt(lambda: sess.put(upload_url, data=data,
                              headers={"Content-Type": content_type},
                              timeout=timeout),
             api_key=key, what="storage upload PUT", sleeper=nap)

    return {"url": file_url, "sha256": digest, "bytes": size,
            "local_path": local, "content_type": content_type,
            "storage_type": FAL_STORAGE_TYPE, "uploaded_at": _now()}


def make_image_url_for(*, session: Any = None, api_key: Optional[str] = None,
                       env: Optional[Dict[str, str]] = None,
                       record: Optional[Callable[[Dict[str, Any]], None]] = None,
                       sleeper: Optional[Callable[[float], None]] = None,
                       ) -> Callable[[Dict[str, Any]], str]:
    """`generate_cuts(image_url_for=...)` 시드를 만든다.

    프레임 매니페스트가 선언한 `output_sha256` 을 디스크에서 **재해시해**
    대조한 뒤에만 올린다 — 선언과 다른 바이트가 나가면 하류 계보가 거짓이
    된다. 같은 경로는 한 번만 올린다 (업로드는 무료지만 왕복은 아니다).
    """
    cache: Dict[str, str] = {}

    def image_url_for(frame: Dict[str, Any]) -> str:
        if not isinstance(frame, dict):
            raise FalUploadError(f"frame 은 dict 여야 한다: {type(frame)}")
        path = str(frame.get("output_path") or "")
        declared = str(frame.get("output_sha256") or "")
        if not path or not declared:
            raise FalUploadError(
                "frame 에 output_path/output_sha256 이 없다 — "
                "계보 없는 프레임은 올리지 않는다")
        if path in cache:
            return cache[path]
        if not os.path.isfile(path):
            raise FalUploadError(f"첫 프레임 파일이 없다: {path!r}")
        actual = sha256_file(path)
        if actual != declared:
            raise FalUploadError(
                f"첫 프레임 해시가 매니페스트와 다르다: {actual} != {declared} "
                f"({path}) — 선언과 다른 바이트를 provider 로 보내지 않는다")

        result = upload_file(path, session=session, api_key=api_key, env=env,
                             sleeper=sleeper)
        if record is not None:
            record(dict(result, cut_index=frame.get("cut_index")))
        cache[path] = result["url"]
        return result["url"]

    return image_url_for


__all__: List[str] = [
    "FAL_REST_URL", "FAL_STORAGE_TYPE", "FAL_INITIATE_PATH",
    "MAX_UPLOAD_BYTES", "DEFAULT_TIMEOUT", "MAX_ATTEMPTS",
    "RETRY_BACKOFF_SECONDS", "REDACTED",
    "FalUploadError", "FalAuthError", "FalUploadSizeError",
    "FalUploadTransportError", "FalUploadResponseError",
    "InsecureUploadUrlError",
    "redact", "resolve_api_key", "assert_https", "sha256_file",
    "upload_file", "make_image_url_for",
]
