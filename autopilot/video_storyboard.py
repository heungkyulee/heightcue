#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V UGC — 근거 결속 마이크로 스토리보드 생성 (Task 8).

한 줄 요약: **근거 없는 컷은 만들지 않는다.**

설계 규칙 (승인된 스펙에서 내려온 하드 룰):

* 컷 하나는 정확히 5초. 컷은 1~3개. 총 길이는 5·10·15초뿐.
  단순 = 5초/1컷, 기본 = 10초/2컷, 복잡 = 15초/3컷.
* **컷 1개 = 동작 1개 = 효용 1개.** 한 컷에 두 아이디어를 넣지 않는다.
* 스토리보드의 모든 주장은 공급된 ``ProductEvidence.provenance`` 원문에
  추적돼야 한다. 근거가 없으면 **크게 실패한다** — 스펙·효용·효과·체험담을
  지어내지 않는다.
* 모델 출력은 **신뢰하지 않는다.** 구조화 JSON 으로 파싱하고 하드 검증하며,
  형식 위반·길이 초과·시장 불일치·근거 미달은 조용히 잘라내지 않고 예외로 죽인다.
* KR 카피는 한국어, US 카피는 영어. 시장은 끝까지 관통·검증된다.
* 제휴 고지 의무(KR 쿠팡 파트너스 / US Amazon Associates)는 스토리보드가
  들고 간다 — 후속 합성 태스크가 렌더링하더라도 여기서 떨어뜨리지 않는다.
* 기준선 비교 수치는 **실측 metrics.jsonl 집계에서만** 나온다. 손으로 적은
  값·플레이스홀더는 거부한다 (2026-08-27 플레이스홀더 사고 재발 방지).

이 모듈은 테스트에서 네트워크를 타지 않는다 — ``model=`` 시임으로 가짜 모델
응답을 주입한다 (``codex_image_bridge.py`` 의 ``runner=`` 와 같은 패턴).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import video_contracts as vc
from video_contracts import (ALLOWED_TOTAL_DURATIONS, CUT_DURATION_SECONDS,
                             MAX_CUTS, MIN_CUTS, ContractError, DurationError,
                             LineageError, ProductEvidence, RightsError)

# ---------------------------------------------------------------------------
# 고정 상수
# ---------------------------------------------------------------------------

#: 복잡도 → 컷 수. 기본값은 항상 standard(10초/2컷)이며, 5초·15초는
#: 명시적 복잡도 규칙으로만 선택된다.
COMPLEXITY_CUTS = {"simple": 1, "standard": 2, "complex": 3}
DEFAULT_COMPLEXITY = "standard"

#: 한 컷 나레이션 최대 길이. 5초에 읽을 수 없는 길이는 거부한다.
VOICE_LINE_MAX_CHARS = 120
PROMPT_MAX_CHARS = 400
ACTION_MAX_CHARS = 80
BENEFIT_MAX_CHARS = 80

#: 모델 출력 컷에서 반드시 존재해야 하는 텍스트 필드 (evidence_id 는 별도 검증).
REQUIRED_CUT_TEXT_FIELDS = ("action", "benefit", "claim", "voice_line",
                            "first_frame_prompt", "motion_prompt")

#: 제휴 고지 — SSOT 부록 A 불변 문구. 시장별로 반드시 하나가 붙는다.
DISCLOSURE_TEXT = {
    "KR": "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
    "US": "As an Amazon Associate I earn from qualifying purchases.",
}

_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")

#: 한 컷에 아이디어를 두 개 이상 담았다는 신호.
_MULTI_IDEA_MARKERS = (
    "그리고", "또한", "동시에", "및", ",", "、",
    " and ", " plus ", " also ", " while also ", ";",
)

#: 몽타주/다중 장면 신호 — 첫 프레임은 단일 장면이어야 한다.
_MONTAGE_MARKERS = (
    "몽타주", "분할 화면", "분할화면", "여러 컷", "여러 장면", "콜라주", "그리드",
    "montage", "split screen", "split-screen", "collage", "grid of",
    "multiple scenes", "multiple shots", "before and after",
)

#: 장면 전환 신호 — I2V 프롬프트는 모션만 기술해야 한다.
_SCENE_CHANGE_MARKERS = (
    "컷 전환", "장면 전환", "다른 장면", "화면 전환", "점프컷",
    "cut to", "scene change", "transition to", "jump cut", "then we see",
)

