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
**이 파이프라인은 상품 동일성을 기계로 검증하지 않는다.** 지각 해시
라이브러리도, 임베딩 유사도 모델도 없다. 이 사실은 Task 10 이래 그대로다.

한때 ``product_identity_screen`` 이 원본 상품 사진과 샘플 프레임의 dHash
거리를 재 '거친 스크리닝'을 했다. 2026-08-29 첫 유료 실행에서 그 검사가
정상 영상을 거리 35(임계 16)로 반려했고, 원인은 영상이 아니라 비교였다:
레퍼런스는 **흰 배경 카탈로그 컷아웃**, 산출물은 **주방·손·자연광 속의
같은 상품**. dHash 는 프레임 전체 밝기 구조를 보므로 배경이 다르면 상품이
완벽해도 거리가 벌어진다. 즉 **어떤 정직한 인신 I2V 컷도 통과할 수 없는
검사**였다. 통과가 구조적으로 불가능한 게이트는 신호가 아니라 잡음이고,
잡음은 곧 무시되며, 무시되는 게이트는 없는 게이트다.

임계를 올려 통과시키지 **않았다** — 그건 검사를 고치는 게 아니라 통과를
위조하는 것이다. 대신 리포트가 사실을 말한다: ``machine_verified: False``,
``advisory_only: True``. dHash 거리는 계속 계산해 참고값으로 싣되 판정하지
않고, 게이트는 **사람 서명**(``identity_signoff``)이 진다. 서명은 산출물
sha256 에 묶여 재사용될 수 없고, 없으면 실패다.

시임(seam) — 무엇을 검사하는가
------------------------------
``frame_sampler`` / ``transcriber`` / ``audio_probe`` 는 전부 주입 가능한
호출 가능 객체다. 기본 구현은 OpenMontage 로 셸아웃한다 (재구현하지 않는다).

**검사 대상 산출물도 시임이다.** 베이스 영상 에셋에는 자막을 넣지 않고
(사용자 명령) 자막은 별도 후처리 패스에서 붙으므로, 산출물이 둘이다:
자막 없는 **클린 마스터**(``master_path``)와 자막을 입힌 **최종
납품물**(``video_path``). 각 검사 결과는 자기가 무엇을 봤는지
``artifact_under_test`` 로 밝히며, 밝히지 않으면 ``run_qa`` 가 크게 죽는다.
제휴 고지는 법적 의무라 **양쪽 모두**에서 확인한다.
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
import unicodedata
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

#: dHash(64bit) 해밍 거리 — **참고값으로만 보고한다.**
#:
#: 2026-08-29 첫 유료 실행에서 이 값이 게이트로 쓰였고, 정상 영상이
#: 거리 35(임계 16)로 반려됐다. 원인은 영상이 아니라 비교 자체다:
#: 레퍼런스는 **흰 배경 카탈로그 컷아웃**이고 산출물은 **주방·손·자연광
#: 속의 같은 상품**이다. dHash 는 프레임 전체의 밝기 구조를 보므로
#: 배경이 바뀌면 상품이 완벽히 보존돼 있어도 거리가 벌어진다. 즉
#: **어떤 정직한 인신(in-scene) I2V 컷도 이 비교를 통과할 수 없었다.**
#: 통과가 구조적으로 불가능한 검사는 신호가 아니라 잡음이고, 잡음은
#: 곧 무시되며, 무시되는 게이트는 없는 게이트다.
#:
#: 그래서 이 값은 이제 판정하지 않는다. 여전히 계산해 리포트에 싣되
#: (완전 흑백 반전·빈 화면 같은 붕괴는 운영자 눈에 띈다) 합격/불합격은
#: 아래 사람 서명이 진다. 임계를 올려 통과시키는 짓은 하지 않았다 —
#: 그건 검사를 고치는 게 아니라 통과를 위조하는 것이다.
MAX_DHASH_DISTANCE = 16

#: 승인 카피에서 벗어나 **용인되는** 잔여 발화의 최대 글자 수.
#: 1 = 전사 아티팩트 한 글자까지만 흡수한다. 한국어에서 3음절은 이미
#: 완결된 한 단어("무조건", "아니요", "확실히")이므로 절대 흡수하지 않는다.
MAX_UNAPPROVED_CHARS = 1

#: 승인 카피와 이만큼 이내로 어긋난 미검출 줄은 **전사 잡음 의심**으로
#: 표시한다. 표시일 뿐 통과가 아니다 — §전사 잡음 참조.
NEAR_MISS_MAX_EDITS = 2

#: 같은 프로바이더로 재생성을 허용하는 최대 횟수. 넘으면 dead letter.
MAX_REGEN_ATTEMPTS = 2

#: 이 파이프라인에 지각적 검증이 존재하는가 — 지금은 아니다.
PERCEPTUAL_VERIFICATION_AVAILABLE = False

#: 상품 동일성 사람 서명을 낼 수 있는 감사 소유자.
#: 현행: haneul-proof (서하늘) / 레거시: mungchi-proof.
#: 핸들을 리터럴로 흩뿌리지 않는다 (AGENTS.md §10).
IDENTITY_SIGNOFF_OWNERS: Tuple[str, ...] = ("haneul-proof", "mungchi-proof")

# ---------------------------------------------------------------------------
# 검사 대상 산출물 — 각 검사가 **무엇을 봤는지** 이름으로 말한다
# ---------------------------------------------------------------------------
#
# 사용자 명령: "기본 영상 에셋 자체는 자막을 넣지 말도록 해". 그래서 compose 가
# ① 자막 없는 **클린 마스터**와 ② 자막을 입힌 **최종 납품물** 두 산출물을 낸다
# (그 분리는 video_compose 를 소유한 다른 에이전트의 작업이다 — 여기서는
# 건드리지 않는다). QA 는 어느 쪽을 봤는지 리포트에 적어야 한다. 안 적으면
# "고지 확인됨"이 두 산출물 중 어느 것에 대한 말인지 아무도 모른다.
#
# 계약이 아직 문서로 오지 않았으므로(task-21b 리포트 부재) **검사 대상은
# 명시적 파라미터**다: ``run_qa(video_path=..., artifact_kind=...,
# master_caption=..., master_overlay_texts=...)``. 계약이 도착하면 호출부만
# 채우면 되고 검사 로직은 그대로다.

ARTIFACT_CLEAN_MASTER = "clean_master"
ARTIFACT_DELIVERABLE = "subtitled_deliverable"
ARTIFACT_KINDS: Tuple[str, ...] = (ARTIFACT_CLEAN_MASTER, ARTIFACT_DELIVERABLE)

#: 프레임 샘플 꼬리 경계 계산에 쓰는 기본 프레임레이트.
#: mp4 헤더 실측(measure_mp4)은 fps 를 돌려주지 않으므로 렌더 파이프라인의
#: 값을 쓴다. 낮게 잡을수록 꼬리 샘플이 안쪽으로 들어와 안전하다.
DEFAULT_SAMPLE_FPS = 30.0

IDENTITY_LIMITATIONS: Tuple[str, ...] = (
    "the machine layer is an AI VISION comparison (generated frame vs the "
    "real staged product photographs); it reads on-pack lettering and counts "
    "product openings, and it is a MANDATORY PRE-FILTER, not the gate",
    "the AI verdict is itself fallible and non-deterministic: it produced "
    "false positives during calibration, so it may only FAIL a run, never "
    "substitute for the recorded human sign-off that still gates release",
    "no perceptual hash library is installed and no embedding similarity "
    "model is available offline",
    "the reported dHash distance is ADVISORY ONLY: the reference is a "
    "white-background catalogue cutout while the footage is the product "
    "in-scene, so a large distance is expected for correct video and a small "
    "distance would not prove correctness either",
    "the AI check samples a bounded number of frames, so a defect visible "
    "only in an unsampled frame is not seen",
    "colour/option variant and capacity wording are only as verified as the "
    "reference photographs make them; a wrong-but-plausible variant that "
    "matches the references' wording would not be caught",
    "a passing result means a named human signed off on this exact artifact "
    "(sha256-bound) AND the AI pre-filter found no forged text or impossible "
    "geometry — not that identity is proven",
)

DEFAULT_OPENMONTAGE_ROOT = os.environ.get(
    "OPENMONTAGE_ROOT", os.path.expanduser("~/OpenMontage"))

DEFAULT_SEAM_TIMEOUT = 300

