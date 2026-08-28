#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V UGC 파이프라인 — 타입 있는 영상 잡·발행 핸드오프 계약.

의존성 경량 원칙: 표준 라이브러리 dataclasses + 명시적 검증만 쓴다.
Pydantic 등 신규 의존성 추가 금지 (requirements.txt는 `requests` 하나뿐).

이 모듈은 네트워크를 호출하지 않는다. 순수 데이터 계약 + 검증 + 원자적 저장.

상류 계약은 이전 태스크에서 이미 확정됐다 — 여기서는 재도출하지 않고 고정한다:

* 이미지 체인: 내부 별칭 ``gpt-image-gen-2`` → hermes provider ``openai-codex``
  → hermes model ``gpt-image-2-medium`` → provider model ``gpt-image-2``
* 영상: fal 엔드포인트 ``minimax/h3-max/image-to-video``, 해상도 ``768P``,
  화면비 ``9:16``, 컷당 정확히 5초, 컷 1~3개(총 5·10·15초)
* 시장: ``KR`` / ``US``

검증은 조용히 넘어가지 않는다 — 계약 위반은 항상 예외로 죽는다.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import redact_secrets

# 계보 검증용 센티널 — 필드가 없으면 조용히 통과하지 않고 크게 실패한다.
_MISSING = object()

# ---------------------------------------------------------------------------
# 고정 상수 (상류 확정 계약 — 변경 금지, 변경 시 상류 태스크와 동기화)
# ---------------------------------------------------------------------------

IMAGE_MODEL_ALIAS = "gpt-image-gen-2"
IMAGE_HERMES_PROVIDER = "openai-codex"
IMAGE_HERMES_MODEL = "gpt-image-2-medium"
IMAGE_PROVIDER_MODEL = "gpt-image-2"

VIDEO_ENDPOINT = "minimax/h3-max/image-to-video"
VIDEO_RESOLUTION = "768P"
VIDEO_ASPECT_RATIO = "9:16"

# ---------------------------------------------------------------------------
# 첫 프레임 형상 — 게이트와 피게이트 대상이 어긋나지 않도록 **여기 한 곳**에만
# 정의한다. codex_image_bridge 와 video_generate 는 이것을 import 해서 쓴다.
# (규칙과 검사가 갈라져 이 빌드에서 이미 두 번 사고가 났다: 거짓 초록 1회,
#  거짓 빨강 1회.)
# ---------------------------------------------------------------------------

#: 파이프라인 전체가 9:16 이다. pipeline_defs/heightcue-ugc.yaml 이 9:16 을
#: 고정하고, build_cut_request 는 9:16 이 아니면 거부하며, Remotion 합성은
#: 768x1360 으로 렌더한다. 따라서 첫 프레임도 9:16 이어야 한다.
FIRST_FRAME_TARGET_RATIO = 9.0 / 16.0          # 0.5625

#: **허용 오차 1.0% (상대오차) — 근거는 실측 산술이다.**
#:
#:   통과해야 하는 것
#:     941 x 1672 (실 provider 출력) = 0.5627990 → 상대오차 0.0532%
#:     768 x 1360 (Remotion 합성 크기) = 0.5647059 → 상대오차 0.3921%
#:   거부해야 하는 것
#:     1024 x 1536 (플러그인이 *요청*하는 크기, 2:3) = 0.6666667 → 18.52%
#:      768 x 1344                                  = 0.5714286 →  1.587%
#:
#: 즉 임계값은 0.3921% 초과 1.587% 미만이어야 한다. 1.0% 는 그 구간의
#: 거의 정중앙이며, 반드시 통과해야 하는 최악 사례(0.3921%)에 2.55배의
#: 여유를 두고도 반드시 거부해야 하는 최선 사례(1.587%)를 막는다.
#: 감으로 고른 둥근 수가 아니라 위 두 경계 사이에서 고른 값이다.
FIRST_FRAME_ASPECT_TOLERANCE = 0.010

#: 해상도 하한. 첫 프레임은 768x1360 합성으로 내려가므로 그보다 작으면
#: 업스케일이 되어 디테일이 뭉개진다. 비율만 맞는 초소형 이미지 차단.
FIRST_FRAME_MIN_WIDTH = 768
FIRST_FRAME_MIN_HEIGHT = 1360


class FirstFrameGeometryError(ValueError):
    """첫 프레임의 실측 크기가 9:16 파이프라인 형상 계약을 위반했다."""


