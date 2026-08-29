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
#: 768x1360 세로 프레임 기준 22px = 프레임 폭의 2.9%. 6.1" 폰(논리폭 약
#: 390pt)에서 약 11pt 로 렌더되며, 이는 iOS Caption2(11pt)와 같은 급이다 —
#: 작지만 확실히 읽힌다.
MIN_DISCLOSURE_FONT_PX = 22
#: 고지 **상한**. 운영자 검수(2026-08-29): "고지를 좀 더 작게, 더 눈에 안
#: 띄게". 이보다 크면 고지가 타이틀 카드처럼 프레임을 지배한다.
MAX_DISCLOSURE_FONT_PX = 26
#: 실제 사용 크기. 하한에 붙여 최대한 조용하게 두되 하한 아래로는 절대 안 간다.
DISCLOSURE_FONT_SIZE_PX = MIN_DISCLOSURE_FONT_PX
#: 굵기 상한 — 700(bold)은 법적 문구를 헤드라인처럼 보이게 한다. 500(medium)은
#: 대비를 유지하면서도 시선을 끌지 않는다.
DISCLOSURE_FONT_WEIGHT = 500
#: 스크림 불투명도. 0.62 짜리 검은 띠는 화면 상단을 잘라먹었다. 0.32 는 밝은
#: 영상 위에서도 흰 글자 대비를 확보하면서 배경을 지우지 않는다. 스크림 자체를
#: 없애면 흰 배경 컷에서 고지가 사라지므로 제거는 금지다.
DISCLOSURE_SCRIM_OPACITY = 0.32
MIN_SAFE_AREA_MARGIN_PX = 48

# ---------------------------------------------------------------------------
# 한국어 줄바꿈 — 레이아웃에서만 일어난다
#
# 운영자 검수(2026-08-29): "줄바꿈도 어색한 느낌이야."
# 실제 프레임에서 CTA 가 "프로필 링크에서 성분표 / 확인하세요" 로,
# 캡션이 "... 하루 한 번이면 / 끝" 으로 끊겼다 — 어절 중간 분리와 고아 음절.
#
# 브라우저 표준 해법 두 가지를 함께 건다.
#   word-break: keep-all  — CJK 를 음절 단위가 아니라 **어절(공백) 단위**로
#                           끊는다. 조사가 명사에서 떨어지지 않는다.
#   text-wrap: balance    — 마지막 줄에 한두 음절만 남기지 않고 줄 길이를
#                           고르게 맞춘다 (Chromium 114+, Remotion 4.0.484 의
#                           헤드리스 Chrome 이 지원).
#
# **문자열은 건드리지 않는다.** 개행문자 삽입·하이픈·말줄임·재배열 금지 —
# `video_qa.py` 가 승인 voice_line 과 축자 비교한다.
# ---------------------------------------------------------------------------

#: CJK 어절 보존. 음절 중간 분리를 막는다.
TEXT_WORD_BREAK = "keep-all"
#: 균형 줄바꿈. 고아 음절(마지막 줄 1~2자)을 막는다.
TEXT_WRAP_STYLE = "balance"

#: 모든 텍스트 레이어가 공유하는 한국어 줄바꿈 규칙. 순수 시각 속성이며
#: 텍스트 내용에는 절대 영향을 주지 않는다.
KOREAN_WRAP_STYLE = {
    "word_break": TEXT_WORD_BREAK,
    "text_wrap": TEXT_WRAP_STYLE,
}

#: 캡션의 유일한 출처. 합성 단계에서 다시 쓰지 않는다.
CAPTION_SOURCE = "storyboard.cuts[].voice_line"

#: CTA 의 유일한 출처. 호출자가 넘기는 자유 문자열이 아니다 — 캡션과 똑같이
#: 승인된 스토리보드에서만 온다. 자유 입력이면 CaptionDriftError 가 영원히
#: 발동하지 않는 사각지대가 생긴다 (승인 집합에 스스로를 넣어버리므로).
CTA_SOURCE = "storyboard.cta.text"

# ---------------------------------------------------------------------------
# 2단 분리 — 클린 마스터 / 자막 패스
#
# 운영자 지시(2026-08-29): "기본 영상 에셋 자체는 자막을 넣지 말도록 해.
# 자막은 opencut 같은 걸로 후보정해서 넣는 게 맞지 않겠어?"
#
# 첫 유료 렌더에서 클로즈업 컷의 캡션이 제품 라벨 위에 앉아 못 쓰게 됐다.
# 그래서 합성은 두 단계로 쪼갠다.
#
#   STAGE_MASTER    — 컷 이어붙이기 + 모델 원음 유지 + **제휴 고지만** 번인.
#                     voice_line 캡션도 CTA 도 굽지 않는다. 여기에 사이드카
#                     자막 파일(SRT)이 함께 나온다.
#   STAGE_SUBTITLED — 마스터를 입력으로 받아 캡션 + CTA 를 얹은 **별개**
#                     산출물. 마스터는 그대로 남는다.
#
# 고지는 마스터에서 빠지지 않는다. 법적 의무이며 캡션과 운명을 같이하지
# 않는다 (SSOT 불변 규칙 2).
# ---------------------------------------------------------------------------

STAGE_MASTER = "clean_master"
STAGE_SUBTITLED = "subtitled_deliverable"

#: 사이드카 자막 형식. SRT 는 OpenCut·DaVinci·Premiere·ffmpeg 가 전부 읽는
#: 최소 공통분모다. 승인 카피를 **바이트 그대로** 담으며 재줄바꿈·말줄임·
#: 대소문자 변경을 하지 않는다.
SUBTITLE_SIDECAR_FORMAT = "srt"

#: 스테이징 하위 경로 — Remotion `public/` 기준. 첫 유료 실행에서
#: `OffthreadVideo` 가 절대경로 `file://` 를 거부해 운영자가 손으로 클립을
#: public/ 에 복사해야 했다. 이제 자동으로 스테이징하고, 복사본을 **다시
#: 해시**해 계보가 끊기지 않았는지 확인한다.
STAGED_CLIP_SUBDIR = "heightcue-staged"

#: 고지가 **픽셀에** 남았는지 확인하는 단계의 이름. 지금은 오프라인이라
#: 프레임을 샘플링하거나 OCR 할 수 없으므로, 이 파이프라인은 고지 생존을
#: '렌더러 보고'로만 안다. 실제 게이팅된 렌더 태스크에서 프레임 샘플/OCR
#: 또는 Remotion 렌더타임 assertion 을 이 이름으로 붙인다. 그때까지
#: 매니페스트는 `disclosure_pixel_verified: False` 를 정직하게 들고 간다.
DISCLOSURE_PIXEL_VERIFICATION_HOOK = (
    "TODO(gated-real-render): frame-sample OCR 또는 Remotion 렌더타임 "
    "assertion 으로 고지 픽셀 생존을 확인한다")

PROBE_TIMEOUT = 60

