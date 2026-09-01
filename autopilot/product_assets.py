#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V 파이프라인 — 검증된 공식 상품 이미지(`truth layer`).

승인된 설계는 영상 파이프라인을 두 층으로 나눈다:

* **truth layer** — 공식 상품 페이지의 실제 사진. 이 모듈이 소유한다.
* **motion layer** — 손·배경·조명·카메라. 생성 모델이 지어내도 되는 영역.

상품의 실제 생김새가 여기서 틀리면 하류의 모든 프레임과 영상이 틀린다.
그래서 이 모듈은 **조용히 넘어가지 않는다** — 공식 이미지를 얻지 못하면
빈칸으로 저장하는 대신 크게 실패한다.

강제 규칙:

* 모든 자산은 완전한 provenance 를 가진다 — 정확한 출처 URL, 시장(KR/US),
  상품 id, 바이트의 sha256, 수집 시각. 하나라도 비면 **거부**한다.
* 공식 상품 페이지 이미지만. 크리에이터 사진·경쟁사·스톡·생성 이미지 금지.
* 확장자와 Content-Type 은 **믿지 않는다**. 매직 바이트를 직접 스니핑해
  포맷을 확인하고, 모든 PNG 청크의 CRC 를 검증한다. 잘리거나 이미지가
  아니면 거부. 픽셀 크기는 **헤더가 선언한 값** 이며 실제 코딩된 데이터의
  존재로 방증될 뿐이다 (Pillow 없이 디코드하지 않으므로 관측이라 부르지
  않는다 — ``DIMENSION_BASIS`` 참조). WebP 는 선언만으로 위조 가능해
  지원하지 않는다.
* 자산 최대 크기와 상품당 최대 개수는 명명 상수로 강제한다.
* 삭제 요청(takedown)을 위해 자산 → 상품 → 출처 URL 계보를 전부 남긴다.

의존성 경량 원칙: 표준 라이브러리만 쓴다. Pillow 금지 —
PNG/JPEG 헤더는 이 파일에서 직접 파싱한다.

네트워크는 `fetcher=` 주입 시드로만 들어온다 (codex_image_bridge 의 `runner=`
패턴과 동일). 테스트는 절대 네트워크를 타지 않는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import struct
import zlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from video_contracts import (MARKETS, ProductEvidence, atomic_write_json,
                             append_event)

# ---------------------------------------------------------------------------
# 명명 상수 — 한계는 코드에 흩어두지 않고 여기서만 정의한다
# ---------------------------------------------------------------------------

#: 자산 1개의 최대 바이트 (8 MiB). 공식 상품 사진은 이보다 훨씬 작다.
MAX_ASSET_BYTES = 8 * 1024 * 1024

#: 상품 1개당 저장할 수 있는 최대 자산 수.
MAX_ASSETS_PER_PRODUCT = 6

#: 최소 픽셀 변 길이. 썸네일·트래킹 픽셀·스페이서를 걸러낸다.
MIN_ASSET_DIMENSION = 200

#: 허용 포맷 (매직 바이트로 확인된 것만).
#:
#: WebP 는 의도적으로 빠져 있다. RIFF/VP8X 헤더는 캔버스 크기를 **선언만** 하고
#: 30바이트짜리 파일도 4000x4000 을 주장할 수 있어서, MIN_ASSET_DIMENSION 이
#: 선언만으로 충족된다. PNG 만큼 엄격하게(청크 CRC 수준으로) 검증할 방법이
#: 표준 라이브러리에 없으므로 지원을 넣는 대신 뺐다.
ALLOWED_FORMATS = ("png", "jpeg")

#: 시장별 공식 상품 이미지 CDN allowlist.
#: `rights_basis` 는 호출자가 적어 넣는 문자열이라 그 자체로는 증거가 되지 못한다.
#: 이미지가 **실제로** 공식 CDN 에서 왔는지는 호스트로만 확인할 수 있다.
#: 여기 없는 호스트(크리에이터 인스타 CDN·경쟁사 서버·스톡)는 크게 거부한다.
OFFICIAL_IMAGE_HOSTS: Dict[str, Tuple[str, ...]] = {
    "KR": ("coupangcdn.com", "coupang.com"),
    "US": ("media-amazon.com", "ssl-images-amazon.com", "images-amazon.com",
           "amazon.com", "vitaminddrops.com"),
}

#: CDN 콘텐츠 협상을 고정한다. requests 기본값 `Accept: */*` 를 그대로 두면
#: 쿠팡 CDN 정책이 바뀌는 순간 WebP 가 돌아와 KR 트랙이 통째로 실패한다.
#: 파싱하지 못하는 포맷은 애초에 요구하지 않는다 (WebP 제외).
IMAGE_ACCEPT_HEADER = "image/png,image/jpeg"

#: 픽셀 크기의 근거. PNG IHDR / JPEG SOF 는 **헤더가 선언한 값** 이며 이 모듈은
#: 픽셀을 디코드하지 않는다 (Pillow 금지). 실제 코딩된 데이터(IDAT/SOS)의
#: 존재로 방증할 뿐이므로, 매니페스트는 이 값을 관측이라 부르지 않는다.
DIMENSION_BASIS = "header_declared_corroborated_by_coded_data"

