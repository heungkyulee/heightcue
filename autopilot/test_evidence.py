#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""증거 원장 테스트 — 실행: python3 test_evidence.py

무인 운영이므로 claim_gate가 유일한 방어선이다. 통과 케이스보다
'반드시 막아야 하는' 케이스를 더 촘촘히 검증한다.
종료 코드: 0=PASS, 1=FAIL.
"""
import copy
import shutil
import sys
import tempfile

import evidence
from common import load_config

GOOD = {
    "evidence_id": "ev-1",
    "claim": "성장호르몬 분비량은 취침 시각 자체보다 깊은 수면 단계의 총량과 연관이 크다",
    "counter_claim": "수면은 여러 변수 중 하나이며, 최종 성인 키의 최대 결정 요인은 유전이다",
    "topic": "sleep",
    "confidence": "moderate",
    "sources": [
        {"type": "paper", "doi": "10.1000/xyz", "year": 2024,
         "url": "https://pubmed.ncbi.nlm.nih.gov/00000000/"},
    ],
    "parent_emotion": "10시 취침 강박에 대한 죄책감",
    "hook_seeds": ["10시 취침 강박, 생각보다 근거가 얇습니다"],
}

FAILURES = []


def check(name, cond):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


def expect_reject(name, mutate, expected_reason):
    rec = copy.deepcopy(GOOD)
    mutate(rec)
    ok, reasons = evidence.claim_gate(rec)
    hit = any(r.startswith(expected_reason) for r in reasons)
    check(f"{name} → {expected_reason}", (not ok) and hit)
    if not hit:
        print(f"        실제 반려 사유: {reasons}")


def main():
    print("[1] 게이트 통과 기준선")
    ok, reasons = evidence.claim_gate(GOOD)
    check("정상 레코드는 통과", ok)
    if not ok:
        print(f"        예상치 못한 반려: {reasons}")

    print("\n[2] 반드시 막아야 하는 케이스")
    expect_reject("1차출처 없이 뉴스만",
                  lambda r: r.update(sources=[{"type": "news", "url": "https://n.example/1"}]),
                  "primary_source_required")
    expect_reject("출처 자체가 없음",
                  lambda r: r.update(sources=[]), "sources_missing")
    expect_reject("출처에 URL/DOI 없음",
                  lambda r: r.update(sources=[{"type": "paper", "year": 2024}]),
                  "source_locator_missing")
    expect_reject("반론 누락(정직성 규칙)",
                  lambda r: r.update(counter_claim=""), "counter_claim_required")
    expect_reject("인과 단정 — '자면 큰다'",
                  lambda r: r.update(claim="일찍 자면 키가 큽니다"), "causal_overreach")
    expect_reject("인과 단정 — cm 수치 약속",
                  lambda r: r.update(hook_seeds=["이것만 하면 5cm 더 큽니다"]), "causal_overreach")
    expect_reject("인과 단정 — 영문 guarantee",
                  lambda r: r.update(claim="This routine will guarantee taller kids"),
                  "causal_overreach")
    expect_reject("상업 요소 혼입 — 링크",
                  lambda r: r.update(hook_seeds=["https://link.coupang.com/x 확인"]),
                  "commercial_leak")
    expect_reject("미분류 주제",
                  lambda r: r.update(topic="cooking"), "topic_unknown")
    expect_reject("확신도 값 오류",
                  lambda r: r.update(confidence="very_sure"), "confidence_invalid")
    expect_reject("claim 누락",
                  lambda r: r.update(claim=""), "claim_missing")

    print("\n[3] 바이럴 수확 — 구조만 허용")
    viral = copy.deepcopy(GOOD)
    viral["sources"].append({"type": "viral_post", "url": "https://threads.net/x",
                             "excerpt": "우리 애 키 때문에 잠 못 잔 밤"})
    ok, reasons = evidence.claim_gate(viral)
    check("structure_only 없으면 반려",
          (not ok) and any(r == "viral_requires_structure_only" for r in reasons))

    viral2 = copy.deepcopy(viral)
    viral2["structure_only"] = True
    ok2, reasons2 = evidence.claim_gate(viral2)
    check("structure_only=True면 통과", ok2)

    viral3 = copy.deepcopy(viral2)
    viral3["hook_seeds"] = ["우리 애 키 때문에 잠 못 잔 밤"]
    ok3, reasons3 = evidence.claim_gate(viral3)
    check("원문 문장 그대로 복사는 반려",
          (not ok3) and any(r == "viral_verbatim_copy" for r in reasons3))

    print("\n[4] 중복 방지")
    dup = copy.deepcopy(GOOD)
    h = evidence._claim_hash(GOOD["claim"])
    ok4, reasons4 = evidence.claim_gate(dup, known_hashes={h})
    check("동일 주장 재등록 차단", (not ok4) and "duplicate_claim" in reasons4)
    spaced = copy.deepcopy(GOOD)
    spaced["claim"] = GOOD["claim"].replace(" ", "  ") + "."
    ok5, reasons5 = evidence.claim_gate(spaced, known_hashes={h})
    check("공백·구두점 흔들려도 중복 판정", (not ok5) and "duplicate_claim" in reasons5)

    print("\n[5] E2E — 적재 → 승격 → 공급 → 소진")
    cfg = load_config()
    tmp = tempfile.mkdtemp(prefix="hc-evidence-test-")
    cfg["paths"]["state_dir"] = tmp
    try:
        evidence.record_evidence(cfg, GOOD)
        bad = copy.deepcopy(GOOD)
        bad.update(evidence_id="ev-2", claim="우유 마시면 키 큽니다")
        evidence.record_evidence(cfg, bad)
        # D2(훈육) 원자 — 주제 확장이 실제로 실린다는 증명
        d2 = copy.deepcopy(GOOD)
        d2.update(evidence_id="ev-3", topic="discipline",
                  claim="일관된 규칙 예고는 아동의 자기조절 발달과 연관이 보고된다",
                  counter_claim="연령·기질에 따라 효과 편차가 크고 인과는 단정할 수 없다",
                  hook_seeds=["훈육이 안 먹히는 진짜 이유"])
        evidence.record_evidence(cfg, d2)

        res = evidence.promote_pending(cfg)
        check("정상 2건 승격 / 위반 1건 반려",
              res["promoted"] == 2 and res["rejected"] == 1)

        inv = evidence.inventory(cfg)
        check("거리 체계 반영 (D0 1건 + D2 1건)",
              inv["by_distance"].get(0) == 1 and inv["by_distance"].get(2) == 1)

        atom = evidence.pick_atom(cfg, country="KR", channel="threads")
        check("원자 공급됨", atom is not None)
        topic_text = evidence.to_generation_topic(atom)
        check("생성용 topic에 반론이 포함됨", "반론" in (topic_text or ""))
        check("생성용 topic에 출처 날조 금지 지시 포함",
              "지어내지" in (topic_text or ""))

        evidence.mark_used(cfg, atom["atom_id"], "threads", "KR", "media-1")
        again = evidence.pick_atom(cfg, country="KR", channel="threads")
        check("Threads에서 소진된 원자는 재공급 안 됨",
              again is not None and again["atom_id"] != atom["atom_id"])

        print("\n[6] 멀티채널 — 채널별 독립 소진")
        tiktok = evidence.pick_atom(cfg, country="KR", channel="tiktok")
        check("Threads에서 쓴 원자도 TikTok에는 신선함",
              tiktok is not None and tiktok["atom_id"] == atom["atom_id"])
        us = evidence.pick_atom(cfg, country="US", channel="threads")
        check("KR에서 쓴 원자도 US에는 신선함",
              us is not None and us["atom_id"] == atom["atom_id"])
        evidence.mark_used(cfg, atom["atom_id"], "tiktok", "KR", "tk-1")
        inv2 = evidence.inventory(cfg)
        check("채널별 미사용 재고가 분리 집계됨",
              inv2["unused_by_channel"].get("threads_kr") == 1
              and inv2["unused_by_channel"].get("tiktok_kr") == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}건 실패: {FAILURES}")
        return 1
    print("PASS — 전체 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
