#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V UGC — 발행 직전 마지막 게이트 (Task 13).

한 줄 요약: **검사할 수 없으면 통과가 아니라 실패다.**

이 모듈은 상류가 전부 옳았더라도 모델이 조용히 다른 짓을 한 경우를 잡는
마지막 관문이다. 그래서 설계 원칙이 딱 세 개다.

1. **실측만 믿는다.** 렌더러·프로바이더가 에코한 길이·해상도·코덱은 증거가
   아니다. 길이/화면비/코덱은 ``video_compose.measure_mp4`` 로 실제 mp4
   박스를 읽어 판정한다 (두 번째 파서를 만들지 않는다).
2. **FAIL CLOSED.** 프레임 샘플러가 없든, 전사기가 미설치든, 상품 이미지가
   깨졌든 — 검사가 돌지 못하면 그 검사는 **실패**다. 못 돌린 검사가 조용히
   통과로 집계되는 구멍은 만들지 않는다.
3. **못 하는 걸 했다고 하지 않는다.** 아래 §제품 동일성 참조.

제품 동일성에 대한 정직한 고지 (읽고 넘어가지 말 것)
-----------------------------------------------------
Task 10 이 기록한 사실이 지금도 그대로다: 이 파이프라인 어디에도 **지각적
검증(perceptual verification)이 없다.** 지각 해시 라이브러리도, 임베딩
유사도 모델도, 사람 검수 게이트도 없다. "상품이 상품답게 유지된다"는 요구는
현재 **프롬프트 텍스트로만** 강제되고 있다.

여기서 구현한 ``product_identity_screen`` 은 그 공백을 메우지 못한다.
그것은 오프라인·결정적으로 돌릴 수 있는 **거친 스크리닝**이다 — 원본 상품
이미지와 샘플 프레임의 저해상도 구조 해시를 비교해 *명백한 붕괴*(완전히
다른 그림, 흑백 반전, 빈 화면)만 걸러낸다. 라벨 오타, 색상 옵션 뒤바뀜,
용량 표기 변경, 브랜드 로고 왜곡 같은 **실제로 위험한 동일성 위반은 이
검사로 검출되지 않는다.**

그래서 이 검사의 리포트에는 항상 ``establishes_identity: False`` 와
``limitations`` 가 실린다. 통과했다고 해서 "상품 동일성이 검증됐다"고
읽으면 안 된다. 진짜 동일성 보증은 지각 해시/임베딩 또는 사람 게이트가
도입돼야 생긴다 — 그 전까지 이 필드가 그 사실을 매 리포트에 적어 남긴다.

시임(seam)
----------
``frame_sampler`` / ``transcriber`` / ``audio_probe`` 는 전부 주입 가능한
호출 가능 객체다. 기본 구현은 OpenMontage 의 ``frame_sampler`` ·
``transcriber`` 로 셸아웃한다 (재구현하지 않는다). 테스트는 시임에
합성 로컬 파일을 물려 네트워크 없이 돈다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import video_contracts as vc
import video_storyboard as vs
from video_compose import measure_mp4
from video_contracts import (ALLOWED_TOTAL_DURATIONS, CUT_DURATION_SECONDS,
                             QAReport, VideoJob)

# ---------------------------------------------------------------------------
# 검사 이름 — 리포트 키의 정본. 여기 없는 이름은 리포트에 존재하지 않는다.
# ---------------------------------------------------------------------------

CHECK_TECHNICAL_CONTAINER = "technical_container"
CHECK_TECHNICAL_AUDIO = "technical_audio_signal"
CHECK_TECHNICAL_FRAMES = "technical_frames"
CHECK_PRODUCT_IDENTITY = "product_identity_screen"
CHECK_SPOKEN_CONTENT = "spoken_content"
CHECK_POLICY_DISCLOSURE = "policy_disclosure"
CHECK_POLICY_CLAIMS = "policy_forbidden_claims"

CHECK_NAMES: Tuple[str, ...] = (
    CHECK_TECHNICAL_CONTAINER,
    CHECK_TECHNICAL_AUDIO,
    CHECK_TECHNICAL_FRAMES,
    CHECK_PRODUCT_IDENTITY,
    CHECK_SPOKEN_CONTENT,
    CHECK_POLICY_DISCLOSURE,
    CHECK_POLICY_CLAIMS,
)

