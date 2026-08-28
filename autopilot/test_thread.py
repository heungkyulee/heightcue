#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""가치글 타래 발행 회귀 — 실행: python3 test_thread.py

핵심 불변식: **한 편이라도 검사에 걸리면 아무것도 발행하지 않는다.**
1편만 올라간 미완성 타래는 삭제 스코프 문제까지 얽혀 수습이 어렵다.
종료 코드: 0=PASS, 1=FAIL.
"""
import shutil
import sys
import tempfile

import publish
import run as run_mod
from common import load_config, read_jsonl, state_path

FAILURES = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)


class FakePublisher:
    """publish.publish_text 대역 — 호출 순서와 reply_to 체인을 기록한다."""

    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def __call__(self, cfg, country, text, link=None, reply_to=None,
                 dry_run=False, meta=None):
        n = len(self.calls) + 1
        self.calls.append({"text": text, "reply_to": reply_to, "meta": meta or {}})
        if self.fail_at and n == self.fail_at:
            return None
        return f"m{n}"


GOOD = ["10시 취침 강박, 내려놓으세요.",
        "시계 바늘보다 깊은 잠의 총량이 더 관련이 큽니다. 10시에 누워 뒤척이는 것보다 낫습니다.",
        "물론 최종 키는 유전이 가장 큰 변수입니다. 어떤 습관으로도 그건 안 바뀝니다."]


def main():
    cfg = load_config()
    tmp = tempfile.mkdtemp(prefix="hc-thread-test-")
    cfg["paths"]["state_dir"] = tmp
    cfg["mode"]["hold_flagged"] = True
    cfg["mode"]["auto_publish_clean"] = True
    orig = publish.publish_text
    try:
        print("[1] 정상 타래 — 답글 체인")
        fake = FakePublisher()
        publish.publish_text = fake
        root, reason = run_mod._publish_thread(cfg, GOOD, "KR",
                                               meta_extra={"atom_id": "a1"})
        check("published 반환", reason == "published" and root == "m1")
        check("3편 모두 발행", len(fake.calls) == 3)
        check("1편은 최상위(reply_to 없음)", fake.calls[0]["reply_to"] is None)
        check("2편은 1편에 답글", fake.calls[1]["reply_to"] == "m1")
        check("3편은 2편에 답글(직전 편 체인)", fake.calls[2]["reply_to"] == "m2")
        check("meta에 편 번호·총편수",
              fake.calls[1]["meta"].get("thread_part") == 2
              and fake.calls[1]["meta"].get("thread_total") == 3)
        check("원자 id가 모든 편에 전파",
              all(c["meta"].get("atom_id") == "a1" for c in fake.calls))

        print("\n[2] 사전 검사 — 한 편이라도 걸리면 전량 취소")
        fake2 = FakePublisher()
        publish.publish_text = fake2
        bad_len = GOOD[:2] + ["가" * 600]  # 3편만 500자 초과
        root2, reason2 = run_mod._publish_thread(cfg, bad_len, "KR")
        check("포맷 실패 시 format_fail", reason2 == "format_fail")
        check("★ 앞 편도 발행되지 않음(미완성 타래 방지)", len(fake2.calls) == 0)

        fake3 = FakePublisher()
        publish.publish_text = fake3
        root3, reason3 = run_mod._publish_thread(cfg, GOOD[:2] + [""], "KR")
        check("빈 편 있으면 전량 취소", reason3 == "format_fail" and not fake3.calls)

        print("\n[3] 언어 게이트")
        fake4 = FakePublisher()
        publish.publish_text = fake4
        root4, reason4 = run_mod._publish_thread(
            cfg, ["Sleep timing is overrated.", "Deep sleep matters more."], "KR")
        check("KR인데 한국어 없으면 취소",
              reason4 == "language_fail" and not fake4.calls)

        fake5 = FakePublisher()
        publish.publish_text = fake5
        root5, reason5 = run_mod._publish_thread(cfg, GOOD, "US")
        check("US인데 한글 섞이면 취소",
              reason5 == "language_fail" and not fake5.calls)

        print("\n[4] 길이 경계")
        fake6 = FakePublisher()
        publish.publish_text = fake6
        edge = ["가" * 480, "나" * 480]
        root6, reason6 = run_mod._publish_thread(cfg, edge, "KR")
        check("각 편 480자는 통과(합계 960자여도 무관)",
              reason6 == "published" and len(fake6.calls) == 2)

        print("\n[5] 최소 편수")
        fake7 = FakePublisher()
        publish.publish_text = fake7
        root7, reason7 = run_mod._publish_thread(cfg, [GOOD[0]], "KR")
        check("1편짜리는 타래가 아님", reason7 == "thread_too_short" and not fake7.calls)

        print("\n[6] 발행 중 API 장애 — 남은 편 보류함")
        fake8 = FakePublisher(fail_at=2)
        publish.publish_text = fake8
        root8, reason8 = run_mod._publish_thread(cfg, GOOD, "KR")
        check("thread_partial 반환", reason8 == "thread_partial")
        check("이미 나간 1편의 root는 보존", root8 == "m1")
        check("3편을 추측 발행하지 않음(중복 방지)", len(fake8.calls) == 2)
        holds = [h for h in read_jsonl(state_path(cfg, "holdbox.jsonl"))
                 if h.get("why") == "thread_broken"]
        check("보류함에 남은 편 기록",
              len(holds) == 1 and len(holds[0]["remaining"]) == 2
              and holds[0]["failed_at_part"] == 2)

        print("\n[7] 수동 모드")
        cfg["mode"]["auto_publish_clean"] = False
        fake9 = FakePublisher()
        publish.publish_text = fake9
        root9, reason9 = run_mod._publish_thread(cfg, GOOD, "KR")
        check("manual_hold 시 발행 없음",
              reason9 == "manual_hold" and not fake9.calls)
    finally:
        publish.publish_text = orig
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}건: {FAILURES}")
        return 1
    print("PASS — 타래 회귀 전체 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