def first_frame_ratio_error(width: int, height: int) -> float:
    """(너비/높이) 가 9:16 에서 벗어난 **상대오차**를 돌려준다."""
    if height <= 0:
        raise FirstFrameGeometryError(f"높이가 0 이하다: {width}x{height}")
    return abs((width / height) - FIRST_FRAME_TARGET_RATIO) / FIRST_FRAME_TARGET_RATIO


def assert_first_frame_geometry(width: Any, height: Any, *,
                                where: str = "") -> tuple:
    """실측 (너비, 높이) 가 9:16 하한 해상도 계약을 만족하는지 강제한다.

    측정된 픽셀만 받는다 — 디스패처가 에코한 aspect 문자열은 요청의 메아리일
    뿐이므로 이 함수에 넘겨선 안 된다. 판정 불가는 통과가 아니라 거부다.
    """
    tail = f": {where}" if where else ""
    try:
        w = int(width)
        h = int(height)
    except (TypeError, ValueError) as exc:
        raise FirstFrameGeometryError(
            f"첫 프레임 크기를 정수로 읽을 수 없다: {width!r}x{height!r}{tail}"
        ) from exc
    if w <= 0 or h <= 0:
        raise FirstFrameGeometryError(
            f"첫 프레임 크기가 유효하지 않다: {w}x{h}{tail}")
    if h <= w:
        raise FirstFrameGeometryError(
            f"첫 프레임 {w}x{h} 는 세로가 아니다{tail} — 9:16 세로 숏폼이어야 한다")
    if w < FIRST_FRAME_MIN_WIDTH or h < FIRST_FRAME_MIN_HEIGHT:
        raise FirstFrameGeometryError(
            f"첫 프레임 {w}x{h} 가 최소 해상도 "
            f"{FIRST_FRAME_MIN_WIDTH}x{FIRST_FRAME_MIN_HEIGHT} 미만이다{tail} — "
            "합성 크기보다 작으면 업스케일된다")
    err = first_frame_ratio_error(w, h)
    if err > FIRST_FRAME_ASPECT_TOLERANCE:
        raise FirstFrameGeometryError(
            f"첫 프레임 {w}x{h} 의 비율 {w / h:.6f} 가 {VIDEO_ASPECT_RATIO} "
            f"({FIRST_FRAME_TARGET_RATIO:.6f}) 에서 상대오차 {err * 100:.3f}% "
            f"벗어났다 (허용 {FIRST_FRAME_ASPECT_TOLERANCE * 100:.1f}%){tail} — "
            "이 프레임은 크롭 없이 9:16 영상에 들어갈 수 없다")
    return w, h


def parse_pixel_size(text: Any) -> tuple:
    """``"941x1672"`` 같은 픽셀 크기 문자열을 (너비, 높이) 로 파싱한다."""
    if not isinstance(text, str):
        raise FirstFrameGeometryError(f"픽셀 크기가 문자열이 아니다: {text!r}")
    parts = text.strip().lower().split("x")
    if len(parts) != 2:
        raise FirstFrameGeometryError(f"픽셀 크기 형식을 알 수 없다: {text!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise FirstFrameGeometryError(
            f"픽셀 크기 형식을 알 수 없다: {text!r}") from exc

CUT_DURATION_SECONDS = 5
MIN_CUTS = 1
MAX_CUTS = 3
ALLOWED_TOTAL_DURATIONS = tuple(
    CUT_DURATION_SECONDS * n for n in range(MIN_CUTS, MAX_CUTS + 1))  # (5, 10, 15)

MARKETS = ("KR", "US")

# ---------------------------------------------------------------------------
# 상태 기계
# ---------------------------------------------------------------------------

STATE_QUEUED = "queued"
STATE_GENERATING = "generating"
STATE_QA_FAILED = "qa_failed"
STATE_READY_TO_PUBLISH = "ready_to_publish"
STATE_PUBLISHING = "publishing"
STATE_PUBLISHED = "published"
STATE_RETRYABLE_FAILED = "retryable_failed"
STATE_DEAD_LETTER = "dead_letter"

STATES = (
    STATE_QUEUED,
    STATE_GENERATING,
    STATE_QA_FAILED,
    STATE_READY_TO_PUBLISH,
    STATE_PUBLISHING,
    STATE_PUBLISHED,
    STATE_RETRYABLE_FAILED,
    STATE_DEAD_LETTER,
)

TERMINAL_STATES = (STATE_PUBLISHED, STATE_DEAD_LETTER)

