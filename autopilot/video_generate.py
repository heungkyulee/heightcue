#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V 파이프라인 — 컷당 첫 프레임 1장 생성 (truth ↔ motion 접합부).

검증된 공식 상품 이미지(`product_assets.py`, truth layer)와 스토리보드의
한 컷(`video_storyboard.py`)을 받아, 그 컷 **하나만**을 위한 첫 프레임
이미지를 만든다. 생성은 오직 `codex_image_bridge.edit_image` 를 통해서만
일어난다 — 다른 provider·다른 모델·스톡 이미지로의 대체는 전면 금지다.

강제 규칙:

* **컷 1개 = 프레임 1장.** 한 장에 전체 스토리를 몰아넣지 않는다.
* 상품은 그대로. 손·배경·조명·카메라(motion layer)는 지어내도 되지만
  상품의 형태·색·로고·질감(truth layer)은 변형 금지.
* 프레임은 `<projects_root>/heightcue_<run_id>/assets/frames/` 에 쓴다.
* **대체 금지, 크게 실패.** Codex OAuth 또는 `gpt-image-2-medium` 티어를
  프리플라이트에서 확인하지 못하면 한 푼도 쓰기 전에 잡을 멈춘다.
* 출력 PNG 의 **IHDR 을 직접 읽어** 실제 1024x1536 세로인지 확인한다.
  Hermes Codex 플러그인은 요청한 aspect_ratio 를 그대로 에코할 뿐
  측정하지 않으므로(`plugins/image_gen/openai-codex/__init__.py:749`,
  `resolve_aspect_ratio` :583) 그 값은 아무것도 증명하지 못한다.
  `pixel_size`(:758) 만이 관측값이지만 Optional 이다. 그래서 여기서
  자체적으로 잰다 — 이 검사는 의도적으로 자립형이며 다른 모듈의 파서를
  import 하지 않는다.
* 상품당 첫 프레임 후보 수는 `MAX_FIRST_FRAME_CANDIDATES` 로 상한.

**알려진 미해결 공백 — 상품 충실도는 검증되지 않는다.**
"상품이 알아볼 수 있게 그대로 남는다"는 요구는 현재 **프롬프트 텍스트로만**
전달된다(`PRODUCT_FIDELITY_CLAUSE`). 생성 **후** 검증은 존재하지 않는다 —
perceptual hash 도, 임베딩 유사도도, 사람 게이트도 없다. 모델이 조항을
무시하고 상품을 변형해도 이 모듈은 통과시킨다. 지금 있는 구조적 보장은
두 가지뿐이다: (1) 권리 검증을 통과하고 디스크에서 **재해시**된 진짜 공식
상품 사진이 참조 입력으로 들어간다, (2) 그 sha256 이 프레임 매니페스트에
기록되므로 위반이 발생하면 최소한 **추적은 된다**. 하류에서 이 모듈이
충실도를 보장한다고 가정하지 말 것. 이 공백을 닫으려면 별도 작업이 필요하다.

네트워크·유료 호출은 `bridge=` / `preflight_runner=` 주입 시드로만 들어온다
(`codex_image_bridge` 의 `runner=` 패턴과 동일). 테스트는 절대 실제
이미지를 생성하지 않는다.

의존성 경량 원칙: 표준 라이브러리만. requirements.txt 는 `requests` 하나뿐.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import time
import zlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import codex_image_bridge as cib
import video_contracts as vc
from video_contracts import (CUT_DURATION_SECONDS, IMAGE_HERMES_MODEL,
                             IMAGE_HERMES_PROVIDER, IMAGE_MODEL_ALIAS,
                             IMAGE_PROVIDER_MODEL, MARKETS, STATE_GENERATING,
                             STATE_QA_FAILED, STATE_READY_TO_PUBLISH,
                             STATE_RETRYABLE_FAILED, VIDEO_ASPECT_RATIO,
                             VIDEO_ENDPOINT, VIDEO_RESOLUTION, append_event,
                             assert_transition, atomic_write_json)

# ---------------------------------------------------------------------------
# 고정 계약 (상류 확정 — 변경 금지)
# ---------------------------------------------------------------------------

MODEL_ALIAS = IMAGE_MODEL_ALIAS            # gpt-image-gen-2
HERMES_PROVIDER = IMAGE_HERMES_PROVIDER    # openai-codex
HERMES_MODEL = IMAGE_HERMES_MODEL          # gpt-image-2-medium
PROVIDER_MODEL = IMAGE_PROVIDER_MODEL      # gpt-image-2

#: 매니페스트에 반드시 있어야 하는 이미지 식별자 4종.
REQUIRED_IMAGE_IDENTIFIERS = {
    "image_model_alias": MODEL_ALIAS,
    "image_hermes_provider": HERMES_PROVIDER,
    "image_hermes_model": HERMES_MODEL,
    "image_provider_model": PROVIDER_MODEL,
}

#: 측정으로 확인해야 하는 실제 픽셀 크기 (세로 숏폼).
PORTRAIT_WIDTH = 1024
PORTRAIT_HEIGHT = 1536

#: 상품 1개당 만들 수 있는 첫 프레임 후보 최대 수 (승인된 설계값).
MAX_FIRST_FRAME_CANDIDATES = 3

#: truth layer 에 허용되는 유일한 권리 근거.
ALLOWED_RIGHTS_BASES = ("official_product_page",)

DEFAULT_PROJECTS_ROOT = os.path.expanduser("~/OpenMontage/projects")

PREFLIGHT_TIMEOUT = 60

#: 모든 프레임 프롬프트에 붙는 불변 조항 — 상품은 진실, 나머지는 연출.
PRODUCT_FIDELITY_CLAUSE = (
    "PRODUCT FIDELITY (absolute): reproduce the product in the supplied "
    "reference photo EXACTLY as it is — identical shape, proportions, "
    "colour, material, finish, label text and logo placement. Do not "
    "restyle, recolour, reshape, embellish, re-letter, or redesign the "
    "product or its packaging. You MAY invent only the surrounding scene: "
    "hands, background, lighting, and camera framing. "
    "Exactly ONE moment, ONE action, ONE benefit — not a collage, "
    "not a storyboard, not a multi-panel image. "
    "Vertical 9:16 portrait still frame, 1024x1536."
)


# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class FirstFrameError(Exception):
    """첫 프레임 생성 계약 위반 공통 베이스."""


