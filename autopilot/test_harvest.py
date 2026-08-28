#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""증거 수집 워커 회귀 — 실행: python3 test_harvest.py

Aside CLI는 호출하지 않는다(느리고 비결정적). 대신 파서·주제선택·적재 경로를
검증한다. 실제 Aside 연동은 `harvest.py --topic X`로 수동 확인.
종료 코드: 0=PASS, 1=FAIL.
"""
import json
import shutil
import sys
import tempfile

import evidence
import harvest
from common import load_config, read_jsonl, state_path

FAILURES = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)


def main():
    print("[1] 출력 파서 — Aside는 잡음을 섞어 보낸다")
    # 최소 증거 레코드 — claim + counter_claim이 증거의 필수 축이다.
    EV = '{"topic":"sleep","claim":"x","counter_claim":"y"}'
    cases = [
        ("순수 배열", f'[{EV}]', 1),
        ("코드펜스", f'```json\n[{EV}]\n```', 1),
        ("앞뒤 잡담", f'Here you go:\n[{EV}]\nDone!', 1),
        # 2026-08-28 실제 1회차 실패 원인
        ("ANSI 리셋 꼬리", f'[{EV}]\x1b[0m\n', 1),
        ("ANSI 앞뒤 감쌈", f'\x1b[32m[{EV}]\x1b[0m', 1),
        # 2026-08-28 실제 2회차 실패 원인: 진행 로그의 대괄호를 find('[')가 먼저 잡음
        ("진행 로그 대괄호 선행",
         'Thinking...\n[tool] openTab https://pubmed.ncbi.nlm.nih.gov\n'
         f'[tool] read\nResult:\n[{EV}]\x1b[0m\n', 1),
        ("로그 + 코드펜스 동시",
         f'[tool] search\n```json\n[{EV}]\n```\n', 1),
        ("배열이 여럿이면 증거 배열을 고름",
         f'[1,2,3]\nnotes\n[{EV}]\n', 1),
        ("문자열 안 대괄호에 속지 않음",
         '[{"topic":"sleep","claim":"see [1] and [2]","counter_claim":"c"}]', 1),
        # 2026-08-28 실제 3회차 사고: Aside가 끝에 붙인 인용 출처 목록을
        # 증거로 오인해 빈 레코드 10건이 적재됐다.
        ("인용 출처 목록만 있으면 0건",
         '[{"source_id":"A1","url":"https://pubmed.ncbi.nlm.nih.gov/3406323/",'
         '"title":"Slow wave sleep","excerpt":"x"}]', 0),
        ("증거 + 인용 목록 공존 시 증거를 고름",
         '[{"topic":"sleep","claim":"c","counter_claim":"cc"}]\n'
         '[{"source_id":"A1","url":"https://x","title":"t"}]', 1),
        ("claim만 있고 counter_claim 없으면 증거 아님",
         '[{"topic":"sleep","claim":"c"}]', 0),
        ("JSON 없음", 'sorry, could not find anything', 0),
        ("객체만(배열 아님)", '{"a":1}', 0),
        ("깨진 JSON", '[{"a":1,]', 0),
        ("빈 문자열", '', 0),
    ]
    for name, raw, expected in cases:
        got = harvest._extract_json(raw)
        check(f"{name} → {expected}건", len(got) == expected)

    print("\n[2] 주제 선택 — 재고가 빈 거리를 우선한다")
    cfg = load_config()
    tmp = tempfile.mkdtemp(prefix="hc-harvest-test-")
    cfg["paths"]["state_dir"] = tmp
    try:
        # 빈 원장: D0이 목표 40%로 가장 크므로 D0부터
        picks = harvest._stale_topics(cfg)
        check("빈 원장이면 주제 2개 제안", len(picks) == 2)
        check("모두 알려진 주제",
              all(p in evidence.TOPIC_DISTANCE for p in picks))
        check("operator_story는 수집 대상 아님",
              "operator_story" not in picks)

        # D0만 잔뜩 쌓으면 다음엔 다른 거리를 제안해야 한다
        store = evidence.atom_store(cfg)
        store["atoms"] = [{"atom_id": f"a{i}", "topic": "sleep", "distance": 0,
                           "used_in": {}} for i in range(10)]
        evidence.save_atoms(cfg, store)
        picks2 = harvest._stale_topics(cfg)
        check("D0 포화 시 D1 이상을 제안",
              all(evidence.TOPIC_DISTANCE[p] > 0 for p in picks2))

        print("\n[3] 프롬프트 — 게이트 규칙이 실려야 한다")
        prompt = harvest.build_prompt(["discipline"], count=3, stamp="202608281200")
        for token in ["1차 출처", "counter_claim", "인과 단정 금지",
                      "지어내면", "JSON"]:
            check(f"프롬프트에 '{token}' 포함", token in prompt)
        check("주제 힌트(수집처) 포함", "AAP" in prompt)
        check("거리 표기 포함", "D2" in prompt)

        print("\n[4] 적재 — Aside 응답을 evidence.jsonl로")
        items = [{"topic": "discipline", "claim": "x", "counter_claim": "y",
                  "confidence": "moderate", "sources": []},
                 {"evidence_id": "ev-custom", "topic": "sleep", "claim": "z",
                  "counter_claim": "w", "confidence": "weak", "sources": []}]
        for i, it in enumerate(items, 1):
            it.setdefault("evidence_id", f"ev-test-{i:02d}")
            evidence.record_evidence(cfg, it)
        rows = read_jsonl(state_path(cfg, "evidence.jsonl"))
        check("2건 적재", len(rows) == 2)
        check("evidence_id 자동 부여", rows[0]["evidence_id"] == "ev-test-01")
        check("명시된 id는 보존", rows[1]["evidence_id"] == "ev-custom")
        check("harvested_at 기록", all(r.get("harvested_at") for r in rows))

        print("\n[5] 워커는 게이트를 우회할 수 없다")
        # 위 2건은 sources가 비어 있으므로 승격에서 전부 반려되어야 한다
        res = evidence.promote_pending(cfg)
        check("출처 없는 수집물은 원자가 되지 못함",
              res["promoted"] == 0 and res["rejected"] == 2)
        rejects = read_jsonl(state_path(cfg, "evidence_rejects.jsonl"))
        check("반려 사유가 기록됨",
              len(rejects) == 2
              and any("sources_missing" in r["reasons"] for r in rejects))

        print("\n[6] dry-run은 Aside를 호출하지 않는다")
        res2 = harvest.harvest_once(cfg, topics=["sleep"], count=2, dry_run=True)
        check("dry_run 플래그 반환", res2.get("dry_run") is True)
        check("적재 없음", res2["harvested"] == 0)

        print("\n[7] 주제별 격리 — 하나가 실패해도 나머지는 남는다")
        import subprocess as sp
        calls = []

        def flaky(prompt, account="u0", timeout=None):
            calls.append(prompt)
            if "discipline" in prompt:
                raise sp.TimeoutExpired("aside", 1800)
            return ('[{"topic":"sleep","claim":"c","counter_claim":"cc",'
                    '"confidence":"weak","sources":[]}]')

        orig = harvest.run_aside
        harvest.run_aside = flaky
        try:
            res3 = harvest.harvest_once(cfg, topics=["discipline", "sleep"], count=1)
        finally:
            harvest.run_aside = orig
        check("주제별로 개별 호출(한 번에 몰지 않음) — 2주제+재시도 1회=3",
              len(calls) == 3)
        check("타임아웃 주제를 건너뛰고 계속", res3["harvested"] == 1)
        check("2회 시도 후 포기하고 기록",
              len(res3["failures"]) == 1
              and res3["failures"][0]["why"] == "timeout"
              and res3["failures"][0]["attempts"] == 2)
        check("무한 재시도하지 않음(호출 총량 제한)", len(calls) <= 4)

        print("\n[7-1] 타임아웃 1회차 실패 → 2회차 성공 시 살려낸다")
        seen = []

        def flaky_once(prompt, account="u0", timeout=None):
            seen.append(prompt)
            if len(seen) == 1:
                raise sp.TimeoutExpired("aside", 1800)
            return ('[{"topic":"sleep","claim":"c","counter_claim":"cc",'
                    '"confidence":"weak","sources":[]}]')

        harvest.run_aside = flaky_once
        try:
            res5 = harvest.harvest_once(cfg, topics=["sleep"], count=1)
        finally:
            harvest.run_aside = orig
        check("재시도로 수확 성공", res5["harvested"] == 1)
        check("재시도 성공은 실패로 남기지 않음", not res5["failures"])

        print("\n[8] 적재 2차 방어 — 빈 껍데기는 원장에 쌓지 않는다")
        before = len(read_jsonl(state_path(cfg, "evidence.jsonl")))

        def citations(prompt, account="u0", timeout=None):
            # 파서를 통과했다고 가정하고 claim 없는 레코드를 흘려보낸다
            return ('[{"claim":"","counter_claim":"c"},'
                    '{"claim":"real","counter_claim":"c"}]')

        harvest.run_aside = citations
        try:
            res4 = harvest.harvest_once(cfg, topics=["sleep"], count=2)
        finally:
            harvest.run_aside = orig
        after = len(read_jsonl(state_path(cfg, "evidence.jsonl")))
        check("claim 빈 레코드는 적재 안 됨", res4["harvested"] == 1)
        check("원장에 1건만 늘어남", after - before == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}건: {FAILURES}")
        return 1
    print("PASS — 수집 워커 회귀 전체 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