#: 효능 암시 · 가짜 체험담 (KR/US 공통 하드 금지 — TruHeight FTC 사례).
_FORBIDDEN_PATTERNS = (
    # 효능·의학적 암시
    r"효과", r"효능", r"성장\s*촉진", r"키\s*크는", r"키가\s*커", r"키를\s*키",
    r"치료", r"완치", r"질환",
    r"\bgrow\s+taller\b", r"\bhelps?\s+(?:kids?|children|them)\s+grow\b",
    r"\bincreases?\s+height\b", r"\bboosts?\s+growth\b", r"\bcures?\b",
    r"\btreats?\b", r"\bclinically\s+proven\b",
    # 가짜 체험담
    r"먹어\s*보니", r"써\s*보니", r"사용해\s*보니", r"우리\s*아이가",
    r"저희\s*아이가", r"직접\s*먹여", r"효과를\s*봤",
    r"\bmy\s+kid\b", r"\bmy\s+child\b", r"\bwe\s+tried\b", r"\bI\s+gave\s+my\b",
    r"\bafter\s+using\s+it\s+my\b",
)

_FORBIDDEN_RE = tuple(re.compile(p, re.I) for p in _FORBIDDEN_PATTERNS)

#: 실측이 아님을 드러내는 기준선 출처 표현.
_PLACEHOLDER_SOURCE_MARKERS = (
    "추정", "예시", "임시", "placeholder", "example", "todo", "tbd",
    "estimate", "guess", "manual", "hand", "dummy", "sample",
)


# ---------------------------------------------------------------------------
# 예외 — 전부 StoryboardError 하위. 계약 위반은 video_contracts 예외를 그대로 쓴다.
# ---------------------------------------------------------------------------


class StoryboardError(ContractError):
    """스토리보드 생성 계약 위반 공통 베이스."""


class ModelOutputError(StoryboardError):
    """모델 출력이 파싱 불가·형식 위반·길이 초과."""


class EvidenceError(StoryboardError):
    """근거 결손 또는 근거로 뒷받침되지 않는 주장."""


class OneIdeaError(StoryboardError):
    """한 컷에 동작/효용/장면이 둘 이상 들어갔다."""


class MarketLanguageError(StoryboardError):
    """시장과 카피 언어가 어긋난다 (KR=한국어 / US=영어)."""


class ForbiddenClaimError(StoryboardError):
    """효능 암시 또는 가짜 체험담."""


class BaselineError(StoryboardError):
    """기준선이 실측 지표에서 유도되지 않았다."""


# ---------------------------------------------------------------------------
# 근거 인덱스
# ---------------------------------------------------------------------------


def evidence_index(evidence: ProductEvidence) -> Dict[str, Dict[str, Any]]:
    """provenance 를 ``ev1``, ``ev2`` … 로 인덱싱한다 (순서 고정 = 재현 가능).

    근거 객체 자체가 없으면 여기서 크게 실패한다.
    """
    if evidence is None:
        raise EvidenceError("ProductEvidence 가 없다 — 근거 없는 스토리보드는 생성 금지")
    if not isinstance(evidence, ProductEvidence):
        raise EvidenceError(f"evidence 는 ProductEvidence 여야 한다: {type(evidence)!r}")
    evidence.validate()
    return {f"ev{i + 1}": dict(entry)
            for i, entry in enumerate(evidence.provenance)}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _claim_is_supported(claim: str, quote: str) -> bool:
    """주장이 인용 원문에 담겨 있는가 — 부분 문자열 양방향 포함만 인정한다."""
    c, q = _normalise(claim), _normalise(quote)
    if not c or not q:
        return False
    return c in q or q in c


# ---------------------------------------------------------------------------
# 언어 게이트 · 금지 표현
# ---------------------------------------------------------------------------


def _assert_language(text: str, market: str, where: str) -> None:
    has_hangul = bool(_HANGUL.search(text))
    if market == "KR":
        if not has_hangul:
            raise MarketLanguageError(
                f"{where}: KR 시장 카피는 한국어여야 한다: {text!r}")
    else:  # US
        if has_hangul:
            raise MarketLanguageError(
                f"{where}: US 시장 카피는 영어여야 한다 (한글 발견): {text!r}")
        if not _LATIN_WORD.search(text):
            raise MarketLanguageError(
                f"{where}: US 시장 카피에 영문이 없다: {text!r}")