class PreflightError(FirstFrameError):
    """Codex OAuth 또는 gpt-image-2-medium 티어 확인 실패 — 대체하지 않고 중단."""


class PortraitError(FirstFrameError):
    """출력 이미지를 직접 재보니 1024x1536 세로가 아니다."""


class CandidateCapError(FirstFrameError):
    """MAX_FIRST_FRAME_CANDIDATES 초과 — 지출 전에 거부."""


class SourceAssetError(FirstFrameError):
    """소스 이미지가 검증된 공식 상품 자산에서 오지 않았다."""


class ManifestLineageError(FirstFrameError):
    """매니페스트에 이미지 식별자 4종 중 하나라도 없거나 값이 다르다."""


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
#: 길이 0 + "IEND" + CRC — 정상 PNG 는 정확히 이 12바이트로 끝난다.
_PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def measure_png(path: str) -> tuple:
    """출력 PNG 의 IHDR 을 **직접** 읽어 (너비, 높이) 를 반환한다.

    디스패처가 에코한 aspect_ratio 는 우리가 보낸 요청의 메아리일 뿐이라
    아무것도 증명하지 못한다. 여기서는 실제 파일 바이트만 본다.
    자립형으로 유지한다 — 다른 모듈의 파서를 import 하지 않는다.

    이 바이트는 브리지를 통해 **네트워크에서** 온다. 그래서 선언된 크기를
    그냥 믿지 않는다: IHDR CRC 를 직접 계산해 대조하고, 비어 있지 않은
    IDAT 가 최소 1개 있어야 하며, 파일이 12바이트 IEND 청크로 끝나야 한다.
    앞 33바이트만 멀쩡한 잘린 응답이 1024x1536 세로로 통과해 매니페스트에
    기록되는 일을 막는다.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise PortraitError(f"출력 이미지를 읽을 수 없다: {path} ({exc})") from exc

    if not data.startswith(_PNG_SIG):
        raise PortraitError(
            f"출력이 PNG 가 아니다 (선두 바이트 {data[:8]!r}): {path} — "
            "확장자나 디스패처 응답이 아니라 실제 바이트로 판정한다")
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise PortraitError(f"PNG IHDR 청크를 찾을 수 없다: {path}")

    ihdr_len = struct.unpack(">I", data[8:12])[0]
    if ihdr_len != 13:
        raise PortraitError(f"IHDR 길이가 13 이 아니다 ({ihdr_len}): {path}")
    declared_crc = struct.unpack(">I", data[29:33])[0]
    actual_crc = zlib.crc32(data[12:29]) & 0xFFFFFFFF
    if declared_crc != actual_crc:
        raise PortraitError(
            f"IHDR CRC 불일치 (선언 {declared_crc:#010x} != 실제 "
            f"{actual_crc:#010x}): {path} — 손상되거나 잘린 응답이다")

    if not data.endswith(_PNG_IEND):
        raise PortraitError(
            f"PNG 가 IEND 청크로 끝나지 않는다: {path} — "
            "잘린 다운로드를 완성된 프레임으로 받아들이지 않는다")

    # 청크를 실제로 걸어 비어 있지 않은 IDAT 가 있는지 확인한다.
    offset = 8
    saw_idat = False
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise PortraitError(
                f"PNG 청크 {tag!r} 가 파일 끝을 넘어간다: {path} — 잘린 응답이다")
        if tag == b"IDAT" and length > 0:
            saw_idat = True
        offset = end
        if tag == b"IEND":
            break
    if offset != len(data):
        raise PortraitError(f"PNG 청크 구조가 파일 길이와 어긋난다: {path}")
    if not saw_idat:
        raise PortraitError(
            f"PNG 에 비어 있지 않은 IDAT 가 없다: {path} — 픽셀이 없는 껍데기다")

    width, height = struct.unpack(">II", data[16:24])
    return width, height


def assert_measured_portrait(path: str) -> tuple:
    """실측 크기가 정확히 1024x1536 이 아니면 PortraitError."""
    width, height = measure_png(path)
    if (width, height) != (PORTRAIT_WIDTH, PORTRAIT_HEIGHT):
        raise PortraitError(
            f"측정된 출력 크기 {width}x{height} 가 요구 세로 크기 "
            f"{PORTRAIT_WIDTH}x{PORTRAIT_HEIGHT} 와 다르다: {path} — "
            "디스패처가 에코한 aspect_ratio 는 신뢰하지 않는다")
    return width, height


def assert_frame_lineage(frame: Dict[str, Any]) -> Dict[str, Any]:
    """프레임 매니페스트의 이미지 식별자 4종을 강제한다."""
    if not isinstance(frame, dict):
        raise ManifestLineageError(f"frame 은 dict 여야 한다: {type(frame)}")
    for key, expected in REQUIRED_IMAGE_IDENTIFIERS.items():
        if key not in frame:
            raise ManifestLineageError(
                f"프레임 매니페스트에 {key} 가 없다 — 이미지 식별자 4종 "
                f"{tuple(REQUIRED_IMAGE_IDENTIFIERS)} 은 전부 필수다")
        if frame[key] != expected:
            raise ManifestLineageError(
                f"{key} 불일치: {frame[key]!r} != {expected!r} (상류 확정 계약)")
    return frame


# ---------------------------------------------------------------------------
# 프리플라이트 — 지출 전에 Codex 자격증명·티어를 확인한다
# ---------------------------------------------------------------------------


def _subprocess_preflight(cmd: List[str], timeout: Optional[int] = None) -> Dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout or PREFLIGHT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError(f"프리플라이트 명령 실패: {cmd} ({exc!r})") from exc
    return {"returncode": proc.returncode, "stdout": proc.stdout,
            "stderr": proc.stderr}


def _run_preflight(runner: Callable, cmd: List[str], label: str) -> str:
    try:
        result = runner(cmd, timeout=PREFLIGHT_TIMEOUT)
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError(f"{label} 실행 실패: {exc!r}") from exc
    if not isinstance(result, dict):
        raise PreflightError(f"{label} 러너가 dict 가 아닌 {type(result)} 반환")
    if result.get("returncode", 1) != 0:
        raise PreflightError(
            f"{label} 이 exit {result.get('returncode')} 로 실패했다: "
            f"{str(result.get('stderr') or '')[-500:]} — "
            "대체 provider 로 진행하지 않고 잡을 멈춘다")
    return str(result.get("stdout") or "")


#: 인증 상태 줄에서 이 토큰들 중 하나라도 보이면 무조건 미인증으로 본다.
#: `authorized` 는 `unauthorized` 의 부분문자열이라 substring 검사로는
#: 탈인증된 provider 가 프리플라이트를 그대로 통과한다 — 토큰으로 본다.
_AUTH_NEGATIVE_TOKENS = frozenset({
    "unauthorized", "unauthenticated", "not", "no", "never", "none",
    "expired", "revoked", "invalid", "missing", "failed", "error",
})
_AUTH_POSITIVE_TOKEN = "authorized"


def _line_says_authorized(line: str) -> bool:
    """`hermes auth list` 한 줄이 **명시적 인증 상태**인지 토큰 단위로 본다."""
    tokens = set(re.findall(r"[a-z]+", str(line or "").lower()))
    if tokens & _AUTH_NEGATIVE_TOKENS:
        return False
    return _AUTH_POSITIVE_TOKEN in tokens


def preflight_codex(*, runner: Optional[Callable] = None) -> Dict[str, Any]:
    """`hermes auth list` + `hermes config get image_gen` 로 계약을 확인한다.

    provider ``openai-codex`` 가 인증돼 있고 이미지 모델이
    ``gpt-image-2-medium`` 이어야 한다. 아니면 PreflightError —
    Hermes 범용 이미지 생성이나 다른 모델로 **절대** 대체하지 않는다.
    """
    dispatch = runner or _subprocess_preflight

    auth = _run_preflight(dispatch, ["hermes", "auth", "list"],
                          "hermes auth list")
    lowered = auth.lower()
    if HERMES_PROVIDER not in lowered:
        raise PreflightError(
            f"`hermes auth list` 에 provider {HERMES_PROVIDER!r} 가 없다 — "
            "Codex OAuth 자격증명 없이는 첫 프레임을 만들 수 없다. "
            "다른 provider 로 대체하지 않고 중단한다.")
    provider_line = next(
        (ln for ln in auth.splitlines() if HERMES_PROVIDER in ln.lower()), "")
    if not _line_says_authorized(provider_line):
        raise PreflightError(
            f"provider {HERMES_PROVIDER!r} 가 인증 상태가 아니다: "
            f"{provider_line.strip()!r} — 중단한다.")

    raw = _run_preflight(dispatch, ["hermes", "config", "get", "image_gen"],
                         "hermes config get image_gen")
    cfg: Dict[str, Any] = {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            cfg = parsed
    except (ValueError, TypeError):
        cfg = {}

    provider = str(cfg.get("provider") or "").strip()
    model = str(cfg.get("model") or "").strip()
    if not provider or not model:
        # JSON 이 아니거나 키가 빠졌으면 키:값 형태를 보수적으로 훑는다.
        for line in raw.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().strip('"').lower()
                value = value.strip().strip(',').strip('"')
                if key == "provider" and not provider:
                    provider = value
                elif key == "model" and not model:
                    model = value

    if not provider:
        raise PreflightError(
            "`hermes config get image_gen` 출력에 provider 키가 아예 없다 "
            f"(원문 {raw.strip()[:200]!r}) — 설정이 비었거나 CLI 출력 형식이 "
            "바뀐 것이다. 잘못된 provider 문제와는 다르다. 중단한다.")
    if not model:
        raise PreflightError(
            "`hermes config get image_gen` 출력에 model 키가 아예 없다 "
            f"(원문 {raw.strip()[:200]!r}) — 설정이 비었거나 CLI 출력 형식이 "
            "바뀐 것이다. 잘못된 모델 문제와는 다르다. 중단한다.")
    if provider != HERMES_PROVIDER:
        raise PreflightError(
            f"image_gen provider 가 {provider!r} 다 — {HERMES_PROVIDER!r} 이어야 "
            "한다. 다른 provider 로 대체하지 않고 중단한다.")
    if model != HERMES_MODEL:
        raise PreflightError(
            f"image_gen model 이 {model!r} 다 — {HERMES_MODEL!r} 티어가 "
            "필요하다. 다른 모델로 대체하지 않고 중단한다.")

    return {"provider": provider, "model": model,
            "model_alias": MODEL_ALIAS, "provider_model": PROVIDER_MODEL,
            "checked_at": _now()}


# ---------------------------------------------------------------------------
# 소스 자산 선택 — 반드시 product_assets 매니페스트에서 온 것
# ---------------------------------------------------------------------------


def select_source_asset(asset_manifest: Any, *, product_id: str,
                        market: str) -> Dict[str, Any]:
    """검증된 공식 상품 자산 하나를 고른다. 아니면 SourceAssetError."""
    if not isinstance(asset_manifest, dict):
        raise SourceAssetError(
            f"asset_manifest 는 product_assets 매니페스트 dict 여야 한다: "
            f"{type(asset_manifest)}")

    if str(asset_manifest.get("product_id") or "") != product_id:
        raise SourceAssetError(
            f"자산 매니페스트의 product_id 가 스토리보드와 다르다: "
            f"{asset_manifest.get('product_id')!r} != {product_id!r} — "
            "다른 상품의 사진으로 프레임을 만들지 않는다")

    manifest_market = str(asset_manifest.get("market") or "")
    if manifest_market not in MARKETS or manifest_market != market:
        raise SourceAssetError(
            f"자산 매니페스트의 market 불일치: {manifest_market!r} != {market!r}")

    assets = asset_manifest.get("assets") or []
    if not isinstance(assets, list) or not assets:
        raise SourceAssetError(
            "자산 매니페스트에 자산이 없다 — 검증된 공식 상품 이미지 없이는 "
            "첫 프레임을 만들 수 없다 (스톡·생성 이미지로 대체 금지)")

    asset = assets[0]
    if not isinstance(asset, dict):
        raise SourceAssetError(f"assets[0] 는 dict 여야 한다: {asset!r}")

    basis = str(asset.get("rights_basis")
                or (asset.get("rights") or {}).get("basis") or "")
    if basis not in ALLOWED_RIGHTS_BASES:
        raise SourceAssetError(
            f"소스 자산의 rights_basis 가 {ALLOWED_RIGHTS_BASES} 가 아니다: "
            f"{basis!r} — 공식 상품 페이지 이미지만 truth layer 로 쓸 수 있다")

    path = str(asset.get("local_path") or "")
    if not path or not os.path.isfile(path):
        raise SourceAssetError(f"소스 자산 파일이 없다: {path!r}")

    declared = str(asset.get("source_sha256") or asset.get("sha256") or "")
    if not declared:
        raise SourceAssetError(
            "소스 자산에 sha256 이 없다 — 해시 없는 자산은 계보를 남길 수 없다")

    actual = sha256_file(path)
    if actual != declared:
        raise SourceAssetError(
            f"소스 자산 해시가 매니페스트와 다르다: {actual} != {declared} "
            f"({path}) — product_assets 가 검증한 그 바이트가 아니다")

    return {"path": path, "sha256": actual, "rights_basis": basis}


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def frames_dir_for(run_id: str, projects_root: Optional[str] = None) -> str:
    root = os.path.abspath(os.path.expanduser(projects_root
                                              or DEFAULT_PROJECTS_ROOT))
    return os.path.join(root, f"heightcue_{run_id}", "assets", "frames")


def build_frame_prompt(cut: Any) -> str:
    """컷 **하나**의 첫 프레임 프롬프트 + 불변 상품 충실도 조항."""
    base = str(getattr(cut, "first_frame_prompt", "") or "").strip()
    if not base:
        raise FirstFrameError(
            f"cuts[{getattr(cut, 'index', '?')}].first_frame_prompt 가 비어 있다")
    return f"{base}\n\n{PRODUCT_FIDELITY_CLAUSE}"


def generate_first_frames(storyboard: Any, asset_manifest: Dict[str, Any], *,
                          projects_root: Optional[str] = None,
                          bridge: Optional[Callable] = None,
                          preflight_runner: Optional[Callable] = None,
                          ) -> Dict[str, Any]:
    """컷마다 정확히 한 장씩 첫 프레임을 만든다.

    ``bridge`` / ``preflight_runner`` 는 테스트 주입 시드다. 프로덕션은
    각각 ``codex_image_bridge.edit_image`` 와 실제 ``hermes`` CLI 를 쓴다.
    실패는 전부 예외다 — 부분 성공으로 조용히 진행하지 않는다.
    """
    run_id = str(getattr(storyboard, "run_id", "") or "").strip()
    product_id = str(getattr(storyboard, "product_id", "") or "").strip()
    market = str(getattr(storyboard, "market", "") or "").strip()
    storyboard_id = str(getattr(storyboard, "storyboard_id", "") or "").strip()
    cuts = list(getattr(storyboard, "cuts", None) or [])

    if not run_id or not product_id or not storyboard_id:
        raise FirstFrameError(
            "run_id / product_id / storyboard_id 는 전부 필요하다 — "
            "계보 없는 프레임은 만들지 않는다")
    if market not in MARKETS:
        raise FirstFrameError(f"market 는 {MARKETS} 중 하나여야 한다: {market!r}")
    if not cuts:
        raise FirstFrameError("컷이 없다 — 만들 첫 프레임이 없다")

    # 1) 상한을 지출·자격증명 확인보다 먼저 강제한다.
    if len(cuts) > MAX_FIRST_FRAME_CANDIDATES:
        raise CandidateCapError(
            f"첫 프레임 {len(cuts)} 장 요구는 상품당 상한 "
            f"MAX_FIRST_FRAME_CANDIDATES={MAX_FIRST_FRAME_CANDIDATES} 를 "
            "초과한다 — 지출 전에 거부한다")

    # 2) 소스 자산이 검증된 공식 이미지인지 (역시 지출 전에).
    source = select_source_asset(asset_manifest, product_id=product_id,
                                 market=market)

    # 3) 프롬프트를 전부 먼저 만든다 — 하나라도 비면 아무것도 생성하지 않는다.
    #    컷 인덱스가 겹치면 같은 output_path 를 두 번 쓰게 되고, 뒤 컷이 앞
    #    컷을 덮어써도 len(frames)==len(cuts) 는 그대로 통과한다 — 실제보다
    #    한 장 적은 결과를 성공으로 보고하게 되므로 여기서 명시적으로 거부한다.
    indices = [int(getattr(cut, "index")) for cut in cuts]
    duplicates = sorted({i for i in indices if indices.count(i) > 1})
    if duplicates:
        raise FirstFrameError(
            f"컷 인덱스가 중복이다: {duplicates} — 같은 출력 경로를 덮어써 "
            "프레임이 조용히 사라진다. 지출 전에 거부한다")
    prompts = {int(getattr(cut, "index")): build_frame_prompt(cut)
               for cut in cuts}

    # 4) 프리플라이트. 실패하면 여기서 끝 — 대체 경로는 존재하지 않는다.
    preflight = preflight_codex(runner=preflight_runner)

    dispatch = bridge or cib.edit_image
    out_dir = frames_dir_for(run_id, projects_root)
    os.makedirs(out_dir, exist_ok=True)

    frames: List[Dict[str, Any]] = []
    written: List[str] = []
    try:
        for cut in cuts:
            index = int(getattr(cut, "index"))
            prompt = prompts[index]
            output_path = os.path.join(
                out_dir, f"{product_id}_cut{index:02d}_first_frame.png")

            # 쓰기 **전에** 기록한다 — 브리지가 다운로드 도중 죽으면(트런케이트,
            # 디스크 오류, Ctrl-C) 경로가 등록돼 있어야 부분 파일을 지울 수 있다.
            written.append(output_path)
            bridge_manifest = dispatch(prompt, [source["path"]], output_path)

            if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
                raise FirstFrameError(
                    f"컷 {index} 의 첫 프레임이 기록되지 않았다: {output_path}")

            # 디스패처의 에코가 아니라 실제 파일에서 잰다.
            width, height = assert_measured_portrait(output_path)

            frame = {
                "run_id": run_id,
                "storyboard_id": storyboard_id,
                "product_id": product_id,
                "market": market,
                "cut_index": index,
                "prompt": prompt,
                "image_model_alias": MODEL_ALIAS,
                "image_hermes_provider": HERMES_PROVIDER,
                "image_hermes_model": HERMES_MODEL,
                "image_provider_model": PROVIDER_MODEL,
                "source_path": source["path"],
                "source_sha256": source["sha256"],
                "source_rights_basis": source["rights_basis"],
                "output_path": output_path,
                "output_sha256": sha256_file(output_path),
                "output_bytes": os.path.getsize(output_path),
                "measured_width": width,
                "measured_height": height,
                "measured_by": "video_generate.measure_png (IHDR 직접 판독)",
                "dispatcher_echoed_aspect_ratio": (
                    bridge_manifest.get("observed_aspect_ratio")
                    if isinstance(bridge_manifest, dict) else None),
                "created_at": _now(),
            }
            assert_frame_lineage(frame)
            frames.append(frame)
    except BaseException:
        # 부분 산출물을 남기지 않는다 — 하류가 반쪽 프레임을 집어가면 안 된다.
        for path in written:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise

    if len(frames) != len(cuts):
        raise FirstFrameError(
            f"컷 {len(cuts)} 개에 프레임 {len(frames)} 장 — 컷 1개당 정확히 "
            "1장이어야 한다")

    manifest = {
        "run_id": run_id,
        "storyboard_id": storyboard_id,
        "product_id": product_id,
        "market": market,
        "frames_dir": out_dir,
        "candidate_cap": MAX_FIRST_FRAME_CANDIDATES,
        "preflight": preflight,
        "created_at": _now(),
        "frames": frames,
    }
    manifest_path = os.path.join(out_dir, "first_frames.json")
    atomic_write_json(manifest_path, manifest)
    manifest["manifest_path"] = manifest_path
    append_event(os.path.join(out_dir, "first_frames_events.jsonl"), {
        "event": "first_frames_generated",
        "run_id": run_id,
        "product_id": product_id,
        "market": market,
        "count": len(frames),
        "sha256": [f["output_sha256"] for f in frames],
    })
    return manifest


# ===========================================================================
# 컷 생성 — MiniMax H3 Max image-to-video (첫 지출 지점)
# ===========================================================================
#
# 이 절은 첫 프레임 1장을 5초짜리 세로 영상 컷 1개로 바꾼다. 규칙은 짧다:
#
# * **컷당 요청 1개.** 정확히 5초 · 768P · 9:16 · image-to-video.
# * **대체 경로 없음.** H3 Max 가 실패하면 잡이 실패한다. 다른 fal 모델,
#   다른 provider, text-to-video 로 갈아타는 코드 경로는 존재하지 않는다.
#   (이 모듈에서 유일하게 허용된 URL 은 FAL_I2V_URL 하나뿐이다.)
# * **지출 전 게이트.** 호출 전에 tool/provider/endpoint/model/추정비용/
#   승인정책을 결정 로그에 남기고, 실행당·일당 상한을 강제한다. 상한을
#   넘길 실행은 요청을 **한 건도** 보내지 않고 거부한다.
# * **추정≠실비.** 예약은 추정으로 잡고, 호출 후 provider 가 알려준 실비로
#   정산한다. 기록에서 추정이 실비를 조용히 대신하는 일은 없다.
#
# 네트워크는 `client=` 주입 시드로만 들어온다. 테스트는 절대 유료 호출을
# 하지 않는다.

#: 실제 fal.ai 호출을 소유하는 OpenMontage 도구 (여기서 재구현하지 않는다).
VIDEO_TOOL = "minimax_fal_video"
VIDEO_PROVIDER = "minimax"
VIDEO_GATEWAY = "fal.ai"
VIDEO_MODEL = "minimax-h3-max"

#: 이 모듈이 아는 유일한 영상 URL. 다른 엔드포인트는 코드에 존재하지 않는다.
FAL_I2V_URL = f"https://queue.fal.run/{VIDEO_ENDPOINT}"

VIDEO_OPERATION = "image_to_video"

#: 컷 생성은 사람 승인 없이 자동 실행되지만 상한 안에서만 가능하다.
APPROVAL_POLICY = "auto_within_caps"

# --- 가격 (fal.ai H3 Max I2V 엔드포인트 게시가) ------------------------------
# 480P $0.025/s, 768P $0.04/s. 엔드포인트 페이지는 768P 가 프로모션 종료 후
# $0.08/s 로 오른다고 경고한다 — 오르면 아래 상수 하나만 고친다.
RATE_USD_PER_SECOND_768P = 0.04
RATE_USD_PER_SECOND_480P = 0.025

#: 실행 1회 지출 상한 (컷 3개 × 재시도 3회 여유).
MAX_RUN_SPEND_USD = 2.00
#: 하루 지출 상한.
MAX_DAILY_SPEND_USD = 10.00

#: 컷 1개의 시도 상한. 여기 도달하면 retryable_failed — 무한 재시도 금지.
MAX_CUT_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2.0, 5.0)

#: 인프라성(재시도 가능) 실패 신호.
RETRYABLE_MARKERS = ("timeout", "timed out", "rate limit", "rate_limit",
                     "too many requests", "429", "500", "502", "503", "504",
                     "connection reset", "connection aborted",
                     "temporarily unavailable", "service unavailable")


class CutGenerationError(FirstFrameError):
    """컷 생성 계약 위반 공통 베이스."""


class CutRequestError(CutGenerationError):
    """요청 형태가 고정 계약(5초/768P/9:16/I2V)을 벗어났다 — 전송 전에 거부."""


class CostGateError(CutGenerationError):
    """실행당 또는 일당 지출 상한 초과 — 호출 전에 거부."""


class CutContentError(CutGenerationError):
    """모델·콘텐츠 실패 (재시도해도 같은 결과) → qa_failed."""


class CutInfraError(CutGenerationError):
    """인프라 실패가 시도 상한까지 반복됐다 → retryable_failed."""


def estimate_cut_cost_usd(duration_seconds: int = CUT_DURATION_SECONDS,
                          resolution: str = VIDEO_RESOLUTION) -> float:
    """고정 계약(5초 768P) 기준 컷 1개 추정가 = $0.20.

    **추정일 뿐이다.** 기록에서 실비를 대신하지 않는다.
    """
    if resolution == "768P":
        rate = RATE_USD_PER_SECOND_768P
    elif resolution == "480P":
        rate = RATE_USD_PER_SECOND_480P
    else:
        raise CutRequestError(
            f"H3 Max image-to-video 는 480P/768P 만 지원한다: {resolution!r}")
    return round(rate * duration_seconds, 6)


def is_retryable_provider_error(exc: BaseException) -> bool:
    """인프라성 실패(타임아웃·5xx·레이트리밋)만 True.

    모델/콘텐츠 실패는 재시도해도 같은 결과라 False — 돈만 태운다.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if "content" in text or "policy" in text or "moderat" in text:
        return False
    return any(marker in text for marker in RETRYABLE_MARKERS)


