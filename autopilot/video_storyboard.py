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

#: 언어 게이트 대상 — 시장에 노출되거나 생성 모델에 전달되는 모든 텍스트.
#: KR 스토리보드에 영어 benefit·프롬프트가 섞이는 구멍을 막는다.
#: ``generation_prompt`` 은 파생 필드지만 **실제로 fal 에 나가는 문자열**이므로
#: 게이트를 면제하지 않는다.
MARKET_FACING_TEXT_FIELDS = ("action", "benefit", "claim", "voice_line",
                             "first_frame_prompt", "motion_prompt",
                             "generation_prompt")

#: 금지 표현 스캔 대상 — 이미지/영상 모델에 도달하는 프롬프트까지 포함한다.
#: 효능 암시를 문장이 아니라 그림으로 렌더링하는 우회로를 막는다.
FORBIDDEN_SCAN_TEXT_FIELDS = ("action", "benefit", "claim", "voice_line",
                              "first_frame_prompt", "motion_prompt",
                              "generation_prompt")

# ---------------------------------------------------------------------------
# 스토리 · 발화 주도 컷 (2026-08-29 유료 1건 반려에서 나온 재설계)
#
# 왜 필요한가: 이전 설계는 ``motion_prompt`` 를 그대로 fal 에 넘겼고, 그 값은
# "Slow subtle handheld push-in on the carton" 같은 **순수 카메라 지시**였다.
# MiniMax H3 Max 는 시키는 대로 무음 클로즈업을 만들었고, 산출물은 -91.0 dB
# (완전 무음) 로 측정돼 ``video_qa.check_spoken_content`` 가 빈 전사로 실패했다.
#
# 핵심 사실: **H3 Max 는 네이티브 오디오(립싱크 대사 포함)를 생성한다.**
# 별도 TTS 단계는 없고 필요도 없다 — 말은 프롬프트에서 나온다. 결함은
# "TTS 가 없다" 가 아니라 "말하라고 시키지 않았다" 였다.
#
# 그래서 이제 컷마다 ``generation_prompt`` 를 파생시킨다. MiniMax 가 문서화한
# 3필드 구조(``integrated_multimodal_description`` / ``overall_soundscape`` /
# ``non_diegetic_music``)를 그대로 따르고, 승인된 ``voice_line`` 을 대사
# 델리미터 ``<d>[언어] …</d>`` 안에 **한 글자도 바꾸지 않고** 싣는다.
# ---------------------------------------------------------------------------

#: 대사 델리미터 — MiniMax 문서 표기. 언어 태그 + 발화문 그대로.
DIALOGUE_OPEN = "<d>"
DIALOGUE_CLOSE = "</d>"
DIALOGUE_LANGUAGE = {"KR": "Korean", "US": "English"}

_DIALOGUE_RE = re.compile(r"<d>\[[A-Za-z]+\]\s*(.*?)</d>", re.S)

#: 컷 서사 역할 — **``viral_ugc.GRAMMAR_FIELDS`` 의 축에서 그대로 가져온다.**
#: 관측된 패턴 원장의 문법을 재사용하는 것이지 새 서사 이론을 지어내지 않는다.
#: 3컷은 훅(0~2초) → 실사용 시연 → 확인 가능한 근거 순으로 전개하고,
#: 1컷은 훅을 따로 세울 자리가 없으므로 그 자체가 완결된 시연이다.
CUT_ROLE_GRAMMAR_AXES = ("hook_0_2s", "demo_action", "proof_moment")

_ROLE_ARCS = {
    1: ("demo_action",),
    2: ("hook_0_2s", "demo_action"),
    3: ("hook_0_2s", "demo_action", "proof_moment"),
}

#: 역할별 장면 비트 (영문 — H3 스캐폴딩 언어. 시장 카피는 컷 필드가 담는다).
_ROLE_BEATS = {
    "hook_0_2s": ("Within the first half second S1 is already mid-gesture, "
                  "lifting the product into the lens so the viewer is caught "
                  "before the first word lands."),
    "demo_action": ("S1 demonstrates the product the way it is actually used "
                    "at home, keeping both hands and the product inside the "
                    "frame the whole time."),
    "proof_moment": ("S1 holds the product close to the lens so its large "
                     "primary label fills much of the frame, letting the "
                     "viewer check the headline figure for themselves."),
}

#: 화면 위 글자 요청 신호 — 자막은 **후반 작업 패스**다. 베이스 영상 소재는
#: 절대 글자를 태우지 않는다 (모델이 렌더한 글자는 지울 수 없다).
_ON_SCREEN_TEXT_MARKERS = (
    "자막", "캡션", "텍스트 오버레이", "글자가 뜨", "문구가 뜨", "타이포",
    "화면에 글", "제목 카드",
    "subtitle", "caption", "on-screen text", "onscreen text", "text overlay",
    "overlay text", "lower third", "title card", "kinetic type", "supertitle",
)

