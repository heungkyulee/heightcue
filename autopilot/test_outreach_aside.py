#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import subprocess

import outreach
import outreach_aside


def test_parser_ignores_ansi_progress_arrays_and_accepts_only_defining_record_shape():
    output = (
        "\x1b[32m[tool] searching\x1b[0m\n"
        '[{"title":"citation without source fields"}]\n'
        "result follows:\n"
        '[{"source_post_id":"abc","source_post_url":"https://www.threads.com/@p/post/ABC",'
        '"source_author":"p","source_text":"storage friction","source_published_at":"2026-08-31T10:00:00Z"}]\n'
    )
    rows = outreach_aside.extract_records(output)
    assert len(rows) == 1
    assert rows[0]["source_post_id"] == "abc"


def test_parser_accepts_explicit_final_empty_array_without_accepting_progress_arrays():
    assert outreach_aside.extract_records("[tool] nothing found\n[]\n") == []


def test_discovery_invokes_aside_u0_once_per_query_and_never_batches_topics():
    calls = []
    counter = {"value": 0}

    def fake_runner(args, **kwargs):
        calls.append((args, kwargs))
        counter["value"] += 1
        n = counter["value"]
        payload = [{
            "source_post_id": f"id-{n}",
            "source_post_url": f"https://www.threads.com/@p/post/P{n}",
            "source_author": "p",
            "source_text": f"scene {n}",
            "source_published_at": "2026-08-31T10:00:00Z",
        }]
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    rows = outreach_aside.discover("US", ["bedtime routine", "lunchbox cleanup"], runner=fake_runner)
    assert [row["discovery_query"] for row in rows] == ["bedtime routine", "lunchbox cleanup"]
    assert len(calls) == 2
    for (args, kwargs), query in zip(calls, ("bedtime routine", "lunchbox cleanup")):
        assert args[:4] == ["aside", "--account", "u0", "exec"]
        assert query in args[4]
        assert "astrology" in args[4]
        assert "사주" in args[4]
        assert kwargs["timeout"] == 360


def test_discovery_third_positional_argument_is_source_limit():
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        payload = [{
            "source_post_id": "id-1",
            "source_post_url": "https://www.threads.com/@p/post/P1",
            "source_author": "p",
            "source_text": "scene",
            "source_published_at": "2026-09-01T10:00:00Z",
        }]
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    rows = outreach_aside.discover("KR", ["등교 준비"], 1, runner=fake_runner)
    assert len(rows) == 1
    assert "up to 1 recent" in calls[0][4]


def test_publish_uses_aside_then_independent_readback_before_verified(tmp_path):
    candidate = {
        "market": "KR", "source_post_id": "source-123",
        "source_post_url": "https://www.threads.com/@parent/post/SOURCE123",
        "source_author": "parent", "source_text": "아침마다 양말 찾느라 늦어요.",
        "source_published_at": "2026-08-31T10:00:00Z", "friction_id": "morning-socks",
        "reply_text": "현관 앞 한 칸을 등교용으로만 비워 두면 찾는 순서를 줄일 수 있어요. 특히 양말과 이름표를 전날 같이 두면 아침에 다시 흩어질 일이 적습니다.",
    }
    ledger = tmp_path / "outreach.jsonl"
    reservation = outreach.reserve(ledger, candidate, "2026-08-31T12:00:00Z")
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            payload = {"publish_status": "submitted", "reply_id": "reply-1", "reply_url": "https://www.threads.com/@heightcue/post/REPLY1"}
        else:
            payload = {
                "reply_id": "reply-1", "reply_url": "https://www.threads.com/@heightcue/post/REPLY1",
                "reply_author": "heightcue", "parent_source_post_id": "source-123",
                "reply_text": candidate["reply_text"],
            }
        return subprocess.CompletedProcess(args, 0, "tool log\n" + json.dumps(payload, ensure_ascii=False), "")

    result = outreach_aside.publish_and_verify(ledger, reservation, runner=fake_runner, now="2026-08-31T12:01:00Z")
    assert result["status"] == "verified"
    assert len(calls) == 2
    assert all(call[:4] == ["aside", "--account", "u0", "exec"] for call in calls)
    assert "Do not like, follow, DM" in calls[0][4]
    assert "Read-only verification" in calls[1][4]


def test_market_account_mismatch_stops_before_reply_side_effect(tmp_path):
    candidate = {
        "market": "US", "source_post_id": "source-us", "source_post_url": "https://www.threads.com/@parent/post/SOURCEUS",
        "source_author": "parent", "source_text": "The morning routine is scattered across the house.",
        "source_published_at": "2026-08-31T10:00:00Z", "friction_id": "fr-us", "friction_category": "sleep_morning",
        "reply_text": "Keeping the first-step items by the door can reduce the number of places everyone has to check.",
    }
    ledger = tmp_path / "outreach.jsonl"
    reservation = outreach.reserve(ledger, candidate, "2026-08-31T12:00:00Z")
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        payload = {"publish_status": "account_mismatch", "observed_active_handle": "heightcue"}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    try:
        outreach_aside.publish_and_verify(ledger, reservation, runner=fake_runner, now="2026-08-31T12:01:00Z")
    except outreach_aside.AsideAdapterError as exc:
        assert "account_mismatch" in str(exc)
    else:
        raise AssertionError("account mismatch reached publication")
    assert len(calls) == 1
    assert "@heightcue_us" in calls[0][4]
    assert [json.loads(line)["status"] for line in ledger.read_text().splitlines()] == ["reserved"]
