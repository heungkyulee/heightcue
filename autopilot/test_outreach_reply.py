#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import outreach_reply


def source():
    return {
        "market": "KR",
        "source_post_id": "source-123",
        "source_post_url": "https://www.threads.com/@parent/post/SOURCE123",
        "source_author": "parent",
        "source_text": "아침마다 양말과 이름표를 찾느라 현관에서 다시 올라갑니다.",
        "source_published_at": "2026-08-31T10:00:00Z",
        "discovery_query": "등교 준비",
    }


def test_reply_generation_is_bound_to_raw_source_anchor_and_one_approved_mechanism():
    seen = {}

    def fake_invoke(cfg, system, payload):
        seen.update({"system": system, "payload": payload})
        return {
            "source_digest": payload["source_digest"],
            "source_anchor": "양말",
            "mechanism_id": payload["mechanism"]["id"],
            "reply_text": "양말처럼 매일 다시 찾는 건 현관 앞 한 칸에 전날 이름표와 함께 두면 찾는 순서를 줄일 수 있어요.",
        }

    row = outreach_reply.generate(source(), "sleep_morning", {"openrouter": {"model": "google/gemini-3.7-flash"}}, invoke=fake_invoke)
    assert row["reply_text"].startswith("양말처럼")
    assert row["friction_category"] == "sleep_morning"
    assert row["reply_model"] == "google/gemini-3.7-flash"
    assert seen["payload"]["source"]["source_text"] == source()["source_text"]
    assert len(seen["payload"]["allowed_mechanisms"]) == 1
    assert "no brand" in seen["system"].lower()


def test_reply_generation_rejects_fabricated_source_anchor_before_reservation():
    def fake_invoke(cfg, system, payload):
        return {
            "source_digest": payload["source_digest"],
            "source_anchor": "냉장고",
            "mechanism_id": payload["mechanism"]["id"],
            "reply_text": "냉장고 앞에 두면 됩니다.",
        }

    try:
        outreach_reply.generate(source(), "sleep_morning", {"openrouter": {"model": "google/gemini-3.7-flash"}}, invoke=fake_invoke)
    except outreach_reply.ReplyGenerationError as exc:
        assert str(exc) == "source_anchor_not_observed"
    else:
        raise AssertionError("fabricated source anchor was accepted")