#: '사람이 등장해 무언가 한다'는 신호. 하나도 없으면 그 컷은 정물 클로즈업이라
#: 판단하고 거부한다 — 무음 제품 클로즈업이 바로 반려된 그 영상이다.
#: 라틴 표기는 단어 경계로 잡는다: "handheld push-in" 은 손이 아니라 카메라다.
_HUMAN_PRESENCE_PATTERNS = (
    r"손", r"손가락", r"엄마", r"아빠", r"부모", r"아이", r"사람", r"인물",
    r"얼굴", r"입", r"말한", r"말하", r"이야기하", r"보여주", r"건네",
    r"\bhand\b", r"\bhands\b", r"\bfinger\b", r"\bfingers\b", r"\bface\b",
    r"\bwoman\b", r"\bman\b", r"\bparent\b", r"\bmother\b", r"\bfather\b",
    r"\bperson\b", r"\bchild\b", r"\bkid\b", r"\bspeaks?\b", r"\bsays?\b",
    r"\btalks?\b", r"\bshows?\b", r"\bholds?\b", r"\bpours?\b", r"\blifts?\b",
)

_HUMAN_PRESENCE_RE = tuple(re.compile(p, re.I) for p in _HUMAN_PRESENCE_PATTERNS)

#: 제휴 고지 — SSOT 부록 A 불변 문구. 시장별로 반드시 하나가 붙는다.
DISCLOSURE_TEXT = {
    "KR": "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
    "US": "As an Amazon Associate I earn from qualifying purchases.",
}

#: 시장별 승인 CTA 카피. **스토리보드가 항상 들고 나간다.**
#:
#: 합성 단계는 CTA 를 스토리보드에서만 축자 회수하는데(`CTA_SOURCE`),
#: 스토리보드가 `cta` 를 내지 않아 첫 유료 실행에서 운영자가 손으로 채워야
#: 했다. 자유 입력을 허용하면 CTA 가 스스로를 승인 집합에 넣는 셈이라
#: CaptionDriftError 가 영원히 발동하지 않는다 — 그래서 여기에 고정한다.
#: 문구는 구매·효능을 말하지 않고 정보 확인만 유도한다 (부록 A 가드레일).
CTA_TEXT = {
    "KR": "프로필 링크에서 성분표 확인하세요",
    "US": "Full ingredient list at the link in bio",
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


def forbidden_patterns() -> tuple:
    """금지 표현 정규식의 **공개 정본 접근자**.

    이 목록은 다른 모듈(예: ``video_qa`` 의 발행 직전 게이트)이 재사용한다 —
    두 벌로 관리하면 한쪽만 갱신되어 조용히 구멍이 난다. 사설 이름
    ``_FORBIDDEN_RE`` 에 직접 의존하면 이름이 바뀌는 순간 하류가 소리 없이
    깨지므로, 외부는 반드시 이 함수를 통해 읽는다.
    """
    return _FORBIDDEN_RE

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


class SilentCutError(StoryboardError):
    """컷이 사람도 발화도 없는 정물/카메라 이동만으로 기술됐다.

    2026-08-29 반려 영상의 정확한 형태다 — 이 예외가 그 형태를 다시 만들지
    못하게 막는다.
    """


class OnScreenTextError(StoryboardError):
    """생성 프롬프트가 화면 위 글자(자막·캡션)를 요청했다.

    자막은 후반 작업에서 붙인다. 모델이 태워버린 글자는 되돌릴 수 없다.
    """


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


#: 발화에서 뽑아낼 "수량" 토큰. 숫자에 붙은 단위까지 한 덩어리로 본다 —
#: ``600 iu`` 와 ``600 mg`` 는 서로 다른 사실이므로 숫자만 봐서는 안 된다.
_QUANTITY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(iu|mg|mcg|ug|µg|g|kg|ml|l|oz|drops?|servings?|%|d3|d2)?",
    re.IGNORECASE)

#: 발화가 근거 없이 얹으면 안 되는 **사실 주장 어휘**. 승인 카피를 자연스럽게
#: 다듬는 표현(관사·연결어)은 자유롭게 허용하되, 인증·시험·순위처럼 검증
#: 대상인 새 사실은 근거 원문에 없으면 막는다.
_UNEVIDENCED_CLAIM_WORDS = (
    "certified", "clinically", "clinical", "tested", "approved", "fda",
    "usda", "organic", "award", "patented", "number one", "#1", "best",
    "guaranteed", "proven", "doctor recommended", "pediatrician",
    "gmo", "vegan", "kosher", "halal", "allergen", "recommended by",
)