# --- 지출 원장 ---------------------------------------------------------------


def load_spend(ledger_path: str) -> Dict[str, Any]:
    """일자별 예약·실비 원장. 없으면 빈 원장."""
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"days": {}}
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        return {"days": {}}
    return data


def _day_bucket(ledger: Dict[str, Any], day: str) -> Dict[str, Any]:
    bucket = ledger["days"].setdefault(
        day, {"reserved_usd": 0.0, "actual_usd": 0.0, "entries": []})
    bucket.setdefault("reserved_usd", 0.0)
    bucket.setdefault("actual_usd", 0.0)
    bucket.setdefault("entries", [])
    return bucket


def reserve_spend(ledger_path: str, day: str, amount_usd: float, *,
                  run_id: str, cut_index: int,
                  daily_cap_usd: float = MAX_DAILY_SPEND_USD) -> Dict[str, Any]:
    """**호출 전에** 추정액을 예약한다. 일당 상한을 넘기면 CostGateError."""
    ledger = load_spend(ledger_path)
    bucket = _day_bucket(ledger, day)
    projected = round(bucket["reserved_usd"] + amount_usd, 6)
    if projected > daily_cap_usd + 1e-9:
        raise CostGateError(
            f"day cap 초과: {day} 예약 {bucket['reserved_usd']:.4f} + "
            f"{amount_usd:.4f} = {projected:.4f} > 일당 상한 "
            f"{daily_cap_usd:.4f} USD — 호출을 보내지 않고 거부한다")
    bucket["reserved_usd"] = projected
    bucket["entries"].append({"run_id": run_id, "cut_index": cut_index,
                              "reserved_usd": amount_usd, "ts": _now()})
    atomic_write_json(ledger_path, ledger)
    return ledger