#: 전사 모델 크기 — **명시적으로 넘긴다.** 안 넘기면 OpenMontage 기본값이
#: 조용히 걸려서, 정확도가 QA 판정에 직결되는데도 이 레포에는 기록이 남지 않는다.
#:
#: 값을 `small` 로 올리려다 실측으로 뒤집었다. 이 맥에서 `say -v Yuna` 로
#: 만든 한국어 실음성 2건을 base/small/medium 으로 각각 전사한 결과:
#:
#:   원문 "하이트큐 성장 마인드셋 / 아이 키는 유전이 전부가 아닙니다"
#:     base   → "하이트큐 성장 마인드색, 아이키는 …"      (브랜드명 정확)
#:     small  → "하이트키오 성장 마인드색, 아이키는 …"    (브랜드명 붕괴)
#:   원문 "안녕하세요 … 아이 키 때문에 …"
#:     base   → "안녕하세요? 아이키 때문에 …"              (정확)
#:     small  → "안녕하세여, IP 때문에 …"                  ("아이 키" → "IP")
#:     medium → "안녕하세 요 아이케 때문에 …" (67초, base 2.9초의 약 23배)
#:
#: 즉 **모델을 키운다고 고유명사 오탐이 줄지 않는다.** 큰 모델은 낯선 한국어
#: 고유명사를 더 그럴듯한 흔한 단어로 '교정'해버린다. 지연만 최대 23배 늘고
#: 문제는 남는다. 그래서 base 를 유지한다 — 다만 이제 명시적 선택이다.
#:
#: **미해결로 남기는 것:** 전사 잡음이 승인 카피 대조에 오탐(멀쩡한 유료
#: 영상 반려)을 낼 위험은 여전하다. MAX_UNAPPROVED_CHARS 를 푸는 것은
#: 드리프트 감지력을 직접 깎으므로 하지 않는다(3음절 = 한국어 한 단어).
#: 실제 생성 영상의 TTS 음성으로 오탐률을 재기 전에는 어느 쪽이 옳은지
#: 결정할 데이터가 없다. 첫 유료 실행의 전사 로그를 보고 다시 판단한다.
TRANSCRIBER_MODEL_SIZE = os.environ.get("OPENMONTAGE_MODEL_SIZE", "base")


def _openmontage_python(root: str) -> str:
    """OpenMontage 자기 venv 의 인터프리터 경로 — 없으면 fail closed.

    호출자(heightcue) venv 는 의도적으로 `requests` 뿐이다. 무거운 ML
    의존성은 OpenMontage 쪽에만 산다. 그래서 sys.executable 로 폴백하면
    설치돼 있는 전사기가 영원히 '없다'고 나온다 — 조용한 폴백 금지.
    """
    exe = os.path.join(root, ".venv", "bin", "python")
    if not os.path.isfile(exe):
        raise CheckUnavailable(
            f"OpenMontage 인터프리터가 없다: {exe} — 이 파이썬으로만 전사/프레임 "
            "툴이 돈다 (호출자 venv 로 대신 돌리지 않는다: 조용한 폴백은 "
            "거짓 초록을 만든다). fail closed 로 처리한다")
    return exe


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
    exe = _openmontage_python(root)
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
            [exe, "-c", script], input=json.dumps(inputs),
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
    """OpenMontage transcriber 로 오디오를 전사한다.

    ``model_size`` 를 명시한다 — 안 넘기면 OpenMontage 기본값 ``base`` 가
    걸리고, base 는 한국어 고유명사를 뭉개 승인 카피 대조에 오탐을 만든다.
    """
    data = _openmontage_call("transcriber", "Transcriber",
                             {"input_path": video_path,
                              "model_size": TRANSCRIBER_MODEL_SIZE})
    segments = data.get("segments") or []
    text = " ".join(str(s.get("text", "")) for s in segments
                    if isinstance(s, dict)).strip()
    if not text:
        text = str(data.get("text") or "").strip()
    return {"text": text, "language": data.get("language") or ""}


#: 오디오 프로브가 돌려줄 수 있는 모양들. **키 이름은 프로브가 정한다.**
#: 2026-08-29 첫 유료 실행에서 이 검사가 실패한 진짜 이유가 여기였다:
#: OpenMontage AudioEnergy 는 초당 ``energy_profile`` (LUFS) 를 돌려주는데
#: 검사는 ``rms_dbfs`` 를 읽고 있었다. 없는 키를 읽으면 KeyError 가 나고,
#: 그것이 '무음'과 구별되지 않는 실패 문구로 보고돼 영상을 의심하게 만들었다.
#: 이제 모양을 명시적으로 판별하고, **모르는 모양이면 무음이 아니라
#: '해석 불가'로 크게 실패한다** (실제 키 목록을 적어 진단 가능하게).
AUDIO_SHAPE_RMS = "rms_dbfs"
AUDIO_SHAPE_ENERGY_PROFILE = "energy_profile"


def _interpret_audio_levels(levels: Any) -> Dict[str, Any]:
    """프로브 출력을 {shape, level_db, ...} 로 정규화한다.

    모양을 알아보지 못하면 ``CheckUnavailable`` — 조용히 0/무음 취급하지
    않는다. 못 읽은 값을 무음으로 적으면 리포트가 운영자를 속인다.
    """
    if not isinstance(levels, dict):
        raise CheckUnavailable(
            f"오디오 프로브 출력이 dict 가 아니다: {type(levels).__name__}")

    if AUDIO_SHAPE_RMS in levels:
        try:
            rms = float(levels[AUDIO_SHAPE_RMS])
        except (TypeError, ValueError) as exc:
            raise CheckUnavailable(
                f"rms_dbfs 를 수로 읽을 수 없다: {levels[AUDIO_SHAPE_RMS]!r} ({exc})"
            ) from exc
        peak = levels.get("peak_dbfs", rms)
        try:
            peak = float(peak)
        except (TypeError, ValueError):
            peak = rms
        return {"shape": AUDIO_SHAPE_RMS, "level_db": rms, "peak_db": peak,
                "unit": "dBFS", "basis": "probe-reported mean RMS"}

    if AUDIO_SHAPE_ENERGY_PROFILE in levels:
        profile = levels[AUDIO_SHAPE_ENERGY_PROFILE]
        if not isinstance(profile, list) or not profile:
            raise CheckUnavailable(
                "energy_profile 이 비어 있다 — 잰 구간이 없으면 소리가 있는지 "
                "판단할 수 없다 (무음과 구별되지 않는다)")
        loudness: List[float] = []
        for seg in profile:
            if not isinstance(seg, dict) or "loudness_lufs" not in seg:
                raise CheckUnavailable(
                    f"energy_profile 항목에 loudness_lufs 가 없다: {seg!r}")
            try:
                loudness.append(float(seg["loudness_lufs"]))
            except (TypeError, ValueError) as exc:
                raise CheckUnavailable(
                    f"loudness_lufs 를 수로 읽을 수 없다: {seg!r} ({exc})") from exc
        # 가장 큰 초를 판정 기준으로 삼는다: 가장 시끄러운 순간조차 무음
        # 문턱 아래면 그 영상은 실제로 소리를 내지 않는다. 평균을 쓰면
        # 앞뒤 무음 여백이 말하는 구간을 희석해 오탐을 낸다.
        peak = max(loudness)
        active = [v for v in loudness if v > -120.0]
        mean = sum(active) / len(active) if active else -120.0
        return {"shape": AUDIO_SHAPE_ENERGY_PROFILE, "level_db": peak,
                "peak_db": peak, "mean_db": round(mean, 2), "unit": "LUFS",
                "seconds_measured": len(loudness),
                "basis": "loudest 1s window of the probe's energy profile"}

    raise CheckUnavailable(
        "오디오 프로브 출력의 모양을 알아볼 수 없다 (기대: "
        f"{AUDIO_SHAPE_RMS} 또는 {AUDIO_SHAPE_ENERGY_PROFILE}; 실제 키: "
        f"{sorted(levels)}) — 해석하지 못한 출력을 무음으로도 정상으로도 "
        "적지 않는다")


def default_audio_probe(video_path: str) -> Dict[str, Any]:
    """OpenMontage audio_energy 로 실제 오디오 레벨을 잰다.

    출력 모양을 여기서 판정하지 않는다 — 검사 쪽 ``_interpret_audio_levels``
    가 정본이다. 프로브는 원본 data 를 그대로 넘긴다 (두 벌 관리 금지).
    """
    return _openmontage_call("audio_energy", "AudioEnergy",
                             {"input_path": video_path})


