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
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import comments  # noqa: E402
import generate  # noqa: E402
import publish  # noqa: E402

POST_ID = "17900326344567271"
COMMENT_ID = "17884339329479178"

_ORIGINAL_DEPENDENCIES = {
    (publish, name): getattr(publish, name)
    for name in ("fetch_conversation", "fetch_replies", "fetch_username", "publish_text")
}
_ORIGINAL_DEPENDENCIES[(generate, "make_reply")] = generate.make_reply


@pytest.fixture(autouse=True)
def restore_patched_dependencies(monkeypatch):
    """Keep legacy tests' direct module assignments from leaking by order."""
    for (module, name), value in _ORIGINAL_DEPENDENCIES.items():
        monkeypatch.setattr(module, name, value)
    yield
    for (module, name), value in _ORIGINAL_DEPENDENCIES.items():
        monkeypatch.setattr(module, name, value)


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
                            "meta": {"post_type": "value", "publish_status": "verified"}}, ensure_ascii=False) + "\n")


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


def test_failed_publication_is_not_counted_as_a_reply():
    convo = [{"id": COMMENT_ID, "text": "답변해 주세요", "username": "reader",
              "replied_to": {"id": POST_ID}}]
    decision = {"category": "question", "action": "reply", "text": "답변입니다.",
                "reason": "응답", "_provenance": {"contract_id": "heightcue-content-v1"}}

    n, _ = run_case(convo, decision, lambda *args, **kwargs: None)

    assert n == 0


def test_reply_targets_comment_not_post():
    calls = []

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        calls.append({"reply_to": reply_to, "meta": meta})
        return "NEWID"

    convo = [{"id": COMMENT_ID, "text": "마그네슘도 도움이 될까요?", "username": "someparent",
              "replied_to": {"id": POST_ID}}]
    decision = {"category": "question_medical", "action": "reply",
                "text": "소아과 선생님께 상담받아보시는 게 제일 정확해요.", "reason": "의료",
                "_provenance": {"contract_id": "heightcue-content-v1", "model": "m"}}
    n, _ = run_case(convo, decision, fake_publish)

    assert n == 1, n
    assert len(calls) == 1
    assert calls[0]["reply_to"] == COMMENT_ID, f"원글 id로 발행됨: {calls[0]['reply_to']}"
    assert calls[0]["reply_to"] != POST_ID
    assert calls[0]["meta"]["to_comment"] == COMMENT_ID
    assert calls[0]["meta"]["root_post"] == POST_ID
    assert calls[0]["meta"]["execution_contract"]["contract_id"] == "heightcue-content-v1"
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
                            "meta": {"post_type": "value", "publish_status": "verified"}}, ensure_ascii=False) + "\n")
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


def test_nested_reply_gets_thread_context():
    """대댓글은 부모 체인이 모델에 전달되어야 한다 ('그럼 몇 개월이요?' 단독은 무의미)."""
    seen = {}
    calls = []

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        calls.append({"reply_to": reply_to, "meta": meta})
        return "NEWID"

    def capture_reply(cfg, **kw):
        seen.update(kw)
        return {"action": "reply", "text": "보통 3개월쯤 보고 판단하시면 돼요.", "reason": ""}

    convo = [
        {"id": "c1", "text": "마그네슘 도움이 될까요?", "username": "someparent",
         "replied_to": {"id": POST_ID}},
        {"id": "r1", "text": "소아과 상담이 제일 정확해요.", "username": "heightcue",
         "replied_to": {"id": "c1"}, "is_reply_owned_by_me": True},
        {"id": "c2", "text": "그럼 몇 개월이요?", "username": "someparent",
         "replied_to": {"id": "r1"}},
    ]
    tmp = tempfile.mkdtemp()
    _seed(tmp)
    cfg = _cfg(tmp)
    publish.fetch_conversation = lambda c, co, m, dry_run=False: convo
    publish.fetch_username = lambda c, co, dry_run=False: "heightcue"
    publish.publish_text = fake_publish
    generate.make_reply = capture_reply

    n = comments.run(cfg, dry_run=False)

    assert n == 1, n
    # 대댓글 c2에 달려야 한다
    assert calls[0]["reply_to"] == "c2", calls[0]["reply_to"]
    assert calls[0]["meta"]["is_nested"] is True
    # 맥락 체인이 시간순으로 전달됐는지
    assert seen["is_nested"] is True
    ctx = seen["thread_context"]
    assert [x["text"] for x in ctx] == ["마그네슘 도움이 될까요?", "소아과 상담이 제일 정확해요."], ctx
    assert [x["speaker"] for x in ctx] == ["them", "me"], ctx
    print("ok: 대댓글에 부모 대화 체인 주입 + 대댓글 id 타깃")