def _quantities(text: str) -> set:
    """텍스트의 (숫자, 단위) 집합. 단위 없는 숫자는 단위를 빈 문자열로 둔다."""
    found = set()
    for number, unit in _QUANTITY_RE.findall(str(text or "")):
        found.add((number.replace(",", ""), (unit or "").lower()))
    return found


def _assert_voice_line_supported(voice_line: str, quote: str,
                                 where: str) -> None:
    """발화가 근거 원문을 **넘어서지 않는지** 판정한다.

    옛 규칙은 ``claim`` 이 ``voice_line`` 의 리터럴 부분문자열일 것을 요구했다.
    그런데 ``claim`` 은 스펙시트 조각(``manufacturer audience: age 1+``)이고,
    콜론과 ``+`` 가 든 그 문자열을 통째로 품는 **자연스러운 영어 문장은
    존재하지 않는다** — 통과 가능한 발화가 없는 게이트였다. 실제 운영에서
    12연속 반려로 확인했다.

    지켜야 할 성질은 글자 일치가 아니라 **"근거가 뒷받침하지 않는 것을 말하지
    않는다"** 이다. 그래서 두 가지를 본다.

    1. **수량 충실도** — 발화가 말하는 모든 (숫자, 단위) 쌍은 근거 원문에도
       있어야 한다. 영양표시에서 ``600 IU`` 가 ``60 IU`` 로 바뀌는 것은 절대
       통과시키면 안 되는 실패다. 단위까지 묶어 비교하므로 ``600 mg`` 도
       다른 사실로 잡힌다.
    2. **미근거 사실 어휘** — 인증·임상·수상처럼 검증 대상인 주장은 근거
       원문에 그 단어가 없으면 거부한다.

    이 검사는 ``_claim_is_supported`` 를 **대체하지 않는다**. claim ↔ quote
    결속은 그대로 유지되고, 여기서는 그 위에 발화를 한 겹 더 검사한다.
    """
    line = str(voice_line or "").strip()
    if not line:
        raise EvidenceError(f"{where}: 발화가 비어 있다 — 말하지 않는 컷은 없다")

    spoken = _quantities(line)
    supported = _quantities(quote)
    unsupported = spoken - supported
    if unsupported:
        raise EvidenceError(
            f"{where}: 발화의 수량이 근거 원문에 없다 "
            f"{sorted(unsupported)} — 근거={quote!r} 발화={line!r}. "
            "영양표시 수치는 한 자리도 바꿀 수 없다")

    lowered = _normalise(line)
    quote_lowered = _normalise(quote)
    for word in _UNEVIDENCED_CLAIM_WORDS:
        if word in lowered and word not in quote_lowered:
            raise EvidenceError(
                f"{where}: 근거에 없는 사실 주장 {word!r} 을 발화가 덧붙였다 "
                f"— 근거={quote!r} 발화={line!r}")


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


def _assert_no_on_screen_text(text: str, where: str) -> None:
    lowered = text.lower()
    for marker in _ON_SCREEN_TEXT_MARKERS:
        if marker in lowered:
            raise OnScreenTextError(
                f"{where}: 베이스 영상 소재에 화면 위 글자를 요청할 수 없다 "
                f"(금지 표현 {marker!r}) — 자막은 후반 작업 패스다: {text!r}")


def _assert_human_presence(text: str, where: str) -> None:
    for pattern in _HUMAN_PRESENCE_RE:
        if pattern.search(text):
            return
    raise SilentCutError(
        f"{where}: 사람도 동작도 없는 카메라 지시뿐이다 — 제품 클로즈업만 "
        f"만들어지고 발화가 나오지 않는다 (2026-08-29 무음 -91.0 dB 반려): "
        f"{text!r}")


def spoken_segments(prompt: str) -> List[str]:
    """생성 프롬프트에서 **실제로 발화되도록 지시된 문장**만 뽑아낸다.

    ``video_qa.check_spoken_content`` 는 전사본을 승인 카피와 대조하며
    ``MAX_UNAPPROVED_CHARS = 1`` 로 초과 발화를 잡는다. 그래서 프롬프트가
    대사 델리미터 밖의 연출 지시를 실수로 발화 대상에 넣지 않았는지
    여기서 기계적으로 확인할 수 있다.
    """
    return [m.strip() for m in _DIALOGUE_RE.findall(str(prompt or ""))]


def story_roles_for(cut_count: int) -> tuple:
    """컷 수 → 서사 역할 배열 (``viral_ugc`` 문법 축 재사용)."""
    try:
        return _ROLE_ARCS[int(cut_count)]
    except (KeyError, TypeError, ValueError):
        raise StoryboardError(
            f"서사 역할을 배정할 수 없는 컷 수: {cut_count!r} "
            f"— 허용: {sorted(_ROLE_ARCS)}")