#: 허용 전이만 명시. 여기 없는 조합은 전부 StateError로 죽는다.
TRANSITIONS: Dict[str, tuple] = {
    STATE_QUEUED: (STATE_GENERATING, STATE_RETRYABLE_FAILED, STATE_DEAD_LETTER),
    STATE_GENERATING: (STATE_READY_TO_PUBLISH, STATE_QA_FAILED,
                       STATE_RETRYABLE_FAILED, STATE_DEAD_LETTER),
    STATE_QA_FAILED: (STATE_QUEUED, STATE_DEAD_LETTER),
    STATE_READY_TO_PUBLISH: (STATE_PUBLISHING, STATE_RETRYABLE_FAILED,
                             STATE_DEAD_LETTER),
    STATE_PUBLISHING: (STATE_PUBLISHED, STATE_RETRYABLE_FAILED, STATE_DEAD_LETTER),
    STATE_PUBLISHED: (),
    STATE_RETRYABLE_FAILED: (STATE_QUEUED, STATE_DEAD_LETTER),
    STATE_DEAD_LETTER: (),
}

#: 핸드오프 문서가 존재할 수 있는 상태 (발행 단계 이후만)
HANDOFF_STATES = (STATE_READY_TO_PUBLISH, STATE_PUBLISHING, STATE_PUBLISHED)


# ---------------------------------------------------------------------------
# 예외 — 전부 ContractError 하위라 한 번에 잡을 수도, 세밀하게 잡을 수도 있다
# ---------------------------------------------------------------------------


class ContractError(ValueError):
    """영상 계약 위반 공통 베이스."""


class LineageError(ContractError):
    """run_id/job_id/product_id/market 등 계보 식별자 결손·불일치."""


class RightsError(ContractError):
    """권리·출처 근거 결손 (제휴 고지 포함)."""


class DurationError(ContractError):
    """지원하지 않는 컷 수 또는 길이."""


class AspectRatioError(ContractError):
    """지원하지 않는 화면비."""


class ModelMismatchError(ContractError):
    """모델 별칭/provider/provider model/엔드포인트 불일치."""


class StateError(ContractError):
    """알 수 없는 상태 또는 허용되지 않은 전이."""


# ---------------------------------------------------------------------------
# 검증 프리미티브
# ---------------------------------------------------------------------------

_SHA256_HEX = set("0123456789abcdef")


def _require_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LineageError(f"{name} 는 비어 있을 수 없는 문자열이어야 한다: {value!r}")
    return value


def _require_market(value: Any, name: str = "market") -> str:
    if value not in MARKETS:
        raise LineageError(f"{name} 는 {MARKETS} 중 하나여야 한다: {value!r}")
    return value


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} 는 비어 있을 수 없는 문자열이어야 한다: {value!r}")
    return value


def _require_sha256(value: Any, name: str, error=ContractError) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or not set(value.lower()) <= _SHA256_HEX):
        raise error(f"{name} 는 64자리 sha256 hex 여야 한다: {value!r}")
    return value


def _require_aspect_ratio(value: Any, name: str = "aspect_ratio") -> str:
    if value != VIDEO_ASPECT_RATIO:
        raise AspectRatioError(
            f"{name} 는 {VIDEO_ASPECT_RATIO} 여야 한다 (세로 숏폼 고정): {value!r}")
    return value


def _require_pinned(value: Any, expected: str, name: str) -> str:
    if value != expected:
        raise ModelMismatchError(f"{name} 불일치: {value!r} != {expected!r} (상류 확정 계약)")
    return value


def _require_cut_duration(value: Any, name: str = "duration_seconds") -> int:
    if value != CUT_DURATION_SECONDS:
        raise DurationError(
            f"{name} 는 정확히 {CUT_DURATION_SECONDS}초여야 한다: {value!r}")
    return value


def _require_cut_count(cuts: List[Any]) -> None:
    if not MIN_CUTS <= len(cuts) <= MAX_CUTS:
        raise DurationError(
            f"컷 수는 {MIN_CUTS}~{MAX_CUTS} 개여야 한다: {len(cuts)}")


def _require_sequential(cuts: List[Any]) -> None:
    expected = list(range(1, len(cuts) + 1))
    actual = [c.index for c in cuts]
    if actual != expected:
        raise ContractError(f"컷 index 는 1부터 연속이어야 한다: {actual} != {expected}")


def assert_state(state: Any, name: str = "state") -> str:
    if state not in STATES:
        raise StateError(f"알 수 없는 {name}: {state!r} — 허용: {STATES}")
    return state


