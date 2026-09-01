#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

import outreach


def candidate(**overrides):
    row = {
        "market": "KR",
        "source_post_id": "source-123",
        "source_post_url": "https://www.threads.com/@parent/post/ABC123",
        "source_author": "parent",
        "source_text": "장난감 아래 상자 하나 꺼낼 때마다 전부 다시 쌓게 돼요.",
        "source_published_at": "2026-08-31T10:00:00+09:00",
        "friction_id": "fr-storage-front-open",
        "friction_category": "storage_cleanup",
        "reply_text": "매번 전부 비우게 되는 구조라면 상자를 바꾸기 전에 여는 방향부터 보는 게 낫습니다. 쌓아둔 채 앞에서 열리는지 확인해 보세요.",
    }
    row.update(overrides)
    return row


def test_gate_accepts_specific_noncommercial_help_and_rejects_unsafe_outreach():
    assert outreach.validate_candidate(candidate())["eligible"] is True
    bad = {
        "affiliate": candidate(reply_text="이 제품 사세요 https://amazon.com/dp/X"),
        "profile_bait": candidate(reply_text="프로필 링크에서 확인하세요."),
        "caregiver_shaming": candidate(reply_text="부모들이 게을러서 계속 이렇게 삽니다."),
        "medical": candidate(source_text="아이 검사 수치와 복용량을 봐주세요."),
        "occult_claim_kr": candidate(source_text="시주와 사주 원국이 아이 수면 리듬을 정해요."),
        "occult_claim_us": candidate(market="US", source_text="A birth chart and zodiac sign determine sleep."),
        "self_reply": candidate(source_author="heightcue"),
        "missing_provenance": candidate(source_post_url="", source_post_id=""),
    }
    for name, row in bad.items():
        result = outreach.validate_candidate(row)
        assert result["eligible"] is False, name
        assert result["reasons"], name


def test_reservation_is_append_only_and_idempotent_before_any_side_effect(tmp_path):
    ledger = tmp_path / "outreach.jsonl"
    first = outreach.reserve(ledger, candidate(), now="2026-08-31T12:00:00+09:00")
    second = outreach.reserve(ledger, candidate(), now="2026-08-31T12:01:00+09:00")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert first["reserved"] is True
    assert second["reserved"] is False
    assert first["idempotency_key"] == second["idempotency_key"]
    assert [row["status"] for row in rows] == ["reserved"]


def test_only_exact_provider_readback_transitions_reservation_to_verified(tmp_path):
    ledger = tmp_path / "outreach.jsonl"
    held = outreach.reserve(ledger, candidate(), now="2026-08-31T12:00:00+09:00")
    mismatch = outreach.record_readback(
        ledger, held["idempotency_key"],
        {"reply_id": "reply-1", "reply_url": "https://www.threads.com/@heightcue/post/REPLY1",
         "reply_author": "heightcue", "parent_source_post_id": "wrong",
         "reply_text": candidate()["reply_text"]},
        now="2026-08-31T12:02:00+09:00",
    )
    assert mismatch["status"] == "verification_pending"
    exact = outreach.record_readback(
        ledger, held["idempotency_key"],
        {"reply_id": "reply-1", "reply_url": "https://www.threads.com/@heightcue/post/REPLY1",
         "reply_author": "heightcue", "parent_source_post_id": "source-123",
         "reply_text": candidate()["reply_text"]},
        now="2026-08-31T12:03:00+09:00",
    )
    assert exact["status"] == "verified"
    assert exact["market"] == "KR"
    assert exact["friction_category"] == "storage_cleanup"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in rows] == ["reserved", "verification_pending", "verified"]
