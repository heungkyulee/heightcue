#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V 파이프라인 — 컷 합성 (Remotion **전용**).

생성된 5초 컷들을 최종 세로 MP4 하나로 합친다. 이 모듈이 강제하는 것:

* **Remotion 외의 런타임은 존재하지 않는다.** `edit_decisions.render_runtime`
  가 `remotion` 이 아니거나, Remotion 이 프리플라이트에서 확인되지 않거나,
  렌더러가 다른 런타임을 보고하면 **크게 실패한다.** HyperFrames·순수
  FFmpeg·MoviePy 로 조용히 갈아타는 코드 경로는 이 파일에 **없다**
  (`test_video_compose.TestRemotionOnly` 가 이름 수준에서도 검사한다).
  OpenMontage AGENT_GUIDE 는 무언의 런타임 스왑을 CRITICAL 거버넌스 위반으로
  규정한다 — 여기서도 같은 기준이다.
* **제휴 고지는 렌더된 영상까지 살아남아야 한다.** 스토리보드가 지정한
  시장별 불변 문구(KR 쿠팡 파트너스 / US Amazon Associates)를 그대로,
  영상 전 구간에, 가독 스타일로 얹는다. 렌더 결과에 고지가 없으면 그
  산출물은 **폐기**된다 — 발행으로 넘기지 않는다 (SSOT 불변 규칙 2).
* **카피는 승인본 그대로.** 캡션은 스토리보드 `voice_line` 을 축자 인용하며
  합성 단계에서 다시 쓰거나 요약하거나 바꾸지 않는다. 렌더된 텍스트 레이어가
  승인 집합과 조금이라도 다르면 실패한다.
* **길이·형식은 실제 파일에서 잰다.** 렌더러가 뭐라고 보고하든 믿지 않고
  출력 mp4 의 moov/mvhd/tkhd/stsd 박스를 직접 읽는다. 이 계열의 앞선 두
  모듈이 '선언된 헤더를 신뢰'해서 버그를 냈다 — 여기서는 매니페스트에도
  `measured_*` 로 이름 붙여, 관측값과 선언값을 섞지 않는다.

렌더는 `renderer=` / `runtime_probe=` 주입 시임으로만 일어난다 (집안 패턴).
테스트는 `npx remotion render` 를 절대 실행하지 않는다.

의존성 경량 원칙: 표준 라이브러리만.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from video_contracts import (ALLOWED_TOTAL_DURATIONS, CUT_DURATION_SECONDS,
                             MARKETS, VIDEO_ASPECT_RATIO, VIDEO_RESOLUTION,
                             ContractError, append_event, atomic_write_json)
from video_storyboard import DISCLOSURE_TEXT

# ---------------------------------------------------------------------------
# 고정 계약 — pipeline_defs/heightcue-ugc.yaml 의 잠긴 metadata 와 1:1
# ---------------------------------------------------------------------------

#: 유일하게 허용된 렌더 런타임. 튜플이지만 원소는 영원히 하나다.
RENDER_RUNTIME = "remotion"
ALLOWED_RENDER_RUNTIMES = (RENDER_RUNTIME,)

#: 잠긴 합성 모드 — 스톡 씬 템플릿은 금지, 손으로 짠 컴포지션만.
COMPOSITION_MODE = "atelier"

#: Remotion 컴포지션 ID (remotion-composer/src/Root.tsx 등록 이름).
COMPOSITION_ID = "HeightCueUgcShort"

#: 768P-class 세로 프레임. 짧은 변이 768 이어야 '768P' 다.
COMPOSITION_SHORT_SIDE = 768
COMPOSITION_WIDTH = 768
COMPOSITION_HEIGHT = 1360
#: 9:16 허용 오차 — 768 은 16/9 로 정수 높이가 나오지 않는다(1365.33).
ASPECT_TOLERANCE = 0.01

FPS = 30

#: 실측 길이 허용 오차 (초). 인코더 반올림만 흡수한다.
DURATION_TOLERANCE_SECONDS = 0.15

