#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue 바이럴 UGC 패턴 원장 (KR/US 격리 · 관측/해석 분리).

이 모듈은 **네트워크를 호출하지 않는다.** 실제로 Threads/YouTube 를 방문해
관측을 수집하는 루틴은 별도 태스크(Task 7)다. 여기서는 이미 수집된 관측
레코드를 받아 정규화·검증하고 패턴 라이프사이클을 관리한다.

설계 불변식 3가지:

1. **시장 격리** — KR 원장과 US 원장은 파일부터 분리된다. KR 패턴이 US
   선택에 새어 들어가는 경로는 존재하지 않는다.
2. **관측 vs 해석 분리** — ``Observation`` 은 실제로 본 것(URL, 관측 시각,
   그 시각에 화면에 있던 지표)만 담는다. ``Inference`` 는 우리 해석(훅 모양,
   구조 문법)만 담는다. 둘은 서로의 필드를 절대 갖지 않는다. 관측되지 않은
   지표는 ``None`` 으로 남으며 추정·보간·조작하지 않는다.
3. **메타데이터·URL 만** — 크리에이터의 이미지·영상은 다운로드·저장·복제하지
   않는다. 미디어 경로/바이너리 필드가 들어오면 거부한다.

의존성 경량 원칙: 표준 라이브러리만 쓴다 (requirements.txt 는 `requests` 하나).
지속성은 video_contracts 의 ``atomic_write_json`` / ``append_event`` 를 재사용한다.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from video_contracts import append_event, atomic_write_json

# ---------------------------------------------------------------------------
# 고정 상수
# ---------------------------------------------------------------------------

MARKETS = ("KR", "US")

#: SSOT §0 카테고리 하드락 — 영양/숙면/자세/운동만.
ALLOWED_CATEGORIES = ("nutrition", "sleep", "posture", "exercise")

PLATFORMS = ("threads", "youtube", "instagram", "tiktok")

# --- 라이프사이클 -----------------------------------------------------------

STATE_CANDIDATE = "candidate"
STATE_ACTIVE = "active"
STATE_FATIGUED = "fatigued"
STATE_RETIRED = "retired"

LIFECYCLE_STATES = (STATE_CANDIDATE, STATE_ACTIVE, STATE_FATIGUED, STATE_RETIRED)

TERMINAL_LIFECYCLE_STATES = (STATE_RETIRED,)

#: 허용 전이만 명시. 여기 없는 조합은 전부 LifecycleError.
LIFECYCLE_TRANSITIONS: Dict[str, tuple] = {
    STATE_CANDIDATE: (STATE_ACTIVE, STATE_RETIRED),
    STATE_ACTIVE: (STATE_FATIGUED, STATE_RETIRED),
    STATE_FATIGUED: (STATE_ACTIVE, STATE_RETIRED),
    STATE_RETIRED: (),
}

# --- 승격 임계값 (설계 승인값 — 변경 시 설계 문서와 동기화) -----------------

PROMOTION_MIN_DISTINCT_PRODUCTS = 3
PROMOTION_MIN_DISTINCT_CATEGORIES = 2
PROMOTION_REQUIRES_BASELINE = True

REASON_INSUFFICIENT_PRODUCTS = "insufficient_distinct_products"
REASON_INSUFFICIENT_CATEGORIES = "insufficient_distinct_categories"
REASON_MISSING_BASELINE = "missing_or_incomplete_baseline"
REASON_POLICY_FLAGGED = "policy_flagged"
REASON_NOT_PROMOTABLE_STATE = "not_promotable_state"

REQUIRED_BASELINE_KEYS = ("metric", "pattern_value", "baseline_value",
                          "sample_size", "source", "compared_at")

# --- 재사용 가능한 UGC 문법 축 (해석 측 전용) ------------------------------

GRAMMAR_FIELDS = (
    "hook_0_2s",
    "product_reveal_seconds",
    "shot_count",
    "hand_face_product_ratio",
    "camera_movement",
    "demo_action",
    "proof_moment",
    "caption_structure",
    "voice_structure",
    "disclosure",
    "cta",
)