def _assert_no_forbidden_claim(text: str, where: str) -> None:
    for pattern in _FORBIDDEN_RE:
        if pattern.search(text):
            raise ForbiddenClaimError(
                f"{where}: 효능 암시/가짜 체험담 표현 금지 "
                f"(패턴 {pattern.pattern!r}): {text!r}")


def _assert_single_idea(text: str, where: str) -> None:
    lowered = f" {text.lower()} "
    for marker in _MULTI_IDEA_MARKERS:
        if marker in lowered:
            raise OneIdeaError(
                f"{where}: 한 컷 = 동작 1개 = 효용 1개 규칙 위반 "
                f"(결합 표현 {marker!r}): {text!r}")


def _assert_no_markers(text: str, markers, where: str, hint: str) -> None:
    lowered = text.lower()
    for marker in markers:
        if marker in lowered:
            raise OneIdeaError(f"{where}: {hint} (금지 표현 {marker!r}): {text!r}")


# ---------------------------------------------------------------------------
# 기준선 — 실측 metrics.jsonl 집계에서만
# ---------------------------------------------------------------------------


def compute_baseline_from_metrics(path: str, market: str, metric: str = "views",
                                  pattern_value: Optional[float] = None
                                  ) -> Dict[str, Any]:
    """``state/metrics.jsonl`` 실측 기록에서 기준선을 계산한다.

    기록이 없으면 그럴듯한 숫자를 만들어 넣지 않고 ``BaselineError`` 로 죽는다.
    """
    vc._require_market(market)
    if not path or not os.path.isfile(path):
        raise BaselineError(
            f"metrics 파일이 없다: {path!r} — 기준선을 지어내지 않는다")

    values: List[float] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if str(row.get("country") or "").upper() != market:
                continue
            insights = row.get("insights") or {}
            value = insights.get(metric) if isinstance(insights, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            values.append(float(value))

    if not values:
        raise BaselineError(
            f"{path} 에 market={market} metric={metric} 실측 기록이 없다 "
            f"— 기준선을 손으로 채우지 말 것 (2026-08-27 플레이스홀더 사고)")

    baseline = {
        "metric": metric,
        "baseline_value": sum(values) / len(values),
        "pattern_value": pattern_value if pattern_value is not None
                         else sum(values) / len(values),
        "sample_size": len(values),
        "source": path,
        "compared_at": vc.datetime.now(vc.timezone.utc).astimezone().isoformat(),
        "derivation": "mean of recorded metrics.jsonl insights",
    }
    return baseline


def assert_measured_baseline(baseline: Any) -> Dict[str, Any]:
    """기준선이 실측 파일에서 유도됐는지 확인한다. 손으로 적은 값은 거부."""
    if not isinstance(baseline, dict):
        raise BaselineError(f"baseline 은 dict 여야 한다: {baseline!r}")
    for key in ("metric", "baseline_value", "pattern_value", "sample_size",
                "source", "compared_at"):
        value = baseline.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise BaselineError(f"baseline.{key} 가 비어 있다")
    source = str(baseline["source"])
    lowered = source.lower()
    for marker in _PLACEHOLDER_SOURCE_MARKERS:
        if marker in lowered:
            raise BaselineError(
                f"baseline.source 가 실측 출처가 아니다: {source!r} "
                f"(금지 표현 {marker!r}) — 실제 metrics 집계만 허용")
    if not os.path.isfile(source):
        raise BaselineError(
            f"baseline.source 가 실존하는 실측 파일이 아니다: {source!r} "
            f"— 기준선은 반드시 기록된 지표에서 계산돼야 한다")
    if not isinstance(baseline["sample_size"], int) or baseline["sample_size"] < 1:
        raise BaselineError(f"baseline.sample_size 가 유효하지 않다: "
                            f"{baseline['sample_size']!r}")
    return baseline


# ---------------------------------------------------------------------------
# 스토리보드 결과 타입
# ---------------------------------------------------------------------------


@dataclass
class StoryboardCut:
    """검증을 통과한 한 컷 — 근거 ID 를 반드시 들고 있다."""

    index: int
    duration_seconds: int
    action: str
    benefit: str
    claim: str
    evidence_id: str
    evidence_quote: str
    evidence_source_url: str
    voice_line: str
    first_frame_prompt: str
    motion_prompt: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "duration_seconds": self.duration_seconds,
            "action": self.action,
            "benefit": self.benefit,
            "claim": self.claim,
            "evidence_id": self.evidence_id,
            "evidence_quote": self.evidence_quote,
            "evidence_source_url": self.evidence_source_url,
            "voice_line": self.voice_line,
            "first_frame_prompt": self.first_frame_prompt,
            "motion_prompt": self.motion_prompt,
        }