def reconcile_spend(ledger_path: str, day: str, reserved_usd: float,
                    actual_usd: float, *, run_id: str,
                    cut_index: int) -> Dict[str, Any]:
    """호출 후 실비로 정산한다 — 예약은 실비로 대체된다."""
    ledger = load_spend(ledger_path)
    bucket = _day_bucket(ledger, day)
    bucket["reserved_usd"] = round(
        max(0.0, bucket["reserved_usd"] - reserved_usd + actual_usd), 6)
    bucket["actual_usd"] = round(bucket["actual_usd"] + actual_usd, 6)
    bucket["entries"].append({"run_id": run_id, "cut_index": cut_index,
                              "reserved_usd": -reserved_usd,
                              "actual_usd": actual_usd, "ts": _now()})
    atomic_write_json(ledger_path, ledger)
    return ledger


def release_spend(ledger_path: str, day: str, amount_usd: float, *,
                  run_id: str, cut_index: int) -> Dict[str, Any]:
    """실패한 호출의 예약을 되돌린다 (실비 0)."""
    ledger = load_spend(ledger_path)
    bucket = _day_bucket(ledger, day)
    bucket["reserved_usd"] = round(
        max(0.0, bucket["reserved_usd"] - amount_usd), 6)
    bucket["entries"].append({"run_id": run_id, "cut_index": cut_index,
                              "released_usd": amount_usd, "ts": _now()})
    atomic_write_json(ledger_path, ledger)
    return ledger