# ---------------------------------------------------------------------------
# 텍스트 정규화 / 승인 카피 대조
# ---------------------------------------------------------------------------

#: 레거시 호환용 — 코드포인트 화이트리스트는 **더 이상 대조에 쓰지 않는다.**
#: 이 정규식은 [0-9a-z가-힣] 밖의 모든 글자를 지웠기 때문에, 주입된 중국어/
#: 일본어/키릴 문장 한 줄이 통째로 빈 문자열이 되어 잔여 0자로 통과했다.
_NORM_STRIP = re.compile(r"[^0-9a-z\uac00-\ud7a3]+")

#: 대조에서 버리는 유니코드 카테고리 — 공백(Z), 제어(C), 문장부호(P), 기호(S).
#: 문자(L)·숫자(N)·결합기호(M)는 **스크립트와 무관하게 전부 남긴다.**
_DROPPED_UNICODE_CATEGORIES = ("Z", "C", "P", "S")


#: 말로 읽힌 숫자 → 숫자 표기. 승인 카피는 ``600 IU`` / ``age 1+`` 처럼
#: **쓰는** 형태인데 성우는 ``six hundred`` / ``one plus`` 로 **읽는다**.
#: 양쪽을 같은 표준형으로 옮겨 **정확히** 비교하기 위한 표다.
#:
#: 이것은 근사 매칭이 아니다. 편집거리로 봐주는 것이 아니라 표기 형태만
#: 통일하므로, 값이 다르면 (``600``→``60``, ``one``→``two``) 여전히 걸린다.
#: 영양 라벨에서 숫자 하나가 틀리는 것은 잡음이 아니라 사실 오류다.
_SPOKEN_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12",
}
#: ``+`` 는 지우지 않는다 — ``1+`` 와 ``1`` 은 다른 말이다. 기호를
#: 낱말로 펴서 발화형(``one plus``)과 만나게 할 뿐이다.
_SPOKEN_SYMBOL_WORDS = {"+": " plus "}

_MULTIPLIER_WORDS = {"hundred": 100, "thousand": 1000}

_WORD_RE = re.compile(r"[a-z]+|\d+|\S")


def canonicalise_spoken_numbers(text: Any) -> str:
    """숫자와 ``+`` 의 **표기 형태만** 통일한다. 값은 절대 바꾸지 않는다.

    2026-08-29 두 번째 유료 실행에서 승인 카피 ``age 1+`` 을 성우가 정확히
    읽었는데, 전사는 ``age one plus`` 라서 멀쩡한 영상이 반려됐다. 모델은
    옳게 말했고 비교기가 표기를 몰랐을 뿐이다.

    **드리프트 탐지를 약화하지 않는다**는 것이 이 함수의 유일한 설계
    제약이다. 그래서 하는 일은 딱 둘뿐이다.

    * 숫자 낱말을 숫자로 옮긴다 (``six hundred`` → ``600``). 값이 다르면
      다른 문자열로 남으므로 ``600 IU`` → ``60 IU`` 는 여전히 실패한다.
    * ``+`` 를 ``plus`` 로 편다. 지우는 것이 아니라 펴는 것이므로
      ``1+`` 와 ``1`` 은 여전히 다르다.

    한국어 문자열은 손대지 않는다 — 1음절 치환이 의미를 뒤집는 언어에
    어떤 관용도 넣지 않는다는 기존 결정은 그대로다.
    """
    s = str(text or "").lower()
    for sym, word in _SPOKEN_SYMBOL_WORDS.items():
        s = s.replace(sym, word)

    tokens = _WORD_RE.findall(s)
    out: List[str] = []
    pending: Optional[int] = None
    for tok in tokens:
        if tok in _SPOKEN_NUMBER_WORDS:
            value = int(_SPOKEN_NUMBER_WORDS[tok])
            pending = value if pending is None else pending + value
            continue
        if tok in _MULTIPLIER_WORDS and pending is not None:
            pending *= _MULTIPLIER_WORDS[tok]
            continue
        if pending is not None:
            out.append(str(pending))
            pending = None
        out.append(tok)
    if pending is not None:
        out.append(str(pending))
    return " ".join(out)


def normalize_speech(text: Any) -> str:
    """대조용 정규화: 공백·문장부호·대소문자만 없앤다. 내용은 바꾸지 않는다.

    **스크립트 중립이다.** 한글·라틴 밖의 글자(한자, 가나, 키릴, 악센트 라틴)도
    그대로 남긴다 — 지워버리면 주입된 외국어 문장이 잔여 0자가 되어 조용히
    통과한다. 버리는 것은 유니코드 카테고리 Z/C/P/S 뿐이다.

    문장부호를 버리기 **전에** 숫자 표기를 표준형으로 옮긴다
    (:func:`canonicalise_spoken_numbers`). ``+`` 는 카테고리 S 라서 그냥
    버려지면 ``1+`` 이 ``1`` 이 되어 버리기 때문이다 — 순서가 중요하다.
    """
    out = []
    for ch in canonicalise_spoken_numbers(text):
        if unicodedata.category(ch)[0] in _DROPPED_UNICODE_CATEGORIES:
            continue
        out.append(ch)
    return "".join(out)


def approved_voice_lines(storyboard: Dict[str, Any]) -> List[str]:
    """전사본과 대조할 승인 발화만 회수한다.

    Ken-Burns 정지 컷은 ``voice_line`` 을 갖지 않으므로 (스토리보드가
    강제한다) 여기서 자연히 빠진다. 그 컷 구간은 소리가 나지 않는 것이
    **의도된 설계**이지 누락이 아니다 — 승인된 대사는 전부 모션 컷에 실려
    있고, 이 함수가 돌려주는 목록이 곧 실제로 들려야 할 전부다.
    """
    lines = []
    for cut in storyboard.get("cuts") or []:
        line = str((cut or {}).get("voice_line") or "").strip()
        if line:
            lines.append(line)
    return lines


def cut_kind_summary(storyboard: Dict[str, Any]) -> Dict[str, Any]:
    """컷 종류 내역 — 어느 컷이 유료 생성물이고 어느 컷이 원본 사진인가.

    라벨 진정성 감사가 읽는 값이다. ``ken_burns`` 로 표시된 컷의 라벨은
    생성물이 아니라 촬영 원본이므로 위조 자체가 성립하지 않는다.
    """
    rows = []
    for cut in storyboard.get("cuts") or []:
        cut = cut or {}
        kind = str(cut.get("cut_kind") or vs.CUT_KIND_MOTION)
        rows.append({
            "cut_index": int(cut.get("index") or 0),
            "cut_kind": kind,
            "generated": kind == vs.CUT_KIND_MOTION,
            "paid": kind == vs.CUT_KIND_MOTION,
            "has_speech": bool(str(cut.get("voice_line") or "").strip()),
            "label_provenance": ("generated_by_i2v_model"
                                 if kind == vs.CUT_KIND_MOTION
                                 else "original_photograph_pixels"),
        })
    return {
        "cuts": rows,
        "paid_motion_cuts": sum(1 for r in rows if r["paid"]),
        "still_cuts": sum(1 for r in rows if not r["paid"]),
        "max_paid_motion_cuts": vs.MAX_PAID_MOTION_CUTS,
    }



def find_forbidden_claims(text: str) -> List[str]:
    """video_storyboard 의 금지 패턴을 그대로 재사용한다 (두 벌 관리 금지)."""
    hits = []
    for pattern in vs.forbidden_patterns():
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
                              expected_seconds: int,
                              artifact_kind: str = ARTIFACT_DELIVERABLE
                              ) -> Dict[str, Any]:
    """길이·세로·768P-class·H.264/AAC 를 **실측 mp4 박스로만** 판정한다."""
    base = {"artifact_under_test": artifact_kind}
    try:
        m = measure_mp4(video_path)
    except Exception as exc:                     # noqa: BLE001 — fail closed
        return dict(base, **_fail(
            f"실측 실패 — 산출물을 잴 수 없으면 통과시키지 않는다: {exc}",
            measured=None))

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
        return dict(base, **_fail("; ".join(problems), measured=m))
    return dict(base, **_ok(
        f"실측 {width}x{height} {measured}s "
        f"{m['video_codec_fourcc']}/{m['audio_codec_fourcc']}",
        measured=m))