#: 금지 표현은 세 면 전부에서 본다 — 캡션만 깨끗한 영상은 통과시키지 않는다.
CLAIM_SCAN_SURFACES: Tuple[str, ...] = ("caption", "transcript", "overlay")

# ---------------------------------------------------------------------------
# 임계값 (전부 결정적·오프라인)
# ---------------------------------------------------------------------------

#: 768P-class 세로 숏폼.
EXPECTED_SHORT_SIDE = 768
ASPECT_TOLERANCE = 0.01
DURATION_TOLERANCE_SECONDS = 0.25
ALLOWED_VIDEO_FOURCC = ("avc1", "h264")
ALLOWED_AUDIO_FOURCC = ("mp4a", "aac")

#: 이보다 조용하면 무음으로 본다 (평균 RMS 기준).
SILENCE_RMS_DBFS = -60.0

#: 프레임 평균 휘도가 이보다 낮으면 블랙 프레임.
BLACK_MEAN_LUMA = 8.0
BLACK_MAX_LUMA = 16.0

#: 8x8 그레이 다운샘플이 이 오차 안에서 같으면 정지/중복 프레임.
DUPLICATE_MAX_ABS_DIFF = 1

#: dHash(64bit) 해밍 거리 상한 — 이보다 멀면 '명백한 붕괴'로 본다.
#: 동일성을 *증명*하는 값이 아니다. 모듈 상단 고지 참조.
MAX_DHASH_DISTANCE = 16

#: 승인 카피에서 벗어난 잔여 발화가 이 글자 수 이상이면 드리프트.
MAX_UNAPPROVED_CHARS = 4

#: 같은 프로바이더로 재생성을 허용하는 최대 횟수. 넘으면 dead letter.
MAX_REGEN_ATTEMPTS = 2

#: 이 파이프라인에 지각적 검증이 존재하는가 — 지금은 아니다.
PERCEPTUAL_VERIFICATION_AVAILABLE = False

IDENTITY_LIMITATIONS: Tuple[str, ...] = (
    "no perceptual hash library is installed — this is a coarse structural "
    "screen (dHash on an 8x8 luma downsample), not a perceptual match",
    "no embedding similarity model is available offline",
    "no human review gate exists in this pipeline",
    "label text, brand logo fidelity, colour/option variant and capacity "
    "wording are NOT verified by this check",
    "passing this check does NOT establish that the product is itself",
)

DEFAULT_OPENMONTAGE_ROOT = os.environ.get(
    "OPENMONTAGE_ROOT", os.path.expanduser("~/OpenMontage"))

DEFAULT_SEAM_TIMEOUT = 300


class QAError(Exception):
    """QA 게이트 내부 오류 — 검사 실패는 예외가 아니라 리포트로 표현한다."""


class CheckUnavailable(QAError):
    """검사를 돌릴 수 없다. **통과가 아니라 실패로 집계된다.**"""