#: 허용 코덱 — H.264 비디오 / AAC 오디오.
ALLOWED_VIDEO_FOURCC = ("avc1", "avc3", "h264")
ALLOWED_AUDIO_FOURCC = ("mp4a",)

#: 고지 가독성 하한. 이보다 작으면 '있긴 한데 안 보이는' 고지다.
MIN_DISCLOSURE_FONT_PX = 28
MIN_SAFE_AREA_MARGIN_PX = 48

#: 캡션의 유일한 출처. 합성 단계에서 다시 쓰지 않는다.
CAPTION_SOURCE = "storyboard.cuts[].voice_line"

PROBE_TIMEOUT = 60


# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class ComposeError(ContractError):
    """합성 계약 위반 공통 베이스."""


class RuntimeSwapError(ComposeError):
    """Remotion 이 아닌 런타임/합성 모드가 요구되거나 보고됐다 — 대체 금지."""


class RuntimeUnavailableError(ComposeError):
    """Remotion 을 확인할 수 없다 — 다른 런타임으로 내려가지 않고 중단한다."""


class DisclosureError(ComposeError):
    """제휴 고지가 없거나, 변형됐거나, 렌더 결과에서 사라졌다."""


class CaptionDriftError(ComposeError):
    """렌더된 텍스트가 승인된 스토리보드 카피와 다르다."""


class ComposeDurationError(ComposeError):
    """컷 수 또는 실측 길이가 5/10/15초 계약을 벗어났다."""


class ComposeFormatError(ComposeError):
    """실측 해상도·화면비·코덱·컨테이너가 계약을 벗어났다."""


class ComposeLineageError(ComposeError):
    """입력 컷의 해시·순서·식별자 계보가 끊겼다."""


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


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 실측 — 출력 mp4 의 박스를 직접 읽는다 (렌더러 보고는 증거가 아니다)
# ---------------------------------------------------------------------------

_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


def _iter_boxes(data: bytes, start: int, end: int):
    offset = start
    while offset + 8 <= end:
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        if size == 0:
            size = end - offset
        if size < 8 or offset + size > end:
            raise ComposeFormatError(
                f"mp4 박스 {tag!r} 가 파일 끝을 넘어간다 (선언 크기 {size}) — "
                "잘린 산출물을 완성본으로 받아들이지 않는다")
        yield tag, offset + 8, offset + size
        offset += size


def _collect(data: bytes, start: int, end: int, found: Dict[str, Any],
             trak: Optional[Dict[str, Any]] = None) -> None:
    for tag, body, stop in _iter_boxes(data, start, end):
        if tag == b"mvhd":
            version = data[body]
            base = body + 4 + (16 if version == 1 else 8)
            if version == 1:
                timescale = struct.unpack(">I", data[base:base + 4])[0]
                duration = struct.unpack(">Q", data[base + 4:base + 12])[0]
            else:
                timescale = struct.unpack(">I", data[base:base + 4])[0]
                duration = struct.unpack(">I", data[base + 4:base + 8])[0]
            found["timescale"] = timescale
            found["duration_units"] = duration
        elif tag == b"trak":
            current: Dict[str, Any] = {}
            found.setdefault("traks", []).append(current)
            _collect(data, body, stop, found, current)
        elif tag == b"tkhd" and trak is not None:
            version = data[body]
            width_off = body + (96 if version == 1 else 84) - 8
            trak["width"] = struct.unpack(
                ">I", data[width_off:width_off + 4])[0] >> 16
            trak["height"] = struct.unpack(
                ">I", data[width_off + 4:width_off + 8])[0] >> 16
        elif tag == b"hdlr" and trak is not None:
            trak["handler"] = data[body + 8:body + 12].decode("latin-1")
        elif tag == b"stsd" and trak is not None:
            entry = body + 8
            if entry + 8 <= stop:
                trak["fourcc"] = data[entry + 4:entry + 8].decode(
                    "latin-1").strip()
        elif tag in _CONTAINER_BOXES:
            _collect(data, body, stop, found, trak)