def check_audio_signal(video_path: str,
                       audio_probe: Optional[Callable],
                       artifact_kind: str = ARTIFACT_DELIVERABLE
                       ) -> Dict[str, Any]:
    """오디오가 실제로 소리를 내는가. 못 재면 실패.

    프로브의 출력 **모양**은 프로브가 정한다 — 여기서 키 이름을 가정하지
    않는다. 알아보지 못한 모양은 무음이 아니라 '해석 불가'로 실패한다.
    """
    base = {"artifact_under_test": artifact_kind}
    probe = audio_probe or default_audio_probe
    try:
        levels = probe(video_path)
    except Exception as exc:                     # noqa: BLE001 — fail closed
        return dict(base, **_fail(
            f"오디오 레벨을 잴 수 없다 ({type(exc).__name__}: {exc}) — "
            "돌지 못한 검사는 통과가 아니다", levels=None))
    try:
        reading = _interpret_audio_levels(levels)
    except CheckUnavailable as exc:
        return dict(base, **_fail(str(exc), levels=levels))

    level = reading["level_db"]
    if level <= SILENCE_RMS_DBFS:
        return dict(base, **_fail(
            f"무음이다: {level:.1f} {reading['unit']} <= {SILENCE_RMS_DBFS} "
            f"({reading['basis']})", levels=levels, **reading))
    return dict(base, **_ok(
        f"{level:.1f} {reading['unit']} ({reading['basis']})",
        levels=levels, **reading))


def sample_timestamps(duration: float, cut_count: int,
                      fps: float = DEFAULT_SAMPLE_FPS) -> List[float]:
    """첫/중간/전환/마지막 프레임 타임스탬프 (결정적).

    꼬리 경계 — 2026-08-29 첫 유료 실행이 여기서 깨졌다. 3장 중 2장만
    돌아와 검사가 규칙대로 '부분 샘플'로 반려했는데, 원인은 영상이 아니라
    이 함수의 고정 오프셋이었다.

    옛 코드는 ``duration - 0.05`` 를 마지막 샘플로 썼다. 이 값은 두 가지를
    모른 채 정해진 상수다.

    1. **프레임 간격을 모른다.** 24fps 에서 한 프레임은 0.0417초다.
       0.05초 여유는 한 프레임 하고도 조금밖에 안 되며, 프레임 경계
       바로 위에 떨어지면 그 시각에 표시되는 프레임이 없다.
    2. **헤더 길이가 마지막 프레임 PTS 보다 최대 한 프레임 길다.** mp4
       muxer 는 마지막 프레임의 *표시 구간 끝*을 길이로 적는다. 즉 실제
       마지막 프레임의 시작은 ``duration - 1/fps`` 이고, 그보다 뒤를
       요청하면 디코더가 돌려줄 프레임이 없다.

    그래서 꼬리 샘플은 **마지막에서 두 번째 프레임의 시작**
    (``duration - 2/fps``) 으로 잡는다. 한 프레임 분의 여유를 두면 위 두
    가지 오차가 겹쳐도 반드시 실재하는 프레임을 가리킨다. 그러면서도
    영상의 마지막 0.1초 안쪽이라 '끝을 봤다'는 성질은 잃지 않는다.

    fail-closed 규칙(부분 샘플 = 실패)은 그대로다 — 경계를 고쳤을 뿐
    못 받은 프레임을 봐주지 않는다.
    """
    stamps = {0.0}
    for k in range(1, max(1, cut_count)):
        boundary = float(CUT_DURATION_SECONDS * k)
        if boundary < duration:
            stamps.add(round(boundary, 3))
    for k in range(max(1, cut_count)):
        mid = CUT_DURATION_SECONDS * k + CUT_DURATION_SECONDS / 2.0
        if mid < duration:
            stamps.add(round(mid, 3))
    frame_step = 1.0 / float(fps or DEFAULT_SAMPLE_FPS)
    last = max(0.0, duration - 2.0 * frame_step)
    # 내림으로 자른다 — 반올림하면 다시 프레임 경계 뒤로 넘어갈 수 있다.
    stamps.add(int(last * 1000) / 1000.0)
    return sorted(stamps)


def frame_sampling_duration(measured: Dict[str, Any]) -> float:
    """프레임을 뽑을 때 기준으로 삼을 길이 — **비디오 트랙**의 길이다.

    2026-08-29 두 번째 유료 실행: 7장을 요청했는데 6장만 왔다. 오프셋도
    샘플러도 정상이었고, 틀린 것은 **어느 길이를 넘겼느냐**였다.

    컨테이너 길이(mvhd)는 트랙 중 **가장 긴 것**을 따른다. 그 산출물은
    오디오 15.062초 / 비디오 15.000초였고, 컨테이너 길이로 계산한 꼬리
    ``15.062 - 2/30 = 14.995`` 는 마지막 실재 비디오 프레임(14.967)보다
    뒤였다. :func:`sample_timestamps` 가 확보한 2프레임 여유가 0.062초짜리
    오디오 꼬리에 통째로 먹힌 것이다. 오디오가 길수록 더 어긋나므로 상수를
    키우는 것은 해법이 아니다 — 애초에 다른 트랙의 길이를 본 것이 문제다.

    ``coded_duration_seconds`` 는 비디오 트랙의 mdhd/stts 에서 실측된
    값이다(``video_compose.probe_mp4``). 그것이 있으면 그것을 쓰고, 없거나
    말이 안 되는 값이면 컨테이너 길이로 물러난다 — 검사를 건너뛰지는 않는다.
    """
    container = float(measured.get("duration_seconds") or 0.0)
    coded = measured.get("coded_duration_seconds")
    try:
        coded = float(coded)
    except (TypeError, ValueError):
        return container
    if coded <= 0:
        return container
    return coded


def check_frames(frames: List[Dict[str, Any]], expected_count: int,
                 error: Optional[str] = None,
                 measured_duration: Optional[float] = None,
                 artifact_kind: str = ARTIFACT_CLEAN_MASTER) -> Dict[str, Any]:
    """블랙 프레임·정지(중복) 프레임 검출. 샘플링이 안 됐으면 실패.

    **검사 대상은 클린 마스터다.** 자막을 입힌 납품물에서 재면 자막 픽셀이
    프레임 간 차이를 만들어 정지 영상 검출을 무력화한다 (자막만 바뀌어도
    '다른 프레임'이 된다). 움직임은 베이스 에셋에서 판정해야 한다.

    추가로 **헤더 길이 vs 실제 미디어 길이 교차검증**을 한다. 컨테이너 검사의
    ``expected_seconds`` 는 선언된 스토리보드에서 온 값이라 독립 증거가 아니다.
    샘플러가 돌려준 실제 프레임 위치(``actual_timestamp``)는 페이로드를 실제로
    읽은 유일한 증거이므로, 헤더가 10초라고 주장하는데 마지막 진짜 프레임이
    7초라면 여기서 잡는다.
    """
    base = {"artifact_under_test": artifact_kind}
    if error:
        return dict(base, **_fail(
            f"프레임을 샘플링할 수 없다 ({error}) — "
            "돌지 못한 검사는 통과가 아니다", sampled=0))
    if len(frames) != expected_count:
        return dict(base, **_fail(
            f"프레임 {expected_count}장을 요청했는데 {len(frames)}장만 "
            "돌아왔다 — 부분 샘플로 판정하지 않는다",
            sampled=len(frames), expected=expected_count))

    stats, problems = [], []
    for frame in frames:
        try:
            rows, _w, _h = decode_png_gray(frame["path"])
        except CheckUnavailable as exc:
            return dict(base, **_fail(f"프레임 디코딩 실패: {exc}",
                                      sampled=len(frames)))
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

    # 독립 길이 교차검증 — 샘플러가 실제 위치를 보고할 때만 판정한다.
    actuals = [float(f["actual_timestamp"]) for f in frames
               if f.get("actual_timestamp") is not None]
    last_actual = max(actuals) if actuals else None
    if last_actual is not None and measured_duration is not None:
        drift = float(measured_duration) - last_actual
        if drift > DURATION_TOLERANCE_SECONDS:
            problems.append(
                f"헤더가 주장하는 길이 {measured_duration}s 가 실제로 샘플된 "
                f"마지막 프레임 {last_actual}s 보다 {drift:.2f}s 길다 — "
                "선언 길이가 아니라 실제 미디어를 믿는다")

    if problems:
        return dict(base, **_fail(
            "; ".join(problems), sampled=len(frames), frames=summary,
            last_actual_timestamp=last_actual,
            measured_duration=measured_duration))
    return dict(base, **_ok(
        f"{len(frames)}장 검사: 블랙 없음, 중복 없음",
        sampled=len(frames), frames=summary,
        last_actual_timestamp=last_actual,
        measured_duration=measured_duration))