#: `npx remotion` 은 **Remotion 프로젝트 디렉터리 안에서만** 해석된다.
#: cwd 를 주지 않으면 임의의 디렉터리에서
#: `npm error could not determine executable to run` 로 죽고, 그러면 프로브가
#: '없다'고 보고해 멀쩡한 설치에서도 잡이 멈춘다. 반대로 이 경로가 실제로
#: 없으면 **그대로 실패해야 한다** — FFmpeg·HyperFrames 로 내려가는 경로는
#: 이 파일에 존재하지 않는다 (fail closed).
REMOTION_COMPOSER_DIR = os.path.expanduser(
    os.environ.get("HEIGHTCUE_REMOTION_COMPOSER_DIR",
                   "~/OpenMontage/remotion-composer"))


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


def _discard(*paths: str) -> None:
    """거부된 산출물을 지운다.

    렌더러가 자기 경로를 보고하면 검증 대상 경로와 우리가 요청한 경로가
    **다를 수 있다.** 둘 다 지워야 거부된 파일이 디스크에 살아남지 않는다.
    """
    seen = set()
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 실측 — 출력 mp4 의 박스를 직접 읽는다 (렌더러 보고는 증거가 아니다)
# ---------------------------------------------------------------------------

_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}

#: 미디어 페이로드 하한 (bytes/초). 실제 768x1360 30fps H.264+AAC 는 초당
#: 100KB 이상이다 — 이 값은 '진짜 인코딩이면 무조건 넘는' 바닥이며,
#: 헤더만 있는 스텁이나 잘린 mdat 을 걸러내는 용도다.
MIN_MEDIA_BYTES_PER_SECOND = 8000

#: 선언 길이(mvhd)와 코딩된 샘플 길이(stts/mdhd)의 허용 괴리.
CODED_DURATION_TOLERANCE_RATIO = 0.05
CODED_DURATION_TOLERANCE_FLOOR = 0.25


def _field(data: bytes, off: int, size: int, stop: int, what: str) -> bytes:
    """박스 **자기 경계** 안에서만 필드를 읽는다.

    파일 끝이 아니라 박스의 ``stop`` 으로 경계를 잡는 것이 핵심이다 — 짧은
    박스에서 파일 경계만 보면 **다음 박스의 바이트를 자기 필드로 오독**한다
    (리뷰어가 재현: 짧은 mvhd 가 인접 0 을 읽어 duration 0 을 보고했다).
    """
    if off < 0 or off + size > stop or off + size > len(data):
        raise ComposeFormatError(
            f"mp4 {what} 가 박스 경계를 넘어간다 (offset {off}, {size}바이트 "
            f"요구, 박스 끝 {stop}) — 잘린 박스를 추측으로 메우지 않는다")
    return data[off:off + size]


def _u32(data: bytes, off: int, stop: int, what: str) -> int:
    return struct.unpack(">I", _field(data, off, 4, stop, what))[0]


def _u64(data: bytes, off: int, stop: int, what: str) -> int:
    return struct.unpack(">Q", _field(data, off, 8, stop, what))[0]


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
        if tag == b"mdat":
            found["mdat_bytes"] = found.get("mdat_bytes", 0) + (stop - body)
            found["mdat_boxes"] = found.get("mdat_boxes", 0) + 1
        elif tag == b"mvhd":
            version = _field(data, body, 1, stop, "mvhd.version")[0]
            base = body + 4 + (16 if version == 1 else 8)
            found["timescale"] = _u32(data, base, stop, "mvhd.timescale")
            found["duration_units"] = (
                _u64(data, base + 4, stop, "mvhd.duration") if version == 1
                else _u32(data, base + 4, stop, "mvhd.duration"))
        elif tag == b"trak":
            current: Dict[str, Any] = {}
            found.setdefault("traks", []).append(current)
            _collect(data, body, stop, found, current)
        elif tag == b"tkhd" and trak is not None:
            version = _field(data, body, 1, stop, "tkhd.version")[0]
            width_off = body + (96 if version == 1 else 84) - 8
            trak["width"] = _u32(data, width_off, stop, "tkhd.width") >> 16
            trak["height"] = _u32(data, width_off + 4, stop,
                                  "tkhd.height") >> 16
        elif tag == b"mdhd" and trak is not None:
            version = _field(data, body, 1, stop, "mdhd.version")[0]
            base = body + 4 + (16 if version == 1 else 8)
            trak["media_timescale"] = _u32(data, base, stop, "mdhd.timescale")
            trak["media_duration_units"] = (
                _u64(data, base + 4, stop, "mdhd.duration") if version == 1
                else _u32(data, base + 4, stop, "mdhd.duration"))
        elif tag == b"hdlr" and trak is not None:
            trak["handler"] = _field(data, body + 8, 4, stop,
                                     "hdlr.handler_type").decode("latin-1")
        elif tag == b"stsd" and trak is not None:
            count = _u32(data, body + 4, stop, "stsd.entry_count")
            trak["stsd_entries"] = count
            if count == 1:
                entry = body + 8
                trak["fourcc"] = _field(data, entry + 4, 4, stop,
                                        "stsd.entry.format").decode(
                                            "latin-1").strip()
        elif tag == b"stts" and trak is not None:
            count = _u32(data, body + 4, stop, "stts.entry_count")
            total = 0
            samples_total = 0
            for i in range(count):
                off = body + 8 + i * 8
                samples = _u32(data, off, stop, "stts.sample_count")
                delta = _u32(data, off + 4, stop, "stts.sample_delta")
                samples_total += samples
                total += samples * delta
            trak["stts_sample_count"] = samples_total
            trak["stts_duration_units"] = total
        elif tag == b"stsz" and trak is not None:
            sample_size = _u32(data, body + 4, stop, "stsz.sample_size")
            sample_count = _u32(data, body + 8, stop, "stsz.sample_count")
            if sample_size:
                total = sample_size * sample_count
            else:
                total = sum(_u32(data, body + 12 + i * 4, stop, "stsz.entry")
                            for i in range(sample_count))
            trak["stsz_sample_count"] = sample_count
            trak["stsz_total_bytes"] = total
        elif tag in _CONTAINER_BOXES:
            _collect(data, body, stop, found, trak)


def _coded_seconds(trak: Dict[str, Any]) -> Optional[float]:
    """샘플 테이블이 실제로 담고 있는 길이 (초). 없으면 None."""
    timescale = int(trak.get("media_timescale") or 0)
    units = trak.get("stts_duration_units")
    if timescale > 0 and units:
        return float(units) / timescale
    units = trak.get("media_duration_units")
    if timescale > 0 and units:
        return float(units) / timescale
    return None