# --- 관측 지표 (실제로 본 것만) ---------------------------------------------

ENGAGEMENT_METRICS = ("likes", "replies", "reposts", "shares", "views")

#: 미디어 사본을 암시하는 키 — 발견 즉시 거부한다.
FORBIDDEN_MEDIA_KEYS = (
    "media_path", "media_bytes", "video_path", "video_bytes",
    "image_path", "image_bytes", "thumbnail_path", "thumbnail_bytes",
    "local_copy", "downloaded_media",
)

# --- 정책 플래그 -------------------------------------------------------------

FLAG_DISCLOSURE_MISSING = "disclosure_missing"

_DISCLOSURE_ABSENT = ("", "none", "no", "없음", "n/a", "missing", "absent")

# --- 점수 가중치 -------------------------------------------------------------

SCORE_WEIGHTS = {
    "market_fit": 0.20,
    "product_fit": 0.20,
    "evidence_quality": 0.20,
    "recency": 0.15,
    "engagement": 0.15,
    "policy_compatibility": 0.10,
}

#: 최신성 반감기 (일). 관측이 오래될수록 recency 점수가 지수적으로 감쇠한다.
RECENCY_HALF_LIFE_DAYS = 60.0

#: 참여도 정규화 기준 (관측된 engagement rate 가 이 값이면 1.0).
ENGAGEMENT_SATURATION_RATE = 0.05


# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class ViralUGCError(ValueError):
    """바이럴 UGC 원장 위반 공통 베이스."""


class ObservationError(ViralUGCError):
    """관측 레코드 결손·모순 (URL/관측일 누락, 음수 지표, 중복 id 등)."""


class InferenceError(ViralUGCError):
    """해석 레코드 결손·모순 (문법 축 누락, 근거 관측 미참조 등)."""


class MarketIsolationError(ViralUGCError):
    """KR/US 경계 침범 — 다른 시장 레코드를 이 원장에 넣으려 했다."""


class MediaPolicyError(ViralUGCError):
    """크리에이터 미디어 사본 반입 시도 — 메타데이터·URL 만 허용한다."""


class LifecycleError(ViralUGCError):
    """알 수 없는 상태, 허용되지 않은 전이, 임계값 미달 승격."""


# ---------------------------------------------------------------------------
# 검증 프리미티브
# ---------------------------------------------------------------------------


def _require_text(value: Any, name: str, error=ViralUGCError) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error(f"{name} 는 비어 있을 수 없는 문자열이어야 한다: {value!r}")
    return value


def _require_market(value: Any, name: str = "market") -> str:
    if value not in MARKETS:
        raise MarketIsolationError(
            f"{name} 는 {MARKETS} 중 하나여야 한다: {value!r}")
    return value


def _require_url(value: Any, name: str, error=ObservationError) -> str:
    _require_text(value, name, error)
    if not value.startswith(("http://", "https://")):
        raise error(f"{name} 는 http(s) URL 이어야 한다 (출처 없는 관측 금지): {value!r}")
    return value


def _require_timestamp(value: Any, name: str, error=ObservationError) -> str:
    _require_text(value, name, error)
    _parse_ts(value, name, error)
    return value