def check_product_identity_screen(frames: List[Dict[str, Any]],
                                  product_image_path: Optional[str],
                                  error: Optional[str] = None,
                                  identity_signoff: Optional[Dict[str, Any]] = None,
                                  fidelity_verdict: Optional[Dict[str, Any]] = None,
                                  artifact_path: Optional[str] = None,
                                  artifact_kind: str = ARTIFACT_CLEAN_MASTER
                                  ) -> Dict[str, Any]:
    """상품 동일성은 **기계가 검증하지 않는다.** 사람 서명이 게이트다.

    왜 바꿨나 (2026-08-29, 첫 유료 실행)
    ------------------------------------
    이 검사는 원래 샘플 프레임의 dHash 를 원본 상품 사진과 비교해
    거리 16 이하를 요구했다. 첫 유료 영상이 거리 35 로 반려됐고, 확인해
    보니 영상은 멀쩡했다. 비교가 틀렸다: 레퍼런스는 흰 배경 카탈로그
    컷아웃이고 산출물은 주방·손·자연광 속의 같은 상품이다. dHash 는
    프레임 전체 밝기 구조를 보므로 배경이 다르면 상품이 완벽해도 거리가
    벌어진다. **통과가 구조적으로 불가능한 검사였다.**

    선택지는 셋이었다.
    ① 임계를 35 이상으로 올린다 → 그러면 아무거나 통과한다. 통과를
      위조하는 것이지 검사를 고치는 게 아니다.
    ② 배경에 강인한 진짜 지각 비교를 넣는다 → 이 파이프라인에 지각 해시
      라이브러리도 임베딩 모델도 없다. 지금 정직하게 만들 수 없다.
    ③ **기계 검증이 없다는 사실을 리포트에 명시하고 사람 서명을 요구한다.**

    ③ 을 택했다. dHash 거리는 계속 계산해 ``advisory_*`` 로 싣는다 —
    흑백 반전이나 빈 화면 같은 붕괴는 운영자 눈에 띄고, 나중에 진짜
    지각 검증이 들어오면 비교할 기준선이 된다. 하지만 **판정하지 않는다.**

    게이트는 서명이다. 서명은 반드시
    ``{signed_off_by, signed_off_at, artifact_sha256}`` 를 갖고,
    ``artifact_sha256`` 은 **지금 검사 중인 바로 그 파일**의 해시여야 한다
    (다른 영상에 대한 승인을 재사용하지 못하게). 서명자는
    ``IDENTITY_SIGNOFF_OWNERS`` 안에 있어야 하며 구 핸들도 허용한다
    (AGENTS.md §10 — 개명 소급 무효화 금지).

    서명이 없으면 **실패**다. 우회로는 없다.
    """
    base: Dict[str, Any] = {
        "artifact_under_test": artifact_kind,
        "establishes_identity": False,
        "machine_verified": True,
        "advisory_only": True,
        "limitations": list(IDENTITY_LIMITATIONS),
        "method": "AI vision fidelity pre-filter (frame vs real product "
                  "photos; mandatory) + recorded human sign-off (gate) + "
                  "advisory dHash distance (reported, never judged)",
        "perceptual_verification_available": PERCEPTUAL_VERIFICATION_AVAILABLE,
        "requires_human_signoff": True,
    }

    # --- 참고값: 돌 수 있으면 재고, 못 재면 그 사실을 적는다 (판정은 안 한다)
    advisory_error: Optional[str] = None
    distances: List[Dict[str, Any]] = []
    if error:
        advisory_error = f"프레임 샘플링 실패: {error}"
    elif not frames:
        advisory_error = "샘플 프레임이 없다"
    elif not product_image_path:
        advisory_error = "원본 상품 이미지 경로가 없다"
    else:
        try:
            source_rows, _w, _h = decode_png_gray(product_image_path)
            source_hash = dhash(source_rows)
            base["source_sha256"] = _sha256_file(product_image_path)
            for frame in frames:
                rows, _fw, _fh = decode_png_gray(frame["path"])
                distances.append({"timestamp": frame.get("timestamp"),
                                  "dhash_distance": hamming(source_hash,
                                                            dhash(rows))})
        except CheckUnavailable as exc:
            # **참고값이 못 나오는 것과 검사가 못 도는 것은 다르다.**
            # 스테이징 상품 자산은 전부 JPEG 인데 이 디코더는 PNG 전용이라,
            # 예전에는 여기서 항상 예외가 나 advisory_error 가 채워졌고 아래
            # fail closed 에 걸려 **product_identity 가 구조적으로 절대 통과할
            # 수 없었다**. 그런데 dHash 는 애초에 판정에 쓰지 않는 참고값이다.
            # 참고값 하나 못 쟀다고 게이트를 닫는 것은 fail closed 가 아니라
            # 그냥 고장이다. 진짜 증거는 아래 AI 충실도 판정과 사람 서명이다.
            advisory_error = str(exc)
            distances = []

    base["advisory_distances"] = distances
    base["advisory_best_distance"] = (min(d["dhash_distance"] for d in distances)
                                      if distances else None)
    base["advisory_reference_threshold"] = MAX_DHASH_DISTANCE
    base["advisory_error"] = advisory_error
    base["advisory_note"] = (
        "이 거리는 판정에 쓰이지 않는다. 흰 배경 컷아웃 vs 인신 촬영이라 "
        "정상 영상에서도 크게 나오는 값이다.")

    # --- fail closed: 참고값조차 기록하지 못했으면 통과시키지 않는다.
    # 거리는 판정에 쓰지 않지만, 사람 서명은 **증거와 함께** 남아야 한다.
    # 프레임도 원본 이미지도 없이 서명만 있는 리포트는 무엇을 보고 승인했는지
    # 되짚을 수 없다 — 그래서 여기서 닫는다 (서명 우회로가 아니다).
    # --- fail closed: 볼 프레임조차 없으면 통과시키지 않는다.
    # 사람 서명은 **증거와 함께** 남아야 한다. 프레임도 없이 서명만 있는
    # 리포트는 무엇을 보고 승인했는지 되짚을 수 없다.
    #
    # 단, dHash 참고값을 못 쟀다는 것만으로는 닫지 않는다 (위 주석 참조) —
    # 그건 판정에 쓰지 않는 값이고, 스테이징 자산이 JPEG 라 늘 실패한다.
    if error or not frames:
        return dict(base, **_fail(
            f"동일성 판단에 필요한 증거를 기록하지 못했다 "
            f"({advisory_error or error}) — 돌지 못한 검사는 통과가 아니다. "
            "사람 서명은 참고 프레임과 함께 남아야 한다"))

    # --- 1단 게이트: AI 비전 충실도 (사람에게 묻기 **전에** 통과해야 한다)
    #
    # 왜 사람 서명을 대체하지 않고 앞에 두는가
    # -----------------------------------------
    # 비전 모델은 오답을 낸다 — 캘리브레이션에서 실제로 확신 0.98 로 결함을
    # 신고했다. 그래서 이것은 **사람의 대체재가 아니라 사람의 필터**다.
    # 기계가 잡을 수 있는 것(위조 글자·불가능 형상)은 기계가 먼저 잡아
    # 사람이 그 쓰레기에 서명하는 일이 없게 하고, 기계가 못 잡는 것은
    # 여전히 사람 눈이 진다. 두 방향 모두 실패 방향으로만 작동한다:
    # AI 가 통과시켜도 서명이 없으면 실패고, AI 가 막으면 서명이 있어도
    # 실패다. 어느 쪽도 다른 쪽을 무마할 수 없다.
    #
    # 사람 서명을 **제거하지 않은** 이유: 이 검사의 오탐률은 아래 캘리브레이션
    # 기준으로 0 이 아니며, 무엇보다 이 검사가 볼 수 없는 것들(옵션·용량 변형,
    # 브랜드 정체성의 미묘한 훼손)이 남아 있다. 검증되지 않은 모델 판정으로
    # 사람 게이트를 걷어내는 것은 검사를 강화하는 게 아니라 책임을 없애는 것이다.
    base["ai_fidelity"] = fidelity_verdict
    if not isinstance(fidelity_verdict, dict) or not fidelity_verdict:
        return dict(base, **_fail(
            "AI 상품 충실도 판정(fidelity_verdict)이 없다 — 기계가 잡을 수 "
            "있는 결함을 걸러내기 전에는 사람에게 서명을 요구하지 않는다. "
            "돌지 못한 검사는 통과가 아니다"))
    if not fidelity_verdict.get("passed"):
        return dict(base, **_fail(
            "AI 상품 충실도 검사가 생성 프레임과 실제 상품 사진의 불일치를 "
            f"보고했다: {fidelity_verdict.get('reason')} — 사람 서명 이전에 "
            "실패한다 (사람에게 결함 있는 프레임을 승인시키지 않는다)",
            ai_fidelity_reason=fidelity_verdict.get("reason")))
    base["ai_fidelity_passed"] = True
    base["machine_prefilter"] = "ai_vision_product_fidelity"

    # --- 2단 게이트: 사람 서명 (여전히 필수)
    if not isinstance(identity_signoff, dict) or not identity_signoff:
        return dict(base, **_fail(
            "상품 동일성은 이 파이프라인에서 기계로 검증되지 않는다. "
            "사람 서명(identity_signoff)이 없으므로 통과시키지 않는다 — "
            f"필요: signed_off_by({'/'.join(IDENTITY_SIGNOFF_OWNERS)}), "
            "signed_off_at, artifact_sha256"))

    owner = str(identity_signoff.get("signed_off_by") or "")
    if owner not in IDENTITY_SIGNOFF_OWNERS:
        return dict(base, **_fail(
            f"동일성 서명자 {owner!r} 가 승인된 감사 소유자가 아니다 "
            f"{IDENTITY_SIGNOFF_OWNERS}", signed_off_by=owner))
    if not str(identity_signoff.get("signed_off_at") or "").strip():
        return dict(base, **_fail(
            "동일성 서명에 signed_off_at 이 없다 — 언제 본 승인인지 알 수 없다",
            signed_off_by=owner))

    claimed = str(identity_signoff.get("artifact_sha256") or "").lower()
    if not claimed:
        return dict(base, **_fail(
            "동일성 서명에 artifact_sha256 이 없다 — 어느 산출물에 대한 "
            "승인인지 묶이지 않으면 다른 영상에 재사용된다", signed_off_by=owner))
    actual = _sha256_file(artifact_path) if artifact_path else ""
    if not actual:
        return dict(base, **_fail(
            f"검사 대상 산출물의 해시를 잴 수 없다 ({artifact_path!r}) — "
            "서명을 대조할 수 없으면 통과가 아니다", signed_off_by=owner))
    if claimed != actual:
        return dict(base, **_fail(
            f"동일성 서명이 다른 산출물에 대한 것이다: 서명 {claimed[:16]}… != "
            f"검사 대상 {actual[:16]}… — 승인 재사용을 허용하지 않는다",
            signed_off_by=owner, signed_artifact_sha256=claimed,
            artifact_sha256=actual))

    return dict(base, **_ok(
        f"{owner} 가 이 산출물({actual[:16]}…)에 대해 상품 동일성을 사람 눈으로 "
        f"확인했다 ({identity_signoff.get('signed_off_at')}). 기계 검증은 "
        f"없다 — 참고 dHash 거리 {base['advisory_best_distance']} "
        "(판정에 쓰이지 않음). limitations 참조",
        signed_off_by=owner, artifact_sha256=actual,
        signed_off_at=identity_signoff.get("signed_off_at"),
        signoff_note=identity_signoff.get("note")))


