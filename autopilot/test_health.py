# -*- coding: utf-8 -*-
"""health.py 회귀 — 상태 점검이 '실제로 문제를 잡는지' 검증한다.

점검 도구가 조용히 통과만 하면 없느니만 못하므로, 각 검사가 고장 상황에서
FAIL을 내는지 확인한다. 특히 2026-08-29에 실제로 있었던 두 사고:
  - mode.publish=false로 하루치가 preview로만 쌓임
  - cron PATH에 aside가 없어 harvest가 매일 죽음

실행: ../.venv/bin/python test_health.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import health  # noqa: E402


def _cfg(tmp, **mode):
    m = {"dry_run": False, "publish": True}
    m.update(mode)
    return {"mode": m, "paths": {"state_dir": tmp}}


def _write(tmp, name, rows):
    with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_publish_gate_catches_disabled():
    tmp = tempfile.mkdtemp()
    s, msg = health.check_publish_gate(_cfg(tmp, publish=False))
    assert s == health.FAIL, (s, msg)
    assert "preview" in msg
    s, _ = health.check_publish_gate(_cfg(tmp, dry_run=True))
    assert s == health.FAIL
    s, _ = health.check_publish_gate(_cfg(tmp))
    assert s == health.OK
    print("ok: 발행 게이트 꺼짐 탐지")


def test_recent_publish_detects_stall():
    tmp = tempfile.mkdtemp()
    now = datetime.now()

    def row(hours_ago):
        return {"country": "KR", "media_id": "1" * 17, "text": "t",
                "meta": {"post_type": "value", "publish_status": "verified"},
                "ts": (now - timedelta(hours=hours_ago)).isoformat()}

    _write(tmp, "published.jsonl", [row(30)])
    s, msg = health.check_recent_publish(_cfg(tmp))
    assert s == health.FAIL, (s, msg)          # 30시간 정지 = 스케줄 death

    _write(tmp, "published.jsonl", [row(1)])
    s, _ = health.check_recent_publish(_cfg(tmp))
    assert s == health.OK
    print("ok: 발행 정지 탐지")


def test_comments_cron_death_detected():
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "replies_handled.json")
    with open(p, "w") as f:
        json.dump([], f)
    s, _ = health.check_comments_alive(_cfg(tmp))
    assert s == health.OK                       # 방금 썼으니 정상

    old = (datetime.now() - timedelta(hours=2)).timestamp()
    os.utime(p, (old, old))
    s, msg = health.check_comments_alive(_cfg(tmp))
    assert s == health.FAIL, (s, msg)           # 3분 주기인데 2시간 정지
    print("ok: 댓글 크론 정지 탐지")


def test_errors_recovered_vs_chronic():
    """고친 문제는 통과, 안 고친 문제는 계속 잡아야 한다."""
    tmp = tempfile.mkdtemp()
    now = datetime.now()
    err_ts = (now - timedelta(hours=3)).isoformat()
    _write(tmp, "errors.jsonl", [
        {"where": "harvest.run_aside", "error": "FileNotFoundError", "ts": err_ts},
        {"where": "harvest.run_aside", "error": "FileNotFoundError", "ts": err_ts},
        {"where": "harvest.harvest_once", "error": "TimeoutExpired", "ts": err_ts},
    ])

    # 에러 이후 수집 성공 기록이 없으면 → 미복구
    _write(tmp, "evidence.jsonl", [{"ts": (now - timedelta(hours=5)).isoformat()}])
    s, msg = health.check_recent_errors(_cfg(tmp))
    assert s == health.FAIL, (s, msg)
    assert "미복구" in msg, msg

    # 에러 이후 수집이 성공했으면 → 복구로 판정
    _write(tmp, "evidence.jsonl", [{"ts": (now - timedelta(minutes=10)).isoformat()}])
    s, msg = health.check_recent_errors(_cfg(tmp))
    assert s == health.OK, (s, msg)
    assert "복구" in msg, msg
    print("ok: 복구/만성 에러 구분")


def test_revenue_readback_error_recovers_after_newer_measured_snapshot():
    tmp = tempfile.mkdtemp()
    now = datetime.now()
    _write(tmp, "errors.jsonl", [{
        "where": "revenue_readback_kr",
        "error": "RuntimeError: Aside KR read-back exit=1",
        "ts": (now - timedelta(hours=1)).isoformat(),
    }])
    with open(os.path.join(tmp, "revenue.json"), "w", encoding="utf-8") as stream:
        json.dump({"markets": {"KR": {
            "measurement_status": "measured",
            "dashboard_readback_timestamp": (now - timedelta(minutes=5)).isoformat(),
        }}}, stream)
    status, message = health.check_recent_errors(_cfg(tmp))
    assert status == health.OK, (status, message)
    assert "revenue_readback_kr" in message


def test_generation_errors_recover_after_matching_preview():
    tmp = tempfile.mkdtemp()
    now = datetime.now()
    _write(tmp,'errors.jsonl',[
        {'where':'kr_value','error':'critic','ts':(now-timedelta(hours=2)).isoformat()},
        {'where':'us_value','error':'critic','ts':(now-timedelta(hours=2)).isoformat()},
        {'where':'post_kr_value','error':'dns generation failure','ts':(now-timedelta(hours=2)).isoformat()},
        {'where':'post_us_value','error':'dns generation failure','ts':(now-timedelta(hours=2)).isoformat()},
        {'where':'us_sales','error':'unknown product','ts':(now-timedelta(hours=2)).isoformat()},
        {'where':'post_us_sales','error':'readback mismatch','ts':(now-timedelta(hours=2)).isoformat()},
    ])
    _write(tmp,'preview.jsonl',[
        {'country':'KR','meta':{'post_type':'value'},'ts':(now-timedelta(minutes=20)).isoformat()},
        {'country':'US','meta':{'post_type':'value'},'ts':(now-timedelta(minutes=15)).isoformat()},
    ])
    _write(tmp,'holdbox.jsonl',[
        {'why':'risk_flagged','country':'US','post_type':'sales','ts':(now-timedelta(minutes=10)).isoformat()},
    ])
    _write(tmp,'published.jsonl',[
        {'country':'US','media_id':'m1','meta':{'post_type':'sales','publish_status':'verified',
          'reconciled_at':(now-timedelta(minutes=5)).isoformat()},
         'ts':(now-timedelta(hours=3)).isoformat()},
    ])
    s,msg=health.check_recent_errors(_cfg(tmp,publish=False))
    assert s==health.OK,(s,msg)
    assert '복구' in msg,msg
    assert msg.count('post_us_sales') == 1, msg


def test_evidence_stock_empty_is_fail():
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "insight_atoms.json"), "w") as f:
        json.dump([], f)
    s, _ = health.check_evidence_stock(_cfg(tmp))
    assert s == health.FAIL

    with open(os.path.join(tmp, "insight_atoms.json"), "w") as f:
        json.dump([{"id": i, "used_in": {}} for i in range(5)], f)
    s, _ = health.check_evidence_stock(_cfg(tmp))
    assert s == health.OK
    print("ok: 증거 재고 고갈 탐지")


def test_companyos_workflow_break_is_fail():
    broken = {"ok": False, "counts": {"held": 2},
              "checks": {"all_us_products_tracked": True, "approvals_current": False}}
    s, msg = health.check_companyos_workflow({}, probe=broken)
    assert s == health.FAIL, (s, msg)
    assert "approvals_current" in msg, msg
    healthy = {"ok": True, "counts": {"sourced": 4}, "claimable_now": 1,
               "checks": {"all_us_products_tracked": True, "approvals_current": True}}
    s, _ = health.check_companyos_workflow({}, probe=healthy)
    assert s == health.OK
    unavailable = {"ok": True, "counts": {"approved": 1, "published": 1}, "claimable_now": 0,
                   "claimable_by_market": {},
                   "next_claimable_at": "2026-09-08T09:14:15+00:00",
                   "checks": {"all_us_products_tracked": True, "approvals_current": True}}
    s, msg = health.check_companyos_workflow({}, probe=unavailable)
    assert s == health.OK, (s, msg)
    assert "cooldown" in msg
    unavailable_without_cooldown = {**unavailable, "next_claimable_at": None}
    s, msg = health.check_companyos_workflow({}, probe=unavailable_without_cooldown)
    assert s == health.WARN, (s, msg)
    assert "즉시 claim 가능 0" in msg
    missing = {"ok": True, "counts": {"approved": 1},
               "checks": {"all_us_products_tracked": True, "approvals_current": True}}
    assert health.check_companyos_workflow({}, probe=missing)[0] == health.WARN
    print("ok: Company OS 상품 인계 단절·실행 재고 탐지")


def test_active_contract_drift_fails_on_retired_persona_measurement_commerce_and_legacy_cadence():
    active = {
        "AGENTS.md": "167cm 팩트폭격기 페르소나를 고정한다\n",
        "LAUNCH-STATUS.md": "신장계 추천 상품을 판매한다\n",
        "config.example.json": '"sales_per_day": 2, "value_per_day": 3',
    }
    status, message = health.check_active_contract_drift(active)
    assert status == health.FAIL
    assert "retired_persona" in message
    assert "measurement_commerce" in message
    assert "legacy_cadence" in message
    current = {
        "LAUNCH-STATUS.md": "persona-free friction commerce. 원문은 시장별 하루 2개. 안 하는 것: 신장계 상업 추천.",
        "config.example.json": '"original_posts_per_market_per_day": 2',
    }
    status, message = health.check_active_contract_drift(files=current)
    assert status == health.OK, (status, message)


def test_outreach_health_requires_recent_verified_rows_for_both_markets_and_no_stale_reservation():
    cfg = {"outreach": {"enabled": True, "publish": True}}
    now = datetime.fromisoformat("2026-08-31T12:00:00+00:00")
    good = [
        {"market": "KR", "status": "verified", "verified_at": "2026-08-31T10:30:00Z"},
        {"market": "US", "status": "verified", "verified_at": "2026-08-31T10:35:00Z"},
    ]
    assert health.check_outreach_alive(cfg, rows=good, now=now)[0] == health.OK
    missing = health.check_outreach_alive(cfg, rows=good[:1], now=now)
    assert missing[0] == health.FAIL and "US" in missing[1]
    stale = good + [{"market": "KR", "status": "reserved", "reserved_at": "2026-08-31T08:00:00Z"}]
    assert health.check_outreach_alive(cfg, rows=stale, now=now)[0] == health.FAIL


def test_outreach_health_requires_only_markets_enabled_for_the_aside_session():
    cfg = {"outreach": {"enabled": True, "publish": True, "markets": ["KR"]}}
    now = datetime.fromisoformat("2026-08-31T12:00:00+00:00")
    rows = [{"idempotency_key": "kr", "status": "verified", "market": "KR", "verified_at": "2026-08-31T11:30:00+00:00"}]
    status, message = health.check_outreach_alive(cfg, rows=rows, now=now)
    assert status == health.OK, message


def test_outreach_health_recovers_market_from_the_reserved_transition_for_legacy_verified_rows():
    cfg = {"outreach": {"enabled": True, "publish": True, "markets": ["KR"]}}
    now = datetime.fromisoformat("2026-08-31T12:00:00+00:00")
    rows = [
        {"idempotency_key": "kr", "status": "reserved", "market": "KR", "reserved_at": "2026-08-31T11:20:00+00:00"},
        {"idempotency_key": "kr", "status": "verified", "verified_at": "2026-08-31T11:30:00+00:00"},
    ]
    status, message = health.check_outreach_alive(cfg, rows=rows, now=now)
    assert status == health.OK, message


def test_outreach_health_distinguishes_observed_zero_from_connector_failure():
    cfg = {"outreach": {"enabled": True, "publish": True, "markets": ["KR"]}}
    now = datetime.fromisoformat("2026-08-31T12:00:00+00:00")
    probe = {"market": "KR", "status": "ok", "source_count": 0,
             "queries": ["sleep"], "observed_at": "2026-08-31T11:30:00Z"}
    old = [{"market": "KR", "status": "verified", "verified_at": "2026-08-30T10:00:00Z"}]
    status, message = health.check_outreach_alive(cfg, rows=old, probes=[probe], now=now)
    assert status == health.WARN and "조회 성공" in message, message
    status, message = health.check_outreach_alive(cfg, rows=old,
        probes=[{**probe, "status": "error", "error": "AsideAdapterError"}], now=now)
    assert status == health.FAIL and "KR" in message, message


def test_all_checks_have_distinct_names():
    names = [n for n, _ in health.CHECKS]
    assert len(names) == len(set(names)), names
    assert len(names) >= 7, names
    assert "활성 계약" in names
    assert "외부 답글" in names
    print(f"ok: 점검 항목 {len(names)}개 등록")


if __name__ == "__main__":
    test_publish_gate_catches_disabled()
    test_recent_publish_detects_stall()
    test_comments_cron_death_detected()
    test_errors_recovered_vs_chronic()
    test_generation_errors_recover_after_matching_preview()
    test_evidence_stock_empty_is_fail()
    test_companyos_workflow_break_is_fail()
    test_all_checks_have_distinct_names()
    print("\n상태 점검 회귀 8/8 통과")
