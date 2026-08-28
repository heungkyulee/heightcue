#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""상관≠인과 출력 검사 회귀 — 실행: python3 test_causal.py

증거 원장의 claim_gate는 '입력'을 막는다. 이 검사는 LLM이 원자를 과장
렌더링한 '출력'을 잡는다. 가치글은 링크가 없어 기존 광고 리스크 검사를
조기 반환으로 통과하므로, 이 검사가 가치글의 유일한 출력 방어선이다.

오탐(정상 글 차단)은 발행을 멈추므로 누락만큼 심각하다. 양쪽 다 검증한다.
종료 코드: 0=PASS, 1=FAIL.
"""
import sys

import post_check as pc

FAILURES = []

MUST_CATCH_KR = [
    "일찍 자면 키가 큽니다",
    "우유 많이 먹으면 자랍니다",
    "이것만 해도 5cm 더 큽니다",
    "무조건 큽니다",
    "효과를 보장합니다",
    "늦게 자서 키가 안 컸어요",
    "스트레스 때문에 못 자란다",
    "잘 먹어서 잘 자란다",
    "수면이 부족해서 키가 안 컸다",
    "이 습관 덕분에 키가 컸어요",
]

MUST_PASS_KR = [
    # 정상 가치글 — 실제 생성물에서 가져온 문장 포함
    "시계 바늘보다 깊은 잠의 총량이 더 관련이 큽니다.",
    "물론 최종 키는 유전이 가장 큰 변수입니다. 어떤 습관으로도 그건 안 바뀝니다.",
    "10시 취침 강박, 내려놓으세요.",
    "깊게 자서 개운하다고 하더라고요.",
    "아이가 어려서 아직 판단하기 이릅니다.",
    "규칙이 명확해서 아이가 덜 헷갈립니다.",
    "바빠서 검진을 놓쳤어요.",
    "자라는 시기에는 잠이 중요하다고 알려져 있습니다.",
    "키가 자라는 속도는 아이마다 다릅니다.",
    "늦게 자서 아침에 힘들어합니다.",
    "밥을 잘 먹어서 다행이에요.",
    "수면 시간이 부족한 아이들이 많다는 조사 결과가 있습니다.",
    "소리 지른다고 듣는 게 아니더라고요.",
    "규칙을 미리 정해두고 일관되게 가는 게 도움 된다는 연구가 많습니다.",
]

MUST_CATCH_US = [
    "This makes kids taller.",
    "They will grow 3 inches.",
    "We guarantee results.",
    "Proven to increase height.",
]

MUST_PASS_US = [
    "Deep sleep matters more than the clock on the wall.",
    "Genetics is still the biggest factor here.",
    "That said, no routine changes that.",
]


def notes(text, country):
    return pc.check_post({"country": country, "post_type": "value",
                          "text": text, "product": {}})["risk_notes"]


def check(name, cond):
    if not cond:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


def main():
    print("[1] 인과 단정 — 반드시 잡아야 함 (KR)")
    for t in MUST_CATCH_KR:
        check(f"놓침: {t}", bool(notes(t, "KR")))
    print(f"  {len(MUST_CATCH_KR)}건 검사")

    print("\n[2] 정상 가치글 — 오탐 금지 (KR)")
    for t in MUST_PASS_KR:
        n = notes(t, "KR")
        check(f"오탐: {t} | {n[:1]}", not n)
    print(f"  {len(MUST_PASS_KR)}건 검사")

    print("\n[3] 인과 단정 — 반드시 잡아야 함 (US)")
    for t in MUST_CATCH_US:
        check(f"놓침: {t}", bool(notes(t, "US")))
    print(f"  {len(MUST_CATCH_US)}건 검사")

    print("\n[4] 정상 가치글 — 오탐 금지 (US)")
    for t in MUST_PASS_US:
        n = notes(t, "US")
        check(f"오탐: {t} | {n[:1]}", not n)
    print(f"  {len(MUST_PASS_US)}건 검사")

    print("\n[5] 인용부 예외 — 원문 인용은 우리 주장이 아님")
    quoted = '리뷰에 이런 말이 있었습니다. "먹으면 큽니다"'
    check("인용 안의 표현은 잡지 않음", not notes(quoted, "KR"))

    print("\n[6] 판매글에서도 동작 (조기 반환에 막히지 않음)")
    check("판매글 인과 단정도 잡힘",
          bool(notes("일찍 자면 키가 큽니다", "KR")))

    total = (len(MUST_CATCH_KR) + len(MUST_PASS_KR)
             + len(MUST_CATCH_US) + len(MUST_PASS_US) + 2)
    print()
    if FAILURES:
        print(f"FAIL — {total}건 중 {len(FAILURES)}건 실패")
        return 1
    print(f"PASS — 인과 검사 {total}/{total} 통과 (놓침 0, 오탐 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