# ---------------------------------------------------------------------------
# PNG 디코더 — 신규 의존성 없이(zlib/struct 만) 프레임을 실제로 읽는다
# ---------------------------------------------------------------------------


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_gray(path: str) -> Tuple[List[List[int]], int, int]:
    """PNG 를 8bit 그레이 2차원 배열로 디코딩한다.

    지원: bit depth 8, color type 0(gray)/2(RGB)/6(RGBA), non-interlaced.
    지원 밖이면 조용히 넘어가지 않고 ``CheckUnavailable`` 로 죽는다 —
    읽지 못한 프레임을 '문제 없음'으로 셈하지 않기 위해서다.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise CheckUnavailable(f"프레임 이미지를 읽을 수 없다: {path} ({exc})") from exc

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CheckUnavailable(
            f"PNG 가 아니다 (선두 {data[:8]!r}): {path} — "
            "확장자가 아니라 실제 바이트로 판정한다")

    offset, idat, header = 8, bytearray(), None
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body[:13])
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        offset += 12 + length

    if header is None:
        raise CheckUnavailable(f"PNG 에 IHDR 이 없다: {path}")
    width, height, depth, colour, _comp, _filt, interlace = header
    if depth != 8 or interlace != 0 or colour not in (0, 2, 6):
        raise CheckUnavailable(
            f"지원하지 않는 PNG 형식 (depth={depth} colour={colour} "
            f"interlace={interlace}): {path}")
    if width <= 0 or height <= 0:
        raise CheckUnavailable(f"PNG 크기가 0 이다: {path}")

    channels = {0: 1, 2: 3, 6: 4}[colour]
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise CheckUnavailable(f"PNG IDAT 압축 해제 실패: {path} ({exc})") from exc

    stride = width * channels
    if len(raw) < (stride + 1) * height:
        raise CheckUnavailable(
            f"PNG 픽셀 데이터가 잘렸다: {path} "
            f"({len(raw)} < {(stride + 1) * height})")

    rows: List[List[int]] = []
    prev = bytearray(stride)
    pos = 0
    for _y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
            elif ftype != 0:
                raise CheckUnavailable(
                    f"알 수 없는 PNG 필터 타입 {ftype}: {path}")
        grey = []
        for x in range(width):
            base = x * channels
            if channels == 1:
                grey.append(line[base])
            else:
                grey.append((line[base] * 299 + line[base + 1] * 587
                             + line[base + 2] * 114) // 1000)
        rows.append(grey)
        prev = line
    return rows, width, height


def _resize_gray(rows: List[List[int]], nw: int, nh: int) -> List[List[int]]:
    """박스 평균 다운샘플 — 결정적이고 의존성이 없다."""
    h = len(rows)
    w = len(rows[0])
    out = []
    for oy in range(nh):
        y0, y1 = oy * h // nh, max(oy * h // nh + 1, (oy + 1) * h // nh)
        line = []
        for ox in range(nw):
            x0, x1 = ox * w // nw, max(ox * w // nw + 1, (ox + 1) * w // nw)
            total = count = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    total += rows[y][x]
                    count += 1
            line.append(total // max(1, count))
        out.append(line)
    return out


def dhash(rows: List[List[int]]) -> int:
    """9x8 다운샘플의 좌우 밝기 비교 → 64bit 구조 해시."""
    small = _resize_gray(rows, 9, 8)
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (1 if small[y][x] < small[y][x + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _luma_stats(rows: List[List[int]]) -> Tuple[float, int]:
    flat = [v for row in rows for v in row]
    return sum(flat) / len(flat), max(flat)


# ---------------------------------------------------------------------------
# 기본 시임 — OpenMontage 로 셸아웃 (재구현 금지)
# ---------------------------------------------------------------------------


def _openmontage_call(tool_module: str, tool_class: str,
                      inputs: Dict[str, Any],
                      root: str = DEFAULT_OPENMONTAGE_ROOT) -> Dict[str, Any]:
    """OpenMontage 툴을 자기 인터프리터로 실행하고 data 를 돌려준다.

    실패하면 ``CheckUnavailable`` — 조용한 폴백은 없다.
    """
    if not os.path.isdir(root):
        raise CheckUnavailable(
            f"OpenMontage 를 찾을 수 없다: {root} — 프레임/전사 검사를 "
            "돌릴 수 없으므로 실패로 처리한다 (fail closed)")
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {root!r})\n"
        f"from tools.analysis.{tool_module} import {tool_class}\n"
        "inputs = json.loads(sys.stdin.read())\n"
        f"r = {tool_class}().execute(inputs)\n"
        "print(json.dumps({'success': bool(getattr(r, 'success', False)),\n"
        "                  'data': getattr(r, 'data', None),\n"
        "                  'error': getattr(r, 'error', None)}))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script], input=json.dumps(inputs),
            capture_output=True, text=True, timeout=DEFAULT_SEAM_TIMEOUT,
            cwd=root)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckUnavailable(f"OpenMontage {tool_class} 실행 실패: {exc}") from exc
    if proc.returncode != 0:
        raise CheckUnavailable(
            f"OpenMontage {tool_class} 가 코드 {proc.returncode} 로 죽었다: "
            f"{(proc.stderr or '').strip()[-400:]}")
    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise CheckUnavailable(
            f"OpenMontage {tool_class} 출력을 해석할 수 없다: {exc}") from exc
    if not payload.get("success"):
        raise CheckUnavailable(
            f"OpenMontage {tool_class} 실패: {payload.get('error')!r}")
    return payload.get("data") or {}


def default_frame_sampler(video_path: str, timestamps: Sequence[float],
                          out_dir: str) -> List[Dict[str, Any]]:
    """OpenMontage frame_sampler 로 지정 타임스탬프 프레임을 뽑는다."""
    os.makedirs(out_dir, exist_ok=True)
    data = _openmontage_call("frame_sampler", "FrameSampler", {
        "input_path": video_path, "strategy": "timestamps",
        "timestamps": [float(t) for t in timestamps],
        "output_dir": out_dir, "format": "png"})
    frames = data.get("frames") or []
    out = []
    for ts, frame in zip(timestamps, frames):
        path = frame.get("path") if isinstance(frame, dict) else frame
        out.append({"timestamp": float(ts), "path": path})
    return out


def default_transcriber(video_path: str) -> Dict[str, Any]:
    """OpenMontage transcriber 로 오디오를 전사한다."""
    data = _openmontage_call("transcriber", "Transcriber",
                             {"input_path": video_path})
    segments = data.get("segments") or []
    text = " ".join(str(s.get("text", "")) for s in segments
                    if isinstance(s, dict)).strip()
    if not text:
        text = str(data.get("text") or "").strip()
    return {"text": text, "language": data.get("language") or ""}


def default_audio_probe(video_path: str) -> Dict[str, Any]:
    """OpenMontage audio_energy 로 실제 오디오 레벨을 잰다."""
    data = _openmontage_call("audio_energy", "AudioEnergy",
                             {"input_path": video_path})
    if "rms_dbfs" not in data:
        raise CheckUnavailable(
            f"오디오 프로브가 rms_dbfs 를 돌려주지 않았다: {sorted(data)}")
    return {"rms_dbfs": float(data["rms_dbfs"]),
            "peak_dbfs": float(data.get("peak_dbfs", data["rms_dbfs"]))}


# ---------------------------------------------------------------------------
# 텍스트 정규화 / 승인 카피 대조
# ---------------------------------------------------------------------------

_NORM_STRIP = re.compile(r"[^0-9a-z\uac00-\ud7a3]+")


def normalize_speech(text: Any) -> str:
    """대조용 정규화: 공백·문장부호·대소문자만 없앤다. 내용은 바꾸지 않는다."""
    return _NORM_STRIP.sub("", str(text or "").lower())


def approved_voice_lines(storyboard: Dict[str, Any]) -> List[str]:
    lines = []
    for cut in storyboard.get("cuts") or []:
        line = str((cut or {}).get("voice_line") or "").strip()
        if line:
            lines.append(line)
    return lines


def find_forbidden_claims(text: str) -> List[str]:
    """video_storyboard 의 금지 패턴을 그대로 재사용한다 (두 벌 관리 금지)."""
    hits = []
    for pattern in vs._FORBIDDEN_RE:
        match = pattern.search(str(text or ""))
        if match:
            hits.append(match.group(0).strip())
    return hits


# ---------------------------------------------------------------------------
# 개별 검사 — 전부 (passed, detail, ...) dict 를 돌려준다
# ---------------------------------------------------------------------------


def _fail(detail: str, **extra: Any) -> Dict[str, Any]:
    return dict(extra, passed=False, detail=detail)


def _ok(detail: str = "", **extra: Any) -> Dict[str, Any]:
    return dict(extra, passed=True, detail=detail)


def check_technical_container(video_path: str,
                              expected_seconds: int) -> Dict[str, Any]:
    """길이·세로·768P-class·H.264/AAC 를 **실측 mp4 박스로만** 판정한다."""
    try:
        m = measure_mp4(video_path)
    except Exception as exc:                     # noqa: BLE001 — fail closed
        return _fail(f"실측 실패 — 산출물을 잴 수 없으면 통과시키지 않는다: {exc}",
                     measured=None)

    problems: List[str] = []
    width, height = m["width"], m["height"]
    if height <= width:
        problems.append(f"세로가 아니다 ({width}x{height})")
    if min(width, height) != EXPECTED_SHORT_SIDE:
        problems.append(f"짧은 변 {min(width, height)} 가 768P-class 가 아니다")
    if height:
        ratio = width / height
        if abs(ratio - 9 / 16) > ASPECT_TOLERANCE:
            problems.append(f"화면비 {ratio:.4f} 가 9:16 (0.5625) 이 아니다")
    if m["video_codec_fourcc"].lower() not in ALLOWED_VIDEO_FOURCC:
        problems.append(f"비디오 코덱이 H.264 가 아니다: {m['video_codec_fourcc']!r}")
    if not m["audio_codec_fourcc"]:
        problems.append("오디오 트랙이 없다 — 무음 산출물은 발행하지 않는다")
    elif m["audio_codec_fourcc"].lower() not in ALLOWED_AUDIO_FOURCC:
        problems.append(f"오디오 코덱이 AAC 가 아니다: {m['audio_codec_fourcc']!r}")

    if expected_seconds not in ALLOWED_TOTAL_DURATIONS:
        problems.append(f"계획 길이 {expected_seconds}s 가 "
                        f"{ALLOWED_TOTAL_DURATIONS} 에 없다")
    measured = m["duration_seconds"]
    if abs(measured - expected_seconds) > DURATION_TOLERANCE_SECONDS:
        problems.append(f"실측 길이 {measured}s 가 계획 {expected_seconds}s 와 "
                        f"다르다 (허용 {DURATION_TOLERANCE_SECONDS}s)")
    if not any(abs(measured - d) <= DURATION_TOLERANCE_SECONDS
               for d in ALLOWED_TOTAL_DURATIONS):
        problems.append(f"실측 길이 {measured}s 가 허용 길이 "
                        f"{ALLOWED_TOTAL_DURATIONS} 중 어느 것도 아니다")

    if problems:
        return _fail("; ".join(problems), measured=m)
    return _ok(f"실측 {width}x{height} {measured}s "
               f"{m['video_codec_fourcc']}/{m['audio_codec_fourcc']}",
               measured=m)


def check_audio_signal(video_path: str,
                       audio_probe: Optional[Callable]) -> Dict[str, Any]:
    """오디오가 실제로 소리를 내는가. 못 재면 실패."""
    probe = audio_probe or default_audio_probe
    try:
        levels = probe(video_path)
    except Exception as exc:                     # noqa: BLE001 — fail closed
        return _fail(f"오디오 레벨을 잴 수 없다 ({type(exc).__name__}: {exc}) — "
                     "돌지 못한 검사는 통과가 아니다", levels=None)
    try:
        rms = float(levels["rms_dbfs"])
    except (KeyError, TypeError, ValueError) as exc:
        return _fail(f"오디오 프로브 출력이 rms_dbfs 를 담고 있지 않다: {exc}",
                     levels=levels)
    if rms <= SILENCE_RMS_DBFS:
        return _fail(f"무음이다: RMS {rms:.1f} dBFS <= {SILENCE_RMS_DBFS} dBFS",
                     levels=levels)
    return _ok(f"RMS {rms:.1f} dBFS", levels=levels)


def sample_timestamps(duration: float, cut_count: int) -> List[float]:
    """첫/중간/전환/마지막 프레임 타임스탬프 (결정적)."""
    stamps = {0.0}
    for k in range(1, max(1, cut_count)):
        boundary = float(CUT_DURATION_SECONDS * k)
        if boundary < duration:
            stamps.add(round(boundary, 3))
    for k in range(max(1, cut_count)):
        mid = CUT_DURATION_SECONDS * k + CUT_DURATION_SECONDS / 2.0
        if mid < duration:
            stamps.add(round(mid, 3))
    stamps.add(round(max(0.0, duration - 0.05), 3))
    return sorted(stamps)


def check_frames(frames: List[Dict[str, Any]], expected_count: int,
                 error: Optional[str] = None) -> Dict[str, Any]:
    """블랙 프레임·정지(중복) 프레임 검출. 샘플링이 안 됐으면 실패."""
    if error:
        return _fail(f"프레임을 샘플링할 수 없다 ({error}) — "
                     "돌지 못한 검사는 통과가 아니다", sampled=0)
    if len(frames) != expected_count:
        return _fail(f"프레임 {expected_count}장을 요청했는데 {len(frames)}장만 "
                     "돌아왔다 — 부분 샘플로 판정하지 않는다",
                     sampled=len(frames), expected=expected_count)

    stats, problems = [], []
    for frame in frames:
        try:
            rows, _w, _h = decode_png_gray(frame["path"])
        except CheckUnavailable as exc:
            return _fail(f"프레임 디코딩 실패: {exc}", sampled=len(frames))
        mean, peak = _luma_stats(rows)
        small = _resize_gray(rows, 8, 8)
        stats.append({"timestamp": frame.get("timestamp"), "mean_luma": round(mean, 2),
                      "max_luma": peak, "small": small,
                      "dhash": dhash(rows)})

    for s in stats:
        if s["mean_luma"] < BLACK_MEAN_LUMA and s["max_luma"] < BLACK_MAX_LUMA:
            problems.append(f"t={s['timestamp']}s 가 전부 검은 프레임 "
                            f"(평균 휘도 {s['mean_luma']})")

    for i in range(len(stats)):
        for j in range(i + 1, len(stats)):
            a, b = stats[i]["small"], stats[j]["small"]
            worst = max(abs(a[y][x] - b[y][x]) for y in range(8) for x in range(8))
            if worst <= DUPLICATE_MAX_ABS_DIFF:
                problems.append(
                    f"t={stats[i]['timestamp']}s 와 t={stats[j]['timestamp']}s 가 "
                    f"사실상 같은 프레임이다 (최대 차 {worst}) — 정지 영상")

    summary = [{k: v for k, v in s.items() if k != "small"} for s in stats]
    if problems:
        return _fail("; ".join(problems), sampled=len(frames), frames=summary)
    return _ok(f"{len(frames)}장 검사: 블랙 없음, 중복 없음",
               sampled=len(frames), frames=summary)


def check_product_identity_screen(frames: List[Dict[str, Any]],
                                  product_image_path: Optional[str],
                                  error: Optional[str] = None) -> Dict[str, Any]:
    """**부분 검사다.** 명백한 붕괴만 걸러낸다 — 동일성을 증명하지 않는다.

    리포트에 ``establishes_identity=False`` 와 ``limitations`` 를 항상 싣는다.
    모듈 상단의 고지를 함께 읽을 것.
    """
    base = {
        "establishes_identity": False,
        "limitations": list(IDENTITY_LIMITATIONS),
        "method": "dHash(8x8 luma) Hamming distance vs verified source image",
        "perceptual_verification_available": PERCEPTUAL_VERIFICATION_AVAILABLE,
    }
    if error:
        return dict(base, **_fail(
            f"프레임을 샘플링할 수 없어 상품 스크리닝을 돌리지 못했다 ({error}) — "
            "돌지 못한 검사는 통과가 아니다"))
    if not frames:
        return dict(base, **_fail("샘플 프레임이 없다 — 스크리닝 불가"))
    if not product_image_path:
        return dict(base, **_fail(
            "검증된 원본 상품 이미지 경로가 주어지지 않았다 — 비교 기준이 없으면 "
            "통과시키지 않는다"))
    try:
        source_rows, _w, _h = decode_png_gray(product_image_path)
    except CheckUnavailable as exc:
        return dict(base, **_fail(
            f"원본 상품 이미지를 읽을 수 없다: {exc} — 비교 기준 없이 통과시키지 않는다"))

    source_hash = dhash(source_rows)
    base["source_sha256"] = _sha256_file(product_image_path)
    distances = []
    for frame in frames:
        try:
            rows, _fw, _fh = decode_png_gray(frame["path"])
        except CheckUnavailable as exc:
            return dict(base, **_fail(f"프레임 디코딩 실패: {exc}"))
        distances.append({"timestamp": frame.get("timestamp"),
                          "dhash_distance": hamming(source_hash, dhash(rows))})
    base["distances"] = distances
    best = min(d["dhash_distance"] for d in distances)
    base["best_distance"] = best
    base["threshold"] = MAX_DHASH_DISTANCE

    if best > MAX_DHASH_DISTANCE:
        return dict(base, **_fail(
            f"모든 샘플 프레임이 원본 상품 이미지와 구조적으로 크게 어긋난다 "
            f"(최소 dHash 거리 {best} > {MAX_DHASH_DISTANCE}) — 상품이 붕괴했을 "
            "가능성이 높다"))
    return dict(base, **_ok(
        f"명백한 붕괴는 없다 (최소 dHash 거리 {best} <= {MAX_DHASH_DISTANCE}). "
        "주의: 이 통과는 상품 동일성을 증명하지 않는다 — limitations 참조"))


def check_spoken_content(transcript: Optional[str],
                         approved_lines: Sequence[str],
                         error: Optional[str] = None) -> Dict[str, Any]:
    """실제 발화가 승인된 카피와 같은가. 전사가 안 되면 실패."""
    if error:
        return _fail(f"오디오를 전사할 수 없다 ({error}) — 승인 카피 대조를 "
                     "돌리지 못했으므로 통과가 아니다", transcript=None)
    if not approved_lines:
        return _fail("승인된 나레이션이 스토리보드에 하나도 없다 — "
                     "무엇과 대조해야 할지 알 수 없다")
    normalized = normalize_speech(transcript)
    if not normalized:
        return _fail("전사 결과가 비어 있다 — 영상이 실제로 말을 하는지 확인 불가",
                     transcript=transcript)

    residual = normalized
    missing = []
    for line in approved_lines:
        norm = normalize_speech(line)
        if norm and norm in residual:
            residual = residual.replace(norm, "", 1)
        else:
            missing.append(line)

    if missing:
        return _fail(
            f"승인된 나레이션 {len(missing)}줄이 실제 발화에 없다: {missing!r} — "
            f"전사: {str(transcript)[:200]!r}",
            transcript=transcript, missing_lines=missing)
    if len(residual) >= MAX_UNAPPROVED_CHARS:
        return _fail(
            f"승인되지 않은 발화가 {len(residual)}자 섞여 있다: {residual[:120]!r} — "
            "승인되지 않은 말은 영상이 하지 않는다",
            transcript=transcript, unapproved_residual=residual)
    return _ok(f"승인 나레이션 {len(approved_lines)}줄과 일치 (잔여 {len(residual)}자)",
               transcript=transcript)


def check_disclosure(caption: str, overlay_texts: Sequence[str],
                     market: str,
                     storyboard: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """제휴 고지 불변 문구가 그대로 살아 있는가 (SSOT 불변 규칙 2)."""
    try:
        required = vs.DISCLOSURE_TEXT[market]
    except KeyError:
        return _fail(f"알 수 없는 시장이라 고지 문구를 정할 수 없다: {market!r}")

    obligation = ((storyboard or {}).get("disclosure") or {})
    if obligation and not obligation.get("required", True):
        return _fail("스토리보드가 제휴 고지 의무를 해제하려 한다 — "
                     "고지는 해제 대상이 아니다 (SSOT 불변 규칙 2)")
    if obligation and obligation.get("text") and obligation["text"] != required:
        return _fail(f"스토리보드 고지 문구가 불변 문구와 다르다: "
                     f"{obligation['text']!r} != {required!r}")

    in_caption = required in str(caption or "")
    in_overlay = any(required in str(t or "") for t in (overlay_texts or []))
    if not in_caption:
        return _fail(f"캡션에 {market} 제휴 고지 불변 문구가 없다 (문구 변형·생략 금지): "
                     f"{required!r}", in_caption=False, in_overlay=in_overlay)
    return _ok(f"{market} 고지 문구 확인 (caption={in_caption}, overlay={in_overlay})",
               in_caption=in_caption, in_overlay=in_overlay)


def check_forbidden_claims(caption: str, transcript: Optional[str],
                           overlay_texts: Sequence[str]) -> Dict[str, Any]:
    """캡션·전사·오버레이 세 면 전부에서 금지 표현을 스캔한다."""
    surfaces = {
        "caption": [str(caption or "")],
        "transcript": [str(transcript or "")],
        "overlay": [str(t or "") for t in (overlay_texts or [])],
    }
    hits: Dict[str, List[str]] = {}
    for name in CLAIM_SCAN_SURFACES:
        found: List[str] = []
        for text in surfaces.get(name, []):
            found.extend(find_forbidden_claims(text))
        if found:
            hits[name] = sorted(set(found))
    if hits:
        parts = [f"{k}: {v}" for k, v in sorted(hits.items())]
        return _fail("금지 표현(효능 암시·가짜 체험담) 검출 — " + "; ".join(parts),
                     hits=hits, scanned=list(CLAIM_SCAN_SURFACES))
    return _ok(f"{len(CLAIM_SCAN_SURFACES)}개 면에서 금지 표현 없음",
               hits={}, scanned=list(CLAIM_SCAN_SURFACES))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 게이트 본체
# ---------------------------------------------------------------------------


def run_qa(*, job_id: str, run_id: str, video_path: str,
           storyboard: Dict[str, Any], caption: str,
           overlay_texts: Optional[Sequence[str]] = None,
           product_image_path: Optional[str] = None,
           frame_sampler: Optional[Callable] = None,
           transcriber: Optional[Callable] = None,
           audio_probe: Optional[Callable] = None,
           workdir: Optional[str] = None) -> QAReport:
    """발행 직전 전체 QA. **어떤 검사도 조용히 건너뛰지 않는다.**

    돌지 못한 검사는 실패로 집계된다 (fail closed). 결과는 검사별 pass/fail 과
    진단 정보를 담은 계약 ``QAReport`` 다.
    """
    storyboard = storyboard or {}
    overlay_texts = list(overlay_texts or [])
    market = storyboard.get("market") or ""
    cuts = storyboard.get("cuts") or []
    cut_count = max(1, len(cuts))
    expected_seconds = sum(int(c.get("duration_seconds") or CUT_DURATION_SECONDS)
                           for c in cuts) or CUT_DURATION_SECONDS

    checks: Dict[str, Any] = {}

    # 1. 컨테이너 실측
    checks[CHECK_TECHNICAL_CONTAINER] = check_technical_container(
        video_path, expected_seconds)

    # 2. 오디오 신호
    checks[CHECK_TECHNICAL_AUDIO] = check_audio_signal(video_path, audio_probe)

    # 3. 프레임 샘플링 (실측 길이 우선, 못 재면 계획 길이)
    measured = (checks[CHECK_TECHNICAL_CONTAINER].get("measured") or {})
    duration = float(measured.get("duration_seconds") or expected_seconds)
    stamps = sample_timestamps(duration, cut_count)

    tmp_holder = None
    if workdir is None:
        tmp_holder = tempfile.TemporaryDirectory(prefix="heightcue-qa-")
        workdir = tmp_holder.name
    frames_dir = os.path.join(workdir, "frames")

    sampler = frame_sampler or default_frame_sampler
    frames: List[Dict[str, Any]] = []
    sample_error: Optional[str] = None
    try:
        frames = list(sampler(video_path, stamps, frames_dir) or [])
    except Exception as exc:                     # noqa: BLE001 — fail closed
        sample_error = f"{type(exc).__name__}: {exc}"

    checks[CHECK_TECHNICAL_FRAMES] = check_frames(frames, len(stamps), sample_error)
    checks[CHECK_PRODUCT_IDENTITY] = check_product_identity_screen(
        frames, product_image_path, sample_error)

    # 4. 발화 내용
    transcribe = transcriber or default_transcriber
    transcript: Optional[str] = None
    transcribe_error: Optional[str] = None
    try:
        result = transcribe(video_path)
        transcript = (result.get("text") if isinstance(result, dict)
                      else str(result or ""))
    except Exception as exc:                     # noqa: BLE001 — fail closed
        transcribe_error = f"{type(exc).__name__}: {exc}"

    checks[CHECK_SPOKEN_CONTENT] = check_spoken_content(
        transcript, approved_voice_lines(storyboard), transcribe_error)

    # 5. 정책
    checks[CHECK_POLICY_DISCLOSURE] = check_disclosure(
        caption, overlay_texts, market, storyboard)
    checks[CHECK_POLICY_CLAIMS] = check_forbidden_claims(
        caption, transcript, overlay_texts)

    if tmp_holder is not None:
        tmp_holder.cleanup()

    # 이름이 하나라도 빠지면 조용히 통과하는 구멍이 된다 — 크게 실패한다.
    missing = [n for n in CHECK_NAMES if n not in checks]
    if missing:
        raise QAError(f"검사 결과가 누락됐다: {missing} — 부분 리포트로 발행하지 않는다")

    failures = [f"{name}: {checks[name].get('detail') or 'failed'}"
                for name in CHECK_NAMES if not checks[name]["passed"]]
    report = QAReport(job_id=job_id, run_id=run_id, passed=not failures,
                      checks=checks, failures=failures)
    return report.validate()


def apply_qa_result(job: VideoJob, report: QAReport) -> VideoJob:
    """QA 결과를 잡에 반영한다. 실패면 계약 간선으로 ``qa_failed`` 로 보낸다.

    허용되지 않은 전이는 ``StateError`` 로 죽고 잡 상태는 바뀌지 않는다 —
    QA 실패가 ``ready_to_publish`` 로 새는 경로는 존재하지 않는다.
    """
    report.validate()
    if report.job_id != job.job_id or report.run_id != job.run_id:
        raise vc.LineageError(
            f"QA 리포트 계보 불일치: {report.job_id}/{report.run_id} != "
            f"{job.job_id}/{job.run_id}")
    if not report.passed:
        job.transition(vc.STATE_QA_FAILED)      # 불법 간선이면 여기서 죽는다
    job.qa_report = report
    return job


def route_after_failure(job: VideoJob, attempt: int) -> str:
    """같은 프로바이더 재생성 예산을 소진했는지 판단하고 전이시킨다."""
    if job.state != vc.STATE_QA_FAILED:
        raise vc.StateError(
            f"route_after_failure 는 {vc.STATE_QA_FAILED} 에서만 호출한다: {job.state!r}")
    target = (vc.STATE_QUEUED if attempt < MAX_REGEN_ATTEMPTS
              else vc.STATE_DEAD_LETTER)
    job.transition(target)
    return target
