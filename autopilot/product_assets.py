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
  포맷과 실제 픽셀 크기를 확인한다. 잘리거나 이미지가 아니면 거부.
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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

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
ALLOWED_FORMATS = ("png", "jpeg")

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
    """관측된 픽셀 크기가 MIN_ASSET_DIMENSION 미만."""


# ---------------------------------------------------------------------------
# 매직 바이트 스니핑 — 확장자도 Content-Type 도 믿지 않는다
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _sniff_png(data: bytes) -> Tuple[int, int]:
    # IHDR 은 시그니처 직후 고정 위치에 온다: len(4) + 'IHDR'(4) + w(4) + h(4)
    if len(data) < 33:
        raise TruncatedAssetError(
            f"PNG 헤더가 잘렸다: {len(data)} 바이트 (IHDR 최소 33 필요)")
    if data[12:16] != b"IHDR":
        raise NotAnImageError("PNG 시그니처는 맞지만 IHDR 청크가 없다")
    width, height = struct.unpack(">II", data[16:24])
    if not data.rstrip().endswith(b"IEND\xae\x42\x60\x82"):
        raise TruncatedAssetError("PNG 가 IEND 로 끝나지 않는다 — 잘린 다운로드")
    return width, height


_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _sniff_jpeg(data: bytes) -> Tuple[int, int]:
    if not data.rstrip(b"\x00").endswith(b"\xff\xd9"):
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
    """바이트에서 (포맷, 너비, 높이) 를 직접 읽는다.

    확장자와 Content-Type 헤더는 전혀 보지 않는다 — 오직 실제 바이트만 본다.
    이미지가 아니면 NotAnImageError, 잘렸으면 TruncatedAssetError.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise NotAnImageError(f"바이트가 아니다: {type(data)}")
    data = bytes(data)
    if not data:
        raise NotAnImageError("빈 페이로드 — 이미지가 아니다")
    if data.startswith(_PNG_SIG):
        return ("png",) + _sniff_png(data)
    if data.startswith(b"\xff\xd8\xff"):
        return ("jpeg",) + _sniff_jpeg(data)
    head = data[:64]
    raise NotAnImageError(
        "매직 바이트가 허용 이미지 포맷"
        f"{ALLOWED_FORMATS} 이 아니다 (확장자/Content-Type 은 신뢰하지 않는다). "
        f"선두 바이트: {head!r}")


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

    resp = requests.get(url, timeout=timeout or DEFAULT_TIMEOUT,
                        stream=True, allow_redirects=True)
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
                f"{url} 관측 크기 {width}x{height} 가 최소 "
                f"{MIN_ASSET_DIMENSION}px 미만이다")

        digest = sha256_bytes(data)
        asset = dict(clean)
        asset.update({
            "sha256": digest,
            # source_sha256 는 바이트 해시와 동일한 값의 계약상 별칭이다.
            "source_sha256": digest,
            "format": fmt,
            "width": width,
            "height": height,
            "bytes": len(data),
            "fetched_at": _now(),
            "final_url": resp.get("final_url") or url,
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
    asset_dir = os.path.join(os.path.abspath(workspace),
                             _SAFE.sub("-", product_id).strip("-.") or "product")
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

    manifest_path = os.path.join(asset_dir, MANIFEST_FILENAME)
    manifest = {
        "product_id": product_id,
        "market": market,
        "product_url": product_url,
        "marketed_option": marketed_option,
        "created_at": _now(),
        "asset_dir": asset_dir,
        "manifest_path": manifest_path,
        "assets": assets,
        "takedowns": [],
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
             reason: str) -> List[Dict[str, Any]]:
    """출처 URL 로 자산을 역추적해 삭제하고 계보를 남긴다.

    권리자 삭제 요청 시 그 자산과 파생물을 전부 제거할 수 있어야 한다.
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
    append_event(
        os.path.join(os.path.dirname(manifest_path), EVENTS_FILENAME), {
            "event": "assets_takedown",
            "product_id": manifest.get("product_id"),
            "source_url": source_url,
            "reason": reason,
            "removed": [a.get("sha256") for a in removed],
        })
    return removed


def to_product_evidence(manifest: Dict[str, Any]) -> ProductEvidence:
    """매니페스트를 하류 영상 계약의 ProductEvidence 로 옮긴다."""
    assets = manifest.get("assets") or []
    if not assets:
        raise AssetProvenanceError(
            "자산이 없는 매니페스트로는 ProductEvidence 를 만들 수 없다")
    first = assets[0]
    return ProductEvidence(
        product_id=manifest["product_id"],
        market=manifest["market"],
        source_urls=[a["source_url"] for a in assets],
        source_sha256=[a["sha256"] for a in assets],
        rights=dict(first["rights"]),
        provenance=[{
            "quote": f"공식 상품 페이지 이미지 {a['width']}x{a['height']} {a['format']}",
            "source_url": a["source_url"],
            "original_location": a["official_page_url"],
        } for a in assets],
        captured_at=manifest.get("created_at") or first["captured_at"],
    )