def assert_transition(from_state: Any, to_state: Any) -> None:
    """허용되지 않은/알 수 없는 전이면 StateError."""
    assert_state(from_state, "from_state")
    assert_state(to_state, "to_state")
    if to_state not in TRANSITIONS[from_state]:
        raise StateError(
            f"허용되지 않은 전이: {from_state} -> {to_state} "
            f"(허용: {TRANSITIONS[from_state] or '(종결 상태)'})")


def is_terminal(state: Any) -> bool:
    return assert_state(state) in TERMINAL_STATES


# ---------------------------------------------------------------------------
# 원자적 저장 — common.write_json 은 원자적이지 않으므로 여기서 별도 구현
# ---------------------------------------------------------------------------


def atomic_write_json(path: str, data: Any) -> str:
    """tmp 파일 + os.replace 로 원자적 JSON 기록.

    직렬화가 실패하면 원본 파일과 디렉터리를 손상 없이 남긴다 (tmp 도 지운다).
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(
        directory,
        f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _redact_deep(value: Any) -> Any:
    """문자열·dict·list·tuple 을 재귀적으로 훑어 모든 문자열에 마스킹을 적용한다."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _redact_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_deep(v) for v in value]
    return value


def append_event(path: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """추가 전용 JSONL 이벤트. 값에 담긴 자격증명은 기록 전에 마스킹한다."""
    row = {k: _redact_deep(v) for k, v in dict(record).items()}
    row.setdefault("ts", datetime.now(timezone.utc).astimezone().isoformat())
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def append_transition_event(path: str, job_id: str, run_id: str,
                            from_state: str, to_state: str,
                            **extra: Any) -> Dict[str, Any]:
    """전이를 먼저 검증하고 나서 기록한다 — 불법 전이는 파일을 만들지 않는다."""
    _require_id(job_id, "job_id")
    _require_id(run_id, "run_id")
    assert_transition(from_state, to_state)
    return append_event(path, dict(extra, job_id=job_id, run_id=run_id,
                                   event="transition", from_state=from_state,
                                   to_state=to_state))


# ---------------------------------------------------------------------------
# 계약 데이터클래스
# ---------------------------------------------------------------------------


@dataclass
class ProductEvidence:
    """소재 상품의 출처·권리 근거. 이 근거 없이는 어떤 영상도 생성될 수 없다."""

    product_id: str
    market: str
    source_urls: List[str]
    source_sha256: List[str]
    rights: Dict[str, Any]
    provenance: List[Dict[str, Any]]
    captured_at: str

    REQUIRED_RIGHTS_KEYS = ("basis", "holder", "source_url", "captured_at")
    REQUIRED_PROVENANCE_KEYS = ("quote", "source_url", "original_location")

    def validate(self) -> "ProductEvidence":
        _require_id(self.product_id, "product_id")
        _require_market(self.market)
        _require_text(self.captured_at, "captured_at")

        if not self.source_urls:
            raise RightsError("source_urls 가 비어 있다 — 출처 없는 소재는 사용 금지")
        for i, url in enumerate(self.source_urls):
            if not isinstance(url, str) or not url.startswith("http"):
                raise RightsError(f"source_urls[{i}] 는 http(s) URL 이어야 한다: {url!r}")

        if not self.source_sha256:
            raise RightsError("source_sha256 가 비어 있다 — 소재 해시 없이는 재현 불가")
        for i, h in enumerate(self.source_sha256):
            _require_sha256(h, f"source_sha256[{i}]", RightsError)

        if not isinstance(self.rights, dict) or not self.rights:
            raise RightsError("rights 근거가 없다 — 권리 근거 없는 소재는 사용 금지")
        for key in self.REQUIRED_RIGHTS_KEYS:
            if not str(self.rights.get(key) or "").strip():
                raise RightsError(f"rights.{key} 가 비어 있다")

        if not self.provenance:
            raise RightsError("provenance 가 비어 있다 — 원문 근거 없는 인용 금지")
        for i, entry in enumerate(self.provenance):
            if not isinstance(entry, dict):
                raise RightsError(f"provenance[{i}] 는 dict 여야 한다: {entry!r}")
            for key in self.REQUIRED_PROVENANCE_KEYS:
                if not str(entry.get(key) or "").strip():
                    raise RightsError(f"provenance[{i}].{key} 가 비어 있다")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "market": self.market,
            "source_urls": list(self.source_urls),
            "source_sha256": list(self.source_sha256),
            "rights": dict(self.rights),
            "provenance": [dict(p) for p in self.provenance],
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProductEvidence":
        return cls(
            product_id=data.get("product_id", ""),
            market=data.get("market", ""),
            source_urls=list(data.get("source_urls") or []),
            source_sha256=list(data.get("source_sha256") or []),
            rights=dict(data.get("rights") or {}),
            provenance=[dict(p) for p in (data.get("provenance") or [])],
            captured_at=data.get("captured_at", ""),
        )


@dataclass
class CutPrompt:
    """스토리보드의 한 컷 — 생성 전 계획."""

    index: int
    prompt: str
    duration_seconds: int = CUT_DURATION_SECONDS

    def validate(self) -> "CutPrompt":
        if not isinstance(self.index, int) or self.index < 1:
            raise ContractError(f"컷 index 는 1 이상 정수여야 한다: {self.index!r}")
        _require_cut_duration(self.duration_seconds, f"cuts[{self.index}].duration_seconds")
        _require_text(self.prompt, f"cuts[{self.index}].prompt")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "prompt": self.prompt,
                "duration_seconds": self.duration_seconds}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CutPrompt":
        return cls(index=data.get("index", 0), prompt=data.get("prompt", ""),
                   duration_seconds=data.get("duration_seconds", CUT_DURATION_SECONDS))