#: CRC 가 맞더라도 이 집합 밖의 청크 태그는 받지 않는다.
PNG_ALLOWED_CHUNKS = frozenset({
    b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB",
    b"iCCP", b"bKGD", b"pHYs", b"sBIT", b"hIST", b"tIME", b"sPLT",
    b"tEXt", b"zTXt", b"iTXt", b"eXIf",
})

#: truth layer 에 들어올 수 있는 유일한 권리 근거.
#: 크리에이터 사진·스톡·경쟁사·생성 이미지는 전부 여기 없다 — 곧 거부된다.
ALLOWED_RIGHTS_BASES = ("official_product_page",)

#: provenance 에 반드시 있어야 하는 키. 하나라도 비면 거부.
REQUIRED_PROVENANCE_KEYS = (
    "source_url", "market", "product_id", "option", "official_page_url",
    "rights_basis", "rights_holder", "captured_at",
)

DEFAULT_TIMEOUT = 30

MANIFEST_FILENAME = "product_assets.json"
EVENTS_FILENAME = "product_assets_events.jsonl"


# ---------------------------------------------------------------------------
# 예외 — 전부 ProductAssetError 하위
# ---------------------------------------------------------------------------


class ProductAssetError(Exception):
    """상품 자산 계약 위반 공통 베이스."""


class AssetProvenanceError(ProductAssetError):
    """provenance 결손 — 빈칸으로 저장하지 않고 거부한다."""


class AssetLineageError(ProductAssetError):
    """시장·상품 id·공식 페이지 계보 불일치."""


class OptionMismatchError(ProductAssetError):
    """마케팅 중인 옵션/변형과 이미지 옵션이 다르다."""


class AssetFetchError(ProductAssetError):
    """자산을 가져오지 못했다 — 조용히 건너뛰지 않는다."""


class NotAnImageError(ProductAssetError):
    """매직 바이트가 이미지가 아니다 (예: .png 로 저장된 HTML 오류 페이지)."""


class TruncatedAssetError(ProductAssetError):
    """이미지 헤더는 맞지만 페이로드가 잘렸다."""


class AssetSizeError(ProductAssetError):
    """MAX_ASSET_BYTES 초과."""


class AssetCountError(ProductAssetError):
    """MAX_ASSETS_PER_PRODUCT 초과."""


class AssetDimensionError(ProductAssetError):
    """헤더가 선언한 픽셀 크기가 MIN_ASSET_DIMENSION 미만."""


class EvidenceLanguageError(ProductAssetError):
    """증거 원문의 언어가 시장과 어긋난다 (KR=한국어 / US=영어).

    2026-08-28 실사고: 이 다리가 **모든** 시장에 한국어 원문을 만들어서
    ``video_storyboard`` 의 시장 언어 게이트가 US 카피를 전량 거부했고,
    US 스토리보드가 구조적으로 생성 불가능했다. 게이트는 옳다 — 틀린 쪽은
    여기였다. 그래서 잘못된 언어의 원문은 하류로 흘려보내지 않고 여기서 죽인다.
    """


class AssetTakedownError(ProductAssetError):
    """삭제 요청된 자산을 다시 수집하려 했다 — takedown 대장은 denylist 다."""


# ---------------------------------------------------------------------------
# 공식 CDN 호스트 검증
# ---------------------------------------------------------------------------


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def normalize_url(url: str) -> str:
    """takedown 대조용 정규 형식으로 URL 을 접는다.

    denylist 를 원시 문자열로 비교하면 사소한 변형 네 가지 —
    ``?v=2`` 추가 / ``http://`` 로 다운그레이드 / 후행 슬래시 / 호스트 대문자 —
    만으로 삭제 요청된 자산이 다시 수집된다. 그래서 **쓸 때와 읽을 때 모두**
    이 함수를 통과시킨다.

    스킴과 호스트는 소문자로, 스킴 차이(http/https)는 무시하고, 쿼리와
    프래그먼트는 버리며, 경로의 후행 슬래시는 제거한다. 같은 바이트를 다른
    URL 로 다시 내주는 우회를 막는 것이 목적이므로 보수적으로 넓게 접는다.
    """
    if not isinstance(url, str):
        return ""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    return f"{host}{path}"


def is_official_image_host(url: str, market: str) -> bool:
    """URL 의 호스트가 해당 시장 공식 CDN allowlist 에 속하는가.

    라벨 경계로만 매칭한다 — `evil-coupangcdn.com.attacker.example` 같은
    유사 접미사 호스트는 통과하지 못한다.
    """
    host = _host_of(url)
    if not host:
        return False
    for allowed in OFFICIAL_IMAGE_HOSTS.get(market, ()):
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def require_official_image_host(url: str, market: str, *, what: str) -> None:
    if not is_official_image_host(url, market):
        raise AssetProvenanceError(
            f"{what} 의 호스트 {_host_of(url)!r} 가 {market} 공식 이미지 CDN "
            f"allowlist {OFFICIAL_IMAGE_HOSTS.get(market, ())} 에 없다: {url!r} — "
            "rights_basis 문자열은 호출자가 적어 넣을 뿐 증거가 아니다. "
            "공식 CDN 이 아닌 이미지는 truth layer 에 들어올 수 없다")