def build_generation_prompt(*, market: str, story_role: str, action: str,
                            voice_line: str, first_frame_prompt: str,
                            motion_prompt: str) -> str:
    """컷 하나를 **말하는 사람이 제품을 쓰는 장면**으로 서술한 H3 Max 프롬프트.

    MiniMax 가 문서화한 3필드 구조를 그대로 쓴다. 필드 이름은 규격이므로
    시장과 무관하게 영문이고, 시장 카피(동작·대사)는 컷이 들고 온 언어
    그대로 들어간다.

    **승인 카피 충실도 vs 자연스러운 딜리버리** — 이 함수의 유일한 어려운
    판단이다. 연기 지시를 대사 안에 섞으면 모델이 그 지시까지 읽어버리고,
    감정어를 대사 옆에 붙이면 추임새("음…", "자!")를 덧붙인다. 둘 다
    ``MAX_UNAPPROVED_CHARS = 1`` 에서 즉사한다. 그래서 딜리버리 언어는
    **전부 델리미터 바깥**에 두고, 델리미터 안에는 승인된 ``voice_line`` 만
    한 글자도 바꾸지 않고 넣으며, 그 직전에 "exactly these words and no
    others" 로 애드리브를 명시적으로 봉쇄한다.
    """
    language = DIALOGUE_LANGUAGE[market]
    beat = _ROLE_BEATS[story_role]
    # 문장 경계를 정리한다 — 모델 필드는 마침표 없이 오는 경우가 많고, 그대로
    # 이어붙이면 다음 문장과 한 덩어리로 읽혀 동작이 뭉개진다.
    motion = motion_prompt.rstrip(" .。") + "."
    action_text = action.rstrip(" .。")
    frame_text = first_frame_prompt.rstrip(" .。")
    return (
        "integrated_multimodal_description: [Shot 1] Live-action, cinematic "
        "hand-held UGC, vertical 9:16, one continuous take, natural daylight. "
        "The supplied image is the exact opening frame and is fully "
        f"referenced: {frame_text}. One ordinary parent (S1), "
        "casually dressed, is the only person in the shot and stays in the "
        f"same room for the whole clip. {beat} S1 performs one action and one "
        f"action only: {action_text}. {motion} S1 then looks into the lens "
        "and speaks in a warm, unhurried, mid-pitch conversational voice, "
        "lips synced precisely to the dialogue, delivering exactly these "
        "words and no others: "
        f"{DIALOGUE_OPEN}[{language}] {voice_line}{DIALOGUE_CLOSE} "
        "S1 stops speaking, keeps holding the product steady, and the shot "
        "ends. Keep the product framed CLOSE for the whole take: its primary "
        "label — the brand wordmark and the dose figure — fills roughly one "
        "third of the frame width or more, and every letter of it is "
        "reproduced exactly as printed on the real pack. Any fine print "
        "(Supplement Facts panel, ingredient list, directions, barcode) stays "
        "OUT OF FRAME or behind the hand — never render it, never make it "
        "legible. Do not rotate or re-present the pack to show more of it. "
        "No on-screen text of any kind: no subtitles, no captions, no "
        "lower thirds, no title cards, no graphic overlays, no added logos "
        "and no invented packaging wording — the frame stays clean so text "
        "can be added later in post.\n\n"
        "overall_soundscape: A quiet indoor room tone with the close-mic "
        "clarity of S1's voice, the soft intake of breath just before they "
        "speak, and the light handling sound of the product against their "
        "fingers. No other voices and no crowd noise.\n\n"
        "non_diegetic_music: N/A"
    )


# ---------------------------------------------------------------------------
# 기준선 — 실측 metrics.jsonl 집계에서만
# ---------------------------------------------------------------------------


#: 재집계 부동소수 비교 허용 오차.
BASELINE_TOLERANCE = 1e-6

#: 기준선 유도 방식 — 현재는 산술 평균 하나뿐. 모르는 method 는 거부한다.
_BASELINE_METHODS = ("mean",)


def _aggregate_metric(path: str, market: str, metric: str) -> List[float]:
    """``metrics.jsonl`` 에서 (market, metric) 실측값만 뽑아낸다.

    집계는 이 함수 하나뿐이다 — 계산 경로와 검증 경로가 같은 코드를 쓰기 때문에
    ``assert_measured_baseline`` 이 진짜로 재유도할 수 있다.
    """
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
    return values