@dataclass
class Storyboard:
    """생성 전 계획: 어떤 바이럴 패턴·초안에서 어떤 컷들이 나오는가."""

    storyboard_id: str
    run_id: str
    product_id: str
    market: str
    viral_pattern_ids: List[str]
    content_draft_id: str
    cuts: List[CutPrompt]

    def total_duration_seconds(self) -> int:
        return sum(c.duration_seconds for c in self.cuts)

    def validate(self) -> "Storyboard":
        _require_id(self.storyboard_id, "storyboard_id")
        _require_id(self.run_id, "run_id")
        _require_id(self.product_id, "product_id")
        _require_market(self.market)
        _require_id(self.content_draft_id, "content_draft_id")
        if not self.viral_pattern_ids:
            raise LineageError("viral_pattern_ids 가 비어 있다 — 선택된 바이럴 패턴 계보 필수")
        for i, pid in enumerate(self.viral_pattern_ids):
            _require_id(pid, f"viral_pattern_ids[{i}]")

        _require_cut_count(self.cuts)
        for cut in self.cuts:
            cut.validate()
        _require_sequential(self.cuts)
        if self.total_duration_seconds() not in ALLOWED_TOTAL_DURATIONS:
            raise DurationError(
                f"총 길이는 {ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다: "
                f"{self.total_duration_seconds()}")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "storyboard_id": self.storyboard_id,
            "run_id": self.run_id,
            "product_id": self.product_id,
            "market": self.market,
            "viral_pattern_ids": list(self.viral_pattern_ids),
            "content_draft_id": self.content_draft_id,
            "cuts": [c.to_dict() for c in self.cuts],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Storyboard":
        return cls(
            storyboard_id=data.get("storyboard_id", ""),
            run_id=data.get("run_id", ""),
            product_id=data.get("product_id", ""),
            market=data.get("market", ""),
            viral_pattern_ids=list(data.get("viral_pattern_ids") or []),
            content_draft_id=data.get("content_draft_id", ""),
            cuts=[CutPrompt.from_dict(c) for c in (data.get("cuts") or [])],
        )


@dataclass
class CutGeneration:
    """생성 후 한 컷의 실제 결과 — provider 요청 ID·비용·산출물 해시 포함."""

    index: int
    prompt: str
    duration_seconds: int
    provider_request_id: str
    cost_usd: float
    output_path: str
    output_sha256: str

    def validate(self) -> "CutGeneration":
        if not isinstance(self.index, int) or self.index < 1:
            raise ContractError(f"컷 index 는 1 이상 정수여야 한다: {self.index!r}")
        _require_cut_duration(self.duration_seconds,
                              f"cuts[{self.index}].duration_seconds")
        _require_text(self.prompt, f"cuts[{self.index}].prompt")
        _require_id(self.provider_request_id,
                    f"cuts[{self.index}].provider_request_id")
        if not isinstance(self.cost_usd, (int, float)) or isinstance(self.cost_usd, bool):
            raise ContractError(f"cuts[{self.index}].cost_usd 는 숫자여야 한다: "
                                f"{self.cost_usd!r}")
        if self.cost_usd < 0:
            raise ContractError(f"cuts[{self.index}].cost_usd 는 음수일 수 없다: "
                                f"{self.cost_usd!r}")
        _require_text(self.output_path, f"cuts[{self.index}].output_path")
        _require_sha256(self.output_sha256, f"cuts[{self.index}].output_sha256")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "prompt": self.prompt,
            "duration_seconds": self.duration_seconds,
            "provider_request_id": self.provider_request_id,
            "cost_usd": self.cost_usd,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CutGeneration":
        return cls(
            index=data.get("index", 0),
            prompt=data.get("prompt", ""),
            duration_seconds=data.get("duration_seconds", 0),
            provider_request_id=data.get("provider_request_id", ""),
            cost_usd=data.get("cost_usd", 0.0),
            output_path=data.get("output_path", ""),
            output_sha256=data.get("output_sha256", ""),
        )