# --- 요청 조립 ---------------------------------------------------------------


def cuts_dir_for(run_id: str, projects_root: Optional[str] = None) -> str:
    root = os.path.abspath(os.path.expanduser(projects_root
                                              or DEFAULT_PROJECTS_ROOT))
    return os.path.join(root, f"heightcue_{run_id}", "assets", "cuts")


def build_cut_request(frame: Dict[str, Any], *, motion_prompt: str,
                      output_path: str,
                      duration_seconds: int = CUT_DURATION_SECONDS,
                      resolution: str = VIDEO_RESOLUTION,
                      aspect_ratio: str = VIDEO_ASPECT_RATIO,
                      operation: str = VIDEO_OPERATION) -> Dict[str, Any]:
    """컷 1개의 fal 요청을 조립한다. 고정 계약을 벗어나면 전송 전에 거부.

    비-5초·비-768P·비-9:16·비-I2V 요청은 여기서 만들어질 수 없다 —
    이 함수가 유일한 요청 생성 지점이다.
    """
    if operation != VIDEO_OPERATION:
        raise CutRequestError(
            f"operation 은 {VIDEO_OPERATION!r} 뿐이다: {operation!r} — "
            "text-to-video 경로는 존재하지 않는다")
    if duration_seconds != CUT_DURATION_SECONDS:
        raise CutRequestError(
            f"컷 길이는 정확히 {CUT_DURATION_SECONDS}초여야 한다: {duration_seconds!r}")
    if resolution != VIDEO_RESOLUTION:
        raise CutRequestError(
            f"해상도는 {VIDEO_RESOLUTION} 고정이다: {resolution!r}")
    if aspect_ratio != VIDEO_ASPECT_RATIO:
        raise CutRequestError(
            f"화면비는 {VIDEO_ASPECT_RATIO} 고정이다: {aspect_ratio!r}")

    if not isinstance(frame, dict):
        raise CutRequestError(f"frame 은 dict 여야 한다: {type(frame)}")
    prompt = str(motion_prompt or "").strip()
    if not prompt:
        raise CutRequestError("motion_prompt 가 비어 있다 — 빈 프롬프트로 지출 금지")
    first_frame_path = str(frame.get("output_path") or "")
    first_frame_sha256 = str(frame.get("output_sha256") or "")
    if not first_frame_path or not first_frame_sha256:
        raise CutRequestError(
            "첫 프레임 경로/해시가 없다 — 계보 없는 컷은 만들지 않는다")

    return {
        "tool": VIDEO_TOOL,
        "provider": VIDEO_PROVIDER,
        "gateway": VIDEO_GATEWAY,
        "model": VIDEO_MODEL,
        "endpoint": VIDEO_ENDPOINT,
        "url": FAL_I2V_URL,
        "operation": VIDEO_OPERATION,
        "cut_index": int(frame.get("cut_index") or 0),
        "first_frame_path": first_frame_path,
        "first_frame_sha256": first_frame_sha256,
        "output_path": output_path,
        "payload": {
            "prompt": prompt,
            "image_url": f"file://{os.path.abspath(first_frame_path)}",
            "duration": duration_seconds,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        },
    }