# ---------------------------------------------------------------------------
# 매직 바이트 스니핑 — 확장자도 Content-Type 도 믿지 않는다
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _sniff_png(data: bytes) -> Tuple[int, int]:
    """PNG 청크 목록을 실제로 순회해 검증하고, IHDR 이 **선언한** 크기를 읽는다.

    반환값은 헤더 선언값이다 — 이 모듈은 픽셀을 디코드하지 않으므로
    (Pillow 금지) 크기를 실측했다고 주장하지 않는다. 대신 IDAT(실제 코딩된
    데이터)의 존재로 방증하고, 그 사실을 ``DIMENSION_BASIS`` 로 매니페스트에
    명시한다.

    시그니처만 보고 넘어가면 헤더로 감싼 HTML 오류 페이지가 "공식 상품 사진"
    으로 저장된다. 그래서 여기서는:

    * 청크 (길이/태그/데이터/CRC) 를 끝까지 걸어가고,
    * **모든** 청크의 CRC 를 검증하며 (세 태그만 검사하면 공격자 바이트가
      보조 청크에 실려 그대로 통과한다),
    * 알려진 PNG 청크 태그(``PNG_ALLOWED_CHUNKS``) 만 허용하고,
    * IDAT(실제 코딩된 데이터) 가 최소 1개 있어야 하며,
    * 청크 길이 합이 페이로드를 **정확히** 소비해야 한다
      (뒤에 쓰레기가 붙어도, 모자라도 거부).

    길이는 매 반복마다 남은 바이트로 검증하므로 무한 루프가 불가능하다.
    """
    if len(data) < 8 + 25:
        raise TruncatedAssetError(
            f"PNG 헤더가 잘렸다: {len(data)} 바이트 (시그니처+IHDR 최소 33 필요)")
    if data[12:16] != b"IHDR":
        raise NotAnImageError("PNG 시그니처는 맞지만 IHDR 청크가 없다")

    total = len(data)
    pos = 8
    width = height = 0
    seen_ihdr = seen_idat = seen_iend = False

    while pos < total:
        if total - pos < 12:  # 길이(4)+태그(4)+CRC(4)
            raise TruncatedAssetError(
                f"PNG 청크 헤더가 잘렸다: 오프셋 {pos} 에 {total - pos} 바이트만 남음")
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        if length > total - pos - 12:
            raise TruncatedAssetError(
                f"PNG {tag!r} 청크가 길이 {length} 를 주장하지만 남은 바이트는 "
                f"{total - pos - 12} 다 — 잘렸거나 위조된 길이")
        body = data[pos + 8:pos + 8 + length]
        (declared_crc,) = struct.unpack(
            ">I", data[pos + 8 + length:pos + 12 + length])

        if tag not in PNG_ALLOWED_CHUNKS:
            raise NotAnImageError(
                f"PNG 에 알 수 없는 청크 태그 {tag!r} 가 있다 — 임의 바이트를 "
                "이미지에 실어 나르는 통로가 되므로 허용하지 않는다")

        # 태그를 가리지 않고 **모든** 청크의 CRC 를 검증한다.
        actual = zlib.crc32(tag + body) & 0xFFFFFFFF
        if actual != declared_crc:
            raise NotAnImageError(
                f"PNG {tag!r} 청크 CRC 불일치 (선언 {declared_crc:#010x} != "
                f"실제 {actual:#010x}) — 손상되었거나 이미지가 아니다")

        if tag == b"IHDR":
            if seen_ihdr or pos != 8 or length != 13:
                raise NotAnImageError("PNG IHDR 청크가 유효하지 않다")
            width, height = struct.unpack(">II", body[0:8])
            seen_ihdr = True
        elif tag == b"IDAT":
            if length > 0:
                seen_idat = True
        elif tag == b"IEND":
            seen_iend = True

        pos += 12 + length
        if seen_iend:
            break

    if not seen_ihdr:
        raise NotAnImageError("PNG 에 IHDR 청크가 없다")
    if not seen_iend:
        raise TruncatedAssetError("PNG 가 IEND 로 끝나지 않는다 — 잘린 다운로드")
    if pos != total:
        raise NotAnImageError(
            f"PNG IEND 뒤에 {total - pos} 바이트의 잉여 데이터가 있다 — "
            "청크 길이가 페이로드를 정확히 소비해야 한다")
    if not seen_idat:
        raise NotAnImageError(
            "PNG 에 IDAT(실제 픽셀 데이터) 청크가 없다 — 래퍼만 있고 이미지가 없다. "
            "IHDR 이 주장하는 크기는 선언일 뿐이므로 그것만으로는 신뢰하지 않는다")
    return width, height