def test_top_level_comment_has_empty_context():
    seen = {}

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        return "NEWID"

    def capture_reply(cfg, **kw):
        seen.update(kw)
        return {"action": "reply", "text": "감사해요.", "reason": ""}

    convo = [{"id": "c1", "text": "공감돼요", "username": "someparent",
              "replied_to": {"id": POST_ID}}]
    tmp = tempfile.mkdtemp()
    _seed(tmp)
    cfg = _cfg(tmp)
    publish.fetch_conversation = lambda c, co, m, dry_run=False: convo
    publish.fetch_username = lambda c, co, dry_run=False: "heightcue"
    publish.publish_text = fake_publish
    generate.make_reply = capture_reply

    comments.run(cfg, dry_run=False)
    assert seen["is_nested"] is False, seen["is_nested"]
    assert seen["thread_context"] == [], seen["thread_context"]
    print("ok: 최상위 댓글은 빈 컨텍스트")


def test_orphan_nested_comment_is_held():
    """부모를 못 읽은 대댓글은 맥락 없이 답하지 않고 보류한다."""
    calls = []

    def fake_publish(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        calls.append(reply_to)
        return "NEWID"

    # 부모 'missing_parent'가 목록에 없다
    convo = [{"id": "c9", "text": "그럼 몇 개월이요?", "username": "someparent",
              "replied_to": {"id": "missing_parent"}}]
    tmp = tempfile.mkdtemp()
    _seed(tmp)
    cfg = _cfg(tmp)
    publish.fetch_conversation = lambda c, co, m, dry_run=False: convo
    publish.fetch_username = lambda c, co, dry_run=False: "heightcue"
    publish.publish_text = fake_publish
    generate.make_reply = lambda cfg, **kw: {"action": "reply", "text": "아무말", "reason": ""}

    n = comments.run(cfg, dry_run=False)
    assert n == 0 and calls == [], (n, calls)
    hold = open(os.path.join(tmp, "holdbox.jsonl"), encoding="utf-8").read()
    assert "context_missing" in hold, hold
    print("ok: 고아 대댓글은 보류(맥락 없는 답변 금지)")


def test_deep_chain_order_and_depth_cap(monkeypatch):
    seen = {}

    def capture_reply(cfg, **kw):
        seen.update(kw)
        return {"action": "skip", "text": "", "reason": "대화 종료"}

    convo = [{"id": "c1", "text": "t1", "username": "u", "replied_to": {"id": POST_ID}}]
    prev = "c1"
    for i in range(2, 14):
        convo.append({"id": f"c{i}", "text": f"t{i}", "username": "u",
                      "replied_to": {"id": prev}})
        prev = f"c{i}"
    tmp = tempfile.mkdtemp()
    _seed(tmp)
    cfg = _cfg(tmp)
    monkeypatch.setattr(publish, "fetch_conversation",
                        lambda c, co, m, dry_run=False: convo)
    monkeypatch.setattr(publish, "fetch_username",
                        lambda c, co, dry_run=False: "heightcue")
    monkeypatch.setattr(publish, "publish_text", lambda *a, **k: "X")
    monkeypatch.setattr(generate, "make_reply", capture_reply)

    comments.run(cfg, dry_run=False)
    ctx = seen["thread_context"]
    assert len(ctx) <= 8, len(ctx)
    texts = [x["text"] for x in ctx]
    assert texts == sorted(texts, key=lambda t: int(t[1:])), texts  # 시간순
    print(f"ok: 깊은 체인 시간순 + 깊이 상한({len(ctx)}단)")


def test_select_posts_tiers_by_age():
    """3분 주기 실행 시 오래된 글은 건너뛰되 새 글은 절대 놓치지 않는다."""
    from datetime import datetime, timedelta
    now = datetime(2026, 8, 28, 14, 7, 0)   # 14:07 — 정시 아님, 15분 배수 아님

    def mk(pid, hours_ago):
        return {"country": "KR", "media_id": pid, "text": "t", "meta": {"post_type": "value", "publish_status": "verified"},
                "ts": (now - timedelta(hours=hours_ago)).isoformat()}

    pub = [mk("hot", 2), mk("day1", 30), mk("week", 100), mk("old", 24 * 20)]

    def ids(**kw):
        return [p["media_id"] for p in comments.select_posts(pub, now=now, **kw)]

    # 평상시(14:07): 6시간 이내만
    assert ids() == ["hot"], ids()
    # 15분 배수(14:15): 48시간 구간까지
    assert ids(minute=15) == ["hot", "day1"], ids(minute=15)
    # 정시 + 3의 배수 시각(15:01): 주간 구간까지
    assert ids(hour=15, minute=1) == ["hot", "day1", "week"], ids(hour=15, minute=1)
    # 09시 정시: 전부
    assert ids(hour=9, minute=0) == ["hot", "day1", "week", "old"], ids(hour=9, minute=0)
    # 09시라도 정시가 아니면 오래된 글 제외 (3분 주기라 하루 1회를 보장)
    assert "old" not in ids(hour=9, minute=30), ids(hour=9, minute=30)

    # ts 없는 레코드는 최신 취급 — 조용히 누락되면 안 된다
    nots = [{"country": "KR", "media_id": "nots", "text": "t",
             "meta": {"post_type": "value", "publish_status": "verified"}}]
    assert [p["media_id"] for p in comments.select_posts(nots, now=now)] == ["nots"]

    # 답글 레코드는 대상에서 제외
    withreply = pub + [{"country": "KR", "media_id": "r1", "text": "t",
                        "meta": {"kind": "reply", "to_comment": "c1"},
                        "ts": now.isoformat()}]
    assert "r1" not in [p["media_id"] for p in comments.select_posts(withreply, now=now)]
    print("ok: 나이별 계층 선택 (새 글 누락 없음)")


def test_deleted_post_is_tombstoned(monkeypatch):
    """삭제된 글은 한 번만 로그하고 이후 실행에서 영구 제외된다(3분마다 400 방지)."""
    tmp = tempfile.mkdtemp()
    _seed(tmp)
    cfg = _cfg(tmp)
    hits = []

    def boom(c, co, m, dry_run=False):
        hits.append(m)
        raise RuntimeError("400 Client Error: Bad Request")

    monkeypatch.setattr(publish, "fetch_conversation", boom)
    monkeypatch.setattr(publish, "fetch_replies", boom)
    monkeypatch.setattr(publish, "fetch_username",
                        lambda c, co, dry_run=False: "heightcue")
    monkeypatch.setattr(publish, "publish_text", lambda *a, **k: "X")
    monkeypatch.setattr(generate, "make_reply",
                        lambda cfg, **kw: {"action": "skip", "text": "", "reason": ""})

    comments.run(cfg, dry_run=False)
    first = len(hits)
    assert first > 0, "첫 실행에서 조회를 시도해야 한다"

    gone = json.load(open(os.path.join(tmp, "gone_posts.json"), encoding="utf-8"))
    assert POST_ID in gone, gone

    comments.run(cfg, dry_run=False)   # 두 번째 실행
    assert len(hits) == first, f"삭제된 글을 재조회했다: {len(hits)} > {first}"
    print("ok: 삭제된 글 묘비 처리(재조회 없음)")


def test_every_tier_runs_at_least_once_per_cycle():
    """3분 주기 하루 480회를 돌려 각 계층이 기대 횟수만큼 실행되는지 확인.

    계층을 잘못 짜면 특정 구간이 '영원히 조회 안 됨'이 될 수 있어 이를 막는다.
    """
    from datetime import datetime, timedelta
    base = datetime(2026, 8, 28, 0, 0, 0)

    def mk(pid, hours_ago):
        return {"country": "KR", "media_id": pid, "text": "t", "meta": {"post_type": "value", "publish_status": "verified"},
                "ts": (base - timedelta(hours=hours_ago)).isoformat()}

    pub = [mk("hot", 1), mk("day1", 30), mk("week", 100), mk("old", 24 * 20)]
    counts = {"hot": 0, "day1": 0, "week": 0, "old": 0}
    total_calls = 0
    for slot in range(480):                      # 3분 간격 하루치
        t = base + timedelta(minutes=3 * slot)
        # 나이는 고정해서 계층 경계 이동 효과를 배제하고 스케줄만 검증
        sel = comments.select_posts(pub, now=base, hour=t.hour, minute=t.minute)
        total_calls += len(sel)
        for p in sel:
            counts[p["media_id"]] += 1

    assert counts["hot"] == 480, counts          # 매 실행
    assert counts["day1"] == 96, counts          # 15분마다 = 96회
    assert counts["week"] == 8, counts           # 3시간마다 = 8회
    assert counts["old"] == 1, counts            # 하루 1회
    assert all(v > 0 for v in counts.values()), counts   # 굶는 계층 없음
    print(f"ok: 하루 스케줄 검증 (호출 {total_calls}회/일, 굶는 계층 없음)")


if __name__ == "__main__":
    # 이 파일은 pytest 픽스처(monkeypatch)를 쓰므로 함수를 직접 호출할 수 없다.
    # 직접 실행하면 pytest로 위임한다 — `python test_comments.py`도 그대로 동작하게.
    import subprocess
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", os.path.abspath(__file__), "-q"]))