def compute_baseline_from_metrics(path: str, market: str, metric: str = "views",
                                  pattern_value: Optional[float] = None
                                  ) -> Dict[str, Any]:
    """``state/metrics.jsonl`` 실측 기록에서 기준선을 계산한다.

    기록이 없으면 그럴듯한 숫자를 만들어 넣지 않고 ``BaselineError`` 로 죽는다.

    ``pattern_value`` 는 **측정된 값을 넘겨줄 때만** 채워진다. 넘겨주지 않으면
    ``None`` 으로 남는다 — 기준선 평균을 대신 넣으면 "패턴이 기준선과 정확히
    같은 성과를 냈다" 는 존재하지 않는 측정 결과를 지어내는 것이다.
    """
    vc._require_market(market)
    if not path or not os.path.isfile(path):
        raise BaselineError(
            f"metrics 파일이 없다: {path!r} — 기준선을 지어내지 않는다")

    values = _aggregate_metric(path, market, metric)

    if not values:
        raise BaselineError(
            f"{path} 에 market={market} metric={metric} 실측 기록이 없다 "
            f"— 기준선을 손으로 채우지 말 것 (2026-08-27 플레이스홀더 사고)")

    if pattern_value is not None and (isinstance(pattern_value, bool)
                                      or not isinstance(pattern_value, (int, float))):
        raise BaselineError(
            f"pattern_value 는 측정된 수치여야 한다: {pattern_value!r}")

    return {
        "metric": metric,
        "baseline_value": sum(values) / len(values),
        # 측정되지 않았으면 None 그대로 — 절대 평균으로 메우지 않는다.
        "pattern_value": float(pattern_value) if pattern_value is not None else None,
        "sample_size": len(values),
        "source": path,
        "compared_at": vc.datetime.now(vc.timezone.utc).astimezone().isoformat(),
        "derivation": {
            "method": "mean",
            "market": market,
            "metric": metric,
            "field": "insights",
        },
    }


def compute_baseline_from_cfg(cfg: Dict[str, Any], market: str,
                              metric: str = "views",
                              pattern_value: Optional[float] = None
                              ) -> Dict[str, Any]:
    """설정의 ``video_storyboard.metrics_path`` 로 기준선을 계산한다.

    설정 키가 없으면 기본 경로로 조용히 넘어가지 않고 크게 실패한다 — 조용히
    무시되는 설정 키가 운영자를 놀라게 하는 방식이다.
    """
    block = (cfg or {}).get("video_storyboard") or {}
    path = block.get("metrics_path")
    if not isinstance(path, str) or not path.strip():
        raise StoryboardError(
            "config.video_storyboard.metrics_path 가 없다 — 기준선 출처를 "
            "설정에서 명시해야 한다 (기본값으로 넘어가지 않는다)")
    return compute_baseline_from_metrics(path.strip(), market, metric,
                                         pattern_value)