@dataclass
class GroundedStoryboard:
    """생성 결과 — 계약 Storyboard 를 감싸고 근거·고지를 함께 들고 간다."""

    storyboard_id: str
    run_id: str
    product_id: str
    market: str
    content_draft_id: str
    viral_pattern_ids: List[str]
    complexity: str
    cuts: List[StoryboardCut]
    disclosure: Dict[str, Any]
    evidence_ids: List[str] = field(default_factory=list)
    baseline: Optional[Dict[str, Any]] = None

    def total_duration_seconds(self) -> int:
        return sum(c.duration_seconds for c in self.cuts)

    def as_contract_storyboard(self) -> vc.Storyboard:
        """상류 계약 타입으로 투영 — 계약 검증기를 그대로 재사용할 수 있다."""
        return vc.Storyboard(
            storyboard_id=self.storyboard_id,
            run_id=self.run_id,
            product_id=self.product_id,
            market=self.market,
            viral_pattern_ids=list(self.viral_pattern_ids),
            content_draft_id=self.content_draft_id,
            cuts=[vc.CutPrompt(index=c.index, prompt=c.first_frame_prompt,
                               duration_seconds=c.duration_seconds)
                  for c in self.cuts],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "storyboard_id": self.storyboard_id,
            "run_id": self.run_id,
            "product_id": self.product_id,
            "market": self.market,
            "content_draft_id": self.content_draft_id,
            "viral_pattern_ids": list(self.viral_pattern_ids),
            "complexity": self.complexity,
            "total_duration_seconds": self.total_duration_seconds(),
            "cuts": [c.to_dict() for c in self.cuts],
            "disclosure": dict(self.disclosure),
            "evidence_ids": list(self.evidence_ids),
            "baseline": dict(self.baseline) if self.baseline else None,
        }


# ---------------------------------------------------------------------------
# 모델 호출 시임
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You plan HeightCue short-form UGC micro-storyboards.

HARD RULES — a violation makes the whole plan invalid:
1. Emit exactly the requested number of cuts. Every cut lasts exactly 5 seconds.
2. ONE CUT = ONE ACTION = ONE BENEFIT. Never combine two actions, two benefits,
   or two scenes in a cut. No montage, split screen, collage, or scene change.
3. Every `claim` must be a literal restatement of the quote of the evidence item
   named by `evidence_id`. Facts absent from the supplied evidence are forbidden:
   never invent a spec, a benefit, an effect, or a testimonial.
4. No efficacy, growth, or medical implication. No first-person product
   experience — the operator has not used the product.
5. Korean market copy in Korean only. US market copy in English only.
6. `first_frame_prompt` describes ONE still vertical 9:16 frame with a single
   subject. `motion_prompt` describes camera/subject motion only.