def measure_mp4(path: str) -> Dict[str, Any]:
    """출력 mp4 를 **바이트로 직접 읽어** 길이·크기·코덱을 돌려준다.

    이 계열에서 세 번 반복된 결함 — '선언을 검증하고 페이로드를 검증하지
    않는 것' — 을 여기서 끊는다. ``mvhd.duration``/``tkhd.width`` /
    ``stsd`` fourcc 는 전부 **헤더 선언**이므로 그것만으로는 통과시키지
    않는다. 실제 미디어(``mdat``)가 존재하고, 선언 길이에 걸맞은 크기이며,
    샘플 테이블(``stts``/``stsz``)·``mdhd`` 가 ``mvhd`` 와 서로 맞는지까지
    본 뒤에야 값을 돌려준다. 돌려주는 값에는 그 값이 관측인지 선언인지
    ``*_basis`` 로 이름을 붙인다 — 선언에 ``measured_`` 딱지를 붙이지 않는다.
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
    try:
        _collect(data, 0, len(data), found)
    except ComposeFormatError:
        raise
    except (struct.error, IndexError, UnicodeDecodeError, ValueError) as exc:
        raise ComposeFormatError(
            f"mp4 박스를 해석할 수 없다: {path} ({type(exc).__name__}: {exc}) — "
            "깨진 산출물은 통과시키지 않는다") from exc

    if "timescale" not in found:
        raise ComposeFormatError(f"mp4 에 moov/mvhd 가 없다: {path}")
    timescale = int(found["timescale"]) or 1
    declared_duration = float(found["duration_units"]) / timescale
    if declared_duration <= 0:
        raise ComposeFormatError(f"선언 길이가 0 이다: {path} — 빈 렌더다")

    traks = found.get("traks") or []
    video = next((t for t in traks if t.get("handler") == "vide"), None)
    audio = next((t for t in traks if t.get("handler") == "soun"), None)
    if video is None:
        raise ComposeFormatError(f"mp4 에 비디오 트랙이 없다: {path}")

    for name, trak in (("비디오", video), ("오디오", audio)):
        if trak is None:
            continue
        entries = trak.get("stsd_entries")
        if entries is None:
            raise ComposeFormatError(
                f"{name} 트랙에 stsd 가 없다: {path} — 코덱을 확인할 수 없다")
        if entries != 1:
            raise ComposeFormatError(
                f"{name} 트랙 stsd 항목이 {entries} 개다: {path} — 다중 샘플 "
                "기술자 레이아웃은 단일 코덱으로 요약할 수 없으므로, 엉뚱한 "
                "코덱을 그럴듯하게 보고하느니 거부한다")

    # --- 페이로드 존재 -----------------------------------------------------
    mdat_bytes = int(found.get("mdat_bytes") or 0)
    if not found.get("mdat_boxes"):
        raise ComposeFormatError(
            f"mp4 에 mdat 박스가 없다: {path} — moov 는 "
            f"{declared_duration:.3f}초를 선언하지만 미디어 페이로드가 0 "
            "바이트다. 헤더만 있는 스텁은 렌더 결과가 아니다")

    # --- 페이로드 크기가 선언 길이에 걸맞은가 ------------------------------
    floor_bytes = int(declared_duration * MIN_MEDIA_BYTES_PER_SECOND)
    if mdat_bytes < floor_bytes:
        raise ComposeFormatError(
            f"mdat 이 {mdat_bytes} 바이트뿐인데 moov 는 "
            f"{declared_duration:.3f}초를 선언한다 (최소 {floor_bytes} 바이트 "
            f"기대): {path} — 선언과 실제 페이로드가 어긋난다")

    # --- 샘플 테이블 교차검증 ---------------------------------------------
    coded = _coded_seconds(video)
    if coded is None:
        raise ComposeFormatError(
            f"비디오 트랙에 mdhd/stts 가 없다: {path} — mvhd 선언만으로는 "
            "길이를 확인할 수 없다")
    if coded <= 0:
        raise ComposeFormatError(
            f"비디오 샘플 테이블 길이가 0 이다: {path} — 프레임이 없다")
    tolerance = max(CODED_DURATION_TOLERANCE_FLOOR,
                    declared_duration * CODED_DURATION_TOLERANCE_RATIO)
    if abs(coded - declared_duration) > tolerance:
        raise ComposeFormatError(
            f"선언 길이 {declared_duration:.3f}초와 코딩된 샘플 길이 "
            f"{coded:.3f}초가 다르다 (허용 {tolerance:.3f}초): {path} — "
            "헤더가 주장하는 길이만큼의 미디어가 실제로 들어있지 않다")

    stsz_total = video.get("stsz_total_bytes")
    if stsz_total is not None and stsz_total > mdat_bytes:
        raise ComposeFormatError(
            f"stsz 샘플 총합 {stsz_total} 바이트가 mdat {mdat_bytes} 바이트를 "
            f"넘는다: {path} — 샘플 테이블이 없는 데이터를 가리킨다")

    return {
        # mvhd 선언이되, mdhd/stts 및 mdat 크기와 교차검증을 통과한 값.
        "duration_seconds": round(declared_duration, 6),
        "duration_basis": "mvhd_corroborated_by_mdhd_and_sample_tables",
        "coded_duration_seconds": round(coded, 6),
        # tkhd 는 선언이다 — 코딩된 데이터가 존재함을 확인했을 뿐,
        # 픽셀을 디코드해 잰 값이 아니다. 이름으로 그렇게 밝힌다.
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "dimension_basis": "header_declared_tkhd_corroborated_by_coded_data",
        "video_codec_fourcc": video.get("fourcc") or "",
        "audio_codec_fourcc": (audio or {}).get("fourcc") or "",
        "codec_basis": "header_declared_stsd_single_entry",
        "mdat_bytes": mdat_bytes,
        "bytes": len(data),
        "measured_by": "video_compose.measure_mp4 "
                       "(moov 박스 판독 + mdat/샘플테이블 교차검증)",
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

    if expected_seconds not in ALLOWED_TOTAL_DURATIONS:
        raise ComposeDurationError(
            f"총 길이는 {ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다: {expected_seconds}")
    if abs(m["duration_seconds"] - expected_seconds) > DURATION_TOLERANCE_SECONDS:
        raise ComposeDurationError(
            f"실측 길이 {m['duration_seconds']}초가 계획 {expected_seconds}초와 "
            f"다르다 (허용 오차 {DURATION_TOLERANCE_SECONDS}초): {path}")
    return m


# ---------------------------------------------------------------------------
# 런타임 프리플라이트 — 확인 실패는 곧 중단이다 (대체 없음)
# ---------------------------------------------------------------------------


def _subprocess_probe() -> Dict[str, Any]:
    """프로덕션 기본 프로브: `npx remotion versions`. 렌더는 하지 않는다.

    **반드시 컴포저 디렉터리에서 실행한다.** `npx` 는 cwd 의 node_modules 를
    보고 실행 파일을 찾으므로, cwd 없이 돌리면 임의 디렉터리에서
    `npm error could not determine executable to run` 으로 죽는다.
    디렉터리가 없으면 대체 런타임으로 내려가지 않고 **그대로 실패한다**.
    """
    composer_dir = REMOTION_COMPOSER_DIR
    if not os.path.isdir(composer_dir):
        raise RuntimeUnavailableError(
            f"Remotion 컴포저 디렉터리가 없다: {composer_dir!r} — 프로브를 "
            "실행할 수 없다. FFmpeg·HyperFrames 로 대체하지 않고 중단한다")
    try:
        proc = subprocess.run(["npx", "remotion", "versions"],
                              capture_output=True, text=True,
                              cwd=composer_dir,
                              timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeUnavailableError(
            f"Remotion 프로브 실행 실패: {exc!r} — 다른 런타임으로 대체하지 않는다"
        ) from exc
    if proc.returncode != 0:
        return {"available": False, "version": "",
                "detail": (proc.stderr or "")[-500:]}
    # 라벨이 붙은 줄에서만 버전을 읽는다. 아무 dotted 토큰이나 집으면 Node·
    # Chrome·FFmpeg 버전을 Remotion 버전으로 계보에 남길 수 있다 —
    # '틀렸지만 그럴듯한' 버전은 없느니만 못하다.
    # 실측: `npx remotion versions` 는 통일 버전일 때 "On version: 4.0.484"
    # 한 줄만 내놓고 그 줄엔 'remotion' 이란 낱말이 없다. 'remotion' 만
    # 찾으면 멀쩡한 설치에서 버전이 빈 채로 fail-closed 된다.
    VERSION_LABELS = ("remotion", "on version")
    version = ""
    for line in (proc.stdout or "").splitlines():
        low = line.lower()
        if not any(label in low for label in VERSION_LABELS):
            continue
        if "node.js" in low or "node =" in low:
            continue
        for token in line.replace(":", " ").replace(",", " ").split():
            token = token.strip("v()[]")
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


def extract_cta(storyboard: Dict[str, Any]) -> str:
    """승인된 CTA 를 **스토리보드에서** 축자 회수한다.

    합성 단계는 CTA 를 지어내지도, 호출자에게서 받지도 않는다. 화면에 박히는
    모든 글자는 승인본에 존재해야 한다 (SSOT: 카피는 승인본 그대로).
    """
    block = storyboard.get("cta")
    if isinstance(block, dict):
        text = str(block.get("text") or "").strip()
    else:
        text = str(block or "").strip()
    if not text:
        raise CaptionDriftError(
            "스토리보드에 승인된 cta.text 가 없다 — CTA 는 호출자 자유 입력이 "
            "아니라 승인본에서만 온다. CTA 없는 발행본은 만들지 않는다")
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


def build_overlay_plan(*, captions: List[str], cta: str,
                       disclosure_text: str,
                       total_seconds: int) -> Dict[str, Any]:
    """결정론적 오버레이 계획. 텍스트는 전부 승인본에서 그대로 온다.

    ``cta`` 는 반드시 :func:`extract_cta` 가 스토리보드에서 꺼낸 값이다.

    .. deprecated::
       캡션·CTA·고지를 한 판에 굽던 **단일 단계** 계획이다. 운영자 지시로
       합성이 2단으로 쪼개진 뒤로 신규 경로는
       :func:`build_master_overlay_plan` / :func:`build_subtitle_overlay_plan`
       를 쓴다. 이 함수는 기존 계약 테스트를 위해 남는다.
    """
    cta = str(cta or "").strip()
    if not cta:
        raise CaptionDriftError(
            "승인된 CTA 가 비어 있다 — CTA 없는 발행본은 만들지 않는다")

    layers = _caption_layers(captions) + [_cta_layer(cta, total_seconds)]
    disclosure = _disclosure_layer(disclosure_text, total_seconds)
    layers.append(dict(disclosure))
    return {"text_layers": layers, "disclosure": disclosure,
            "rendered_by": RENDER_RUNTIME,
            "note": "텍스트는 영상 모델이 아니라 Remotion 이 결정론적으로 렌더한다"}


def _caption_layers(captions: List[str]) -> List[Dict[str, Any]]:
    return [{
        "role": "caption",
        "cut_index": i + 1,
        "text": text,
        "verbatim_from": CAPTION_SOURCE,
        "start_seconds": i * CUT_DURATION_SECONDS,
        "end_seconds": (i + 1) * CUT_DURATION_SECONDS,
        "style": {"font_size_px": 44, "safe_area_margin_px": 96,
                  "background_scrim": True, "position": "lower_third",
                  **KOREAN_WRAP_STYLE},
    } for i, text in enumerate(captions)]


def _cta_layer(cta: str, total_seconds: int) -> Dict[str, Any]:
    return {
        "role": "cta",
        "text": cta,
        "verbatim_from": CTA_SOURCE,
        "start_seconds": max(0, total_seconds - CUT_DURATION_SECONDS),
        "end_seconds": total_seconds,
        "style": {"font_size_px": 40, "safe_area_margin_px": 96,
                  "background_scrim": True, "position": "center",
                  **KOREAN_WRAP_STYLE},
    }


def _disclosure_layer(disclosure_text: str,
                      total_seconds: int) -> Dict[str, Any]:
    return {
        "role": "disclosure",
        "text": disclosure_text,
        "required": True,
        # 고지는 영상 전 구간을 덮는다 — 스크롤로 지나쳐도 보이도록.
        "start_seconds": 0,
        "end_seconds": total_seconds,
        "style": {"font_size_px": DISCLOSURE_FONT_SIZE_PX,
                  "font_weight": DISCLOSURE_FONT_WEIGHT,
                  "safe_area_margin_px": MIN_SAFE_AREA_MARGIN_PX,
                  "background_scrim": True,
                  "scrim_opacity": DISCLOSURE_SCRIM_OPACITY,
                  "position": "top",
                  "opacity": 1.0,
                  **KOREAN_WRAP_STYLE},
    }


def build_master_overlay_plan(*, disclosure_text: str,
                              total_seconds: int) -> Dict[str, Any]:
    """**클린 마스터**의 오버레이 계획 — 제휴 고지 단 한 겹뿐이다.

    voice_line 캡션도 CTA 도 여기 들어가지 않는다. 첫 유료 렌더에서 캡션이
    제품 라벨을 가린 사고가 이 분리의 이유다. 반대로 고지는 **캡션과 함께
    빠지지 않는다** — 법적 의무이고 마스터 자체가 발행 가능한 에셋이다.
    """
    disclosure = _disclosure_layer(disclosure_text, total_seconds)
    return {"text_layers": [dict(disclosure)], "disclosure": disclosure,
            "rendered_by": RENDER_RUNTIME,
            "stage": STAGE_MASTER,
            "note": ("클린 마스터 — 제휴 고지만 번인한다. 자막·CTA 는 별도 "
                     "자막 패스(compose_subtitled) 또는 사이드카 SRT 로 "
                     "외부 편집기가 얹는다")}


def build_subtitle_overlay_plan(*, captions: List[str], cta: str,
                                total_seconds: int) -> Dict[str, Any]:
    """**자막 패스**의 오버레이 계획 — 캡션 + CTA. 고지는 다시 굽지 않는다.

    고지는 이미 마스터 픽셀에 있다. 여기서 한 겹 더 얹으면 화면에 고지가 두
    개 뜬다.
    """
    cta = str(cta or "").strip()
    if not cta:
        raise CaptionDriftError(
            "승인된 CTA 가 비어 있다 — CTA 없는 발행본은 만들지 않는다")
    layers = _caption_layers(captions) + [_cta_layer(cta, total_seconds)]
    return {"text_layers": layers,
            "rendered_by": RENDER_RUNTIME,
            "stage": STAGE_SUBTITLED,
            "disclosure_inherited_from_master": True,
            "note": ("자막 패스 — 고지는 마스터 픽셀에 이미 있으므로 다시 "
                     "굽지 않는다 (두 겹으로 뜬다)")}


# ---------------------------------------------------------------------------
# 사이드카 자막 (SRT) — 외부 후보정 도구의 입력
# ---------------------------------------------------------------------------


def srt_timestamp(seconds: float) -> str:
    """``HH:MM:SS,mmm`` — SRT 규격 타임스탬프."""
    if seconds < 0:
        raise CaptionDriftError(f"음수 타임스탬프: {seconds!r}")
    total_ms = int(round(float(seconds) * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    return (f"{total_s // 3600:02d}:{(total_s % 3600) // 60:02d}:"
            f"{total_s % 60:02d},{ms:03d}")


def build_srt(captions: List[str]) -> str:
    """승인된 ``voice_line`` 을 컷 경계 타이밍의 SRT 로 직렬화한다.

    **텍스트는 바이트 그대로 나간다.** 재줄바꿈·자르기·말줄임·대소문자
    변경을 하지 않는다 — ``video_qa`` 가 승인 집합과 축자 대조하며, 여기서
    한 글자라도 손대면 그 게이트가 무의미해진다.
    """
    if not captions:
        raise CaptionDriftError("자막으로 만들 승인 카피가 하나도 없다")
    blocks: List[str] = []
    for i, text in enumerate(captions):
        if not str(text or "").strip():
            raise CaptionDriftError(
                f"컷 {i + 1} 의 카피가 비어 있다 — 빈 큐를 조용히 건너뛰면 "
                "자막과 승인본의 줄 수가 어긋난다")
        start = i * CUT_DURATION_SECONDS
        end = (i + 1) * CUT_DURATION_SECONDS
        blocks.append(f"{i + 1}\n{srt_timestamp(start)} --> "
                      f"{srt_timestamp(end)}\n{text}\n")
    return "\n".join(blocks)


def write_subtitle_sidecar(path: str, captions: List[str]) -> str:
    """사이드카 SRT 를 UTF-8 로 쓴다. 외부 편집기(OpenCut 등)의 입력."""
    body = build_srt(captions)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# 클립 스테이징 — Remotion 은 절대 경로를 읽지 못한다
# ---------------------------------------------------------------------------


def stage_clips_for_remotion(cuts: List[Dict[str, Any]], *, job_id: str,
                             composer_dir: Optional[str] = None,
                             ) -> List[Dict[str, Any]]:
    """컷을 Remotion ``public/`` 안으로 복사하고 **다시 해시**한다.

    첫 유료 실행에서 ``OffthreadVideo`` 가 절대경로 ``file://`` 소스를
    거부해 운영자가 클립을 손으로 ``remotion-composer/public/`` 에 복사해야
    했다. 그 수작업을 여기로 흡수한다. 복사 뒤 sha256 을 다시 재서 원본과
    같은지 확인하므로 계보는 그대로 유지된다 — 다르면 계보 오류다.
    """
    composer = composer_dir or REMOTION_COMPOSER_DIR
    public = os.path.join(composer, "public")
    if not os.path.isdir(public):
        raise RuntimeUnavailableError(
            f"Remotion public 디렉터리가 없다: {public!r} — 클립을 스테이징할 "
            "수 없다. 다른 런타임으로 대체하지 않고 중단한다")

    staged_dir = os.path.join(public, STAGED_CLIP_SUBDIR, job_id)
    os.makedirs(staged_dir, exist_ok=True)

    out: List[Dict[str, Any]] = []
    for cut in cuts:
        index = int(cut["cut_index"])
        name = f"cut{index:02d}.mp4"
        dest = os.path.join(staged_dir, name)
        with open(cut["output_path"], "rb") as src, open(dest, "wb") as dst:
            for block in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(block)
        actual = sha256_file(dest)
        if actual != cut["output_sha256"]:
            raise ComposeLineageError(
                f"컷 {index} 스테이징 복사본 해시가 원본과 다르다: {actual} != "
                f"{cut['output_sha256']} ({dest}) — 계보가 끊긴 바이트는 "
                "렌더에 넣지 않는다")
        out.append({
            "cut_index": index,
            "src": f"{STAGED_CLIP_SUBDIR}/{job_id}/{name}",
            "staged_path": dest,
            "staged_sha256": actual,
            "source_path": cut["output_path"],
            "duration_seconds": cut["duration_seconds"],
        })
    return out


def assert_rendered_text(rendered: Any, *, approved: List[str],
                         disclosure_text: str) -> List[str]:
    """렌더러가 **보고한** 텍스트 레이어가 승인 집합과 정확히 같은지 본다.

    .. warning::
       이것은 **픽셀 검증이 아니다.** ``rendered`` 는 렌더러의 자기보고이므로,
       오버레이를 떨어뜨리고 문자열만 되돌려주는 렌더러는 여기를 통과한다.
       그래서 결과 매니페스트는 이 통과를 ``disclosure_included: True`` 라는
       관측 사실이 아니라 ``disclosure_verification_basis=
       "renderer_reported_text_layers"`` 라는 **근거의 출처**로 기록한다.
       실제 화면 확인은 :data:`DISCLOSURE_PIXEL_VERIFICATION_HOOK` 참고.

    공백은 **자르지 않는다.** 앞뒤 공백 드리프트도 승인본과 화면이 달라진
    것이며, 고지 문구는 축자 일치가 요구된다.
    """
    if not isinstance(rendered, (list, tuple)):
        raise CaptionDriftError(
            f"렌더 결과에 text_layers 가 없다 ({type(rendered)}) — 오버레이가 "
            "실제로 얹혔는지 확인할 수 없으면 발행하지 않는다")
    actual = [str(t) for t in rendered]

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
                  output_path: str,
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
    cta = extract_cta(storyboard)
    overlay_plan = build_overlay_plan(captions=captions, cta=cta,
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
        "cta": cta, "cta_source": CTA_SOURCE,
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

    rendered_path = ""
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

        # 9) 고지·카피가 렌더러 보고상 남아 있는지. **자기보고**이며 픽셀
        #    검증이 아니다 — 매니페스트에도 그 한계를 그대로 적는다.
        rendered_texts = assert_rendered_text(
            response.get("text_layers"), approved=approved_texts,
            disclosure_text=disclosure_text)

        # 10) 길이·크기·코덱을 **파일에서** 잰다.
        measured = assert_measured_output(rendered_path, expected_seconds)
    except Exception:
        # 검증은 rendered_path 에 걸렸다 — 우리가 요청한 경로와 다를 수 있으니
        # 둘 다 지운다. 거부된 산출물이 발행 단계로 새어 나가면 안 된다.
        _discard(rendered_path, output_path)
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
        "duration_basis": measured["duration_basis"],
        "coded_duration_seconds": measured["coded_duration_seconds"],
        "dimension_basis": measured["dimension_basis"],
        "codec_basis": measured["codec_basis"],
        "mdat_bytes": measured["mdat_bytes"],
        "video_codec_fourcc": measured["video_codec_fourcc"],
        "audio_codec_fourcc": measured["audio_codec_fourcc"],
        "captions": list(captions),
        "caption_source": CAPTION_SOURCE,
        "rendered_text_layers": rendered_texts,
        # 렌더러 자기보고다. 관측이 아니므로 `disclosure_included: True` 라고
        # 단정하지 않는다 — 오버레이를 떨어뜨리고 문자열만 에코하는 렌더러는
        # 이 검사를 통과한다. 실제 화면 확인은 아래 훅에서 이뤄져야 한다.
        "disclosure_reported_by_renderer": True,
        "disclosure_verification_basis": "renderer_reported_text_layers",
        "disclosure_pixel_verified": False,
        "disclosure_pixel_verification_hook":
            DISCLOSURE_PIXEL_VERIFICATION_HOOK,
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
        "disclosure_reported_by_renderer": True,
        "disclosure_pixel_verified": False,
        "fallback_taken": False,
    })
    return result


# ---------------------------------------------------------------------------
# 2단 합성 — 스테이지 1: 자막 없는 클린 마스터 (+ 사이드카 SRT)
# ---------------------------------------------------------------------------


def compose_master(*, storyboard: Dict[str, Any], cut_lineage: Any,
                   edit_decisions: Dict[str, Any], job_id: str,
                   output_path: str, renderer: Callable,
                   runtime_probe: Optional[Callable] = None,
                   ) -> Dict[str, Any]:
    """**스테이지 1 — 클린 마스터.** 컷을 잇고 원음을 살리고 고지만 굽는다.

    화면에 들어가는 텍스트는 제휴 고지 **한 겹뿐**이다. 승인된 voice_line
    캡션도 CTA 도 여기서는 굽지 않는다 — 첫 유료 렌더에서 캡션이 클로즈업
    컷의 제품 라벨을 덮어써서 못 쓰게 된 것이 이 분리의 이유다.

    같은 디렉터리에 승인 카피를 축자로 담은 사이드카 ``.srt`` 를 함께
    쓴다. 그것이 OpenCut 같은 외부 편집기가 소비할 표준 입력이다.

    반환된 dict 는 그대로 :func:`compose_subtitled` 의 ``master=`` 인자다.
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

    for name, value in (("job_id", job), ("run_id", run_id),
                        ("storyboard_id", storyboard_id),
                        ("product_id", product_id)):
        if not value:
            raise ComposeLineageError(f"{name} 가 비어 있다 — 계보 없는 합성 금지")
    if market not in MARKETS:
        raise ComposeLineageError(f"market 는 {MARKETS} 중 하나여야 한다: {market!r}")
    if not str(output_path or "").strip():
        raise ComposeLineageError("output_path 가 비어 있다")

    settings = assert_runtime_lock(edit_decisions)

    cuts = list(storyboard.get("cuts") or [])
    expected_seconds = len(cuts) * CUT_DURATION_SECONDS
    if expected_seconds not in ALLOWED_TOTAL_DURATIONS:
        raise ComposeDurationError(
            f"컷 {len(cuts)} 개 = {expected_seconds}초 — 총 길이는 "
            f"{ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다")

    disclosure_text = extract_disclosure(storyboard, market)

    # 캡션은 마스터에 굽지 않지만 **사이드카를 만들기 위해** 지금 축자
    # 회수한다. 회수 자체가 검증이다 — 빈 voice_line 은 여기서 죽는다.
    captions = extract_captions(storyboard)

    overlay_plan = build_master_overlay_plan(
        disclosure_text=disclosure_text, total_seconds=expected_seconds)

    inputs = verify_input_cuts(cut_lineage, len(cuts))
    runtime = assert_remotion_available(runtime_probe)

    # Remotion 은 절대경로 file:// 를 읽지 못한다 — public/ 으로 스테이징한다.
    staged = stage_clips_for_remotion(inputs, job_id=job)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    events = os.path.join(out_dir, "compose_events.jsonl")

    sidecar_path = os.path.join(
        out_dir, f"{job}_subtitles.{SUBTITLE_SIDECAR_FORMAT}")
    write_subtitle_sidecar(sidecar_path, captions)

    props = {
        "job_id": job, "run_id": run_id, "storyboard_id": storyboard_id,
        "product_id": product_id, "market": market,
        "composition_id": COMPOSITION_ID,
        "stage": STAGE_MASTER,
        "width": COMPOSITION_WIDTH, "height": COMPOSITION_HEIGHT, "fps": FPS,
        "duration_seconds": expected_seconds,
        "clips": [{"cut_index": c["cut_index"], "src": c["src"],
                   "sha256": c["staged_sha256"],
                   "duration_seconds": c["duration_seconds"]}
                  for c in staged],
        # 마스터는 캡션을 굽지 않는다. 승인 카피는 사이드카로만 나간다.
        "captions": [],
        "caption_source": CAPTION_SOURCE,
        "cta": "",
        "disclosure": dict(overlay_plan["disclosure"]),
        "overlay_plan": overlay_plan,
    }
    props_path = os.path.join(out_dir, f"{job}_master_remotion_props.json")
    atomic_write_json(props_path, props)

    request = {
        "operation": "remotion_render",
        "stage": STAGE_MASTER,
        "render_runtime": RENDER_RUNTIME,
        "composition_mode": COMPOSITION_MODE,
        "composition_id": COMPOSITION_ID,
        "props_path": props_path, "props": props,
        "input_cuts": [dict(c) for c in inputs],
        "staged_clips": [dict(c) for c in staged],
        "overlay_plan": overlay_plan,
        "output_path": output_path,
        "width": COMPOSITION_WIDTH, "height": COMPOSITION_HEIGHT,
        "fps": FPS, "duration_seconds": expected_seconds,
        "video_codec": "h264", "audio_codec": "aac",
        "aspect_ratio": VIDEO_ASPECT_RATIO, "resolution": VIDEO_RESOLUTION,
    }

    append_event(events, {
        "event": "master_started", "job_id": job, "run_id": run_id,
        "stage": STAGE_MASTER, "render_runtime": RENDER_RUNTIME,
        "runtime_version": runtime["version"], "cuts": len(inputs),
        "captions_burned_in": False, "disclosure_required": True,
        "fallback_runtime_available": False,
    })

    rendered_path = ""
    try:
        response = renderer(request)
        if not isinstance(response, dict):
            raise ComposeError(f"렌더러가 dict 가 아닌 {type(response)} 를 반환했다")
        reported = response.get("runtime")
        if reported not in ALLOWED_RENDER_RUNTIMES:
            raise RuntimeSwapError(
                f"렌더러가 런타임 {reported!r} 로 렌더했다고 보고했다 — "
                f"{RENDER_RUNTIME!r} 이어야 한다")
        rendered_path = str(response.get("output_path") or output_path)
        if not os.path.isfile(rendered_path):
            raise ComposeFormatError(f"렌더 산출물이 없다: {rendered_path}")

        # 마스터의 승인 집합은 **비어 있다** — 고지 외 어떤 텍스트도 화면에
        # 있어선 안 된다. 승인된 캡션이 들어가도 계약 위반이다.
        rendered_texts = assert_rendered_text(
            response.get("text_layers"), approved=[],
            disclosure_text=disclosure_text)

        measured = assert_measured_output(rendered_path, expected_seconds)
    except Exception:
        _discard(rendered_path, output_path)
        append_event(events, {
            "event": "master_rejected", "job_id": job, "run_id": run_id,
            "stage": STAGE_MASTER, "output_discarded": True,
            "fallback_taken": False,
        })
        raise

    result = {
        "job_id": job, "run_id": run_id, "storyboard_id": storyboard_id,
        "product_id": product_id, "market": market,
        "content_draft_id": content_draft_id,
        "stage": STAGE_MASTER,
        "captions_burned_in": False,
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
        "staged_clips": staged,
        "clips_staged": True,
        "props_path": props_path,
        "output_path": rendered_path,
        "output_sha256": sha256_file(rendered_path),
        "output_bytes": measured["bytes"],
        "expected_duration_seconds": expected_seconds,
        "measured_duration_seconds": measured["duration_seconds"],
        "measured_width": measured["width"],
        "measured_height": measured["height"],
        "measured_by": measured["measured_by"],
        "duration_basis": measured["duration_basis"],
        "coded_duration_seconds": measured["coded_duration_seconds"],
        "dimension_basis": measured["dimension_basis"],
        "codec_basis": measured["codec_basis"],
        "mdat_bytes": measured["mdat_bytes"],
        "video_codec_fourcc": measured["video_codec_fourcc"],
        "audio_codec_fourcc": measured["audio_codec_fourcc"],
        # 승인 카피는 굽지 않았지만 **사이드카/자막 패스로 넘길 값**으로
        # 계보에 남긴다. 여기가 사이드카와 자막 패스의 공통 출처다.
        "captions": list(captions),
        "caption_source": CAPTION_SOURCE,
        "subtitle_sidecar_path": sidecar_path,
        "subtitle_sidecar_format": SUBTITLE_SIDECAR_FORMAT,
        "rendered_text_layers": rendered_texts,
        "disclosure_reported_by_renderer": True,
        "disclosure_verification_basis": "renderer_reported_text_layers",
        "disclosure_pixel_verified": False,
        "disclosure_pixel_verification_hook":
            DISCLOSURE_PIXEL_VERIFICATION_HOOK,
        "disclosure_text": disclosure_text,
        "overlay_plan": overlay_plan,
        "created_at": _now(),
    }
    atomic_write_json(
        os.path.join(out_dir, f"{job}_master_manifest.json"), result)
    append_event(events, {
        "event": "master_finished", "job_id": job, "run_id": run_id,
        "stage": STAGE_MASTER, "output_sha256": result["output_sha256"],
        "subtitle_sidecar_path": sidecar_path,
        "captions_burned_in": False, "fallback_taken": False,
    })
    return result


