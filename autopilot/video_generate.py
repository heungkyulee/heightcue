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

네트워크·유료 호출은 `bridge=` / `preflight_runner=` 주입 시드로만 들어온다
(`codex_image_bridge` 의 `runner=` 패턴과 동일). 테스트는 절대 실제
이미지를 생성하지 않는다.

의존성 경량 원칙: 표준 라이브러리만. requirements.txt 는 `requests` 하나뿐.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import codex_image_bridge as cib
from video_contracts import (IMAGE_HERMES_MODEL, IMAGE_HERMES_PROVIDER,
                             IMAGE_MODEL_ALIAS, IMAGE_PROVIDER_MODEL, MARKETS,
                             append_event, atomic_write_json)

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


def measure_png(path: str) -> tuple:
    """출력 PNG 의 IHDR 을 **직접** 읽어 (너비, 높이) 를 반환한다.

    디스패처가 에코한 aspect_ratio 는 우리가 보낸 요청의 메아리일 뿐이라
    아무것도 증명하지 못한다. 여기서는 실제 파일 바이트만 본다.
    자립형으로 유지한다 — 다른 모듈의 파서를 import 하지 않는다.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(33)
    except OSError as exc:
        raise PortraitError(f"출력 이미지를 읽을 수 없다: {path} ({exc})") from exc

    if not head.startswith(_PNG_SIG):
        raise PortraitError(
            f"출력이 PNG 가 아니다 (선두 바이트 {head[:8]!r}): {path} — "
            "확장자나 디스패처 응답이 아니라 실제 바이트로 판정한다")
    if len(head) < 24 or head[12:16] != b"IHDR":
        raise PortraitError(f"PNG IHDR 청크를 찾을 수 없다: {path}")

    width, height = struct.unpack(">II", head[16:24])
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
    if "authorized" not in provider_line.lower():
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
    if not cfg:
        # JSON 이 아니면 키:값 형태를 보수적으로 훑는다.
        for line in raw.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().strip('"').lower()
                value = value.strip().strip(',').strip('"')
                if key == "provider":
                    provider = value
                elif key == "model":
                    model = value

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

            bridge_manifest = dispatch(prompt, [source["path"]], output_path)
            written.append(output_path)

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