_MP4_BRANDS = (b"ftyp",)


def _assert_playable_mp4(path: str) -> int:
    """산출물이 실제로 mp4 컨테이너인지 바이트로 확인한다."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
        size = os.path.getsize(path)
    except OSError as exc:
        raise CutContentError(f"컷 산출물을 읽을 수 없다: {path} ({exc})") from exc
    if size <= 0 or not any(brand in head for brand in _MP4_BRANDS):
        raise CutContentError(
            f"컷 산출물이 mp4 가 아니다 (선두 {head[:12]!r}): {path}")
    return size


# --- 공개 API ----------------------------------------------------------------


def generate_cuts(storyboard: Any, frames_manifest: Dict[str, Any], *,
                  client: Callable,
                  job_id: str,
                  ledger_path: str,
                  projects_root: Optional[str] = None,
                  run_cap_usd: float = MAX_RUN_SPEND_USD,
                  daily_cap_usd: float = MAX_DAILY_SPEND_USD,
                  today: Optional[str] = None,
                  sleep: Optional[Callable] = None) -> Dict[str, Any]:
    """첫 프레임마다 5초 H3 Max I2V 컷을 정확히 하나씩 만든다.

    ``client`` 는 주입 시드다 — ``build_cut_request`` 가 만든 요청 dict 를
    받아 ``{"request_id", "output_path", "cost_usd"?}`` 를 돌려주고, 실패하면
    예외를 던진다. 프로덕션은 OpenMontage ``minimax_fal_video`` 도구를 감싼다.

    반환 state 는 계약 전이표의 ``generating`` 하위 간선만 쓴다:
    성공 ``ready_to_publish`` / 모델·콘텐츠 실패 ``qa_failed`` /
    인프라 소진 ``retryable_failed``. **대체 모델 경로는 없다.**
    """
    run_id = str(getattr(storyboard, "run_id", "") or "").strip()
    product_id = str(getattr(storyboard, "product_id", "") or "").strip()
    market = str(getattr(storyboard, "market", "") or "").strip()
    storyboard_id = str(getattr(storyboard, "storyboard_id", "") or "").strip()
    sb_cuts = list(getattr(storyboard, "cuts", None) or [])
    frames = list((frames_manifest or {}).get("frames") or [])
    napper = sleep or time.sleep
    day = today or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    if not run_id or not product_id or not storyboard_id or not job_id:
        raise CutGenerationError(
            "job_id / run_id / product_id / storyboard_id 는 전부 필요하다")
    if market not in MARKETS:
        raise CutGenerationError(f"market 는 {MARKETS} 중 하나여야 한다: {market!r}")
    if not frames:
        raise CutGenerationError("첫 프레임이 없다 — 만들 컷이 없다")
    if len(frames) != len(sb_cuts):
        raise CutGenerationError(
            f"프레임 {len(frames)} 장 != 컷 {len(sb_cuts)} 개 — 1:1 이어야 한다")

    out_dir = cuts_dir_for(run_id, projects_root)
    os.makedirs(out_dir, exist_ok=True)
    events = os.path.join(out_dir, "cut_generation_events.jsonl")

    # --- 지출 게이트: 상한은 상수보다 커질 수 없고, 초과 실행은 호출 0건으로 거부 ---
    if run_cap_usd > MAX_RUN_SPEND_USD + 1e-9:
        raise CostGateError(
            f"run cap {run_cap_usd} 은 상수 MAX_RUN_SPEND_USD="
            f"{MAX_RUN_SPEND_USD} 보다 클 수 없다 — 런타임에 상한을 올릴 수 없다")
    if daily_cap_usd > MAX_DAILY_SPEND_USD + 1e-9:
        raise CostGateError(
            f"day cap {daily_cap_usd} 은 상수 MAX_DAILY_SPEND_USD="
            f"{MAX_DAILY_SPEND_USD} 보다 클 수 없다")

    per_cut_estimate = estimate_cut_cost_usd()
    job_estimate = round(per_cut_estimate * len(frames), 6)
    if job_estimate > run_cap_usd + 1e-9:
        raise CostGateError(
            f"run cap 초과: 컷 {len(frames)}개 추정 {job_estimate:.4f} USD > "
            f"실행당 상한 {run_cap_usd:.4f} USD — 요청을 한 건도 보내지 않는다")
    already = _day_bucket(load_spend(ledger_path), day)["reserved_usd"]
    if round(already + job_estimate, 6) > daily_cap_usd + 1e-9:
        raise CostGateError(
            f"day cap 초과: {day} 기예약 {already:.4f} + 추정 "
            f"{job_estimate:.4f} > 일당 상한 {daily_cap_usd:.4f} USD — "
            "요청을 한 건도 보내지 않는다")

    motions = {int(getattr(c, "index")): str(getattr(c, "motion_prompt", "")
                                             or "").strip()
               for c in sb_cuts}

    lineage: List[Dict[str, Any]] = []
    attempts: Dict[int, int] = {}
    run_spent = 0.0
    state = STATE_READY_TO_PUBLISH
    failure: Optional[str] = None

    for frame in sorted(frames, key=lambda f: int(f.get("cut_index") or 0)):
        index = int(frame.get("cut_index") or 0)
        output_path = os.path.join(
            out_dir, f"{product_id}_cut{index:02d}.mp4")
        request = build_cut_request(frame, motion_prompt=motions.get(index, ""),
                                    output_path=output_path)
        attempts[index] = 0
        last_error: Optional[BaseException] = None

        while attempts[index] < MAX_CUT_ATTEMPTS:
            attempts[index] += 1
            attempt = attempts[index]

            # 실행당 상한을 시도 단위로도 다시 확인한다 (재시도도 돈이다).
            if round(run_spent + per_cut_estimate, 6) > run_cap_usd + 1e-9:
                state = STATE_RETRYABLE_FAILED
                failure = (f"run cap 초과로 컷 {index} 시도 {attempt} 중단 "
                           f"(누적 {run_spent:.4f} USD)")
                break

            # 결정 로그 — **호출 직전**에 남긴다.
            append_event(events, {
                "event": "cost_gate",
                "job_id": job_id, "run_id": run_id, "cut_index": index,
                "attempt": attempt,
                "tool": VIDEO_TOOL, "provider": VIDEO_PROVIDER,
                "gateway": VIDEO_GATEWAY, "endpoint": VIDEO_ENDPOINT,
                "model": VIDEO_MODEL, "url": FAL_I2V_URL,
                "operation": VIDEO_OPERATION,
                "duration_seconds": CUT_DURATION_SECONDS,
                "resolution": VIDEO_RESOLUTION,
                "aspect_ratio": VIDEO_ASPECT_RATIO,
                "estimated_cost_usd": per_cut_estimate,
                "rate_usd_per_second": RATE_USD_PER_SECOND_768P,
                "approval_policy": APPROVAL_POLICY,
                "run_cap_usd": run_cap_usd, "daily_cap_usd": daily_cap_usd,
                "run_spent_usd": round(run_spent, 6),
            })

            reserve_spend(ledger_path, day, per_cut_estimate, run_id=run_id,
                          cut_index=index, daily_cap_usd=daily_cap_usd)

            try:
                response = client(request)
                path = str((response or {}).get("output_path") or output_path)
                size = _assert_playable_mp4(path)
            except CutContentError as exc:
                release_spend(ledger_path, day, per_cut_estimate,
                              run_id=run_id, cut_index=index)
                _discard(output_path)
                state, failure, last_error = STATE_QA_FAILED, str(exc), exc
                break
            except BaseException as exc:  # provider 예외
                release_spend(ledger_path, day, per_cut_estimate,
                              run_id=run_id, cut_index=index)
                _discard(output_path)
                last_error = exc
                retryable = is_retryable_provider_error(exc)
                append_event(events, {
                    "event": "cut_attempt_failed", "job_id": job_id,
                    "run_id": run_id, "cut_index": index, "attempt": attempt,
                    "retryable": retryable, "error": repr(exc),
                    "endpoint": VIDEO_ENDPOINT,
                    "fallback_taken": False,
                })
                if not retryable:
                    state, failure = STATE_QA_FAILED, str(exc)
                    break
                if attempt >= MAX_CUT_ATTEMPTS:
                    state = STATE_RETRYABLE_FAILED
                    failure = (f"컷 {index}: 인프라 실패가 시도 상한 "
                               f"{MAX_CUT_ATTEMPTS} 회를 소진했다 — {exc}")
                    break
                napper(RETRY_BACKOFF_SECONDS[
                    min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue

            # 성공 — 실비로 정산한다 (추정이 실비를 대신하지 않는다).
            reported = (response or {}).get("cost_usd")
            reported_ok = isinstance(reported, (int, float)) and not isinstance(
                reported, bool)
            actual = float(reported) if reported_ok else per_cut_estimate
            reconcile_spend(ledger_path, day, per_cut_estimate, actual,
                            run_id=run_id, cut_index=index)
            run_spent = round(run_spent + actual, 6)

            lineage.append({
                "job_id": job_id, "run_id": run_id,
                "storyboard_id": storyboard_id, "product_id": product_id,
                "market": market, "cut_index": index,
                "prompt": request["payload"]["prompt"],
                "first_frame_path": request["first_frame_path"],
                "first_frame_sha256": request["first_frame_sha256"],
                "tool": VIDEO_TOOL, "provider": VIDEO_PROVIDER,
                "gateway": VIDEO_GATEWAY, "endpoint": VIDEO_ENDPOINT,
                "model": VIDEO_MODEL, "operation": VIDEO_OPERATION,
                "resolution": VIDEO_RESOLUTION,
                "aspect_ratio": VIDEO_ASPECT_RATIO,
                "duration_seconds": CUT_DURATION_SECONDS,
                "provider_request_id": str(
                    (response or {}).get("request_id") or ""),
                "attempts": attempt,
                "estimated_cost_usd": per_cut_estimate,
                "actual_cost_usd": actual,
                "actual_cost_is_provider_reported": bool(reported_ok),
                "output_path": path,
                "output_sha256": sha256_file(path),
                "output_bytes": size,
                "created_at": _now(),
            })
            break

        if state != STATE_READY_TO_PUBLISH:
            break  # 실패한 잡은 다음 컷으로 넘어가지 않는다 — 돈을 더 태우지 않는다.

    assert_transition(STATE_GENERATING, state)  # 계약 전이표만이 권위다

    manifest: Optional[Dict[str, Any]] = None
    if state == STATE_READY_TO_PUBLISH:
        manifest = vc.GenerationManifest(
            job_id=job_id, run_id=run_id, storyboard_id=storyboard_id,
            product_id=product_id, market=market,
            image_model_alias=MODEL_ALIAS,
            image_hermes_provider=HERMES_PROVIDER,
            image_hermes_model=HERMES_MODEL,
            image_provider_model=PROVIDER_MODEL,
            video_endpoint=VIDEO_ENDPOINT, resolution=VIDEO_RESOLUTION,
            aspect_ratio=VIDEO_ASPECT_RATIO,
            cuts=[vc.CutGeneration(
                index=c["cut_index"], prompt=c["prompt"],
                duration_seconds=c["duration_seconds"],
                provider_request_id=c["provider_request_id"],
                cost_usd=c["actual_cost_usd"], output_path=c["output_path"],
                output_sha256=c["output_sha256"]) for c in lineage],
        ).validate().to_dict()
        atomic_write_json(os.path.join(out_dir, "cuts.json"),
                          {"manifest": manifest, "cut_lineage": lineage})
    else:
        for cut in lineage:  # 부분 산출물을 하류에 남기지 않는다
            _discard(cut["output_path"])

    result = {
        "job_id": job_id, "run_id": run_id, "state": state,
        "cuts_dir": out_dir, "manifest": manifest, "cut_lineage": lineage,
        "attempts": [attempts[i] for i in sorted(attempts)],
        "estimated_cost_usd": job_estimate,
        "actual_cost_usd": round(run_spent, 6),
        "failure": failure,
    }
    append_event(events, {
        "event": "cut_generation_finished", "job_id": job_id, "run_id": run_id,
        "state": state, "cuts": len(lineage), "endpoint": VIDEO_ENDPOINT,
        "model": VIDEO_MODEL, "estimated_cost_usd": job_estimate,
        "actual_cost_usd": round(run_spent, 6), "failure": failure,
    })
    return result


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