@dataclass
class GenerationManifest:
    """실제로 무엇이 어떤 모델로 생성됐는가 — 모델 체인이 상류 계약과 일치해야 한다."""

    job_id: str
    run_id: str
    storyboard_id: str
    product_id: str
    market: str
    image_model_alias: str
    image_hermes_provider: str
    image_hermes_model: str
    image_provider_model: str
    video_endpoint: str
    resolution: str
    aspect_ratio: str
    cuts: List[CutGeneration]

    def total_cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.cuts), 6)

    def total_duration_seconds(self) -> int:
        return sum(c.duration_seconds for c in self.cuts)

    def validate(self) -> "GenerationManifest":
        _require_id(self.job_id, "job_id")
        _require_id(self.run_id, "run_id")
        _require_id(self.storyboard_id, "storyboard_id")
        _require_id(self.product_id, "product_id")
        _require_market(self.market)

        _require_pinned(self.image_model_alias, IMAGE_MODEL_ALIAS, "image_model_alias")
        _require_pinned(self.image_hermes_provider, IMAGE_HERMES_PROVIDER,
                        "image_hermes_provider")
        _require_pinned(self.image_hermes_model, IMAGE_HERMES_MODEL,
                        "image_hermes_model")
        _require_pinned(self.image_provider_model, IMAGE_PROVIDER_MODEL,
                        "image_provider_model")
        _require_pinned(self.video_endpoint, VIDEO_ENDPOINT, "video_endpoint")
        _require_pinned(self.resolution, VIDEO_RESOLUTION, "resolution")
        _require_aspect_ratio(self.aspect_ratio)

        _require_cut_count(self.cuts)
        for cut in self.cuts:
            cut.validate()
        _require_sequential(self.cuts)
        if self.total_duration_seconds() not in ALLOWED_TOTAL_DURATIONS:
            raise DurationError(
                f"총 길이는 {ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다: "
                f"{self.total_duration_seconds()}")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "storyboard_id": self.storyboard_id,
            "product_id": self.product_id,
            "market": self.market,
            "image_model_alias": self.image_model_alias,
            "image_hermes_provider": self.image_hermes_provider,
            "image_hermes_model": self.image_hermes_model,
            "image_provider_model": self.image_provider_model,
            "video_endpoint": self.video_endpoint,
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "cuts": [c.to_dict() for c in self.cuts],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GenerationManifest":
        return cls(
            job_id=data.get("job_id", ""),
            run_id=data.get("run_id", ""),
            storyboard_id=data.get("storyboard_id", ""),
            product_id=data.get("product_id", ""),
            market=data.get("market", ""),
            image_model_alias=data.get("image_model_alias", ""),
            image_hermes_provider=data.get("image_hermes_provider", ""),
            image_hermes_model=data.get("image_hermes_model", ""),
            image_provider_model=data.get("image_provider_model", ""),
            video_endpoint=data.get("video_endpoint", ""),
            resolution=data.get("resolution", ""),
            aspect_ratio=data.get("aspect_ratio", ""),
            cuts=[CutGeneration.from_dict(c) for c in (data.get("cuts") or [])],
        )


