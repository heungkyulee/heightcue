# -*- coding: utf-8 -*-
"""댓글 응대 회귀 테스트.

핵심 회귀: 답글은 반드시 '댓글 id'에 달려야 한다(원글 id 금지).
2026-08-28 사고 — reply_to=post['media_id'] 때문에 원글에 새 댓글이 달렸다.

실행: ../.venv/bin/python test_comments.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import comments  # noqa: E402
import generate  # noqa: E402
import publish  # noqa: E402

POST_ID = "17900326344567271"
COMMENT_ID = "17884339329479178"


def _cfg(tmp):
    return {
        "cadence": {"max_replies_per_run": 5},
        "mode": {"publish": True},
        "paths": {"state_dir": tmp, "story_bank": os.path.join(tmp, "story-bank.md")},
        "threads": {"kr_user_id": "1", "kr_access_token": "t"},
    }


def _seed(tmp):
    with open(os.path.join(tmp, "story-bank.md"), "w", encoding="utf-8") as f:
        f.write("# story bank\n")
    with open(os.path.join(tmp, "published.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"country": "KR", "text": "원글 본문", "media_id": POST_ID,
                            "meta": {"post_type": "value"}}, ensure_ascii=False) + "\n")


def run_case(conversation, decision, monkey_publish):
    tmp = tempfile.mkdtemp()
    _seed(tmp)
    cfg = _cfg(tmp)

    publish.fetch_conversation = lambda c, co, m, dry_run=False: conversation
    publish.fetch_replies = lambda c, co, m, dry_run=False: conversation
    publish.fetch_username = lambda c, co, dry_run=False: "heightcue"
    publish.publish_text = monkey_publish
    generate.make_reply = lambda cfg, **kw: decision

    n = comments.run(cfg, dry_run=False)
    return n, tmp


def test_reply_targets_comment_not_post():
    calls = []

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        calls.append({"reply_to": reply_to, "meta": meta})
        return "NEWID"

    convo = [{"id": COMMENT_ID, "text": "마그네슘도 도움이 될까요?", "username": "someparent",
              "replied_to": {"id": POST_ID}}]
    decision = {"category": "question_medical", "action": "reply",
                "text": "소아과 선생님께 상담받아보시는 게 제일 정확해요.", "reason": "의료"}
    n, _ = run_case(convo, decision, fake_publish)

    assert n == 1, n
    assert len(calls) == 1
    assert calls[0]["reply_to"] == COMMENT_ID, f"원글 id로 발행됨: {calls[0]['reply_to']}"
    assert calls[0]["reply_to"] != POST_ID
    assert calls[0]["meta"]["to_comment"] == COMMENT_ID
    assert calls[0]["meta"]["root_post"] == POST_ID
    print("ok: 답글이 댓글 id를 타깃")


def test_skips_own_comments():
    calls = []

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        calls.append(reply_to)
        return "NEWID"

    convo = [
        {"id": "own1", "text": "우리 계정이 단 답글", "username": "heightcue"},
        {"id": "own2", "text": "is_reply_owned_by_me 판정", "username": "someone",
         "is_reply_owned_by_me": True},
        {"id": "blank", "text": "   ", "username": "someparent"},
    ]
    decision = {"action": "reply", "text": "답글", "reason": ""}
    n, _ = run_case(convo, decision, fake_publish)
    assert n == 0, n
    assert calls == [], calls
    print("ok: 자기 댓글·빈 댓글 스킵")


def test_no_duplicate_reply_from_published_log():
    """replies_handled.json이 없어도 published.jsonl의 to_comment로 중복을 막는다."""
    calls = []

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        calls.append(reply_to)
        return "NEWID"

    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "story-bank.md"), "w", encoding="utf-8") as f:
        f.write("# story bank\n")
    with open(os.path.join(tmp, "published.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"country": "KR", "text": "원글", "media_id": POST_ID,
                            "meta": {"post_type": "value"}}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"country": "KR", "text": "이미 단 답글", "media_id": "R1",
                            "meta": {"kind": "reply", "to_comment": COMMENT_ID}},
                           ensure_ascii=False) + "\n")
    cfg = _cfg(tmp)
    publish.fetch_conversation = lambda c, co, m, dry_run=False: [
        {"id": COMMENT_ID, "text": "같은 댓글", "username": "someparent"}]
    publish.fetch_username = lambda c, co, dry_run=False: "heightcue"
    publish.publish_text = fake_publish
    generate.make_reply = lambda cfg, **kw: {"action": "reply", "text": "x", "reason": ""}

    n = comments.run(cfg, dry_run=False)
    assert n == 0, n
    assert calls == []
    print("ok: 발행 로그 기반 중복 차단")


def test_conversation_fallback_to_replies():
    calls = []

    def boom(*a, **k):
        raise RuntimeError("conversation 미지원")

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        calls.append(reply_to)
        return "NEWID"

    tmp = tempfile.mkdtemp()
    _seed(tmp)
    cfg = _cfg(tmp)
    publish.fetch_conversation = boom
    publish.fetch_replies = lambda c, co, m, dry_run=False: [
        {"id": "c9", "text": "폴백 댓글", "username": "someparent"}]
    publish.fetch_username = lambda c, co, dry_run=False: "heightcue"
    publish.publish_text = fake_publish
    generate.make_reply = lambda cfg, **kw: {"action": "reply", "text": "답", "reason": ""}

    n = comments.run(cfg, dry_run=False)
    assert n == 1 and calls == ["c9"], (n, calls)
    print("ok: conversation 실패 시 replies 폴백")


if __name__ == "__main__":
    test_reply_targets_comment_not_post()
    test_skips_own_comments()
    test_no_duplicate_reply_from_published_log()
    test_conversation_fallback_to_replies()
    print("\n댓글 응대 회귀 4/4 통과")