def _parse_ts(value: str, name: str, error=ObservationError) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise error(f"{name} 는 ISO-8601 타임스탬프여야 한다: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _reject_media(data: Dict[str, Any], where: str) -> None:
    """미디어 사본 필드가 섞여 들어오면 거부한다 (메타데이터·URL 만 허용)."""
    for key in FORBIDDEN_MEDIA_KEYS:
        if key in data:
            raise MediaPolicyError(
                f"{where} 에 미디어 사본 필드 {key!r} 가 있다 — "
                "크리에이터 미디어는 다운로드·저장·재사용 금지, "
                "메타데이터와 URL 만 보관한다")


def assert_lifecycle_state(state: Any, name: str = "state") -> str:
    if state not in LIFECYCLE_STATES:
        raise LifecycleError(
            f"알 수 없는 {name}: {state!r} — 허용: {LIFECYCLE_STATES}")
    return state


def assert_lifecycle_transition(from_state: Any, to_state: Any) -> None:
    assert_lifecycle_state(from_state, "from_state")
    assert_lifecycle_state(to_state, "to_state")
    if to_state not in LIFECYCLE_TRANSITIONS[from_state]:
        raise LifecycleError(
            f"허용되지 않은 전이: {from_state} -> {to_state} "
            f"(허용: {LIFECYCLE_TRANSITIONS[from_state] or '(종결 상태)'})")


# ---------------------------------------------------------------------------
# OBSERVATION — 실제로 본 것만. 추정 금지.
# ---------------------------------------------------------------------------


@dataclass
class EngagementSnapshot:
    """어떤 시각에 그 게시물에서 **실제로 보인** 지표 스냅샷.

    관측되지 않은 지표는 ``None`` 으로 남는다. 절대 0 으로 채우거나 추정하지
    않는다 — ``observed_metrics()`` 는 실제로 본 것만 돌려준다.
    """

    observed_at: str
    likes: Optional[int] = None
    replies: Optional[int] = None
    reposts: Optional[int] = None
    shares: Optional[int] = None
    views: Optional[int] = None

    def validate(self) -> "EngagementSnapshot":
        _require_timestamp(self.observed_at, "engagement.observed_at")
        seen = self.observed_metrics()
        if not seen:
            raise ObservationError(
                "engagement 에 관측된 지표가 하나도 없다 — 빈 스냅샷은 기록하지 않는다")
        for name, value in seen.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ObservationError(
                    f"engagement.{name} 는 정수여야 한다: {value!r}")
            if value < 0:
                raise ObservationError(
                    f"engagement.{name} 는 음수일 수 없다: {value!r}")
        return self

    def observed_metrics(self) -> Dict[str, int]:
        """실제로 관측된 지표만. 미관측은 키 자체가 없다."""
        return {m: getattr(self, m) for m in ENGAGEMENT_METRICS
                if getattr(self, m) is not None}

    def engagement_rate(self) -> Optional[float]:
        """views 가 관측된 경우에만 계산. 아니면 None (추정하지 않는다)."""
        if not self.views:
            return None
        interactions = sum(v for k, v in self.observed_metrics().items()
                           if k != "views")
        return interactions / float(self.views)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"observed_at": self.observed_at}
        out.update(self.observed_metrics())
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EngagementSnapshot":
        data = dict(data or {})
        _reject_media(data, "engagement")
        return cls(observed_at=data.get("observed_at", ""),
                   **{m: data.get(m) for m in ENGAGEMENT_METRICS})


