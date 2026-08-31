#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical active policy for the HeightCue friction-commerce journey.

This module contains only current operating rules. Historical copy, publication
logs, and archived strategy documents must not import from or modify it.
"""

from __future__ import annotations

import re
from typing import Any


POSITIONING = {
    "KR": "아이 키우는 집의 반복되는 귀찮음을 줄이는 제품 판정",
    "US": "Product verdicts for recurring parenting friction",
}

PUBLIC_CATEGORIES = {
    "sleep_morning": {
        "KR": "잠과 아침",
        "US": "Sleep & Morning",
        "aliases": ("sleep", "bedtime", "morning", "sleep_environment"),
    },
    "meals_lunch": {
        "KR": "식사와 도시락",
        "US": "Meals & Lunchboxes",
        "aliases": ("nutrition", "meal", "meals", "food", "lunch", "lunchbox"),
    },
    "play_movement": {
        "KR": "놀이와 움직임",
        "US": "Play & Movement",
        "aliases": ("exercise", "activity", "play", "movement", "sports"),
    },
    "study_routine": {
        "KR": "공부와 루틴",
        "US": "Study & Routine",
        "aliases": ("posture", "study", "desk", "routine", "schoolwork"),
    },
    "storage_cleanup": {
        "KR": "정리와 어질러짐",
        "US": "Storage & Cleanup",
        "aliases": ("storage", "cleanup", "cleaning", "laundry", "organization", "mess"),
    },
}

GENERIC_REPLY_MECHANISMS = {
    "sleep_morning": (
        {"id": "next_day_station", "KR": "다음 날 첫 동작에 필요한 물건을 한 자리에 전날 모아 둔다", "US": "stage tomorrow's first-step items together the night before"},
        {"id": "same_order_transition", "KR": "바뀌는 순서를 매일 같은 두세 단계로 줄인다", "US": "reduce the transition to the same two or three steps each day"},
    ),
    "meals_lunch": (
        {"id": "next_use_grouping", "KR": "다음 사용 순서대로 준비물을 한 묶음으로 둔다", "US": "group supplies in the order they will be used next"},
        {"id": "single_reset_zone", "KR": "씻고 다시 둘 자리를 한 곳으로 고정한다", "US": "use one fixed landing zone for washing and reset"},
    ),
    "play_movement": (
        {"id": "first_step_ready", "KR": "시작에 필요한 첫 단계만 미리 꺼내 둔다", "US": "leave only the first step ready to start"},
        {"id": "bounded_reset", "KR": "놀이가 끝나는 경계와 돌아갈 자리를 같이 정한다", "US": "define both the stopping boundary and the reset spot"},
    ),
    "study_routine": (
        {"id": "visible_next_action", "KR": "다음 행동 하나만 눈에 보이게 둔다", "US": "keep only the next action visible"},
        {"id": "transition_cue", "KR": "시간보다 행동 전환 신호를 일정하게 둔다", "US": "use a consistent cue for the change of activity"},
    ),
    "storage_cleanup": (
        {"id": "front_access", "KR": "위에서 쌓지 않고 앞에서 꺼내도록 접근 방향을 바꾼다", "US": "change access from top-stacked to front-reachable"},
        {"id": "one_home_rule", "KR": "자주 흩어지는 종류마다 돌아갈 자리 하나만 둔다", "US": "give each frequently scattered type one return spot"},
    ),
}

OUTREACH_QUERY_PACKS = {
    "KR": (
        {"category": "sleep_morning", "query": "잠들기 전 준비 실랑이"},
        {"category": "sleep_morning", "query": "아침 등교 준비 깜빡"},
        {"category": "meals_lunch", "query": "도시락통 씻기 귀찮음"},
        {"category": "meals_lunch", "query": "아이 아침 식사 준비 시간"},
        {"category": "play_movement", "query": "실내 놀이 정리 반복"},
        {"category": "play_movement", "query": "아이 운동 준비 실랑이"},
        {"category": "study_routine", "query": "숙제 시작 실랑이"},
        {"category": "study_routine", "query": "아이 책상 준비 루틴"},
        {"category": "storage_cleanup", "query": "장난감 수납 매일 어질러짐"},
        {"category": "storage_cleanup", "query": "아이 빨래 정리 반복"},
    ),
    "US": (
        {"category": "sleep_morning", "query": "bedtime routine friction parents"},
        {"category": "sleep_morning", "query": "school morning scramble parents"},
        {"category": "meals_lunch", "query": "lunchbox cleanup parent routine"},
        {"category": "meals_lunch", "query": "kids breakfast prep time"},
        {"category": "play_movement", "query": "indoor play reset parents"},
        {"category": "play_movement", "query": "kids activity setup friction"},
        {"category": "study_routine", "query": "homework transition struggle"},
        {"category": "study_routine", "query": "kids desk routine clutter"},
        {"category": "storage_cleanup", "query": "toy clutter daily reset"},
        {"category": "storage_cleanup", "query": "family laundry sorting kids"},
    ),
}

AFFILIATE_DISCLOSURES = {
    "KR": "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
    "US_LINK": "#ad",
    "US_ACCOUNT": "As an Amazon Associate I earn from qualifying purchases.",
}

CADENCE = {
    "original_posts_per_market_per_day": 2,
    "outreach_replies_per_market_per_day": (10, 15),
}

_RETIRED_PERSONA_PATTERNS = (
    re.compile(r"\b167\s*cm\b", re.I),
    re.compile(r"\b5\s*['’]\s*6\s*(?:[\"”]|inch(?:es)?)?", re.I),
    re.compile(r"팩트\s*폭격기", re.I),
    re.compile(r"\b(?:the\s+)?5\s*['’]\s*6\s*(?:[\"”]\s*)?uncle\b", re.I),
)

_MEASUREMENT_COMMERCE_PATTERNS = (
    re.compile(r"신장계|키\s*재기|키재기|키즈\s*미터|키\s*측정기|벽걸이\s*키", re.I),
    re.compile(r"\bstadiometer\b|height\s*(?:meter|measure(?:ment|r)?)|growth\s*chart\s*rod", re.I),
)

_GROWTH_COMMERCE_PATTERNS = (
    re.compile(r"키\s*성장|성장판\s*(?:자극|활성)|성장\s*호르몬\s*(?:촉진|분비)", re.I),
    re.compile(r"height\s*growth|grow\s*taller|growth\s*plate\s*stimulat", re.I),
)

_CAREGIVER_SHAMING = {
    "KR": (
        re.compile(r"(?:이걸|그걸|이런\s*걸).{0,12}(?:사는|산|고르는).{0,8}(?:부모|엄마|아빠).{0,8}호구", re.I),
        re.compile(r"(?:부모|엄마|아빠).{0,12}(?:호구|무지|멍청|게으르|게을러|게으른|귀찮아서.{0,12}(?:때우|사))", re.I),
        re.compile(r"귀찮아서.{0,16}(?:때우|사).{0,12}(?:부모|엄마|아빠)", re.I),
    ),
    "US": (
        re.compile(r"\blazy\s+(?:parents?|moms?|dads?|caregivers?)\b", re.I),
        re.compile(r"\b(?:parents?|moms?|dads?|caregivers?)\b.{0,32}\b(?:stupid|ignorant|lazy|suckers?)\b", re.I),
        re.compile(r"\bif\s+you\s+buy\s+this\b.{0,48}\b(?:did\s+not|didn['’]t)\s+read\b", re.I),
    ),
}


def map_category(value: str | None) -> str | None:
    """Map a source category to one public problem category."""
    normalized = str(value or "").strip().lower()
    if normalized in PUBLIC_CATEGORIES:
        return normalized
    for key, data in PUBLIC_CATEGORIES.items():
        if normalized in data["aliases"]:
            return key
    return None


def retired_product_reasons(text: str) -> list[str]:
    """Return non-negotiable commerce exclusions independent of category data."""
    value = str(text or "")
    reasons = []
    if any(pattern.search(value) for pattern in _MEASUREMENT_COMMERCE_PATTERNS):
        reasons.append("measurement_commerce_retired")
    if any(pattern.search(value) for pattern in _GROWTH_COMMERCE_PATTERNS):
        reasons.append("height_growth_claim")
    return reasons


def product_eligibility(product: dict[str, Any]) -> dict[str, Any]:
    """Fail closed for retired products and unsupported friction categories."""
    haystack = " ".join(
        str(product.get(key) or "")
        for key in ("product_name", "name", "title", "category", "description", "claim")
    )
    reasons = retired_product_reasons(haystack)
    if not map_category(product.get("category")):
        reasons.append("unsupported_friction_category")
    return {"eligible": not reasons, "reasons": reasons}


def caregiver_shaming_reasons(text: str, market: str) -> list[str]:
    """Return deterministic reasons when copy attacks the caregiver or buyer."""
    patterns = _CAREGIVER_SHAMING.get(str(market or "").upper(), ())
    return ["caregiver_shaming" for pattern in patterns if pattern.search(str(text or ""))]


def retired_persona_reasons(text: str) -> list[str]:
    """Reject retired narrator identity on active reader-facing surfaces."""
    return ["retired_persona" for pattern in _RETIRED_PERSONA_PATTERNS if pattern.search(str(text or ""))]
