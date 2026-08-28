#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""콘텐츠 브리프 회귀 — 실행: python3 test_briefing.py

브리프는 사람이 읽고 그대로 콘텐츠를 만드는 문서다. 가드레일(반론·확신도
취급법·비상업)이 빠지면 팀이 위험한 글을 쓰게 되므로 그 존재를 검증한다.
종료 코드: 0=PASS, 1=FAIL.
"""
import shutil
import sys
import tempfile

import briefing
import evidence
from common import load_config

FAILURES = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)


ATOM_WEAK = {
    "atom_id": "a-weak", "topic": "sleep", "distance": 0, "confidence": "weak",
    "claim": "소규모 실험에서 성장호르몬 맥동이 서파수면과 연관됐다",
    "counter_claim": "같은 연구에서 서파수면을 줄여도 분비는 바뀌지 않았다",
    "parent_emotion": "못 재웠다는 죄책감",
    "hook_seeds": ["깊은 잠과 성장호르몬은 같은 말이 아닙니다"],
    "sources": [{"type": "paper", "doi": "10.1/x", "year": 2022,
                 "excerpt": "GH pulses were temporally associated with SWS."}],
    "used_in": {},
}
ATOM_STRONG = {
    "atom_id": "a-strong", "topic": "discipline", "distance": 2,
    "confidence": "strong",
    "claim": "메타분석에서 일관된 규칙 예고가 자기조절과 연관됐다",
    "counter_claim": "기질·연령 편차가 크다",
    "parent_emotion": "매일 소리지르는 자책",
    "hook_seeds": ["훈육 강도의 문제가 아닙니다"],
    "sources": [{"type": "guideline", "url": "https://aap.org/x", "year": 2023}],
    "used_in": {"threads_kr": ["17999"]},
}


def main():
    cfg = load_config()
    tmp = tempfile.mkdtemp(prefix="hc-brief-test-")
    cfg["paths"]["state_dir"] = tmp
    try:
        store = evidence.atom_store(cfg)
        store["atoms"] = [ATOM_WEAK, ATOM_STRONG]
        evidence.save_atoms(cfg, store)
        text = briefing.build(cfg)

        print("[1] 안전 가드레일이 문서에 실린다")
        check("반론이 카드에 포함", ATOM_WEAK["counter_claim"] in text)
        check("반론 생략 금지 지시", "반론은 생략 금지" in text)
        check("비상업 레이어 명시", "제품·링크·브랜드는 넣지 않는다" in text)
        check("인과 단정 경고", "인과 단정" in text)
        check("weak 확신도 취급법 명시",
              "'이런 연구도 있다' 이상 나가지 말 것" in text)
        check("훅 씨앗 변형 지시", "그대로 쓰지 말고 변형" in text)

        print("\n[2] 출처가 추적 가능하게 실린다")
        check("DOI 노출", "10.1/x" in text)
        check("원문 인용 노출", "GH pulses were temporally" in text)

        print("\n[3] 앵글은 원자마다 다르게 제안된다")
        weak_angles = {a[0] for a in briefing._suggest_angles(ATOM_WEAK)}
        strong_angles = {a[0] for a in briefing._suggest_angles(ATOM_STRONG)}
        check("weak엔 질문 응답(단정 회피)", "community_qa" in weak_angles)
        check("strong엔 팩트폭격 허용", "rant" in strong_angles)
        check("weak엔 팩트폭격 없음", "rant" not in weak_angles)
        check("반전형 반론엔 통념 파괴", "myth_bust" in weak_angles)
        check("D2 주제엔 서사 앵글", "raw_memory" in strong_angles)
        check("죄책감 지점엔 해소 앵글",
              "reassurance" in weak_angles and "reassurance" in strong_angles)
        check("두 원자의 앵글 조합이 다름", weak_angles != strong_angles)

        print("\n[4] 멀티채널 재활용 정보")
        check("미사용 채널 표기", "아직 신선" in text)
        check("전 채널 신규 표기", "전 채널 신규" in text)
        check("발행 링크 노출", "threads.net/t/17999" in text)
        check("포맷 사양표 포함",
              "릴스/틱톡 대본" in text and "카드뉴스" in text)

        print("\n[5] 재고 격차 — 무엇을 더 모을지")
        check("비어 있는 거리 경보", "🔴 비어 있음" in text)
        check("미커버 주제 나열", "아직 한 번도 안 다룬 주제" in text)
        check("수집 명령 제시", "harvest.py --topic" in text)
        check("운영자 서사는 수집 대상 아님", "`operator_story`" not in text)

        print("\n[6] 엇갈리는 근거 묶기")
        A = dict(ATOM_STRONG, atom_id="t-a", topic="mindset",
                 claim="메타분석에서 작은 연관 효과가 보고됐다(d=0.14)",
                 counter_claim="예측구간이 넓어 편차가 컸다")
        B = dict(ATOM_STRONG, atom_id="t-b", topic="mindset",
                 claim="다른 메타분석에서 효과가 작았고(d=0.05)",
                 counter_claim="출판편향 보정 후 유의하지 않았다")
        pairs = briefing._find_tensions([A, B])
        check("상반 결과 쌍을 찾아냄", len(pairs) == 1 and pairs[0][0] == "mindset")
        tl = "\n".join(briefing.tension_lines([A, B]))
        check("체리피킹 경고 포함", "한쪽만 골라 쓰면" in tl)
        check("양쪽 주장 모두 노출", "d=0.14" in tl and "d=0.05" in tl)
        check("같은 방향 근거는 묶지 않음",
              not briefing._find_tensions([A, dict(A, atom_id="t-c")]))
        check("주제가 다르면 묶지 않음",
              not briefing._find_tensions([A, dict(B, topic="sleep")]))

        print("\n[7] 주제 필터")
        only = briefing.build(cfg, topic="discipline")
        check("해당 주제만 카드로", "소재 카드 (1건)" in only)
        check("다른 주제 카드 제외", ATOM_WEAK["claim"] not in only)

        print("\n[8] 원장이 비어도 죽지 않는다")
        empty = load_config()
        empty["paths"]["state_dir"] = tempfile.mkdtemp(prefix="hc-brief-empty-")
        out = briefing.build(empty)
        check("빈 원장 안내 문구", "원자가 없습니다" in out)
        check("격차 섹션은 여전히 나옴", "재고 격차" in out)
        shutil.rmtree(empty["paths"]["state_dir"], ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}건: {FAILURES}")
        return 1
    print("PASS — 브리프 회귀 전체 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