@dataclass
class Observation:
    """공개 게시물에서 **실제로 관측한** 사실. 해석은 여기 들어오지 않는다.

    저장하는 것: 출처 URL, 관측 시각, 플랫폼, 시장, 연결 상품/카테고리,
    그 시각의 지표 스냅샷, 자유 메모.
    저장하지 않는 것: 크리에이터의 이미지·영상 사본, 우리 해석/문법 판단.
    """

    observation_id: str
    market: str
    platform: str
    source_url: str
    observed_at: str
    product_id: str
    category: str
    engagement: EngagementSnapshot
    notes: str = ""

    def validate(self) -> "Observation":
        _require_text(self.observation_id, "observation_id", ObservationError)
        _require_market(self.market)
        if self.platform not in PLATFORMS:
            raise ObservationError(
                f"platform 은 {PLATFORMS} 중 하나여야 한다: {self.platform!r}")
        _require_url(self.source_url, "source_url")
        _require_timestamp(self.observed_at, "observed_at")
        _require_text(self.product_id, "product_id", ObservationError)
        if self.category not in ALLOWED_CATEGORIES:
            raise ObservationError(
                f"category 하드락 위반 — {ALLOWED_CATEGORIES} 중 하나여야 한다: "
                f"{self.category!r}")
        if not isinstance(self.engagement, EngagementSnapshot):
            raise ObservationError("engagement 는 EngagementSnapshot 이어야 한다")
        self.engagement.validate()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "market": self.market,
            "platform": self.platform,
            "source_url": self.source_url,
            "observed_at": self.observed_at,
            "product_id": self.product_id,
            "category": self.category,
            "engagement": self.engagement.to_dict(),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Observation":
        data = dict(data or {})
        _reject_media(data, "observation")
        return cls(
            observation_id=data.get("observation_id", ""),
            market=data.get("market", ""),
            platform=data.get("platform", ""),
            source_url=data.get("source_url", ""),
            observed_at=data.get("observed_at", ""),
            product_id=data.get("product_id", ""),
            category=data.get("category", ""),
            engagement=EngagementSnapshot.from_dict(data.get("engagement") or {}),
            notes=data.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# INFERENCE — 우리 해석. 지표는 여기 들어오지 않는다.
# ---------------------------------------------------------------------------


@dataclass
class Inference:
    """관측에 대한 **우리 해석** — 어떤 문법이 먹혔다고 보는가.

    지표를 담지 않는다. 지표는 언제나 Observation 쪽에만 있다.
    """

    inference_id: str
    observation_id: str
    market: str
    grammar: Dict[str, Any]
    analyst_notes: str = ""
    confidence: float = 0.5
    created_at: str = ""

    def validate(self) -> "Inference":
        _require_text(self.inference_id, "inference_id", InferenceError)
        if not str(self.observation_id or "").strip():
            raise InferenceError(
                "observation_id 가 비어 있다 — 근거 관측 없는 해석은 기록하지 않는다")
        _require_market(self.market)
        if not isinstance(self.grammar, dict):
            raise InferenceError("grammar 는 dict 여야 한다")
        missing = [f for f in GRAMMAR_FIELDS if f not in self.grammar]
        if missing:
            raise InferenceError(f"grammar 축 누락: {missing}")
        unknown = [k for k in self.grammar if k not in GRAMMAR_FIELDS]
        if unknown:
            raise InferenceError(
                f"grammar 에 지원하지 않는 축이 있다 (조용히 학습 금지): {unknown}")
        if (isinstance(self.confidence, bool)
                or not isinstance(self.confidence, (int, float))):
            raise InferenceError(f"confidence 는 숫자여야 한다: {self.confidence!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise InferenceError(f"confidence 는 0.0~1.0 이어야 한다: {self.confidence!r}")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inference_id": self.inference_id,
            "observation_id": self.observation_id,
            "market": self.market,
            "grammar": dict(self.grammar),
            "analyst_notes": self.analyst_notes,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Inference":
        data = dict(data or {})
        _reject_media(data, "inference")
        for metric in ENGAGEMENT_METRICS + ("engagement",):
            if metric in data:
                raise InferenceError(
                    f"inference 에 관측 지표 {metric!r} 가 있다 — "
                    "관측과 해석은 섞지 않는다")
        return cls(
            inference_id=data.get("inference_id", ""),
            observation_id=data.get("observation_id", ""),
            market=data.get("market", ""),
            grammar=dict(data.get("grammar") or {}),
            analyst_notes=data.get("analyst_notes", ""),
            confidence=data.get("confidence", 0.5),
            created_at=data.get("created_at", ""),
        )


def policy_flags(inference: Inference) -> List[str]:
    """정책 비호환 신호를 **플래그로 드러낸다** — 조용히 학습하지 않는다."""
    flags: List[str] = []
    disclosure = str(inference.grammar.get("disclosure", "")).strip().lower()
    if disclosure in _DISCLOSURE_ABSENT:
        flags.append(FLAG_DISCLOSURE_MISSING)
    return flags


# ---------------------------------------------------------------------------
# PATTERN
# ---------------------------------------------------------------------------


@dataclass
class Pattern:
    """관측+해석 묶음으로 뒷받침되는 재사용 가능한 UGC 패턴."""

    pattern_id: str
    market: str
    name: str
    state: str = STATE_CANDIDATE
    observation_ids: List[str] = field(default_factory=list)
    inference_ids: List[str] = field(default_factory=list)
    baseline: Optional[Dict[str, Any]] = None
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> "Pattern":
        _require_text(self.pattern_id, "pattern_id", ViralUGCError)
        _require_market(self.market)
        _require_text(self.name, "name", ViralUGCError)
        assert_lifecycle_state(self.state)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "market": self.market,
            "name": self.name,
            "state": self.state,
            "observation_ids": list(self.observation_ids),
            "inference_ids": list(self.inference_ids),
            "baseline": dict(self.baseline) if self.baseline else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Pattern":
        data = dict(data or {})
        _reject_media(data, "pattern")
        baseline = data.get("baseline")
        return cls(
            pattern_id=data.get("pattern_id", ""),
            market=data.get("market", ""),
            name=data.get("name", ""),
            state=data.get("state", STATE_CANDIDATE),
            observation_ids=list(data.get("observation_ids") or []),
            inference_ids=list(data.get("inference_ids") or []),
            baseline=dict(baseline) if baseline else None,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class PromotionCheck:
    """승격 심사 결과 — 통과 여부와 **불통과 사유 전부**를 함께 돌려준다."""

    pattern_id: str
    ok: bool
    distinct_products: int
    distinct_categories: int
    has_baseline: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "ok": self.ok,
            "distinct_products": self.distinct_products,
            "distinct_categories": self.distinct_categories,
            "has_baseline": self.has_baseline,
            "reasons": list(self.reasons),
        }


@dataclass
class PatternScore:
    pattern_id: str
    total: float
    components: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {"pattern_id": self.pattern_id, "total": self.total,
                "components": dict(self.components)}


def is_complete_baseline(baseline: Any) -> bool:
    """비교 기준선이 완전한가 — 키 하나라도 비면 승격 근거로 인정하지 않는다."""
    if not isinstance(baseline, dict):
        return False
    for key in REQUIRED_BASELINE_KEYS:
        value = baseline.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            return False
    return True


# ---------------------------------------------------------------------------
# 로더 (로컬 파일 전용 — 네트워크 없음)
# ---------------------------------------------------------------------------


def load_observations(path: str) -> List[Observation]:
    """로컬 JSONL 픽스처/수집 산출물에서 관측을 읽어 **전부 검증**한다.

    한 줄이라도 검증에 실패하면 예외로 죽는다 — 부분 학습을 허용하지 않는다.
    """
    out: List[Observation] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                data = json.loads(line)
            except ValueError as exc:
                raise ObservationError(f"{path}:{lineno} JSON 파싱 실패: {exc}") from exc
            if data.get("_fixture_note") and "observation_id" not in data:
                continue  # 픽스처 헤더 주석 레코드
            try:
                out.append(Observation.from_dict(data).validate())
            except ViralUGCError as exc:
                raise type(exc)(f"{path}:{lineno} {exc}") from exc
    return out


# ---------------------------------------------------------------------------
# LEDGER — 시장별로 완전히 분리된 저장소
# ---------------------------------------------------------------------------


class PatternLedger:
    """한 시장(KR 또는 US) 전용 원장.

    파일 경로부터 시장별로 갈라지므로 KR 레코드가 US 조회에 나타날 물리적
    경로가 없다. 더해서 모든 쓰기 경로에서 market 을 재확인한다 (이중 방어).
    """

    def __init__(self, base_dir: str, market: str):
        self.market = _require_market(market)
        self.base_dir = os.path.join(os.path.abspath(base_dir),
                                     self.market.lower())
        os.makedirs(self.base_dir, exist_ok=True)
        self.observations_path = os.path.join(self.base_dir, "observations.jsonl")
        self.inferences_path = os.path.join(self.base_dir, "inferences.jsonl")
        self.patterns_path = os.path.join(self.base_dir, "patterns.json")
        self.events_path = os.path.join(self.base_dir, "events.jsonl")

    # -- 내부 IO ------------------------------------------------------------

    def _read_jsonl(self, path: str) -> List[Dict[str, Any]]:
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def _append_jsonl(self, path: str, row: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _guard_market(self, value: str, what: str) -> None:
        if value != self.market:
            raise MarketIsolationError(
                f"{what} 의 market={value!r} 이 이 원장({self.market}) 과 다르다 — "
                "KR/US 원장은 절대 섞이지 않는다")

    # -- 관측 ---------------------------------------------------------------

    def observations(self) -> List[Observation]:
        return [Observation.from_dict(r) for r in
                self._read_jsonl(self.observations_path)]

    def record_observation(self, obs: Observation) -> Observation:
        obs.validate()
        self._guard_market(obs.market, "observation")
        if any(o.observation_id == obs.observation_id
               for o in self.observations()):
            raise ObservationError(
                f"observation_id 중복: {obs.observation_id!r} — 추가 전용 원장이다")
        self._append_jsonl(self.observations_path, obs.to_dict())
        self.log_event(event="observation_recorded",
                       observation_id=obs.observation_id,
                       source_url=obs.source_url, observed_at=obs.observed_at)
        return obs

    def get_observation(self, observation_id: str) -> Optional[Observation]:
        for o in self.observations():
            if o.observation_id == observation_id:
                return o
        return None

    # -- 해석 ---------------------------------------------------------------

    def inferences(self) -> List[Inference]:
        return [Inference.from_dict(r) for r in
                self._read_jsonl(self.inferences_path)]

    def record_inference(self, inf: Inference) -> Inference:
        inf.validate()
        self._guard_market(inf.market, "inference")
        if self.get_observation(inf.observation_id) is None:
            raise MarketIsolationError(
                f"observation_id={inf.observation_id!r} 가 이 원장"
                f"({self.market}) 에 없다 — 다른 시장 관측을 참조할 수 없다")
        self._append_jsonl(self.inferences_path, inf.to_dict())
        self.log_event(event="inference_recorded",
                       inference_id=inf.inference_id,
                       observation_id=inf.observation_id,
                       flags=policy_flags(inf))
        return inf

    def get_inference(self, inference_id: str) -> Optional[Inference]:
        for i in self.inferences():
            if i.inference_id == inference_id:
                return i
        return None

    # -- 패턴 ---------------------------------------------------------------

    def patterns(self) -> List[Pattern]:
        if not os.path.exists(self.patterns_path):
            return []
        with open(self.patterns_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return [Pattern.from_dict(r) for r in (raw.get("patterns") or [])]

    def _write_patterns(self, patterns: List[Pattern]) -> None:
        atomic_write_json(self.patterns_path, {
            "market": self.market,
            "patterns": [p.to_dict() for p in patterns],
        })

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        for p in self.patterns():
            if p.pattern_id == pattern_id:
                return p
        return None

    def upsert_pattern(self, pattern: Pattern) -> Pattern:
        pattern.validate()
        self._guard_market(pattern.market, "pattern")
        existing = self.patterns()
        for i, p in enumerate(existing):
            if p.pattern_id == pattern.pattern_id:
                existing[i] = pattern
                break
        else:
            existing.append(pattern)
        self._write_patterns(existing)
        return pattern

    def _require_pattern(self, pattern_id: str) -> Pattern:
        p = self.get_pattern(pattern_id)
        if p is None:
            raise LifecycleError(
                f"패턴 없음: {pattern_id!r} (원장={self.market})")
        return p

    # -- 지원 근거 ----------------------------------------------------------

    def supporting_observations(self, pattern: Pattern) -> List[Observation]:
        wanted = set(pattern.observation_ids)
        return [o for o in self.observations() if o.observation_id in wanted]

    def supporting_inferences(self, pattern: Pattern) -> List[Inference]:
        wanted = set(pattern.inference_ids)
        return [i for i in self.inferences() if i.inference_id in wanted]

    # -- 승격 ---------------------------------------------------------------

    def evaluate_promotion(self, pattern_id: str,
                           baseline: Optional[Dict[str, Any]] = None
                           ) -> PromotionCheck:
        """candidate → active 심사. 임계값은 모듈 상수로 고정돼 있다."""
        pattern = self._require_pattern(pattern_id)
        obs = self.supporting_observations(pattern)
        products = {o.product_id for o in obs}
        categories = {o.category for o in obs}
        effective_baseline = baseline if baseline is not None else pattern.baseline
        has_baseline = is_complete_baseline(effective_baseline)

        reasons: List[str] = []
        if len(products) < PROMOTION_MIN_DISTINCT_PRODUCTS:
            reasons.append(REASON_INSUFFICIENT_PRODUCTS)
        if len(categories) < PROMOTION_MIN_DISTINCT_CATEGORIES:
            reasons.append(REASON_INSUFFICIENT_CATEGORIES)
        if PROMOTION_REQUIRES_BASELINE and not has_baseline:
            reasons.append(REASON_MISSING_BASELINE)
        if any(policy_flags(i) for i in self.supporting_inferences(pattern)):
            reasons.append(REASON_POLICY_FLAGGED)
        if pattern.state not in (STATE_CANDIDATE, STATE_FATIGUED):
            reasons.append(REASON_NOT_PROMOTABLE_STATE)

        return PromotionCheck(
            pattern_id=pattern_id, ok=not reasons,
            distinct_products=len(products),
            distinct_categories=len(categories),
            has_baseline=has_baseline, reasons=reasons)

    def promote(self, pattern_id: str,
                baseline: Optional[Dict[str, Any]] = None) -> Pattern:
        check = self.evaluate_promotion(pattern_id, baseline=baseline)
        if not check.ok:
            raise LifecycleError(
                f"{pattern_id!r} 승격 불가 — 사유: {check.reasons} "
                f"(상품 {check.distinct_products}/{PROMOTION_MIN_DISTINCT_PRODUCTS}, "
                f"카테고리 {check.distinct_categories}/"
                f"{PROMOTION_MIN_DISTINCT_CATEGORIES}, "
                f"기준선 {'있음' if check.has_baseline else '없음'})")
        pattern = self._require_pattern(pattern_id)
        if baseline is not None:
            pattern.baseline = dict(baseline)
            self.upsert_pattern(pattern)
        return self.transition(pattern_id, STATE_ACTIVE, check=check)

    def transition(self, pattern_id: str, to_state: str,
                   check: Optional[PromotionCheck] = None) -> Pattern:
        pattern = self._require_pattern(pattern_id)
        assert_lifecycle_transition(pattern.state, to_state)
        if to_state == STATE_ACTIVE and check is None:
            check = self.evaluate_promotion(pattern_id)
            if not check.ok:
                raise LifecycleError(
                    f"{pattern_id!r} active 전이 불가 — 사유: {check.reasons}")
        from_state = pattern.state
        pattern.state = to_state
        pattern.updated_at = _now_iso()
        self.upsert_pattern(pattern)
        self.log_event(event="pattern_transition", pattern_id=pattern_id,
                       from_state=from_state, to_state=to_state,
                       check=check.to_dict() if check else None)
        return pattern

    # -- 점수·선택 ----------------------------------------------------------

    def score_pattern(self, pattern_id: str,
                      now: Optional[str] = None) -> PatternScore:
        pattern = self._require_pattern(pattern_id)
        obs = self.supporting_observations(pattern)
        infs = self.supporting_inferences(pattern)
        now_dt = _parse_ts(now, "now") if now else datetime.now(timezone.utc)

        products = {o.product_id for o in obs}
        categories = {o.category for o in obs}

        market_fit = 1.0 if obs and all(o.market == self.market for o in obs) else 0.0
        product_fit = _ratio(len(products), PROMOTION_MIN_DISTINCT_PRODUCTS)

        evidence = 0.0
        if obs:
            url_ok = sum(1 for o in obs if o.source_url) / len(obs)
            cat_cov = _ratio(len(categories), PROMOTION_MIN_DISTINCT_CATEGORIES)
            conf = (sum(i.confidence for i in infs) / len(infs)) if infs else 0.0
            baseline_bonus = 1.0 if is_complete_baseline(pattern.baseline) else 0.0
            evidence = (url_ok + cat_cov + conf + baseline_bonus) / 4.0

        recency = 0.0
        if obs:
            ages = [max(0.0, (now_dt - _parse_ts(o.observed_at, "observed_at"))
                        .total_seconds() / 86400.0) for o in obs]
            recency = 2.0 ** (-(sum(ages) / len(ages)) / RECENCY_HALF_LIFE_DAYS)

        rates = [r for r in (o.engagement.engagement_rate() for o in obs)
                 if r is not None]
        engagement = _ratio(sum(rates) / len(rates),
                            ENGAGEMENT_SATURATION_RATE) if rates else 0.0

        flagged = sum(1 for i in infs if policy_flags(i))
        policy = 1.0 if not infs else max(0.0, 1.0 - flagged / len(infs))

        components = {
            "market_fit": _clamp(market_fit),
            "product_fit": _clamp(product_fit),
            "evidence_quality": _clamp(evidence),
            "recency": _clamp(recency),
            "engagement": _clamp(engagement),
            "policy_compatibility": _clamp(policy),
        }
        total = sum(components[k] * w for k, w in SCORE_WEIGHTS.items())
        return PatternScore(pattern_id=pattern_id, total=_clamp(total),
                            components=components)

    def select_patterns(self, now: Optional[str] = None,
                        limit: Optional[int] = None) -> List[Pattern]:
        """이 시장의 **active 패턴만** 결정적 순서로 돌려준다.

        같은 입력이면 언제나 같은 순서 — 점수 내림차순, 동점이면 id 오름차순.
        """
        active = [p for p in self.patterns() if p.state == STATE_ACTIVE]
        scored = [(self.score_pattern(p.pattern_id, now=now).total,
                   p.pattern_id, p) for p in active]
        scored.sort(key=lambda t: (-t[0], t[1]))
        out = [t[2] for t in scored]
        return out[:limit] if limit else out

    # -- 이벤트 -------------------------------------------------------------

    def log_event(self, **fields: Any) -> Dict[str, Any]:
        return append_event(self.events_path, dict(fields, market=self.market))

    def events(self) -> List[Dict[str, Any]]:
        return self._read_jsonl(self.events_path)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _ratio(value: float, target: float) -> float:
    if target <= 0:
        return 0.0
    return _clamp(value / float(target))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


__all__ = [
    "MARKETS", "ALLOWED_CATEGORIES", "PLATFORMS", "GRAMMAR_FIELDS",
    "ENGAGEMENT_METRICS", "FORBIDDEN_MEDIA_KEYS", "SCORE_WEIGHTS",
    "LIFECYCLE_STATES", "LIFECYCLE_TRANSITIONS",
    "STATE_CANDIDATE", "STATE_ACTIVE", "STATE_FATIGUED", "STATE_RETIRED",
    "PROMOTION_MIN_DISTINCT_PRODUCTS", "PROMOTION_MIN_DISTINCT_CATEGORIES",
    "PROMOTION_REQUIRES_BASELINE", "REQUIRED_BASELINE_KEYS",
    "REASON_INSUFFICIENT_PRODUCTS", "REASON_INSUFFICIENT_CATEGORIES",
    "REASON_MISSING_BASELINE", "REASON_POLICY_FLAGGED",
    "FLAG_DISCLOSURE_MISSING",
    "ViralUGCError", "ObservationError", "InferenceError",
    "MarketIsolationError", "MediaPolicyError", "LifecycleError",
    "EngagementSnapshot", "Observation", "Inference", "Pattern",
    "PromotionCheck", "PatternScore", "PatternLedger",
    "policy_flags", "is_complete_baseline", "load_observations",
]