def measure_mp4(path: str) -> Dict[str, Any]:
    """출력 mp4 를 **바이트로 직접 재서** 길이·크기·코덱을 돌려준다.

    렌더러가 에코한 값은 우리가 보낸 요청의 메아리일 뿐 아무것도 증명하지
    못한다. 여기서 나온 값만 매니페스트에 ``measured_*`` 로 기록된다.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise ComposeFormatError(f"출력 영상을 읽을 수 없다: {path} ({exc})") from exc

    if len(data) < 16 or data[4:8] != b"ftyp":
        raise ComposeFormatError(
            f"출력이 mp4 컨테이너가 아니다 (선두 {data[:16]!r}): {path} — "
            "확장자나 렌더러 보고가 아니라 실제 바이트로 판정한다")

    found: Dict[str, Any] = {}
    _collect(data, 0, len(data), found)

    if "timescale" not in found:
        raise ComposeFormatError(f"mp4 에 moov/mvhd 가 없다: {path}")
    timescale = int(found["timescale"]) or 1
    duration = float(found["duration_units"]) / timescale
    if duration <= 0:
        raise ComposeFormatError(f"실측 길이가 0 이다: {path} — 빈 렌더다")

    traks = found.get("traks") or []
    video = next((t for t in traks if t.get("handler") == "vide"), None)
    audio = next((t for t in traks if t.get("handler") == "soun"), None)
    if video is None:
        raise ComposeFormatError(f"mp4 에 비디오 트랙이 없다: {path}")

    return {
        "duration_seconds": round(duration, 6),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec_fourcc": video.get("fourcc") or "",
        "audio_codec_fourcc": (audio or {}).get("fourcc") or "",
        "bytes": len(data),
        "measured_by": "video_compose.measure_mp4 (moov 박스 직접 판독)",
    }


def assert_measured_output(path: str, expected_seconds: int) -> Dict[str, Any]:
    """실측값이 9:16 · 768P-class 세로 · H.264/AAC · 정확한 길이인지 강제한다."""
    m = measure_mp4(path)

    width, height = m["width"], m["height"]
    if height <= width:
        raise ComposeFormatError(
            f"실측 {width}x{height} 는 세로가 아니다: {path} — 세로 숏폼만 발행한다")
    if min(width, height) != COMPOSITION_SHORT_SIDE:
        raise ComposeFormatError(
            f"실측 짧은 변 {min(width, height)} 가 768P-class "
            f"({COMPOSITION_SHORT_SIDE}) 가 아니다: {width}x{height} ({path})")
    ratio = width / height
    if abs(ratio - 9 / 16) > ASPECT_TOLERANCE:
        raise ComposeFormatError(
            f"실측 화면비 {ratio:.4f} ({width}x{height}) 가 "
            f"{VIDEO_ASPECT_RATIO} (0.5625) 와 다르다: {path}")

    if m["video_codec_fourcc"].lower() not in ALLOWED_VIDEO_FOURCC:
        raise ComposeFormatError(
            f"비디오 코덱이 H.264 가 아니다: {m['video_codec_fourcc']!r} ({path})")
    if not m["audio_codec_fourcc"]:
        raise ComposeFormatError(
            f"오디오 트랙이 없다: {path} — 무음 산출물은 발행하지 않는다")
    if m["audio_codec_fourcc"].lower() not in ALLOWED_AUDIO_FOURCC:
        raise ComposeFormatError(
            f"오디오 코덱이 AAC 가 아니다: {m['audio_codec_fourcc']!r} ({path})")

    if abs(m["duration_seconds"] - expected_seconds) > DURATION_TOLERANCE_SECONDS:
        raise ComposeDurationError(
            f"실측 길이 {m['duration_seconds']}초가 계획 {expected_seconds}초와 "
            f"다르다 (허용 오차 {DURATION_TOLERANCE_SECONDS}초): {path}")
    if expected_seconds not in ALLOWED_TOTAL_DURATIONS:
        raise ComposeDurationError(
            f"총 길이는 {ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다: {expected_seconds}")
    return m


# ---------------------------------------------------------------------------
# 런타임 프리플라이트 — 확인 실패는 곧 중단이다 (대체 없음)
# ---------------------------------------------------------------------------


def _subprocess_probe() -> Dict[str, Any]:
    """프로덕션 기본 프로브: `npx remotion versions`. 렌더는 하지 않는다."""
    try:
        proc = subprocess.run(["npx", "remotion", "versions"],
                              capture_output=True, text=True,
                              timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeUnavailableError(
            f"Remotion 프로브 실행 실패: {exc!r} — 다른 런타임으로 대체하지 않는다"
        ) from exc
    if proc.returncode != 0:
        return {"available": False, "version": "",
                "detail": (proc.stderr or "")[-500:]}
    version = ""
    for line in (proc.stdout or "").splitlines():
        for token in line.replace(":", " ").split():
            if token and token[0].isdigit() and "." in token:
                version = token
                break
        if version:
            break
    return {"available": True, "version": version, "detail": ""}


def assert_remotion_available(probe: Optional[Callable] = None) -> Dict[str, Any]:
    """Remotion 이 실제로 쓸 수 있는지 확인한다. 아니면 RuntimeUnavailableError.

    **폴백은 없다.** 이 함수가 던지면 잡은 여기서 끝난다.
    """
    result = (probe or _subprocess_probe)()
    if not isinstance(result, dict):
        raise RuntimeUnavailableError(
            f"런타임 프로브가 dict 가 아닌 {type(result)} 를 반환했다")
    if not result.get("available"):
        raise RuntimeUnavailableError(
            "Remotion 을 사용할 수 없다 "
            f"({str(result.get('detail') or '')[:300]!r}) — 이 파이프라인은 "
            "remotion 으로만 렌더한다. HyperFrames·FFmpeg 로 대체하지 않고 "
            "잡을 실패시킨다 (무언의 런타임 스왑은 거버넌스 위반이다)")
    version = str(result.get("version") or "").strip()
    if not version:
        raise RuntimeUnavailableError(
            "Remotion 버전을 확인할 수 없다 — 버전 없는 런타임은 계보에 "
            "기록할 수 없으므로 렌더하지 않는다")
    return {"runtime": RENDER_RUNTIME, "version": version,
            "checked_at": _now()}


def assert_runtime_lock(edit_decisions: Any) -> Dict[str, Any]:
    """`edit_decisions` 의 잠긴 값들을 **지출·렌더 전에** 대조한다."""
    if not isinstance(edit_decisions, dict):
        raise RuntimeSwapError(
            f"edit_decisions 는 dict 여야 한다: {type(edit_decisions)}")

    runtime = edit_decisions.get("render_runtime")
    if runtime not in ALLOWED_RENDER_RUNTIMES:
        raise RuntimeSwapError(
            f"edit_decisions.render_runtime 이 {runtime!r} 다 — 허용되는 값은 "
            f"{ALLOWED_RENDER_RUNTIMES} 뿐이다. 파이프라인 매니페스트가 잠근 "
            "값이며, 다른 런타임으로 내려가는 경로는 존재하지 않는다")

    mode = edit_decisions.get("composition_mode")
    if mode != COMPOSITION_MODE:
        raise RuntimeSwapError(
            f"composition_mode 가 {mode!r} 다 — {COMPOSITION_MODE!r} 고정 "
            "(스톡 씬 템플릿 금지)")

    aspect = edit_decisions.get("aspect_ratio", VIDEO_ASPECT_RATIO)
    if aspect != VIDEO_ASPECT_RATIO:
        raise RuntimeSwapError(
            f"aspect_ratio 가 {aspect!r} 다 — {VIDEO_ASPECT_RATIO} 고정")
    resolution = edit_decisions.get("resolution", VIDEO_RESOLUTION)
    if resolution != VIDEO_RESOLUTION:
        raise RuntimeSwapError(
            f"resolution 이 {resolution!r} 다 — {VIDEO_RESOLUTION} 고정")

    return {"render_runtime": RENDER_RUNTIME,
            "composition_mode": COMPOSITION_MODE,
            "aspect_ratio": VIDEO_ASPECT_RATIO,
            "resolution": VIDEO_RESOLUTION}


# ---------------------------------------------------------------------------
# 고지 · 캡션 — 승인본에서만 나온다
# ---------------------------------------------------------------------------


def extract_disclosure(storyboard: Dict[str, Any], market: str) -> str:
    """스토리보드가 들고 온 고지 문구를 꺼낸다. 없거나 변형됐으면 실패."""
    block = storyboard.get("disclosure")
    if not isinstance(block, dict) or not block:
        raise DisclosureError(
            "스토리보드에 disclosure 블록이 없다 — 고지 의무 없는 합성은 "
            "금지다 (SSOT 불변 규칙 2)")
    text = str(block.get("text") or "").strip()
    if not text:
        raise DisclosureError("disclosure.text 가 비어 있다 — 빈 고지는 고지가 아니다")
    expected = DISCLOSURE_TEXT.get(market)
    if text != expected:
        raise DisclosureError(
            f"고지 문구가 {market} 불변 문구와 다르다: {text!r} != {expected!r} — "
            "고지는 요약·의역·재작성할 수 없다")
    return text


def extract_captions(storyboard: Dict[str, Any]) -> List[str]:
    """승인된 컷 카피(`voice_line`)를 **축자** 회수한다. 재작성 금지."""
    captions: List[str] = []
    for cut in storyboard.get("cuts") or []:
        text = str((cut or {}).get("voice_line") or "").strip()
        if not text:
            raise CaptionDriftError(
                f"컷 {(cut or {}).get('index')!r} 의 voice_line 이 비어 있다 — "
                "합성 단계에서 카피를 지어내지 않는다")
        captions.append(text)
    return captions


def build_overlay_plan(*, captions: List[str], cta_text: str,
                       disclosure_text: str,
                       total_seconds: int) -> Dict[str, Any]:
    """결정론적 오버레이 계획. 텍스트는 전부 승인본에서 그대로 온다."""
    cta = str(cta_text or "").strip()
    if not cta:
        raise CaptionDriftError("cta_text 가 비어 있다 — CTA 없는 발행본은 만들지 않는다")

    layers: List[Dict[str, Any]] = []
    for i, text in enumerate(captions):
        layers.append({
            "role": "caption",
            "cut_index": i + 1,
            "text": text,
            "verbatim_from": CAPTION_SOURCE,
            "start_seconds": i * CUT_DURATION_SECONDS,
            "end_seconds": (i + 1) * CUT_DURATION_SECONDS,
            "style": {"font_size_px": 44, "safe_area_margin_px": 96,
                      "background_scrim": True, "position": "lower_third"},
        })
    layers.append({
        "role": "cta",
        "cut_index": len(captions),
        "text": cta,
        "verbatim_from": "caller.cta_text",
        "start_seconds": max(0, total_seconds - CUT_DURATION_SECONDS),
        "end_seconds": total_seconds,
        "style": {"font_size_px": 40, "safe_area_margin_px": 96,
                  "background_scrim": True, "position": "center"},
    })
    disclosure = {
        "role": "disclosure",
        "text": disclosure_text,
        "required": True,
        # 고지는 영상 전 구간을 덮는다 — 스크롤로 지나쳐도 보이도록.
        "start_seconds": 0,
        "end_seconds": total_seconds,
        "style": {"font_size_px": MIN_DISCLOSURE_FONT_PX,
                  "safe_area_margin_px": MIN_SAFE_AREA_MARGIN_PX,
                  "background_scrim": True, "position": "top",
                  "opacity": 1.0},
    }
    layers.append(dict(disclosure))
    return {"text_layers": layers, "disclosure": disclosure,
            "rendered_by": RENDER_RUNTIME,
            "note": "텍스트는 영상 모델이 아니라 Remotion 이 결정론적으로 렌더한다"}


def assert_rendered_text(rendered: Any, *, approved: List[str],
                         disclosure_text: str) -> List[str]:
    """렌더러가 실제로 얹은 텍스트가 승인 집합과 **정확히** 같은지 본다."""
    if not isinstance(rendered, (list, tuple)):
        raise CaptionDriftError(
            f"렌더 결과에 text_layers 가 없다 ({type(rendered)}) — 오버레이가 "
            "실제로 얹혔는지 확인할 수 없으면 발행하지 않는다")
    actual = [str(t).strip() for t in rendered]

    if disclosure_text not in actual:
        raise DisclosureError(
            "렌더된 영상에 제휴 고지가 없다 — 고지 없이 렌더된 합성본은 "
            "폐기한다 (발행 금지, SSOT 불변 규칙 2)")

    missing = [t for t in approved if t not in actual]
    if missing:
        raise CaptionDriftError(
            f"승인된 카피가 렌더 결과에 없다: {missing!r} — 승인본과 화면이 "
            "달라선 안 된다")
    allowed = set(approved) | {disclosure_text}
    extra = sorted({t for t in actual if t not in allowed})
    if extra:
        raise CaptionDriftError(
            f"승인되지 않은 텍스트가 렌더됐다: {extra!r} — 합성 단계에서 "
            "카피를 추가·재작성하지 않는다")
    return actual


# ---------------------------------------------------------------------------
# 입력 컷 계보
# ---------------------------------------------------------------------------


def verify_input_cuts(cut_lineage: Any, expected_count: int) -> List[Dict[str, Any]]:
    """입력 컷을 디스크에서 **재해시**해 계보를 확인한다."""
    if not isinstance(cut_lineage, (list, tuple)) or not cut_lineage:
        raise ComposeLineageError("합성할 컷이 없다 — 입력 컷 계보가 비어 있다")
    if len(cut_lineage) != expected_count:
        raise ComposeLineageError(
            f"입력 컷 {len(cut_lineage)} 개 != 스토리보드 컷 {expected_count} 개 — "
            "1:1 이어야 한다")

    verified: List[Dict[str, Any]] = []
    for position, cut in enumerate(cut_lineage, start=1):
        if not isinstance(cut, dict):
            raise ComposeLineageError(f"cut_lineage[{position}] 는 dict 여야 한다")
        index = int(cut.get("cut_index") or 0)
        if index != position:
            raise ComposeLineageError(
                f"컷 순서가 어긋났다: 위치 {position} 의 cut_index 가 {index} — "
                "컷은 1부터 연속이어야 하고 순서대로 이어붙는다")
        path = str(cut.get("output_path") or "")
        if not path or not os.path.isfile(path):
            raise ComposeLineageError(f"컷 {index} 파일이 없다: {path!r}")
        declared = str(cut.get("output_sha256") or "")
        if not declared:
            raise ComposeLineageError(f"컷 {index} 에 sha256 이 없다")
        actual = sha256_file(path)
        if actual != declared:
            raise ComposeLineageError(
                f"컷 {index} 해시가 계보와 다르다: {actual} != {declared} "
                f"({path}) — 생성 단계가 검증한 그 바이트가 아니다")
        duration = int(cut.get("duration_seconds") or CUT_DURATION_SECONDS)
        if duration != CUT_DURATION_SECONDS:
            raise ComposeDurationError(
                f"컷 {index} 길이가 {duration}초다 — {CUT_DURATION_SECONDS}초 고정")
        verified.append({"cut_index": index, "output_path": path,
                         "output_sha256": actual,
                         "duration_seconds": duration,
                         "output_bytes": os.path.getsize(path)})
    return verified


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def compose_video(*, storyboard: Dict[str, Any], cut_lineage: Any,
                  edit_decisions: Dict[str, Any], job_id: str,
                  output_path: str, cta_text: str,
                  renderer: Callable,
                  runtime_probe: Optional[Callable] = None,
                  ) -> Dict[str, Any]:
    """컷들을 최종 세로 MP4 하나로 합친다 — Remotion 으로만.

    ``renderer`` 는 주입 시임이다. 요청 dict 를 받아
    ``{"output_path", "runtime", "runtime_version", "text_layers"}`` 를
    돌려주고 실패하면 예외를 던진다. 프로덕션은 OpenMontage
    ``video_compose`` 도구의 ``remotion_render`` 연산을 감싼다.

    거부는 전부 렌더 **전에** 일어난다 (계보 → 런타임 락 → 길이 → 고지 →
    카피 → 입력 해시 → 프리플라이트). 렌더 후 검증에 실패하면 산출물을
    폐기한다 — 반쪽 결과가 발행 단계로 흘러가지 않는다.
    """
    if not isinstance(storyboard, dict):
        raise ComposeLineageError(
            f"storyboard 는 dict 여야 한다: {type(storyboard)}")

    run_id = str(storyboard.get("run_id") or "").strip()
    storyboard_id = str(storyboard.get("storyboard_id") or "").strip()
    product_id = str(storyboard.get("product_id") or "").strip()
    market = str(storyboard.get("market") or "").strip()
    content_draft_id = str(storyboard.get("content_draft_id") or "").strip()
    job = str(job_id or "").strip()

    # 1) 계보 식별자 — 없으면 아무것도 하지 않는다.
    for name, value in (("job_id", job), ("run_id", run_id),
                        ("storyboard_id", storyboard_id),
                        ("product_id", product_id)):
        if not value:
            raise ComposeLineageError(f"{name} 가 비어 있다 — 계보 없는 합성 금지")
    if market not in MARKETS:
        raise ComposeLineageError(f"market 는 {MARKETS} 중 하나여야 한다: {market!r}")
    if not str(output_path or "").strip():
        raise ComposeLineageError("output_path 가 비어 있다")

    # 2) 런타임 락 — 프리플라이트보다 먼저. 잠긴 값 위반은 즉시 거부한다.
    settings = assert_runtime_lock(edit_decisions)

    # 3) 길이 계약 (컷 수 × 5초).
    cuts = list(storyboard.get("cuts") or [])
    expected_seconds = len(cuts) * CUT_DURATION_SECONDS
    if expected_seconds not in ALLOWED_TOTAL_DURATIONS:
        raise ComposeDurationError(
            f"컷 {len(cuts)} 개 = {expected_seconds}초 — 총 길이는 "
            f"{ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다")

    # 4) 고지 — 렌더 전에 존재·불변 문구 확인.
    disclosure_text = extract_disclosure(storyboard, market)

    # 5) 승인 카피 — 축자 회수 + CTA.
    captions = extract_captions(storyboard)
    overlay_plan = build_overlay_plan(captions=captions, cta_text=cta_text,
                                      disclosure_text=disclosure_text,
                                      total_seconds=expected_seconds)
    approved_texts = [layer["text"] for layer in overlay_plan["text_layers"]
                      if layer["role"] != "disclosure"]

    # 6) 입력 컷 재해시.
    inputs = verify_input_cuts(cut_lineage, len(cuts))

    # 7) Remotion 프리플라이트. 실패하면 여기서 끝 — 대체 경로는 없다.
    runtime = assert_remotion_available(runtime_probe)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    events = os.path.join(out_dir, "compose_events.jsonl")

    props = {
        "job_id": job, "run_id": run_id, "storyboard_id": storyboard_id,
        "product_id": product_id, "market": market,
        "composition_id": COMPOSITION_ID,
        "width": COMPOSITION_WIDTH, "height": COMPOSITION_HEIGHT, "fps": FPS,
        "duration_seconds": expected_seconds,
        "clips": [{"cut_index": c["cut_index"], "src": c["output_path"],
                   "sha256": c["output_sha256"],
                   "duration_seconds": c["duration_seconds"]}
                  for c in inputs],
        "captions": list(captions),
        "caption_source": CAPTION_SOURCE,
        "cta": str(cta_text).strip(),
        "disclosure": dict(overlay_plan["disclosure"]),
        "overlay_plan": overlay_plan,
    }
    props_path = os.path.join(out_dir, f"{job}_remotion_props.json")
    atomic_write_json(props_path, props)

    request = {
        "operation": "remotion_render",
        "render_runtime": RENDER_RUNTIME,
        "composition_mode": COMPOSITION_MODE,
        "composition_id": COMPOSITION_ID,
        "props_path": props_path,
        "props": props,
        "input_cuts": [dict(c) for c in inputs],
        "overlay_plan": overlay_plan,
        "output_path": output_path,
        "width": COMPOSITION_WIDTH, "height": COMPOSITION_HEIGHT,
        "fps": FPS, "duration_seconds": expected_seconds,
        "video_codec": "h264", "audio_codec": "aac",
        "aspect_ratio": VIDEO_ASPECT_RATIO, "resolution": VIDEO_RESOLUTION,
    }

    append_event(events, {
        "event": "compose_started", "job_id": job, "run_id": run_id,
        "render_runtime": RENDER_RUNTIME, "runtime_version": runtime["version"],
        "composition_mode": COMPOSITION_MODE, "composition_id": COMPOSITION_ID,
        "cuts": len(inputs), "expected_duration_seconds": expected_seconds,
        "disclosure_required": True, "fallback_runtime_available": False,
    })

    try:
        response = renderer(request)
        if not isinstance(response, dict):
            raise ComposeError(
                f"렌더러가 dict 가 아닌 {type(response)} 를 반환했다")

        # 8) 렌더러가 **정말로** Remotion 이었는지. 스왑은 여기서도 막힌다.
        reported = response.get("runtime")
        if reported not in ALLOWED_RENDER_RUNTIMES:
            raise RuntimeSwapError(
                f"렌더러가 런타임 {reported!r} 로 렌더했다고 보고했다 — "
                f"{RENDER_RUNTIME!r} 이어야 한다. 무언의 런타임 스왑은 "
                "CRITICAL 거버넌스 위반이며 산출물을 폐기한다")

        rendered_path = str(response.get("output_path") or output_path)
        if not os.path.isfile(rendered_path):
            raise ComposeFormatError(f"렌더 산출물이 없다: {rendered_path}")

        # 9) 고지·카피가 실제로 화면에 남았는지.
        rendered_texts = assert_rendered_text(
            response.get("text_layers"), approved=approved_texts,
            disclosure_text=disclosure_text)

        # 10) 길이·크기·코덱을 **파일에서** 잰다.
        measured = assert_measured_output(rendered_path, expected_seconds)
    except BaseException:
        _discard(output_path)
        append_event(events, {
            "event": "compose_rejected", "job_id": job, "run_id": run_id,
            "render_runtime": RENDER_RUNTIME, "output_discarded": True,
            "fallback_taken": False,
        })
        raise

    result = {
        "job_id": job, "run_id": run_id, "storyboard_id": storyboard_id,
        "product_id": product_id, "market": market,
        "content_draft_id": content_draft_id,
        "render_runtime": RENDER_RUNTIME,
        "runtime_version": runtime["version"],
        "runtime_checked_at": runtime["checked_at"],
        "composition_mode": COMPOSITION_MODE,
        "composition_id": COMPOSITION_ID,
        "render_settings": dict(settings, width=COMPOSITION_WIDTH,
                                height=COMPOSITION_HEIGHT, fps=FPS,
                                video_codec="h264", audio_codec="aac",
                                composition_id=COMPOSITION_ID),
        "input_cuts": inputs,
        "input_cut_sha256": [c["output_sha256"] for c in inputs],
        "props_path": props_path,
        "output_path": rendered_path,
        "output_sha256": sha256_file(rendered_path),
        "output_bytes": measured["bytes"],
        "expected_duration_seconds": expected_seconds,
        "measured_duration_seconds": measured["duration_seconds"],
        "measured_width": measured["width"],
        "measured_height": measured["height"],
        "measured_by": measured["measured_by"],
        "video_codec_fourcc": measured["video_codec_fourcc"],
        "audio_codec_fourcc": measured["audio_codec_fourcc"],
        "captions": list(captions),
        "caption_source": CAPTION_SOURCE,
        "rendered_text_layers": rendered_texts,
        "disclosure_included": True,
        "disclosure_text": disclosure_text,
        "overlay_plan": overlay_plan,
        "created_at": _now(),
    }
    atomic_write_json(os.path.join(out_dir, f"{job}_compose_manifest.json"),
                      result)
    append_event(events, {
        "event": "compose_finished", "job_id": job, "run_id": run_id,
        "render_runtime": RENDER_RUNTIME, "runtime_version": runtime["version"],
        "output_sha256": result["output_sha256"],
        "measured_duration_seconds": measured["duration_seconds"],
        "measured_size": f"{measured['width']}x{measured['height']}",
        "disclosure_included": True, "fallback_taken": False,
    })
    return result