_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _sniff_jpeg(data: bytes) -> Tuple[int, int]:
    # 관용(rstrip) 없이 정확히 EOI 로 끝나야 한다 — 임의의 트레일러를 허용하면
    # 잘림 탐지 자체가 헐거워진다.
    if not data.endswith(b"\xff\xd9"):
        raise TruncatedAssetError("JPEG 가 EOI(FFD9) 로 끝나지 않는다 — 잘린 다운로드")
    stream = io.BytesIO(data)
    stream.read(2)  # SOI
    while True:
        head = stream.read(1)
        if not head:
            raise TruncatedAssetError("JPEG 에서 SOF 프레임 헤더를 찾지 못했다")
        if head != b"\xff":
            continue
        marker = stream.read(1)
        while marker == b"\xff":  # 패딩
            marker = stream.read(1)
        if not marker:
            raise TruncatedAssetError("JPEG 마커가 잘렸다")
        code = marker[0]
        if code in (0xD8, 0x01) or 0xD0 <= code <= 0xD7:
            continue
        length_bytes = stream.read(2)
        if len(length_bytes) < 2:
            raise TruncatedAssetError("JPEG 세그먼트 길이가 잘렸다")
        (length,) = struct.unpack(">H", length_bytes)
        if code in _JPEG_SOF:
            body = stream.read(5)
            if len(body) < 5:
                raise TruncatedAssetError("JPEG SOF 본문이 잘렸다")
            height, width = struct.unpack(">HH", body[1:5])
            return width, height
        stream.seek(max(length - 2, 0), os.SEEK_CUR)