@dataclass
class QAReport:
    """QA 결과. passed 와 failures 는 서로 모순될 수 없다."""

    job_id: str
    run_id: str
    passed: bool
    checks: Dict[str, Any] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)

    def validate(self) -> "QAReport":
        _require_id(self.job_id, "job_id")
        _require_id(self.run_id, "run_id")
        if not isinstance(self.passed, bool):
            raise ContractError(f"passed 는 bool 이어야 한다: {self.passed!r}")
        if self.passed and self.failures:
            raise ContractError(f"passed=True 인데 failures 가 있다: {self.failures}")
        if not self.passed and not self.failures:
            raise ContractError("passed=False 인데 failures 가 비어 있다 — 실패 사유 필수")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {"job_id": self.job_id, "run_id": self.run_id, "passed": self.passed,
                "checks": dict(self.checks), "failures": list(self.failures)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QAReport":
        return cls(job_id=data.get("job_id", ""), run_id=data.get("run_id", ""),
                   passed=bool(data.get("passed", False)),
                   checks=dict(data.get("checks") or {}),
                   failures=list(data.get("failures") or []))


@dataclass
class PublishingHandoff:
    """발행 계층에 넘기는 최종 산출물 계약."""

    job_id: str
    run_id: str
    product_id: str
    market: str
    state: str
    content_draft_id: str
    video_path: str
    video_sha256: str
    duration_seconds: int
    aspect_ratio: str
    caption: str
    disclosure_included: bool

    def validate(self) -> "PublishingHandoff":
        _require_id(self.job_id, "job_id")
        _require_id(self.run_id, "run_id")
        _require_id(self.product_id, "product_id")
        _require_market(self.market)
        _require_id(self.content_draft_id, "content_draft_id")

        assert_state(self.state)
        if self.state not in HANDOFF_STATES:
            raise StateError(
                f"핸드오프는 {HANDOFF_STATES} 상태에서만 존재할 수 있다: {self.state!r}")

        _require_text(self.video_path, "video_path")
        _require_sha256(self.video_sha256, "video_sha256")
        _require_aspect_ratio(self.aspect_ratio)
        if self.duration_seconds not in ALLOWED_TOTAL_DURATIONS:
            raise DurationError(
                f"duration_seconds 는 {ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다: "
                f"{self.duration_seconds!r}")
        _require_text(self.caption, "caption")
        if not self.disclosure_included:
            raise RightsError("제휴 고지 누락 — 고지 없는 발행 핸드오프는 금지 (SSOT 불변 규칙 2)")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "product_id": self.product_id,
            "market": self.market,
            "state": self.state,
            "content_draft_id": self.content_draft_id,
            "video_path": self.video_path,
            "video_sha256": self.video_sha256,
            "duration_seconds": self.duration_seconds,
            "aspect_ratio": self.aspect_ratio,
            "caption": self.caption,
            "disclosure_included": self.disclosure_included,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PublishingHandoff":
        return cls(
            job_id=data.get("job_id", ""),
            run_id=data.get("run_id", ""),
            product_id=data.get("product_id", ""),
            market=data.get("market", ""),
            state=data.get("state", ""),
            content_draft_id=data.get("content_draft_id", ""),
            video_path=data.get("video_path", ""),
            video_sha256=data.get("video_sha256", ""),
            duration_seconds=data.get("duration_seconds", 0),
            aspect_ratio=data.get("aspect_ratio", ""),
            caption=data.get("caption", ""),
            disclosure_included=bool(data.get("disclosure_included", False)),
        )


@dataclass
class VideoJob:
    """영상 잡 집합체 — 계보·근거·계획·생성·QA·핸드오프를 한 문서로 묶는다."""

    job_id: str
    run_id: str
    product_id: str
    market: str
    state: str
    evidence: ProductEvidence
    storyboard: Storyboard
    manifest: Optional[GenerationManifest] = None
    qa_report: Optional[QAReport] = None
    handoff: Optional[PublishingHandoff] = None

    def validate(self) -> "VideoJob":
        _require_id(self.job_id, "job_id")
        _require_id(self.run_id, "run_id")
        _require_id(self.product_id, "product_id")
        _require_market(self.market)
        assert_state(self.state)

        self.evidence.validate()
        self.storyboard.validate()

        self._require_lineage("evidence", self.evidence, run_id=False)
        self._require_lineage("storyboard", self.storyboard)

        if self.manifest is not None:
            self.manifest.validate()
            self._require_lineage("manifest", self.manifest)
            if self.manifest.job_id != self.job_id:
                raise LineageError(
                    f"manifest.job_id 불일치: {self.manifest.job_id!r} != {self.job_id!r}")
            if self.manifest.storyboard_id != self.storyboard.storyboard_id:
                raise LineageError(
                    f"manifest.storyboard_id 불일치: {self.manifest.storyboard_id!r} "
                    f"!= {self.storyboard.storyboard_id!r}")

        if self.qa_report is not None:
            self.qa_report.validate()
            if self.qa_report.job_id != self.job_id:
                raise LineageError(
                    f"qa_report.job_id 불일치: {self.qa_report.job_id!r} != {self.job_id!r}")
            if self.qa_report.run_id != self.run_id:
                raise LineageError(
                    f"qa_report.run_id 불일치: {self.qa_report.run_id!r} != {self.run_id!r}")

        if self.handoff is not None:
            self.handoff.validate()
            self._require_lineage("handoff", self.handoff)
            if self.handoff.job_id != self.job_id:
                raise LineageError(
                    f"handoff.job_id 불일치: {self.handoff.job_id!r} != {self.job_id!r}")
            if self.handoff.content_draft_id != self.storyboard.content_draft_id:
                raise LineageError(
                    "handoff.content_draft_id 가 스토리보드와 다르다: "
                    f"{self.handoff.content_draft_id!r} != "
                    f"{self.storyboard.content_draft_id!r}")
            if self.handoff.state != self.state:
                raise StateError(
                    "handoff.state 가 잡 상태와 다르다 — 상태의 진실은 하나여야 한다: "
                    f"{self.handoff.state!r} != {self.state!r}")

        if self.state in HANDOFF_STATES:
            if self.manifest is None:
                raise ContractError(f"{self.state} 상태에는 manifest 가 필요하다")
            if self.qa_report is None:
                raise ContractError(f"{self.state} 상태에는 qa_report 가 필요하다")
            if not self.qa_report.passed:
                raise ContractError(f"{self.state} 상태인데 QA 가 통과하지 않았다: "
                                    f"{self.qa_report.failures}")
            if self.handoff is None:
                raise ContractError(f"{self.state} 상태에는 handoff 가 필요하다")

        if self.state == STATE_QA_FAILED:
            if self.qa_report is None:
                raise ContractError("qa_failed 상태에는 실패 근거인 qa_report 가 필요하다")
            if self.qa_report.passed:
                raise ContractError("qa_failed 상태인데 QA 리포트가 통과로 표시돼 있다")
        return self

    def _require_lineage(self, name: str, obj: Any, run_id: bool = True) -> None:
        fields = [("product_id", self.product_id), ("market", self.market)]
        if run_id:
            fields.append(("run_id", self.run_id))
        for attr, expected in fields:
            actual = getattr(obj, attr, _MISSING)
            if actual is _MISSING:
                raise LineageError(
                    f"{name}.{attr} 가 없다 — 계보를 검증할 수 없다 (fail loudly)")
            if actual != expected:
                raise LineageError(f"{name}.{attr} 불일치: "
                                   f"{actual!r} != {expected!r}")

    def transition(self, to_state: str) -> str:
        """상태 전이. 불법 전이면 StateError 로 죽고 상태는 바뀌지 않는다."""
        assert_transition(self.state, to_state)
        self.state = to_state
        return self.state

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "product_id": self.product_id,
            "market": self.market,
            "state": self.state,
            "evidence": self.evidence.to_dict(),
            "storyboard": self.storyboard.to_dict(),
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "qa_report": self.qa_report.to_dict() if self.qa_report else None,
            "handoff": self.handoff.to_dict() if self.handoff else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoJob":
        manifest = data.get("manifest")
        qa_report = data.get("qa_report")
        handoff = data.get("handoff")
        return cls(
            job_id=data.get("job_id", ""),
            run_id=data.get("run_id", ""),
            product_id=data.get("product_id", ""),
            market=data.get("market", ""),
            state=data.get("state", ""),
            evidence=ProductEvidence.from_dict(data.get("evidence") or {}),
            storyboard=Storyboard.from_dict(data.get("storyboard") or {}),
            manifest=GenerationManifest.from_dict(manifest) if manifest else None,
            qa_report=QAReport.from_dict(qa_report) if qa_report else None,
            handoff=PublishingHandoff.from_dict(handoff) if handoff else None,
        )


# ---------------------------------------------------------------------------
# 저장/로드 — 항상 검증을 통과한 것만 디스크에 남는다
# ---------------------------------------------------------------------------


def save_job(path: str, job: VideoJob) -> str:
    """검증 후 원자적으로 저장. 검증 실패 시 파일을 만들지 않는다."""
    job.validate()
    return atomic_write_json(path, job.to_dict())


def load_job(path: str, validate: bool = True) -> VideoJob:
    with open(path, encoding="utf-8") as fh:
        job = VideoJob.from_dict(json.load(fh))
    if validate:
        job.validate()
    return job