def _edit_distance(a: str, b: str, cap: int) -> int:
    """레벤슈타인 거리 (cap 초과는 cap+1 로 잘라 반환 — 진단용이라 충분).

    **판정에 쓰지 않는다.** 실패한 줄이 '전사 잡음스러운가'를 표시하는
    용도뿐이다. 판정에 쓰는 순간 드리프트 감지력이 깎인다.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
        if min(prev) > cap:
            return cap + 1
    return prev[-1]


def _near_misses(normalized: str, missing: Sequence[str]) -> List[Dict[str, Any]]:
    """검출 실패한 승인 줄 중 전사 잡음으로 설명되는 것들을 찾아 표시한다."""
    out: List[Dict[str, Any]] = []
    for line in missing:
        norm = normalize_speech(line)
        if not norm:
            continue
        best = (NEAR_MISS_MAX_EDITS + 1, "")
        window = len(norm)
        for start in range(max(1, len(normalized) - window + 1
                               + NEAR_MISS_MAX_EDITS)):
            for width in range(max(1, window - NEAR_MISS_MAX_EDITS),
                               window + NEAR_MISS_MAX_EDITS + 1):
                chunk = normalized[start:start + width]
                if not chunk:
                    continue
                d = _edit_distance(norm, chunk, NEAR_MISS_MAX_EDITS)
                if d < best[0]:
                    best = (d, chunk)
        if best[0] <= NEAR_MISS_MAX_EDITS:
            out.append({"approved_line": line, "heard_as": best[1],
                        "edit_distance": best[0]})
    return out


def check_spoken_content(transcript: Optional[str],
                         approved_lines: Sequence[str],
                         error: Optional[str] = None,
                         artifact_kind: str = ARTIFACT_CLEAN_MASTER
                         ) -> Dict[str, Any]:
    """실제 발화가 승인된 카피와 같은가. 전사가 안 되면 실패.

    **검사 대상은 클린 마스터의 오디오다.** 자막 패스는 오디오를 건드리지
    않으므로 마스터에서 재는 게 맞고, 자막 렌더링 실패가 발화 판정을
    오염시키지도 않는다.

    전사 잡음에 대한 결정 (2026-08-29)
    ----------------------------------
    faster-whisper base 가 "마인드셋"을 "마인드색"으로 적는 건 실측된
    사실이고, 그래서 **멀쩡한 유료 영상이 여기서 반려될 수 있다.**
    그럼에도 근사 매칭으로 흡수하지 **않는다**:

    한국어는 1음절 치환이 의미를 뒤집는다. "잘 커요"/"안 커요",
    "됩니다"/"안됩니다", "있어요"/"없어요". 편집거리 1~2 를 통과시키면
    이 쌍들이 전부 같은 문장이 된다 — 승인 카피 대조가 존재하는 이유가
    바로 그 차이를 잡는 것인데, 그걸 포기하는 셈이다. 전사기는 브랜드명을
    틀리지만 **모델이 다른 말을 하는 드리프트도 대개 몇 글자다.** 잡음과
    드리프트를 문자열 거리로는 구별할 수 없다.

    그래서 게이트는 닫아둔 채, 실패 리포트에 ``near_misses`` 와
    ``likely_transcription_noise`` 를 붙인다. 운영자는 이 표시를 보고
    "재생성"이 아니라 "사람이 1분 듣고 판단"으로 라우팅할 수 있다.
    오탐 비용을 게이트 완화가 아니라 **정확한 진단**으로 낮춘 것이다.
    진짜 해법은 승인 카피를 TTS 로 읽혀 기준 전사를 만들어 두는 것인데
    (같은 모델의 같은 오류가 양쪽에 나타나 상쇄된다), 그건 생성 쪽 작업이라
    여기서 하지 않는다 — 미해결로 남긴다.
    """
    base = {"artifact_under_test": artifact_kind}
    if error:
        return dict(base, **_fail(
            f"오디오를 전사할 수 없다 ({error}) — 승인 카피 대조를 "
            "돌리지 못했으므로 통과가 아니다", transcript=None))
    if not approved_lines:
        return dict(base, **_fail(
            "승인된 나레이션이 스토리보드에 하나도 없다 — "
            "무엇과 대조해야 할지 알 수 없다"))
    normalized = normalize_speech(transcript)
    if not normalized:
        return dict(base, **_fail(
            "전사 결과가 비어 있다 — 영상이 실제로 말을 하는지 확인 불가",
            transcript=transcript))

    # 컷 순서대로만 찾는다 — 3컷 서사에서 순서가 뒤바뀌면 다른 영상이다.
    # 각 줄은 직전 줄이 끝난 지점(cursor) 이후에서만 매칭된다.
    residual_parts: List[str] = []
    missing: List[str] = []
    out_of_order: List[str] = []
    cursor = 0
    for line in approved_lines:
        norm = normalize_speech(line)
        if not norm:
            continue
        at = normalized.find(norm, cursor)
        if at >= 0:
            residual_parts.append(normalized[cursor:at])
            cursor = at + len(norm)
            continue
        if norm in normalized:
            # 발화는 됐지만 승인된 컷 순서를 지키지 않았다.
            out_of_order.append(line)
        else:
            missing.append(line)

    residual_parts.append(normalized[cursor:])
    residual = "".join(residual_parts)

    if missing:
        near = _near_misses(normalized, missing)
        return dict(base, **_fail(
            f"승인된 나레이션 {len(missing)}줄이 실제 발화에 없다: {missing!r} — "
            f"전사: {str(transcript)[:200]!r}"
            + (f" | 주의: {len(near)}줄이 편집거리 {NEAR_MISS_MAX_EDITS} 이내로 "
               "가깝다 — 전사기 오류일 수 있으니 재생성 전에 사람이 들어볼 것"
               if near else ""),
            transcript=transcript, missing_lines=missing,
            near_misses=near, likely_transcription_noise=bool(near),
            out_of_order_lines=out_of_order))
    if out_of_order:
        return dict(base, **_fail(
            f"승인된 나레이션 {len(out_of_order)}줄이 스토리보드 컷 순서와 다른 "
            f"자리에서 발화됐다: {out_of_order!r} — 순서가 바뀌면 다른 서사다",
            transcript=transcript, out_of_order_lines=out_of_order,
            near_misses=[], likely_transcription_noise=False,
            missing_lines=[]))
    if len(residual) > MAX_UNAPPROVED_CHARS:
        return dict(base, **_fail(
            f"승인되지 않은 발화가 {len(residual)}자 섞여 있다: {residual[:120]!r} — "
            "승인되지 않은 말은 영상이 하지 않는다",
            transcript=transcript, unapproved_residual=residual,
            near_misses=[], likely_transcription_noise=False))
    # 통과해도 흡수된 잔여를 리포트에 남긴다 — 운영자가 무엇이 용인됐는지 본다.
    return dict(base, **_ok(
        f"승인 나레이션 {len(approved_lines)}줄과 순서까지 일치 "
        f"(용인된 잔여 {len(residual)}자: {residual!r})",
        transcript=transcript, unapproved_residual=residual,
        unapproved_residual_tolerance=MAX_UNAPPROVED_CHARS,
        near_misses=[], likely_transcription_noise=False,
        out_of_order_lines=[]))


def check_disclosure(caption: str, overlay_texts: Sequence[str],
                     market: str,
                     storyboard: Optional[Dict[str, Any]] = None,
                     master_caption: Optional[str] = None,
                     master_overlay_texts: Optional[Sequence[str]] = None
                     ) -> Dict[str, Any]:
    """제휴 고지 불변 문구가 그대로 살아 있는가 (SSOT 불변 규칙 2).

    **양쪽 산출물에서 확인한다.** 베이스 영상 에셋에는 자막을 넣지 않고
    자막은 별도 후처리 패스에서 붙이지만, 제휴 고지는 자막이 아니라 **법적
    의무**이고 전 구간에 존재해야 한다. 자막 패스가 실패하거나 마스터가
    단독으로 유출돼도 고지는 붙어 있어야 하므로, 클린 마스터와 최종
    납품물 **둘 다** 검사한다. 한쪽만 보면 "고지 확인됨"이 어느 산출물에
    대한 말인지 알 수 없다.

    마스터 쪽 텍스트를 주지 않으면 납품물 텍스트를 마스터 텍스트로도
    본다 (compose 분리 전 단일 산출물 경로 — 그때는 실제로 같은 파일이다).
    """
    caption = str(caption or "")
    overlays = [str(t or "") for t in (overlay_texts or [])]
    if master_caption is None:
        master_caption = caption
        master_overlays = list(overlays)
    else:
        master_caption = str(master_caption)
        master_overlays = [str(t or "") for t in (master_overlay_texts or [])]

    base: Dict[str, Any] = {
        "artifact_under_test": ARTIFACT_DELIVERABLE,
        "artifacts_verified": [ARTIFACT_CLEAN_MASTER, ARTIFACT_DELIVERABLE],
    }
    try:
        required = vs.DISCLOSURE_TEXT[market]
    except KeyError:
        return dict(base, **_fail(
            f"알 수 없는 시장이라 고지 문구를 정할 수 없다: {market!r}"))

    obligation = ((storyboard or {}).get("disclosure") or {})
    if obligation and not obligation.get("required", True):
        return dict(base, **_fail(
            "스토리보드가 제휴 고지 의무를 해제하려 한다 — "
            "고지는 해제 대상이 아니다 (SSOT 불변 규칙 2)"))
    if obligation and obligation.get("text") and obligation["text"] != required:
        return dict(base, **_fail(
            f"스토리보드 고지 문구가 불변 문구와 다르다: "
            f"{obligation['text']!r} != {required!r}"))

    per_artifact = {
        ARTIFACT_CLEAN_MASTER: {
            "in_caption": required in master_caption,
            "in_overlay": any(required in t for t in master_overlays),
        },
        ARTIFACT_DELIVERABLE: {
            "in_caption": required in caption,
            "in_overlay": any(required in t for t in overlays),
        },
    }
    base["per_artifact"] = per_artifact

    failed = [kind for kind in ARTIFACT_KINDS
              if not per_artifact[kind]["in_caption"]]
    if failed:
        return dict(base, **_fail(
            f"{failed} 에 {market} 제휴 고지 불변 문구가 없다 "
            f"(문구 변형·생략 금지): {required!r} — 고지는 클린 마스터와 최종 "
            "납품물 양쪽에 있어야 한다",
            in_caption=per_artifact[ARTIFACT_DELIVERABLE]["in_caption"],
            in_overlay=per_artifact[ARTIFACT_DELIVERABLE]["in_overlay"],
            missing_on=failed))
    return dict(base, **_ok(
        f"{market} 고지 문구를 {list(ARTIFACT_KINDS)} 양쪽에서 확인",
        in_caption=per_artifact[ARTIFACT_DELIVERABLE]["in_caption"],
        in_overlay=per_artifact[ARTIFACT_DELIVERABLE]["in_overlay"],
        missing_on=[]))


def check_forbidden_claims(caption: str, transcript: Optional[str],
                           overlay_texts: Sequence[str]) -> Dict[str, Any]:
    """캡션·전사·오버레이 세 면 전부에서 금지 표현을 스캔한다.

    **검사 대상은 자막이 입혀진 최종 납품물이다.** 화면에 실제로 뜨는
    글자(=자막/오버레이)가 여기서 판정되며, 자막 패스가 새 텍스트를 넣을
    수 있으므로 클린 마스터만 봐서는 안 된다.

    전사가 실패해 본문이 없으면 그 면은 ``scanned`` 가 아니라 ``unscanned``
    로 보고한다 — 읽지 못한 면을 '깨끗하다'고 적으면 리포트가 운영자를 속인다.
    (게이트 자체는 ``spoken_content`` 에서 이미 fail closed 된다.)
    """
    base = {"artifact_under_test": ARTIFACT_DELIVERABLE}
    surfaces = {
        "caption": [str(caption or "")],
        "transcript": [str(transcript or "")],
        "overlay": [str(t or "") for t in (overlay_texts or [])],
    }
    unscanned: List[str] = []
    if transcript is None:
        unscanned.append("transcript")
    scanned = [n for n in CLAIM_SCAN_SURFACES if n not in unscanned]

    hits: Dict[str, List[str]] = {}
    for name in scanned:
        found: List[str] = []
        for text in surfaces.get(name, []):
            found.extend(find_forbidden_claims(text))
        if found:
            hits[name] = sorted(set(found))
    if hits:
        parts = [f"{k}: {v}" for k, v in sorted(hits.items())]
        return dict(base, **_fail(
            "금지 표현(효능 암시·가짜 체험담) 검출 — " + "; ".join(parts),
            hits=hits, scanned=scanned, unscanned=unscanned))
    detail = f"{len(scanned)}개 면에서 금지 표현 없음"
    if unscanned:
        detail += (f" — 단 {unscanned} 면은 본문이 없어 **스캔하지 못했다** "
                   "(깨끗하다는 뜻이 아니다)")
    return dict(base, **_ok(detail, hits={}, scanned=scanned,
                            unscanned=unscanned))


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
           identity_signoff: Optional[Dict[str, Any]] = None,
           product_asset_dir: Optional[str] = None,
           fidelity_checker: Optional[Callable] = None,
           fidelity_max_calls: int = 4,
           master_path: Optional[str] = None,
           master_caption: Optional[str] = None,
           master_overlay_texts: Optional[Sequence[str]] = None,
           fps: float = DEFAULT_SAMPLE_FPS,
           frame_sampler: Optional[Callable] = None,
           transcriber: Optional[Callable] = None,
           audio_probe: Optional[Callable] = None,
           workdir: Optional[str] = None) -> QAReport:
    """발행 직전 전체 QA. **어떤 검사도 조용히 건너뛰지 않는다.**

    돌지 못한 검사는 실패로 집계된다 (fail closed). 결과는 검사별 pass/fail 과
    진단 정보를 담은 계약 ``QAReport`` 다.

    검사 대상 산출물 (seam)
    -----------------------
    베이스 영상 에셋에는 자막이 없고 자막은 별도 후처리 패스에서 붙는다.
    그래서 **어느 산출물을 검사하는지는 호출자가 명시한다**:

    - ``video_path`` = 최종 납품물 (자막 포함). 컨테이너·오디오·금지표현.
    - ``master_path`` = 자막 없는 클린 마스터. 프레임 움직임·발화·상품
      동일성 서명. 생략하면 ``video_path`` 를 쓴다 (compose 분리 전 경로 —
      그때는 실제로 같은 파일이므로 정직하다).
    - 제휴 고지는 **양쪽 모두**에서 확인한다 (법적 의무, 전 구간 존재).

    각 검사 결과에는 ``artifact_under_test`` 가 실린다. 이 값을 리포트에서
    빼면 "고지 확인됨"이 어느 파일에 대한 말인지 아무도 모른다.
    """
    storyboard = storyboard or {}
    overlay_texts = list(overlay_texts or [])
    master_path = master_path or video_path
    market = storyboard.get("market") or ""
    cuts = storyboard.get("cuts") or []
    cut_count = max(1, len(cuts))
    expected_seconds = sum(int(c.get("duration_seconds") or CUT_DURATION_SECONDS)
                           for c in cuts) or CUT_DURATION_SECONDS

    checks: Dict[str, Any] = {}

    # 1. 컨테이너 실측 — 최종 납품물 (실제로 발행되는 파일)
    checks[CHECK_TECHNICAL_CONTAINER] = check_technical_container(
        video_path, expected_seconds, ARTIFACT_DELIVERABLE)

    # 2. 오디오 신호 — 최종 납품물 (자막 패스가 오디오를 잃어버리는 경우도 잡는다)
    checks[CHECK_TECHNICAL_AUDIO] = check_audio_signal(
        video_path, audio_probe, ARTIFACT_DELIVERABLE)

    # 3. 프레임 샘플링 — 클린 마스터 (자막 픽셀이 정지 검출을 무력화하지 않게)
    measured = (checks[CHECK_TECHNICAL_CONTAINER].get("measured") or {})
    # 컨테이너 길이가 아니라 **비디오 트랙** 길이로 샘플 시각을 잡는다.
    # 오디오가 더 길면 컨테이너 길이는 마지막 비디오 프레임보다 뒤를 가리킨다.
    duration = frame_sampling_duration(measured) or expected_seconds
    stamps = sample_timestamps(duration, cut_count, fps)

    tmp_holder = None
    if workdir is None:
        tmp_holder = tempfile.TemporaryDirectory(prefix="heightcue-qa-")
        workdir = tmp_holder.name
    frames_dir = os.path.join(workdir, "frames")

    sampler = frame_sampler or default_frame_sampler
    frames: List[Dict[str, Any]] = []
    sample_error: Optional[str] = None
    try:
        frames = list(sampler(master_path, stamps, frames_dir) or [])
    except Exception as exc:                     # noqa: BLE001 — fail closed
        sample_error = f"{type(exc).__name__}: {exc}"

    checks[CHECK_TECHNICAL_FRAMES] = check_frames(
        frames, len(stamps), sample_error,
        measured_duration=(float(measured["duration_seconds"])
                           if measured.get("duration_seconds") is not None
                           else None),
        artifact_kind=ARTIFACT_CLEAN_MASTER)
    # AI 비전 충실도 — 사람에게 서명을 요구하기 **전에** 도는 기계 필터.
    # 호출 예산(fidelity_max_calls)으로 QA 1회 비용이 묶인다.
    fidelity_verdict: Optional[Dict[str, Any]] = None
    if fidelity_checker is not None:
        try:
            fidelity_verdict = fidelity_checker(
                [f["path"] for f in frames], product_asset_dir,
                fidelity_max_calls)
        except Exception as exc:                 # noqa: BLE001 — fail closed
            fidelity_verdict = {
                "passed": False,
                "reason": f"AI 충실도 검사 실행 실패: {type(exc).__name__}: {exc}"}
    else:
        try:
            import product_fidelity as _pf
            refs = _pf.reference_photos(product_asset_dir or "")
            fidelity_verdict = _pf.check_frames(
                [f["path"] for f in frames], refs,
                known_wording=_pf.known_wording(product_asset_dir or ""),
                max_calls=fidelity_max_calls)
        except Exception as exc:                 # noqa: BLE001 — fail closed
            fidelity_verdict = {
                "passed": False,
                "reason": f"AI 충실도 검사 실행 실패: {type(exc).__name__}: {exc}"}

    checks[CHECK_PRODUCT_IDENTITY] = check_product_identity_screen(
        frames, product_image_path, sample_error,
        identity_signoff=identity_signoff, artifact_path=master_path,
        fidelity_verdict=fidelity_verdict,
        artifact_kind=ARTIFACT_CLEAN_MASTER)
    # 라벨 진정성의 출처를 리포트에 명시한다 — 어느 구간이 생성물이고 어느
    # 구간이 촬영 원본인지 밝히지 않으면 사람 서명이 무엇을 승인하는지 모호해진다.
    checks[CHECK_PRODUCT_IDENTITY]["cut_kinds"] = cut_kind_summary(storyboard)


    # 4. 발화 내용 — 클린 마스터의 오디오 (자막 패스는 오디오를 바꾸지 않는다)
    transcribe = transcriber or default_transcriber
    transcript: Optional[str] = None
    transcribe_error: Optional[str] = None
    try:
        result = transcribe(master_path)
        transcript = (result.get("text") if isinstance(result, dict)
                      else str(result or ""))
    except Exception as exc:                     # noqa: BLE001 — fail closed
        transcribe_error = f"{type(exc).__name__}: {exc}"

    checks[CHECK_SPOKEN_CONTENT] = check_spoken_content(
        transcript, approved_voice_lines(storyboard), transcribe_error,
        artifact_kind=ARTIFACT_CLEAN_MASTER)

    # 5. 정책 — 고지는 양쪽, 금지표현은 자막이 입혀진 납품물
    checks[CHECK_POLICY_DISCLOSURE] = check_disclosure(
        caption, overlay_texts, market, storyboard,
        master_caption=master_caption,
        master_overlay_texts=master_overlay_texts)
    checks[CHECK_POLICY_CLAIMS] = check_forbidden_claims(
        caption, transcript, overlay_texts)

    if tmp_holder is not None:
        tmp_holder.cleanup()

    # 이름이 하나라도 빠지면 조용히 통과하는 구멍이 된다 — 크게 실패한다.
    missing = [n for n in CHECK_NAMES if n not in checks]
    if missing:
        raise QAError(f"검사 결과가 누락됐다: {missing} — 부분 리포트로 발행하지 않는다")

    # 어느 산출물을 봤는지 밝히지 않은 검사도 구멍이다 — 리포트를 읽는 사람이
    # "고지 확인됨"을 잘못된 파일에 대한 말로 읽게 된다.
    unnamed = [n for n in CHECK_NAMES
               if checks[n].get("artifact_under_test") not in ARTIFACT_KINDS]
    if unnamed:
        raise QAError(
            f"검사 {unnamed} 가 어느 산출물을 봤는지 밝히지 않았다 — "
            "대상 없는 판정은 리포트에 싣지 않는다")

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
        # 리포트를 먼저 단다 — video_contracts 는 qa_failed 잡이 실패 리포트를
        # 지니도록 요구한다. 순서를 뒤집으면 두 문장 사이에서 잡이 자기
        # 불변식을 위반한 상태가 된다 (transition 이 validate 를 부르는 순간 터진다).
        job.qa_report = report
        job.transition(vc.STATE_QA_FAILED)      # 불법 간선이면 여기서 죽는다
    else:
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