def sniff_image(data: bytes) -> Tuple[str, int, int]:
    """바이트에서 (포맷, **선언된** 너비, **선언된** 높이) 를 직접 읽는다.

    확장자와 Content-Type 헤더는 전혀 보지 않는다 — 오직 실제 바이트만 본다.
    이미지가 아니면 NotAnImageError, 잘렸으면 TruncatedAssetError.

    크기는 PNG IHDR / JPEG SOF 가 선언한 값이며, 실제 코딩된 데이터의 존재로
    방증될 뿐 디코드로 실측되지 않는다 (``DIMENSION_BASIS`` 참조).
    선언 크기가 0 이면 여기서 바로 거부한다 — 하류의 MIN_ASSET_DIMENSION 에
    떠넘기지 않는다.

    WebP 는 **지원하지 않는다**. RIFF 헤더는 캔버스 크기를 선언만 하고
    30바이트 파일도 4000x4000 을 주장할 수 있어서, 파서를 두면 PNG 경로에서
    막은 위조가 WebP 경로로 그대로 돌아온다. 표준 라이브러리만으로 PNG 수준의
    엄격함을 낼 수 없으므로 절반짜리 파서를 남기는 대신 삭제했다.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise NotAnImageError(f"바이트가 아니다: {type(data)}")
    data = bytes(data)
    if not data:
        raise NotAnImageError("빈 페이로드 — 이미지가 아니다")
    if data.startswith(_PNG_SIG):
        fmt, (width, height) = "png", _sniff_png(data)
    elif data.startswith(b"\xff\xd8\xff"):
        fmt, (width, height) = "jpeg", _sniff_jpeg(data)
    else:
        head = data[:64]
        raise NotAnImageError(
            "매직 바이트가 허용 이미지 포맷"
            f"{ALLOWED_FORMATS} 이 아니다 (확장자/Content-Type 은 신뢰하지 않는다). "
            f"선두 바이트: {head!r}")
    if width <= 0 or height <= 0:
        raise AssetDimensionError(
            f"선언된 픽셀 크기가 {width}x{height} 다 — 픽셀이 없는 이미지는 "
            "공식 상품 사진이 될 수 없다")
    return fmt, width, height


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# provenance / 계보 검증
# ---------------------------------------------------------------------------


def _nonempty(spec: Dict[str, Any], key: str, index: int) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AssetProvenanceError(
            f"official_image_provenance[{index}].{key} 가 비어 있다 — "
            "불완전한 provenance 는 빈칸으로 저장하지 않고 거부한다")
    return value.strip()


def validate_provenance(spec: Any, index: int, *, product_id: str,
                        market: str, product_url: str,
                        marketed_option: str) -> Dict[str, Any]:
    """자산 1개의 provenance 를 검증하고 정규화한다. 결손이면 예외."""
    if not isinstance(spec, dict):
        raise AssetProvenanceError(
            f"official_image_provenance[{index}] 는 dict 여야 한다: {spec!r}")

    clean = {key: _nonempty(spec, key, index) for key in REQUIRED_PROVENANCE_KEYS}

    if not clean["source_url"].startswith(("http://", "https://")):
        raise AssetProvenanceError(
            f"official_image_provenance[{index}].source_url 은 http(s) 여야 한다: "
            f"{clean['source_url']!r}")

    if clean["rights_basis"] not in ALLOWED_RIGHTS_BASES:
        raise AssetProvenanceError(
            f"official_image_provenance[{index}].rights_basis 가 "
            f"{ALLOWED_RIGHTS_BASES} 가 아니다: {clean['rights_basis']!r} — "
            "공식 상품 페이지 이미지만 truth layer 에 들어올 수 있다 "
            "(크리에이터 사진·경쟁사·스톡·생성 이미지 금지)")

    if clean["market"] not in MARKETS:
        raise AssetLineageError(
            f"official_image_provenance[{index}].market 는 {MARKETS} 중 하나여야 "
            f"한다: {clean['market']!r}")
    if clean["market"] != market:
        raise AssetLineageError(
            f"official_image_provenance[{index}].market 불일치: "
            f"{clean['market']!r} != 상품 market {market!r}")

    if clean["product_id"] != product_id:
        raise AssetLineageError(
            f"official_image_provenance[{index}].product_id 불일치: "
            f"{clean['product_id']!r} != {product_id!r} — "
            "다른 상품의 이미지는 저장하지 않는다")

    if clean["official_page_url"] != product_url:
        raise AssetLineageError(
            f"official_image_provenance[{index}].official_page_url 이 상품의 "
            f"공식 페이지와 다르다: {clean['official_page_url']!r} != "
            f"{product_url!r}")

    if clean["option"] != marketed_option:
        raise OptionMismatchError(
            f"official_image_provenance[{index}].option 이 마케팅 중인 옵션과 "
            f"다르다: {clean['option']!r} != {marketed_option!r} — "
            "실제 판매 옵션과 다른 사진은 하류 영상을 전부 틀리게 만든다")

    # 시장 계보가 확정된 뒤에 호스트를 검증한다.
    # rights_basis 는 호출자가 적어 넣는 문자열이라 그 자체로는 증거가 아니다 —
    # 이미지가 실제로 공식 CDN 에서 왔는지는 호스트로만 확인된다.
    require_official_image_host(
        clean["source_url"], market,
        what=f"official_image_provenance[{index}].source_url")

    return clean


# ---------------------------------------------------------------------------
# 안전한 로컬 파일명 — 원격이 준 이름은 절대 쓰지 않는다
# ---------------------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(product_id: str, digest: str, fmt: str) -> str:
    """해시 기반 파일명. 경로 구분자·상위 디렉터리 탈출이 원천 차단된다."""
    stem = _SAFE.sub("-", product_id).strip("-.") or "product"
    return f"{stem}-{digest[:16]}.{fmt}"


# ---------------------------------------------------------------------------
# 기본 fetcher (실제 네트워크) — 테스트에서는 절대 쓰이지 않는다
# ---------------------------------------------------------------------------


def _requests_fetcher(url: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    import requests  # 지연 import — 테스트 경로는 여기 오지 않는다

    # Accept 를 명시해 CDN 콘텐츠 협상을 고정한다. 기본 `*/*` 를 두면
    # CDN 정책 변경만으로 예고 없이 포맷이 바뀐다.
    resp = requests.get(url, timeout=timeout or DEFAULT_TIMEOUT,
                        stream=True, allow_redirects=True,
                        headers={"Accept": IMAGE_ACCEPT_HEADER})
    chunks, total = [], 0
    for chunk in resp.iter_content(64 * 1024):
        total += len(chunk)
        if total > MAX_ASSET_BYTES:
            resp.close()
            raise AssetSizeError(
                f"{url} 응답이 MAX_ASSET_BYTES({MAX_ASSET_BYTES}) 를 초과했다")
        chunks.append(chunk)
    return {
        "bytes": b"".join(chunks),
        "status": resp.status_code,
        "content_type": resp.headers.get("Content-Type", ""),
        "final_url": resp.url,
        "redirect_chain": [r.url for r in resp.history] + [resp.url],
    }


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _existing_takedowns(manifest_path: str) -> List[Dict[str, Any]]:
    """이전 매니페스트의 takedown 대장을 읽어온다.

    삭제 요청 기록은 법적 기록이다 — 재수집이 덮어써서 지워버리면 안 된다.
    """
    if not os.path.isfile(manifest_path):
        return []
    try:
        prior = load_manifest(manifest_path)
    except (OSError, ValueError):
        return []
    entries = prior.get("takedowns")
    return list(entries) if isinstance(entries, list) else []


def _takedown_denylist(takedowns: List[Dict[str, Any]]) -> Tuple[set, set]:
    """takedown 대장에서 (정규화된 URL 집합, sha256 집합) denylist 를 만든다.

    URL 은 반드시 ``normalize_url`` 를 통과시킨다. 원시 문자열로 비교하면
    ``?v=2`` 하나만 붙여도 삭제 요청된 자산이 다시 수집된다.
    """
    urls, digests = set(), set()
    for entry in takedowns:
        if not isinstance(entry, dict):
            continue
        for key in ("source_url", "final_url"):
            value = entry.get(key)
            if value:
                urls.add(normalize_url(value))
        digest = entry.get("sha256")
        if digest:
            digests.add(digest)
    return urls, digests


def acquire_product_assets(product: Dict[str, Any], workspace: str, *,
                           fetcher: Optional[Callable] = None,
                           timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """승인된 공식 이미지를 내려받아 검증하고 provenance 와 함께 저장한다.

    ``fetcher`` 는 테스트용 주입 시드다 (codex_image_bridge 의 ``runner=`` 와
    같은 패턴). 프로덕션은 requests 기반 fetcher 를 쓴다.

    실패는 전부 예외다 — 부분 성공으로 조용히 진행하지 않는다.
    """
    product_id = str(product.get("product_key") or "").strip()
    if not product_id:
        raise AssetLineageError("product_key 가 없다 — 계보 없는 자산은 저장 금지")

    market = str(product.get("country") or "").strip()
    if market not in MARKETS:
        raise AssetLineageError(f"market 는 {MARKETS} 중 하나여야 한다: {market!r}")

    product_url = str(product.get("product_url") or "").strip()
    if not product_url:
        raise AssetLineageError("product_url 이 없다 — 공식 페이지 계보 필수")

    marketed_option = str(product.get("marketed_option") or "").strip()
    if not marketed_option:
        raise OptionMismatchError(
            "marketed_option 이 없다 — 어떤 옵션을 파는지 모르면 "
            "이미지가 맞는지 확인할 수 없다")

    specs = product.get("official_image_provenance") or []
    if not isinstance(specs, list) or not specs:
        raise AssetProvenanceError(
            "official_image_provenance 가 비어 있다 — 공식 이미지를 얻지 못하면 "
            "하류가 잘못된 모습으로 진행되지 않도록 크게 실패한다")

    # 한계 검사는 fetch 이전에. 초과 요청은 네트워크를 아예 타지 않는다.
    if len(specs) > MAX_ASSETS_PER_PRODUCT:
        raise AssetCountError(
            f"자산 {len(specs)} 개는 MAX_ASSETS_PER_PRODUCT"
            f"({MAX_ASSETS_PER_PRODUCT}) 를 초과한다")

    # 전체 provenance 를 먼저 검증한다 — 하나라도 결손이면 아무것도 쓰지 않는다.
    cleaned = [validate_provenance(spec, i, product_id=product_id,
                                   market=market, product_url=product_url,
                                   marketed_option=marketed_option)
               for i, spec in enumerate(specs)]

    # 저장 위치는 provenance 검증 뒤에 확정한다 (계보 없는 디렉터리 생성 금지).
    asset_dir = os.path.join(os.path.abspath(workspace),
                             _SAFE.sub("-", product_id).strip("-.") or "product")
    manifest_path = os.path.join(asset_dir, MANIFEST_FILENAME)

    # 이전 takedown 대장 = denylist. 삭제 요청된 자산은 절대 다시 받지 않는다.
    prior_takedowns = _existing_takedowns(manifest_path)
    denied_urls, denied_sha256 = _takedown_denylist(prior_takedowns)

    for i, clean in enumerate(cleaned):
        if normalize_url(clean["source_url"]) in denied_urls:
            raise AssetTakedownError(
                f"official_image_provenance[{i}].source_url "
                f"{clean['source_url']!r} 는 이미 삭제 요청된(takedown) 자산이다 — "
                "다시 수집하지 않는다 (쿼리·스킴·후행 슬래시·대소문자 변형 포함)")

    dispatch = fetcher or _requests_fetcher
    staged: List[Tuple[Dict[str, Any], bytes]] = []

    for i, clean in enumerate(cleaned):
        url = clean["source_url"]
        try:
            resp = dispatch(url, timeout=timeout)
        except ProductAssetError:
            raise
        except Exception as exc:  # 네트워크·드라이버 오류를 조용히 삼키지 않는다
            raise AssetFetchError(f"{url} 다운로드 실패: {exc!r}") from exc

        if not isinstance(resp, dict):
            raise AssetFetchError(f"fetcher 가 dict 가 아닌 {type(resp)} 를 반환했다")

        status = resp.get("status", 200)
        if status != 200:
            raise AssetFetchError(
                f"{url} 이 HTTP {status} 를 반환했다 — 공식 이미지를 얻지 못했다")

        data = resp.get("bytes")
        if not isinstance(data, (bytes, bytearray)):
            raise AssetFetchError(f"{url} 응답에 바이트가 없다")
        data = bytes(data)

        final_url = resp.get("final_url") or url
        # 리다이렉트 최종 URL 도 denylist 대조 대상이다 — 삭제된 자산을
        # 다른 URL 로 리다이렉트시켜 되돌려주는 우회를 막는다.
        if normalize_url(final_url) in denied_urls:
            raise AssetTakedownError(
                f"{url} 의 리다이렉트 최종 URL {final_url!r} 가 이미 삭제 "
                "요청된 자산이다 — 다시 수집하지 않는다")
        # 리다이렉트 대상도 같은 allowlist 로 검증한다 — 선언된 URL 만 보면
        # CDN 에서 임의 호스트로 튕겨나가는 경로가 열린다.
        require_official_image_host(
            final_url, market,
            what=f"official_image_provenance[{i}] 의 리다이렉트 최종 URL")
        for hop in (resp.get("redirect_chain") or []):
            require_official_image_host(
                hop, market,
                what=f"official_image_provenance[{i}] 의 리다이렉트 경유 URL")

        if len(data) > MAX_ASSET_BYTES:
            raise AssetSizeError(
                f"{url} 은 {len(data)} 바이트로 MAX_ASSET_BYTES"
                f"({MAX_ASSET_BYTES}) 를 초과한다")

        # 확장자도 Content-Type 도 믿지 않는다 — 실제 바이트만 본다.
        fmt, width, height = sniff_image(data)
        if fmt not in ALLOWED_FORMATS:
            raise NotAnImageError(f"{url} 포맷 {fmt!r} 는 허용되지 않는다")
        if width < MIN_ASSET_DIMENSION or height < MIN_ASSET_DIMENSION:
            raise AssetDimensionError(
                f"{url} 선언 크기 {width}x{height} 가 최소 "
                f"{MIN_ASSET_DIMENSION}px 미만이다")

        digest = sha256_bytes(data)
        if digest in denied_sha256:
            raise AssetTakedownError(
                f"{url} 의 바이트 sha256 {digest} 는 이미 삭제 요청된 자산이다 — "
                "URL 을 바꿔도 동일한 바이트는 다시 수집하지 않는다")
        asset = dict(clean)
        asset.update({
            "sha256": digest,
            # source_sha256 는 바이트 해시와 동일한 값의 계약상 별칭이다.
            "source_sha256": digest,
            "format": fmt,
            # width/height 는 **헤더가 선언한** 값이다. 이 모듈은 픽셀을
            # 디코드하지 않으므로(Pillow 금지) 실측했다고 주장하지 않는다 —
            # dimension_basis 가 그 근거를 명시한다.
            "width": width,
            "height": height,
            "declared_width": width,
            "declared_height": height,
            "dimension_basis": DIMENSION_BASIS,
            "bytes": len(data),
            "fetched_at": _now(),
            "final_url": final_url,
            "redirect_chain": list(resp.get("redirect_chain") or []),
            "declared_content_type": resp.get("content_type") or "",
            "rights": {
                "basis": clean["rights_basis"],
                "holder": clean["rights_holder"],
                "source_url": clean["source_url"],
                "captured_at": clean["captured_at"],
            },
        })
        staged.append((asset, data))

    # 여기까지 왔으면 전부 검증됐다 — 이제서야 디스크에 쓴다.
    os.makedirs(asset_dir, exist_ok=True)

    assets: List[Dict[str, Any]] = []
    for asset, data in staged:
        name = safe_filename(product_id, asset["sha256"], asset["format"])
        path = os.path.join(asset_dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        asset["local_path"] = path
        asset["local_filename"] = name
        assets.append(asset)

    manifest = {
        "product_id": product_id,
        "market": market,
        "product_url": product_url,
        "marketed_option": marketed_option,
        # 기록된 스펙 원문(on-pack/공식 페이지 표기). 하류 증거 다리가 이 문구를
        # **그대로** 옮긴다 — 상품에 대해 무언가를 주장하는 템플릿을 쓰지 않는다.
        "spec_facts": [str(f).strip() for f in (product.get("spec_facts") or [])
                       if str(f).strip()],
        "created_at": _now(),
        "asset_dir": asset_dir,
        "manifest_path": manifest_path,
        "assets": assets,
        # 재수집이 법적 삭제 기록을 지우지 않는다 — 대장은 누적된다.
        "takedowns": prior_takedowns,
    }
    atomic_write_json(manifest_path, manifest)
    append_event(os.path.join(asset_dir, EVENTS_FILENAME), {
        "event": "assets_acquired",
        "product_id": product_id,
        "market": market,
        "count": len(assets),
        "sha256": [a["sha256"] for a in assets],
    })
    return manifest


def load_manifest(manifest_path: str) -> Dict[str, Any]:
    with open(manifest_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def takedown(manifest_path: str, *, source_url: str,
             reason: str) -> Dict[str, Any]:
    """출처 URL 로 **저장된 원본 자산** 을 역추적해 삭제하고 계보를 남긴다.

    범위를 정확히 밝힌다: 이 함수는 이 매니페스트가 소유한 바이트와 자산 행만
    제거한다. **하류 파생물(프레임·영상)은 쓸어내지 않는다** — 이 모듈에는
    파생물 역인덱스가 없기 때문이다. 그래서 완료를 조용히 참칭하지 않고,
    반환값에 ``derivatives_swept=False`` 와 ``sweep_sha256`` (파생물 스윕에
    사용해야 할 해시 목록) 을 실어 호출자가 반드시 처리하게 만든다.

    기록된 takedown 은 이후 ``acquire_product_assets`` 에서 denylist 로
    작동한다 — 같은 URL 도, 같은 바이트도 다시 수집되지 않는다.

    매칭되는 자산이 없으면 조용한 성공 대신 AssetLineageError.
    """
    manifest = load_manifest(manifest_path)
    keep, removed = [], []
    for asset in manifest.get("assets", []):
        if source_url in (asset.get("source_url"), asset.get("final_url")):
            removed.append(asset)
        else:
            keep.append(asset)

    if not removed:
        raise AssetLineageError(
            f"{source_url} 에 해당하는 자산이 매니페스트에 없다 — "
            "삭제 요청을 조용히 성공 처리하지 않는다")

    for asset in removed:
        path = asset.get("local_path")
        if path and os.path.isfile(path):
            os.unlink(path)
        manifest.setdefault("takedowns", []).append({
            "source_url": asset.get("source_url"),
            "final_url": asset.get("final_url"),
            "sha256": asset.get("sha256"),
            "local_path": path,
            "product_id": manifest.get("product_id"),
            "market": manifest.get("market"),
            "reason": reason,
            "removed_at": _now(),
        })

    manifest["assets"] = keep
    atomic_write_json(manifest_path, manifest)
    sweep_sha256 = [a.get("sha256") for a in removed if a.get("sha256")]
    append_event(
        os.path.join(os.path.dirname(manifest_path), EVENTS_FILENAME), {
            "event": "assets_takedown",
            "product_id": manifest.get("product_id"),
            "source_url": source_url,
            "reason": reason,
            "removed": [a.get("sha256") for a in removed],
            "derivatives_swept": False,
            "derivative_sweep": "not_swept",
        })
    return {
        "removed": removed,
        "sweep_sha256": sweep_sha256,
        # 파생물은 지우지 않았다 — 호출자가 이 해시로 직접 스윕해야 한다.
        "derivatives_swept": False,
        "derivative_sweep": "not_swept",
        "manifest_path": manifest_path,
    }


#: 자산 서술 원문 템플릿 — 시장 언어별. 이 문장은 **자산 자체에 대한 사실**만
#: 말한다 (포맷·헤더 선언 크기·근거). 상품의 성능·효용에 대해서는 한 마디도
#: 하지 않는다 — 그건 기록된 spec_facts 원문만이 말할 수 있다.
ASSET_QUOTE_TEMPLATES: Dict[str, str] = {
    "KR": ("공식 상품 페이지 이미지 {fmt} "
           "(헤더 선언 크기 {width}x{height}, basis={basis})"),
    "US": ("Official product page image {fmt} "
           "(header-declared size {width}x{height}, basis={basis})"),
}

_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def assert_evidence_language(text: str, market: str, where: str) -> str:
    """증거 원문이 시장 언어와 맞는지 확인한다 (KR=한국어 / US=영어).

    ``video_storyboard._assert_language`` 와 같은 판정을 **상류에서** 한 번 더
    한다. 게이트를 복제해 약화시키려는 것이 아니라, 잘못된 언어의 원문이
    애초에 증거가 되지 못하게 막으려는 것이다 — 하류 게이트는 그대로 둔다.
    """
    if market not in ASSET_QUOTE_TEMPLATES:
        raise AssetLineageError(
            f"{where}: 알 수 없는 시장 {market!r} — 지원 시장 "
            f"{tuple(ASSET_QUOTE_TEMPLATES)} 중 하나여야 한다. 어느 한쪽 언어로 "
            "기본값을 주지 않는다 (그 기본값이 US 스토리보드를 막았다)")
    has_hangul = bool(_HANGUL_RE.search(text))
    if market == "KR":
        if not has_hangul:
            raise EvidenceLanguageError(
                f"{where}: KR 증거 원문은 한국어여야 한다: {text!r}")
    else:
        if has_hangul:
            raise EvidenceLanguageError(
                f"{where}: US 증거 원문은 영어여야 한다 (한글 발견): {text!r}")
        if not _LATIN_WORD_RE.search(text):
            raise EvidenceLanguageError(
                f"{where}: US 증거 원문에 영문이 없다: {text!r}")
    return text


def to_product_evidence(manifest: Dict[str, Any]) -> ProductEvidence:
    """매니페스트를 하류 영상 계약의 ProductEvidence 로 옮긴다.

    **시장을 안다.** 원문(quote)은 시장 언어로 나온다 — KR 은 한국어, US 는
    영어. 알 수 없거나 빠진 시장은 어느 한쪽 언어로 기본값을 주지 않고 크게
    실패한다 (2026-08-28: 한국어 하드코딩이 US 스토리보드를 구조적으로
    불가능하게 만들었다).

    원문의 출처는 두 가지뿐이다.

    * 기록된 스펙 원문(``manifest['spec_facts']``) — 표기 그대로 옮긴다.
      번역하지도, 다시 쓰지도, 무언가를 덧붙이지도 않는다.
    * 자산 자체에 대한 사실(포맷·헤더 선언 크기) — 상품에 대한 주장이 아니다.

    둘 다 상품의 효용·효과를 말하지 않는다. 이 모듈은 진실 계층이며,
    지어낸 문장은 여기서 나오면 안 된다.
    """
    assets = manifest.get("assets") or []
    if not assets:
        raise AssetProvenanceError(
            "자산이 없는 매니페스트로는 ProductEvidence 를 만들 수 없다")

    market = manifest.get("market")
    if not isinstance(market, str) or market not in ASSET_QUOTE_TEMPLATES:
        raise AssetLineageError(
            f"매니페스트 market 이 {tuple(ASSET_QUOTE_TEMPLATES)} 중 하나가 "
            f"아니다: {market!r} — 시장을 모르면 증거의 언어도 알 수 없다. "
            "기본 언어로 넘어가지 않는다")

    first = assets[0]
    official_page = (manifest.get("product_url")
                     or first.get("official_page_url"))

    provenance: List[Dict[str, Any]] = []

    # 1) 기록된 스펙 원문 — 표기 그대로. 시장 언어와 어긋나면 거부한다.
    for i, fact in enumerate(manifest.get("spec_facts") or []):
        text = str(fact).strip()
        if not text:
            continue
        assert_evidence_language(text, market, f"spec_facts[{i}]")
        provenance.append({
            "quote": text,
            "source_url": first["source_url"],
            "original_location": official_page,
        })

    # 2) 자산 서술 — 시장 언어 템플릿.
    template = ASSET_QUOTE_TEMPLATES[market]
    for i, a in enumerate(assets):
        quote = template.format(
            fmt=a["format"], width=a["width"], height=a["height"],
            basis=a.get("dimension_basis", DIMENSION_BASIS))
        assert_evidence_language(quote, market, f"assets[{i}].quote")
        provenance.append({
            "quote": quote,
            "source_url": a["source_url"],
            "original_location": a["official_page_url"],
        })

    return ProductEvidence(
        product_id=manifest["product_id"],
        market=market,
        source_urls=[a["source_url"] for a in assets],
        source_sha256=[a["sha256"] for a in assets],
        rights=dict(first["rights"]),
        provenance=provenance,
        captured_at=manifest.get("created_at") or first["captured_at"],
    )