# ---------------------------------------------------------------------------
# 2단 합성 — 스테이지 2: 자막 패스 (캡션 + CTA)
# ---------------------------------------------------------------------------


def compose_subtitled(*, master: Dict[str, Any], storyboard: Dict[str, Any],
                      edit_decisions: Dict[str, Any], job_id: str,
                      output_path: str, renderer: Callable,
                      runtime_probe: Optional[Callable] = None,
                      ) -> Dict[str, Any]:
    """**스테이지 2 — 자막 패스.** 클린 마스터 위에 캡션 + CTA 를 얹는다.

    ``master`` 는 :func:`compose_master` 가 돌려준 매니페스트다. 마스터
    파일을 **다시 해시**해 계보를 확인하고, 마스터를 유일한 클립으로 삼아
    별개의 산출물을 만든다. 마스터는 손대지 않고 그대로 남는다 — 그것이
    이 분리의 요점이다 (외부 후보정 도구가 언제든 원본을 쓸 수 있어야 한다).

    고지는 여기서 다시 굽지 않는다. 이미 마스터 픽셀에 있다.
    """
    if not isinstance(master, dict):
        raise ComposeLineageError(f"master 는 dict 여야 한다: {type(master)}")
    if master.get("stage") != STAGE_MASTER:
        raise ComposeLineageError(
            f"master.stage 가 {master.get('stage')!r} 다 — {STAGE_MASTER!r} "
            "산출물만 자막 패스의 입력이 된다")

    master_path = str(master.get("output_path") or "")
    declared = str(master.get("output_sha256") or "")
    if not master_path or not os.path.isfile(master_path):
        raise ComposeLineageError(f"마스터 파일이 없다: {master_path!r}")
    if not declared:
        raise ComposeLineageError("마스터에 output_sha256 이 없다")
    actual = sha256_file(master_path)
    if actual != declared:
        raise ComposeLineageError(
            f"마스터 해시가 계보와 다르다: {actual} != {declared} "
            f"({master_path}) — 마스터 단계가 검증한 그 바이트가 아니다")

    job = str(job_id or "").strip()
    if not job:
        raise ComposeLineageError("job_id 가 비어 있다 — 계보 없는 합성 금지")
    if not str(output_path or "").strip():
        raise ComposeLineageError("output_path 가 비어 있다")
    if os.path.abspath(output_path) == os.path.abspath(master_path):
        raise ComposeLineageError(
            "자막 패스가 마스터를 덮어쓰려 한다 — 클린 마스터는 별개 산출물로 "
            "남아야 외부 후보정이 가능하다")

    settings = assert_runtime_lock(edit_decisions)

    market = str(storyboard.get("market") or "").strip()
    if market not in MARKETS:
        raise ComposeLineageError(f"market 는 {MARKETS} 중 하나여야 한다: {market!r}")

    expected_seconds = int(master.get("expected_duration_seconds") or 0)
    if expected_seconds not in ALLOWED_TOTAL_DURATIONS:
        raise ComposeDurationError(
            f"마스터 길이 {expected_seconds}초가 계약을 벗어난다 "
            f"({ALLOWED_TOTAL_DURATIONS})")

    # 캡션·CTA 는 여전히 **승인본에서만** 온다.
    captions = extract_captions(storyboard)
    cta = extract_cta(storyboard)
    overlay_plan = build_subtitle_overlay_plan(
        captions=captions, cta=cta, total_seconds=expected_seconds)
    approved_texts = [l["text"] for l in overlay_plan["text_layers"]]

    disclosure_text = extract_disclosure(storyboard, market)
    runtime = assert_remotion_available(runtime_probe)

    staged = stage_clips_for_remotion(
        [{"cut_index": 1, "output_path": master_path,
          "output_sha256": actual, "duration_seconds": expected_seconds}],
        job_id=f"{job}-subtitled")

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    events = os.path.join(out_dir, "compose_events.jsonl")

    props = {
        "job_id": job,
        "run_id": master.get("run_id", ""),
        "storyboard_id": master.get("storyboard_id", ""),
        "product_id": master.get("product_id", ""),
        "market": market,
        "composition_id": COMPOSITION_ID,
        "stage": STAGE_SUBTITLED,
        "disclosure_inherited_from_master": True,
        "width": COMPOSITION_WIDTH, "height": COMPOSITION_HEIGHT, "fps": FPS,
        "duration_seconds": expected_seconds,
        "clips": [{"cut_index": 1, "src": staged[0]["src"],
                   "sha256": staged[0]["staged_sha256"],
                   "duration_seconds": expected_seconds}],
        "captions": list(captions),
        "caption_source": CAPTION_SOURCE,
        "cta": cta, "cta_source": CTA_SOURCE,
        "overlay_plan": overlay_plan,
    }
    props_path = os.path.join(out_dir, f"{job}_subtitled_remotion_props.json")
    atomic_write_json(props_path, props)

    request = {
        "operation": "remotion_render",
        "stage": STAGE_SUBTITLED,
        "render_runtime": RENDER_RUNTIME,
        "composition_mode": COMPOSITION_MODE,
        "composition_id": COMPOSITION_ID,
        "props_path": props_path, "props": props,
        "master_path": master_path, "master_sha256": actual,
        "staged_clips": [dict(c) for c in staged],
        "overlay_plan": overlay_plan,
        "output_path": output_path,
        "width": COMPOSITION_WIDTH, "height": COMPOSITION_HEIGHT,
        "fps": FPS, "duration_seconds": expected_seconds,
        "video_codec": "h264", "audio_codec": "aac",
        "aspect_ratio": VIDEO_ASPECT_RATIO, "resolution": VIDEO_RESOLUTION,
    }

    append_event(events, {
        "event": "subtitle_pass_started", "job_id": job,
        "stage": STAGE_SUBTITLED, "render_runtime": RENDER_RUNTIME,
        "runtime_version": runtime["version"], "master_sha256": actual,
        "captions": len(captions), "fallback_runtime_available": False,
    })

    rendered_path = ""
    try:
        response = renderer(request)
        if not isinstance(response, dict):
            raise ComposeError(f"렌더러가 dict 가 아닌 {type(response)} 를 반환했다")
        reported = response.get("runtime")
        if reported not in ALLOWED_RENDER_RUNTIMES:
            raise RuntimeSwapError(
                f"렌더러가 런타임 {reported!r} 로 렌더했다고 보고했다 — "
                f"{RENDER_RUNTIME!r} 이어야 한다")
        rendered_path = str(response.get("output_path") or output_path)
        if not os.path.isfile(rendered_path):
            raise ComposeFormatError(f"렌더 산출물이 없다: {rendered_path}")

        # 이 단계가 **새로** 굽는 텍스트는 캡션 + CTA 뿐이다. 고지는 마스터
        # 픽셀에서 오므로 렌더러 보고 목록에 없는 것이 정상이다.
        rendered = response.get("text_layers")
        if not isinstance(rendered, (list, tuple)):
            raise CaptionDriftError(
                f"렌더 결과에 text_layers 가 없다 ({type(rendered)})")
        actual_texts = [str(t) for t in rendered]
        missing = [t for t in approved_texts if t not in actual_texts]
        if missing:
            raise CaptionDriftError(
                f"승인된 카피가 자막 패스 결과에 없다: {missing!r}")
        extra = sorted({t for t in actual_texts
                        if t not in set(approved_texts) | {disclosure_text}})
        if extra:
            raise CaptionDriftError(
                f"승인되지 않은 텍스트가 렌더됐다: {extra!r}")

        measured = assert_measured_output(rendered_path, expected_seconds)
    except Exception:
        _discard(rendered_path, output_path)
        append_event(events, {
            "event": "subtitle_pass_rejected", "job_id": job,
            "stage": STAGE_SUBTITLED, "output_discarded": True,
            "master_preserved": os.path.isfile(master_path),
            "fallback_taken": False,
        })
        raise

    result = {
        "job_id": job,
        "run_id": master.get("run_id", ""),
        "storyboard_id": master.get("storyboard_id", ""),
        "product_id": master.get("product_id", ""),
        "market": market,
        "content_draft_id": master.get("content_draft_id", ""),
        "stage": STAGE_SUBTITLED,
        "captions_burned_in": True,
        "disclosure_inherited_from_master": True,
        "master_path": master_path,
        "master_sha256": actual,
        "subtitle_sidecar_path": master.get("subtitle_sidecar_path", ""),
        "subtitle_sidecar_format": SUBTITLE_SIDECAR_FORMAT,
        "render_runtime": RENDER_RUNTIME,
        "runtime_version": runtime["version"],
        "runtime_checked_at": runtime["checked_at"],
        "composition_mode": COMPOSITION_MODE,
        "composition_id": COMPOSITION_ID,
        "render_settings": dict(settings, width=COMPOSITION_WIDTH,
                                height=COMPOSITION_HEIGHT, fps=FPS,
                                video_codec="h264", audio_codec="aac",
                                composition_id=COMPOSITION_ID),
        "staged_clips": staged,
        "clips_staged": True,
        "props_path": props_path,
        "output_path": rendered_path,
        "output_sha256": sha256_file(rendered_path),
        "output_bytes": measured["bytes"],
        "expected_duration_seconds": expected_seconds,
        "measured_duration_seconds": measured["duration_seconds"],
        "measured_width": measured["width"],
        "measured_height": measured["height"],
        "measured_by": measured["measured_by"],
        "duration_basis": measured["duration_basis"],
        "coded_duration_seconds": measured["coded_duration_seconds"],
        "dimension_basis": measured["dimension_basis"],
        "codec_basis": measured["codec_basis"],
        "mdat_bytes": measured["mdat_bytes"],
        "video_codec_fourcc": measured["video_codec_fourcc"],
        "audio_codec_fourcc": measured["audio_codec_fourcc"],
        "captions": list(captions),
        "caption_source": CAPTION_SOURCE,
        "cta": cta, "cta_source": CTA_SOURCE,
        "rendered_text_layers": actual_texts,
        "disclosure_text": disclosure_text,
        # 이 단계는 고지를 굽지 않았다 — 고지 픽셀 검증은 마스터 대상이다.
        "disclosure_reported_by_renderer": False,
        "disclosure_verification_basis": "inherited_from_master_pixels",
        "disclosure_pixel_verified": False,
        "disclosure_pixel_verification_hook":
            DISCLOSURE_PIXEL_VERIFICATION_HOOK,
        "overlay_plan": overlay_plan,
        "created_at": _now(),
    }
    atomic_write_json(
        os.path.join(out_dir, f"{job}_subtitled_manifest.json"), result)
    append_event(events, {
        "event": "subtitle_pass_finished", "job_id": job,
        "stage": STAGE_SUBTITLED, "output_sha256": result["output_sha256"],
        "master_preserved": os.path.isfile(master_path),
        "captions_burned_in": True, "fallback_taken": False,
    })
    return result
