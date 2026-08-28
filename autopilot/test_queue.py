#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""브라우저 큐 E2E 테스트 — 실행: python3 test_queue.py

요청 버퍼 생성 → Aside 결과 주입 → 소비 → 요청 상태 갱신 → 중복 방지 → 버퍼 재충전 →
블랙리스트 차단까지 자동 검증한다. 임시 디렉터리를 사용하므로 실제 state/는 건드리지 않는다.
종료 코드: 0=PASS, 1=FAIL.
"""
import json
import shutil
import sys
import tempfile

import sourcing
from common import load_config, read_json

RESULT_TEMPLATE = {
    "status": "done", "country": "KR", "category": "sleep",
    "audit_status": "approved",
    "audited_by": "mungchi-proof",
    "source_reverified_via": "aside:u0",
    "source_reverified_at": "2026-08-27T20:05:00+09:00",
    "demand_provenance": {"signal_id": "d-1", "source_post_id": "post-1",
                          "observed_at": "2026-08-27T19:00:00+09:00",
                          "signal": "독서할 때 고개 숙임을 묻는 반복 질문",
                          "connection_reason": "질문에서 확인된 자세 수고를 높이조절 독서대로 조사"},
    "product_name": "테스트 어린이 침대 토퍼", "is_food": False,
    "is_certified_health_food": False, "approved_claims": [],
    "product_url": "https://www.coupang.com/vp/products/123",
    "collected_at": "2026-08-27T20:00:00+09:00",
    "price_info": "89,000원", "review_count": 2100, "review_rating": 4.6,
    "price_provenance": {"regular_price": "89,000원", "variable_price": None,
                         "source_url": "https://www.coupang.com/vp/products/123"},
    "review_quotes": ["커버 세탁이 편해요"], "spec_facts": ["고밀도 폼"],
    "review_provenance": [{"review_id": "review-1", "quote": "커버 세탁이 편해요",
                           "source_url": "https://www.coupang.com/vp/products/123?review=review-1",
                           "original_location": "상품평 > 베스트순 > review-1"}],
    "official_provenance": [{"quote": "고밀도 폼", "source_url": "https://www.coupang.com/vp/products/123",
                             "original_location": "상품 상세 > 제품 사양"}],
    "link": "https://link.coupang.com/TEST", "sub_id": "hc-test",
    "candidate_pool": [{"name": f"후보-{i}", "archetype": kind} for i, kind in enumerate(
        ["branded_anchor", "bestseller", "budget", "ux_novel", "alternate_formfactor"], 1)],
    "compared_candidates": [{"name": "후보-1"}, {"name": "후보-4"}, {"name": "후보-5"}],
    "rejected_candidates": [
        {"name": "후보-1", "reason": "고가인데 구조 차이가 작음"},
        {"name": "후보-5", "reason": "부모 마찰 감소 근거가 약함"},
    ],
    "winner_reasons": ["가격·구조·부모 마찰 감소 근거가 가장 구체적"],
    "winner_count": 1,
    "judgment_status": "approved",
    "judged_by": "openrouter/google/gemini-3.7-flash",
}


def main():
    cfg = load_config()
    tmp = tempfile.mkdtemp(prefix="hc-queue-test-")
    cfg["paths"]["state_dir"] = tmp
    cfg["coupang"]["access_key"] = ""  # 테스트 중 API 경로 차단
    with open(f"{tmp}/demand_signals.json", "w", encoding="utf-8") as f:
        json.dump([
            {"signal_id": f"d-{i}", "status": "validated", "source_post_id": f"post-{i}",
             "observed_at": f"2026-08-27T19:0{i}:00+09:00", "signal": "반복 질문",
             "connection_reason": "가치글 반응에서 확인된 문제", "repeated_count": 2,
             "category": "posture", "sourcing_keyword": "어린이 높이조절 독서대", "is_food": False}
            for i in range(1, 6)
        ], f, ensure_ascii=False)

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + name)
        ok = ok and cond

    print("브라우저 큐 E2E 테스트")

    # 1) 버퍼 충전
    added = sourcing.top_up_requests(cfg)
    req_p, res_p, _ = sourcing._queue_paths(cfg)
    reqs = read_json(req_p, [])
    check("요청 버퍼 3건 생성", added == 3 and len(reqs) == 3 and all(q["status"] == "pending" for q in reqs))

    # 2) Aside 결과 주입 → 소비
    result = dict(RESULT_TEMPLATE, request_id=reqs[0]["id"], product_key="t-1")
    with open(res_p, "w", encoding="utf-8") as f:
        json.dump([result], f, ensure_ascii=False)
    picked = sourcing.pick(cfg, dry_run=False)
    check("결과 소비 (pick이 큐 결과 반환)", bool(picked) and picked.get("product_key") == "t-1")
    check("소비된 요청 상태 → consumed", read_json(req_p, [])[0].get("status") == "consumed")

    # 3) 중복 방지 + 버퍼 재충전
    picked2 = sourcing.pick(cfg, dry_run=False)
    check("같은 결과 중복 소비 방지", picked2 is None)
    check("버퍼 자동 재충전", any(q.get("status") == "pending" for q in read_json(req_p, [])))

    # 4) 블랙리스트 차단
    bad = dict(RESULT_TEMPLATE, request_id="x", product_key="t-2", product_name="어린이 무게 담요 6kg")
    with open(res_p, "w", encoding="utf-8") as f:
        json.dump([bad], f, ensure_ascii=False)
    check("블랙리스트 상품 차단", sourcing.pick(cfg, dry_run=False) is None)

    # 5) provenance 또는 감사 승인 결손 차단
    hold = dict(RESULT_TEMPLATE, request_id="y", product_key="t-3", audit_status="hold")
    with open(res_p, "w", encoding="utf-8") as f:
        json.dump([hold], f, ensure_ascii=False)
    check("감사 보류 상품 소비 차단", sourcing.pick(cfg, dry_run=False) is None)

    # 6) 가치글 수요 신호가 없어도 Discovery 레인은 유지
    with open(f"{tmp}/demand_signals.json", "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(req_p, "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(res_p, "w", encoding="utf-8") as f:
        json.dump([], f)
    added = sourcing.top_up_requests(cfg)
    discovery_reqs = read_json(req_p, [])
    check("수요 신호 없어도 Discovery 요청 유지",
          added == 3 and all(q.get("lane") == "discovery" for q in discovery_reqs))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n결과:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