def assert_measured_baseline(baseline: Any) -> Dict[str, Any]:
    """기준선이 실측 파일에서 **실제로 유도됐는지** 확인한다.

    문자열 휴리스틱이 아니라 재유도다: ``derivation`` 이 기술한 방식대로
    ``source`` 를 다시 집계해서 ``baseline_value`` · ``sample_size`` 가 정말
    그 파일에서 나오는지 대조한다. 손으로 적은 숫자는 실존 파일 경로를
    적어 넣어도 통과하지 못한다 (2026-08-27 플레이스홀더 사고 재발 방지).
    """
    if not isinstance(baseline, dict):
        raise BaselineError(f"baseline 은 dict 여야 한다: {baseline!r}")
    for key in ("metric", "baseline_value", "sample_size", "source",
                "compared_at", "derivation"):
        value = baseline.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise BaselineError(f"baseline.{key} 가 비어 있다")

    # pattern_value 는 None 이 허용된다 (미측정). 다만 있다면 반드시 수치다.
    if "pattern_value" not in baseline:
        raise BaselineError(
            "baseline.pattern_value 키가 없다 — 미측정이면 명시적으로 None 이어야 한다")
    pattern_value = baseline["pattern_value"]
    if pattern_value is not None and (isinstance(pattern_value, bool)
                                      or not isinstance(pattern_value, (int, float))):
        raise BaselineError(
            f"baseline.pattern_value 는 측정된 수치이거나 None 이어야 한다: "
            f"{pattern_value!r}")

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

    # --- 여기서부터가 진짜 검증: source 를 다시 집계한다 -------------------
    derivation = baseline["derivation"]
    if not isinstance(derivation, dict):
        raise BaselineError(
            f"baseline.derivation 은 유도 방식을 기술한 dict 여야 한다: "
            f"{derivation!r}")
    method = derivation.get("method")
    if method not in _BASELINE_METHODS:
        raise BaselineError(
            f"알 수 없는 baseline.derivation.method: {method!r} "
            f"— 허용: {list(_BASELINE_METHODS)}")
    market = derivation.get("market")
    metric = derivation.get("metric", baseline["metric"])
    if not isinstance(market, str) or not market.strip():
        raise BaselineError("baseline.derivation.market 이 없다 — 재집계 불가")
    if metric != baseline["metric"]:
        raise BaselineError(
            f"baseline.metric 과 derivation.metric 이 다르다: "
            f"{baseline['metric']!r} != {metric!r}")

    try:
        values = _aggregate_metric(source, str(market).upper(), str(metric))
    except OSError as exc:
        raise BaselineError(
            f"baseline.source 재집계 실패: {source!r} :: {exc}") from exc

    if not values:
        raise BaselineError(
            f"재집계 결과가 비어 있다: {source!r} 에 market={market} "
            f"metric={metric} 실측 기록이 없다 — 기준선이 이 파일에서 나올 수 없다")

    recomputed_value = sum(values) / len(values)
    if abs(recomputed_value - float(baseline["baseline_value"])) > BASELINE_TOLERANCE:
        raise BaselineError(
            f"baseline_value 가 source 재집계 결과와 다르다: "
            f"기록 {baseline['baseline_value']!r} != 재집계 {recomputed_value!r} "
            f"({source}) — 손으로 적은 수치는 거부한다")
    if len(values) != baseline["sample_size"]:
        raise BaselineError(
            f"sample_size 가 source 재집계 결과와 다르다: "
            f"기록 {baseline['sample_size']!r} != 재집계 {len(values)} "
            f"({source}) — 손으로 적은 수치는 거부한다")
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
    #: 서사 역할 (viral_ugc 문법 축). 기본값은 자기완결 시연.
    story_role: str = "demo_action"
    #: fal 로 실제 나가는 문자열. 빈 값으로 두면 후처리에서 채워진다.
    generation_prompt: str = ""

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
            "story_role": self.story_role,
            "generation_prompt": self.generation_prompt,
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
        """상류 계약 타입으로 **검증 전용** 투영 — 핸드오프 타입이 아니다.

        .. warning::
           이 투영은 손실적이다. ``disclosure``, ``evidence_ids``,
           ``evidence_quote``/``evidence_source_url``, ``claim``,
           ``voice_line``, ``benefit``, ``action``, ``baseline`` 이 전부
           떨어져 나가고 ``first_frame_prompt`` 만 ``CutPrompt.prompt`` 로
           남는다. 계약 검증기를 재사용하려고 만든 것이며, 후속 태스크에
           넘기는 핸드오프 타입으로 **절대 쓰지 말 것** — 고지 의무와 근거
           결속이 조용히 사라진다. 핸드오프에는 ``to_dict()`` 를 쓴다.
        """

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
            # CTA 는 자막/후보정 단계가 소비한다. 스토리보드가 내주지 않으면
            # 운영자가 손으로 채우게 되고, 그러면 승인 집합 대조가 무의미해진다.
            "cta": cta_for(self.market),
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
   subject. It must NOT ask for any rendered text.
