# -*- coding: utf-8 -*-
"""댓글 자동 응대: 최근 발행 글의 답글을 수집 → A5 스킬로 분류·초안 → 정책에 따라 답글/보류.

정책 (SSOT §8):
  - reply: 즉시 발행 (운영자 보이스, 스토리 뱅크 사실 범위 안에서만)
  - hold : 의료·분쟁·판단곤란 → holdbox.jsonl (주간 다이제스트에서 사람이 확인)
  - skip : 스팸·응답 불필요
안전장치: 실행당 답글 수 상한, 동일 댓글 재응답 방지, 자기 댓글 자문자답 방지.

발행 대상: **댓글 자체에 달리는 답글**(reply_to_id = 댓글 media_id).
원글 id를 reply_to로 넘기면 원글에 새 최상위 댓글이 하나 더 달릴 뿐이고
질문자에게 알림도 가지 않는다 — 2026-08-28 실사고.
"""
import time

import generate
import publish
from common import (append_jsonl, is_real_publication, load_story_episodes, log, read_json,
                    read_jsonl, state_path, write_json)


def _is_own(comment, self_username):
    """계정 자신이 남긴 댓글/답글이면 True (자문자답 차단)."""
    owned = comment.get("is_reply_owned_by_me")
    if owned is True:
        return True
    uname = (comment.get("username") or "").lstrip("@").lower()
    return bool(self_username) and uname == self_username.lstrip("@").lower()


def _collect(cfg, country, media_id, dry_run=False):
    """스레드 전체(대댓글 포함)를 우선 시도하고, 실패하면 직속 답글로 폴백."""
    try:
        return publish.fetch_conversation(cfg, country, media_id, dry_run=dry_run)
    except Exception as e:
        log(f"conversation 조회 실패 {media_id} ({e}) → replies 폴백")
        return publish.fetch_replies(cfg, country, media_id, dry_run=dry_run)


def run(cfg, dry_run=False):
    handled = set(read_json(state_path(cfg, "replies_handled.json"), []))
    published = read_jsonl(state_path(cfg, "published.jsonl"))
    recent = [p for p in published if is_real_publication(p)
              and p.get("meta", {}).get("kind") != "reply"][-30:]
    # 이미 우리가 답글을 단 댓글 id — state 파일이 유실돼도 중복 응답을 막는 2차 방어선.
    already = {str(p.get("meta", {}).get("to_comment")) for p in published
               if p.get("meta", {}).get("kind") == "reply"}
    handled |= {c for c in already if c and c != "None"}

    episodes = load_story_episodes(cfg)
    story_facts = [f"{e['id']}: {e['title']}" for e in episodes]
    cap = cfg["cadence"].get("max_replies_per_run", 20)
    replied = 0
    self_names = {}

    for post in recent:
        if replied >= cap:
            break
        country = post.get("country", "KR")
        if country not in self_names:
            self_names[country] = publish.fetch_username(cfg, country, dry_run=dry_run)
        try:
            comments = _collect(cfg, country, post["media_id"], dry_run=dry_run)
        except Exception as e:
            log(f"댓글 조회 실패 {post['media_id']}: {e}")
            continue
        for c in comments:
            if replied >= cap:
                break
            cid = c.get("id")
            if not cid or cid in handled:
                continue
            if _is_own(c, self_names.get(country)):
                handled.add(cid)  # 자기 글은 영구 스킵
                continue
            if not (c.get("text") or "").strip():
                handled.add(cid)
                continue
            handled.add(cid)
            decision = generate.make_reply(
                cfg, comment=c.get("text", ""),
                post_summary=post.get("text", "")[:200],
                post_type=post.get("meta", {}).get("post_type", "value"),
                story_facts=story_facts, dry_run=dry_run,
                country=country,
            )
            append_jsonl(state_path(cfg, "comments_log.jsonl"),
                         {"comment_id": cid, "comment": c.get("text"), "country": country,
                          "decision": decision})
            action = decision.get("action")
            if action == "reply" and decision.get("text"):
                # reply_to는 반드시 '댓글 id'. 원글 id를 넣으면 답글이 아니라
                # 원글에 새 댓글이 달리고 질문자에게 알림이 가지 않는다.
                publish.publish_text(cfg, country, decision["text"],
                                     reply_to=cid, dry_run=dry_run,
                                     meta={"kind": "reply", "to_comment": cid,
                                           "root_post": post["media_id"],
                                           "to_username": c.get("username")})
                replied += 1
                time.sleep(1)
            elif action == "hold":
                append_jsonl(state_path(cfg, "holdbox.jsonl"),
                             {"why": "comment_hold", "comment": c.get("text"),
                              "comment_id": cid,
                              "post": post.get("media_id"), "reason": decision.get("reason")})

    write_json(state_path(cfg, "replies_handled.json"), sorted(handled))
    log(f"댓글 응대 완료: 답글 {replied}건")
    return replied