Return JSON: {"cuts": [{"index", "duration_seconds", "action", "benefit",
"claim", "evidence_id", "voice_line", "first_frame_prompt", "motion_prompt"}]}
"""


def _default_model(system_prompt: str, payload: Dict[str, Any]) -> Any:
    """운영 경로: 기존 OpenRouter 호출 계층을 재사용한다 (새 HTTP 클라이언트 금지)."""
    import generate
    return generate.llm_call(_default_model.cfg, system_prompt, payload,
                             json_mode=True, temperature=0.4)


# ---------------------------------------------------------------------------
# 생성기
# ---------------------------------------------------------------------------


def _coerce_response(response: Any) -> Dict[str, Any]:
    """신뢰할 수 없는 모델 출력을 구조화 dict 로 강제한다."""
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError as exc:
            raise ModelOutputError(
                f"모델 출력이 JSON 이 아니다: {exc} :: {response[:120]!r}") from exc
    if not isinstance(response, dict):
        raise ModelOutputError(
            f"모델 출력은 JSON 객체여야 한다: {type(response).__name__}")
    cuts = response.get("cuts")
    if not isinstance(cuts, list):
        raise ModelOutputError(
            f"모델 출력에 cuts 배열이 없다: keys={sorted(response)}")
    return response


def _require_field(cut: Dict[str, Any], name: str, where: str,
                   max_chars: int) -> str:
    value = cut.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ModelOutputError(f"{where}.{name} 는 비어 있을 수 없는 문자열이어야 한다: "
                               f"{value!r}")
    value = value.strip()
    if len(value) > max_chars:
        raise ModelOutputError(
            f"{where}.{name} 가 최대 {max_chars}자를 넘는다: {len(value)}자")
    return value


def _validate_cut(raw: Any, position: int, market: str,
                  index_by_id: Dict[str, Dict[str, Any]]) -> StoryboardCut:
    where = f"cuts[{position}]"
    if not isinstance(raw, dict):
        raise ModelOutputError(f"{where} 는 객체여야 한다: {raw!r}")

    index = raw.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        raise ModelOutputError(f"{where}.index 는 1 이상 정수여야 한다: {index!r}")

    # 길이는 계약 검증기가 판단한다 (5초 아니면 DurationError).
    vc._require_cut_duration(raw.get("duration_seconds"),
                             f"{where}.duration_seconds")

    limits = {"action": ACTION_MAX_CHARS, "benefit": BENEFIT_MAX_CHARS,
              "claim": VOICE_LINE_MAX_CHARS, "voice_line": VOICE_LINE_MAX_CHARS,
              "first_frame_prompt": PROMPT_MAX_CHARS,
              "motion_prompt": PROMPT_MAX_CHARS}
    fields = {name: _require_field(raw, name, where, limits[name])
              for name in REQUIRED_CUT_TEXT_FIELDS}

    # 1) 시장·언어 게이트 (근거 검사보다 먼저 — 언어가 틀리면 언어로 죽는다)
    _assert_language(fields["voice_line"], market, f"{where}.voice_line")

    # 2) 금지 표현
    for name in ("voice_line", "claim", "benefit"):
        _assert_no_forbidden_claim(fields[name], f"{where}.{name}")

    # 3) 컷 1개 = 동작 1개 = 효용 1개
    _assert_single_idea(fields["action"], f"{where}.action")
    _assert_single_idea(fields["benefit"], f"{where}.benefit")
    _assert_no_markers(fields["first_frame_prompt"], _MONTAGE_MARKERS,
                       f"{where}.first_frame_prompt",
                       "첫 프레임은 단일 장면이어야 한다")
    _assert_no_markers(fields["motion_prompt"], _SCENE_CHANGE_MARKERS,
                       f"{where}.motion_prompt",
                       "I2V 프롬프트는 모션만 기술해야 한다")

    # 4) 근거 결속
    evidence_id = raw.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise EvidenceError(f"{where}.evidence_id 가 없다 — 근거 없는 주장 금지")
    evidence_id = evidence_id.strip()
    entry = index_by_id.get(evidence_id)
    if entry is None:
        raise EvidenceError(
            f"{where}.evidence_id={evidence_id!r} 가 공급된 근거에 없다 "
            f"(허용: {sorted(index_by_id)})")
    if not _claim_is_supported(fields["claim"], entry.get("quote", "")):
        raise EvidenceError(
            f"{where}.claim 이 근거 원문으로 뒷받침되지 않는다 — "
            f"claim={fields['claim']!r} quote={entry.get('quote')!r}")
    if _normalise(fields["claim"]) not in _normalise(fields["voice_line"]):
        raise EvidenceError(
            f"{where}.voice_line 이 근거 주장을 담고 있지 않다: "
            f"{fields['voice_line']!r}")

    return StoryboardCut(
        index=index,
        duration_seconds=CUT_DURATION_SECONDS,
        evidence_id=evidence_id,
        evidence_quote=entry.get("quote", ""),
        evidence_source_url=entry.get("source_url", ""),
        **fields,
    )


def disclosure_for(market: str) -> Dict[str, Any]:
    """시장별 제휴 고지 의무 — 스토리보드가 끝까지 들고 간다."""
    vc._require_market(market)
    return {"market": market, "required": True, "text": DISCLOSURE_TEXT[market],
            "placement": "on_screen_and_caption"}


def generate_storyboard(cfg: Dict[str, Any], evidence: Optional[ProductEvidence],
                        market: str, run_id: str, content_draft_id: str,
                        viral_pattern_ids: List[str],
                        *, complexity: str = DEFAULT_COMPLEXITY,
                        model: Optional[Callable] = None,
                        storyboard_id: Optional[str] = None,
                        baseline: Optional[Dict[str, Any]] = None,
                        ) -> GroundedStoryboard:
    """근거에 결속된 5/10/15초 마이크로 스토리보드를 만든다.

    ``model`` 은 테스트 주입 시임 (``codex_image_bridge.runner=`` 와 같은 패턴).
    생략하면 기존 OpenRouter 호출 계층을 쓴다.
    """
    # --- 상류 계보 검증 (모델을 부르기 전에 전부 확인) -------------------
    vc._require_market(market)
    vc._require_id(run_id, "run_id")
    vc._require_id(content_draft_id, "content_draft_id")
    if not viral_pattern_ids:
        raise LineageError("viral_pattern_ids 가 비어 있다 — 선택된 바이럴 패턴 계보 필수")
    for i, pid in enumerate(viral_pattern_ids):
        vc._require_id(pid, f"viral_pattern_ids[{i}]")

    if complexity not in COMPLEXITY_CUTS:
        raise StoryboardError(
            f"알 수 없는 complexity: {complexity!r} — 허용: {sorted(COMPLEXITY_CUTS)}")
    cut_count = COMPLEXITY_CUTS[complexity]

    index_by_id = evidence_index(evidence)
    if evidence.market != market:
        raise LineageError(
            f"근거 시장과 요청 시장이 다르다: evidence={evidence.market!r} "
            f"request={market!r} — 시장 간 근거 전용 금지")

    if baseline is not None:
        assert_measured_baseline(baseline)

    disclosure = disclosure_for(market)

    payload = {
        "market": market,
        "language": "Korean only" if market == "KR" else "English only",
        "product_id": evidence.product_id,
        "content_draft_id": content_draft_id,
        "viral_pattern_ids": list(viral_pattern_ids),
        "complexity": complexity,
        "cut_count": cut_count,
        "cut_duration_seconds": CUT_DURATION_SECONDS,
        "total_duration_seconds": cut_count * CUT_DURATION_SECONDS,
        "aspect_ratio": vc.VIDEO_ASPECT_RATIO,
        "resolution": vc.VIDEO_RESOLUTION,
        "evidence": index_by_id,
        "disclosure": disclosure,
        "rules": [
            "one cut = one action = one benefit",
            "every claim must restate the quote of its evidence_id",
            "no fact outside the supplied evidence",
            "no efficacy, growth, or medical implication",
            "no first-person product experience",
        ],
    }
    if baseline is not None:
        payload["baseline"] = baseline

    caller = model
    if caller is None:
        _default_model.cfg = cfg
        caller = _default_model

    response = _coerce_response(caller(SYSTEM_PROMPT, payload))
    raw_cuts = response["cuts"]

    # --- 신뢰할 수 없는 출력 하드 검증 -----------------------------------
    # 계약 범위(1~3컷)를 먼저 본다 — 4컷은 DurationError 로 죽는다.
    vc._require_cut_count(raw_cuts)
    if len(raw_cuts) != cut_count:
        raise ModelOutputError(
            f"요청한 컷 수와 모델 출력이 다르다: 요청 {cut_count} != 출력 "
            f"{len(raw_cuts)} — 조용히 잘라내지 않는다")

    cuts = [_validate_cut(raw, i + 1, market, index_by_id)
            for i, raw in enumerate(raw_cuts)]
    vc._require_sequential(cuts)

    total = sum(c.duration_seconds for c in cuts)
    if total not in ALLOWED_TOTAL_DURATIONS:
        raise DurationError(
            f"총 길이는 {ALLOWED_TOTAL_DURATIONS} 중 하나여야 한다: {total}")

    board = GroundedStoryboard(
        storyboard_id=storyboard_id or f"sb-{run_id}-{evidence.product_id}",
        run_id=run_id,
        product_id=evidence.product_id,
        market=market,
        content_draft_id=content_draft_id,
        viral_pattern_ids=list(viral_pattern_ids),
        complexity=complexity,
        cuts=cuts,
        disclosure=disclosure,
        evidence_ids=[c.evidence_id for c in cuts],
        baseline=dict(baseline) if baseline else None,
    )
    # 상류 계약 검증기로 한 번 더 통과시킨다 — 계약 위반이면 여기서 죽는다.
    board.as_contract_storyboard().validate()
    return board