7. `motion_prompt` describes what the PERSON does — a hand, a parent, a body
   performing the demonstration. A pure camera instruction ("slow push-in on
   the carton") is rejected: it produces a silent product close-up, which is
   worthless. Camera language may support the action, never replace it.
8. `voice_line` is the line the person will actually SPEAK on camera. The video
   model generates native lip-synced audio from it, so write something a parent
   would really say out loud in five seconds.
9. NEVER ask for subtitles, captions, on-screen text or graphic overlays.
   Subtitles are added later in post-production.
10. TIGHT PRODUCT FRAMING — this is measured, not stylistic. The video model
   re-draws small on-pack lettering from memory and gets it WRONG (`ORGANIC`
   came back as `CACINI`, `Booster` as `Broster`). Large glyphs survive; small
   ones forge. So every `first_frame_prompt` that shows the pack must compose
   it CLOSE, with the primary label — brand wordmark and dose figure — filling
   roughly one third of the frame width or more. NEVER write a wide shot with
   a small pack, and NEVER ask for a Supplement Facts panel, ingredient list,
   directions block, or any other fine print to be visible or legible: crop it
   out of frame or let the hand cover it.

LENGTH AND SINGULARITY LIMITS — these are enforced by a hard gate that rejects
the whole plan, so respect them exactly:
- `action`: at most 80 characters. Write a short verb phrase, e.g.
  "A parent lifts the bottle to the lens", not a full sentence with clauses.
- `benefit`: at most 80 characters.
- `voice_line`: at most 120 characters — five seconds of speech.
- `first_frame_prompt` / `motion_prompt`: at most 400 characters.
- `action` and `benefit` must each express exactly ONE idea. The literal words
  " and ", " plus ", "그리고" are rejected inside them — if you need "and", the
  cut is doing two things and must be split or trimmed.
- `voice_line` must be natural spoken English/Korean that conveys its `claim`.
  You do NOT need to quote the claim verbatim — say it the way a parent would.
  BUT: every number and unit you speak (600, IU, D3, 2.8 mL, 100 drops, age 1 …)
  must appear in the evidence quote. Changing "600 IU" to "60 IU" or "600 mg",
  or inventing a quantity, is REJECTED — this is a nutrition label. Do not add
  facts the quote does not contain (certified, clinically tested, award-winning,
  organic, doctor recommended …).

Return JSON: {"cuts": [{"index", "duration_seconds", "action", "benefit",
"claim", "evidence_id", "voice_line", "first_frame_prompt", "motion_prompt"}]}
"""


def _default_model(cfg: Dict[str, Any]) -> Callable[[str, Dict[str, Any]], Any]:
    """운영 경로 시임 팩토리 — cfg 를 **클로저로** 묶어 호출자를 돌려준다.

    함수 속성에 cfg 를 얹으면 모듈 전역 가변 상태가 되어 재진입·동시 실행에서
    서로의 설정을 덮어쓴다. 그래서 호출마다 새 클로저를 만든다.
    """

    def _call(system_prompt: str, payload: Dict[str, Any]) -> Any:
        import generate
        return generate.llm_call(cfg, system_prompt, payload,
                                 json_mode=True, temperature=0.4)

    return _call


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
                  index_by_id: Dict[str, Dict[str, Any]],
                  story_role: str = "demo_action") -> StoryboardCut:
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
    #    시장에 노출되거나 이미지/영상 모델에 도달하는 모든 텍스트를 덮는다.
    for name in MARKET_FACING_TEXT_FIELDS:
        if name in fields:
            _assert_language(fields[name], market, f"{where}.{name}")

    # 2) 금지 표현 — 말로 하든 그림으로 그리든 효능 암시는 막는다.
    #    first_frame_prompt/motion_prompt 가 빠지면 효능을 시각적으로
    #    렌더링하는 우회로가 열린다.
    for name in FORBIDDEN_SCAN_TEXT_FIELDS:
        if name in fields:
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

    # 3-b) 화면 위 글자 금지 — 자막은 후반 작업 패스다.
    _assert_no_on_screen_text(fields["first_frame_prompt"],
                              f"{where}.first_frame_prompt")
    _assert_no_on_screen_text(fields["motion_prompt"], f"{where}.motion_prompt")

    # 3-c) 사람이 실제로 무언가를 해야 한다 — 카메라 이동만인 컷은 거부.
    _assert_human_presence(fields["motion_prompt"], f"{where}.motion_prompt")

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
    # 근거 항목의 형태도 신뢰하지 않는다 — 빈 값으로 흘려보내지 않고 죽는다.
    quote = entry.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        raise EvidenceError(
            f"{where}.evidence_id={evidence_id!r} 근거 항목에 quote 가 없다: "
            f"{entry!r}")
    source_url = entry.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        raise RightsError(
            f"{where}.evidence_id={evidence_id!r} 근거 항목에 source_url 이 없다 "
            f"— 출처 없는 근거는 근거가 아니다: {entry!r}")

    if not _claim_is_supported(fields["claim"], quote):
        raise EvidenceError(
            f"{where}.claim 이 근거 원문으로 뒷받침되지 않는다 — "
            f"claim={fields['claim']!r} quote={quote!r}")
    _assert_voice_line_supported(fields["voice_line"], quote,
                                 f"{where}.voice_line")

    # 5) 실제로 fal 에 나갈 문자열을 여기서 조립하고, 같은 게이트를 다시 건다.
    #    파생 필드라고 면제하면 검사받지 않은 문자열이 모델에 도달한다.
    generation_prompt = build_generation_prompt(
        market=market, story_role=story_role,
        action=fields["action"], voice_line=fields["voice_line"],
        first_frame_prompt=fields["first_frame_prompt"],
        motion_prompt=fields["motion_prompt"])
    _assert_language(generation_prompt, market, f"{where}.generation_prompt")
    _assert_no_forbidden_claim(generation_prompt, f"{where}.generation_prompt")
    spoken = spoken_segments(generation_prompt)
    if spoken != [fields["voice_line"]]:
        raise ModelOutputError(
            f"{where}.generation_prompt 의 발화 지시가 승인 카피와 다르다 "
            f"(MAX_UNAPPROVED_CHARS=1 에서 QA 가 떨어진다): {spoken!r}")

    return StoryboardCut(
        index=index,
        duration_seconds=CUT_DURATION_SECONDS,
        evidence_id=evidence_id,
        evidence_quote=quote.strip(),
        evidence_source_url=source_url.strip(),
        story_role=story_role,
        generation_prompt=generation_prompt,
        **fields,
    )


def disclosure_for(market: str) -> Dict[str, Any]:
    """시장별 제휴 고지 의무 — 스토리보드가 끝까지 들고 간다."""
    vc._require_market(market)
    return {"market": market, "required": True, "text": DISCLOSURE_TEXT[market],
            "placement": "on_screen_and_caption"}


def cta_for(market: str) -> Dict[str, Any]:
    """시장별 승인 CTA 블록.

    합성 단계(`video_compose.extract_cta`)는 CTA 를 **스토리보드에서만**
    읽는데 스토리보드가 `cta` 를 내놓지 않아, 첫 유료 실행에서 운영자가
    손으로 넣어야 했다. 자유 입력 CTA 는 승인 집합에 스스로를 넣는 것과
    같아 CaptionDriftError 를 영원히 무력화한다 — 그래서 카피를 여기에
    고정하고 스토리보드가 항상 들고 나가게 한다.
    """
    vc._require_market(market)
    return {"market": market, "text": CTA_TEXT[market],
            "source": "video_storyboard.CTA_TEXT"}


def resolve_complexity(cfg: Dict[str, Any],
                       complexity: Optional[str] = None) -> str:
    """복잡도 결정: 명시 인자 > ``config.video_storyboard.default_complexity`` > standard.

    설정에 이상한 값이 들어 있으면 조용히 기본값으로 되돌아가지 않고 죽는다.
    """
    if complexity is not None:
        chosen, origin = complexity, "인자"
    else:
        block = (cfg or {}).get("video_storyboard") or {}
        configured = block.get("default_complexity")
        if configured is None:
            chosen, origin = DEFAULT_COMPLEXITY, "기본값"
        else:
            chosen, origin = configured, "config.video_storyboard.default_complexity"
    if chosen not in COMPLEXITY_CUTS:
        raise StoryboardError(
            f"알 수 없는 complexity ({origin}): {chosen!r} "
            f"— 허용: {sorted(COMPLEXITY_CUTS)}")
    return chosen


def generate_storyboard(cfg: Dict[str, Any], evidence: Optional[ProductEvidence],
                        market: str, run_id: str, content_draft_id: str,
                        viral_pattern_ids: List[str],
                        *, complexity: Optional[str] = None,
                        model: Optional[Callable] = None,
                        storyboard_id: Optional[str] = None,
                        baseline: Optional[Dict[str, Any]] = None,
                        ) -> GroundedStoryboard:
    """근거에 결속된 5/10/15초 마이크로 스토리보드를 만든다.

    ``model`` 은 테스트 주입 시임 (``codex_image_bridge.runner=`` 와 같은 패턴).
    생략하면 기존 OpenRouter 호출 계층을 쓴다.

    ``complexity`` 를 생략하면 ``config.video_storyboard.default_complexity``
    를 따르고, 그것도 없으면 ``standard`` (10초/2컷) 다.
    """
    # --- 상류 계보 검증 (모델을 부르기 전에 전부 확인) -------------------
    vc._require_market(market)
    vc._require_id(run_id, "run_id")
    vc._require_id(content_draft_id, "content_draft_id")
    if not viral_pattern_ids:
        raise LineageError("viral_pattern_ids 가 비어 있다 — 선택된 바이럴 패턴 계보 필수")
    for i, pid in enumerate(viral_pattern_ids):
        vc._require_id(pid, f"viral_pattern_ids[{i}]")

    complexity = resolve_complexity(cfg, complexity)
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
            "motion_prompt must describe a person demonstrating the product, "
            "not a camera move over a static object",
            "voice_line is spoken aloud on camera by that person",
            "never request subtitles, captions or any on-screen text",
        ],
    }
    payload["story_roles"] = list(story_roles_for(cut_count))
    if baseline is not None:
        payload["baseline"] = baseline

    caller = model
    if caller is None:
        caller = _default_model(cfg)

    response = _coerce_response(caller(SYSTEM_PROMPT, payload))
    raw_cuts = response["cuts"]

    # --- 신뢰할 수 없는 출력 하드 검증 -----------------------------------
    # 계약 범위(1~3컷)를 먼저 본다 — 4컷은 DurationError 로 죽는다.
    vc._require_cut_count(raw_cuts)
    if len(raw_cuts) != cut_count:
        raise ModelOutputError(
            f"요청한 컷 수와 모델 출력이 다르다: 요청 {cut_count} != 출력 "
            f"{len(raw_cuts)} — 조용히 잘라내지 않는다")

    roles = story_roles_for(len(raw_cuts))
    cuts = [_validate_cut(raw, i + 1, market, index_by_id, roles[i])
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
