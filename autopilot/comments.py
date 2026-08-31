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
from datetime import datetime

import generate
import publish
import execution_contract
from common import (append_jsonl, is_real_publication, log, read_json,
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
    except Exception:
        # conversation 미지원/일시오류 → replies로 폴백. 여기서 로그를 남기면
        # 삭제된 글 때문에 3분마다 같은 줄이 쌓이므로 조용히 넘어간다.
        return publish.fetch_replies(cfg, country, media_id, dry_run=dry_run)


def _parent_id(comment):
    r = comment.get("replied_to")
    if isinstance(r, dict):
        return r.get("id")
    return r or None


def build_thread_context(comment, by_id, root_id, self_username, max_depth=8):
    """원글 바로 다음부터 이 댓글 직전까지의 대화 체인을 시간순으로 만든다.

    대댓글("그럼 몇 개월이요?")은 단독으로 의미가 서지 않으므로 부모 체인을 모델에 넘겨야 한다.
    반환: ([{speaker, username, text}, ...], is_nested)
    """
    chain = []
    seen = {comment.get("id")}
    pid = _parent_id(comment)
    while pid and pid != root_id and pid not in seen and len(chain) < max_depth:
        seen.add(pid)
        parent = by_id.get(pid)
        if not parent:
            break  # 부모를 못 읽었으면 체인을 여기서 끊는다(추측 금지)
        chain.append({
            "speaker": "me" if _is_own(parent, self_username) else "them",
            "username": parent.get("username"),
            "text": parent.get("text") or "",
        })
        pid = _parent_id(parent)
    chain.reverse()  # 오래된 것 → 최신
    return chain, bool(chain)


def _age_hours(post, now=None):
    """발행 후 경과 시간(시간 단위). ts를 못 읽으면 0(=최신 취급)으로 본다."""
    ts = post.get("ts")
    if not ts:
        return 0.0
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", ""))
    except ValueError:
        return 0.0
    return max(0.0, ((now or datetime.now()) - t).total_seconds() / 3600.0)


def select_posts(published, now=None, hour=None, minute=None, max_posts=30):
    """이번 실행에서 댓글을 확인할 글 고르기.

    3분마다 실행되므로(하루 480회) 매번 전 글을 조회하면 호출이 폭증한다.
    댓글은 발행 직후에 몰리므로 나이별로 확인 빈도를 달리한다 —
    새 글일수록 촘촘히, 오래될수록 드물게. 새 글은 절대 놓치지 않는다:
      - 6시간 이내 : 매 실행(3분)   ← 댓글이 실제로 달리는 구간
      - 6~48시간   : 15분마다
      - 48시간~7일 : 3시간마다 (정시 부근)
      - 7일 초과   : 하루 1회(09시 정시 부근)
    """
    now = now or datetime.now()
    hour = now.hour if hour is None else hour
    minute = now.minute if minute is None else minute
    recent = [p for p in published if is_real_publication(p)
              and p.get("meta", {}).get("kind") != "reply"][-max_posts:]
    # 3분 주기라 정시에 정확히 안 걸릴 수 있어 '정시 부근'(0~2분)으로 판정한다.
    on_hour = minute < 3
    out = []
    for p in recent:
        age = _age_hours(p, now)
        if age <= 6:
            out.append(p)
        elif age <= 48:
            if minute % 15 < 3:
                out.append(p)
        elif age <= 24 * 7:
            if on_hour and hour % 3 == 0:
                out.append(p)
        elif on_hour and hour == 9:
            out.append(p)
    return out


def run(cfg, dry_run=False):
    handled = set(read_json(state_path(cfg, "replies_handled.json"), []))
    published = read_jsonl(state_path(cfg, "published.jsonl"))
    # 삭제되어 조회 불가한 글 — 3분마다 400을 맞지 않도록 영구 제외한다.
    gone = set(read_json(state_path(cfg, "gone_posts.json"), []))
    recent = [p for p in select_posts(published) if p.get("media_id") not in gone]
    # 이미 우리가 답글을 단 댓글 id — state 파일이 유실돼도 중복 응답을 막는 2차 방어선.
    already = {str(p.get("meta", {}).get("to_comment")) for p in published
               if p.get("meta", {}).get("kind") == "reply"}
    handled |= {c for c in already if c and c != "None"}

    story_facts = []  # narrative archives are never active reply context
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
            # 삭제된 글은 영구 400이라 3분마다 재시도할 이유가 없다 — 묘비를 남겨 건너뛴다.
            if "400" in str(e) and post["media_id"] not in gone:
                gone.add(post["media_id"])
                write_json(state_path(cfg, "gone_posts.json"), sorted(gone))
                log(f"게시물 조회 불가(삭제 추정) — 이후 건너뜀: {post['media_id']}")
            elif "400" not in str(e):
                log(f"댓글 조회 실패 {post['media_id']}: {e}")
            continue
        by_id = {c.get("id"): c for c in comments if c.get("id")}
        # 이미 우리 답글이 달려 있는 댓글 = 응답 완료. state가 없어도 스레드 자체가 증거다.
        answered = {_parent_id(c) for c in comments
                    if _is_own(c, self_names.get(country)) and _parent_id(c)}
        for c in comments:
            if replied >= cap:
                break
            cid = c.get("id")
            if not cid or cid in handled:
                continue
            if _is_own(c, self_names.get(country)):
                handled.add(cid)  # 자기 글은 영구 스킵
                continue
            if cid in answered:
                handled.add(cid)  # 이 댓글엔 이미 답했다
                continue
            if not (c.get("text") or "").strip():
                handled.add(cid)
                continue

            thread_context, is_nested = build_thread_context(
                c, by_id, post["media_id"], self_names.get(country))
            # 대댓글인데 부모 체인을 복원하지 못하면 맥락 없이 답하게 된다 → 보류.
            if is_nested is False and _parent_id(c) not in (None, post["media_id"]):
                handled.add(cid)
                append_jsonl(state_path(cfg, "holdbox.jsonl"),
                             {"why": "context_missing", "comment": c.get("text"),
                              "comment_id": cid, "post": post["media_id"],
                              "reason": "대댓글 부모 체인 복원 실패 — 맥락 없이 답하지 않음"})
                continue

            handled.add(cid)
            decision = generate.make_reply(
                cfg, comment=c.get("text", ""),
                post_summary=post.get("text", "")[:200],
                post_type=post.get("meta", {}).get("post_type", "value"),
                story_facts=story_facts, dry_run=dry_run,
                country=country,
                thread_context=thread_context, is_nested=is_nested,
                input_ids=[f"comment:{cid}", f"post:{post['media_id']}"],
            )
            append_jsonl(state_path(cfg, "comments_log.jsonl"),
                         {"comment_id": cid, "comment": c.get("text"), "country": country,
                          "is_nested": is_nested, "context_depth": len(thread_context),
                          "decision": decision})
            action = decision.get("action")
            if action == "reply" and decision.get("text"):
                # reply_to는 반드시 '댓글 id'. 원글 id를 넣으면 답글이 아니라
                # 원글에 새 댓글이 달리고 질문자에게 알림이 가지 않는다.
                reply_meta = {"kind": "reply", "to_comment": cid,
                              "root_post": post["media_id"],
                              "is_nested": is_nested,
                              "to_username": c.get("username")}
                provenance = decision.get("_provenance")
                if isinstance(provenance, dict):
                    reply_meta = execution_contract.merge_provenance(reply_meta, provenance)
                media_id = publish.publish_text(
                    cfg, country, decision["text"], reply_to=cid,
                    dry_run=dry_run, meta=reply_meta)
                if media_id:
                    replied += 1
                    time.sleep(1)
            elif action == "hold":
                append_jsonl(state_path(cfg, "holdbox.jsonl"),
                             {"why": "comment_hold", "comment": c.get("text"),
                              "comment_id": cid,
                              "post": post.get("media_id"), "reason": decision.get("reason")})

    write_json(state_path(cfg, "replies_handled.json"), sorted(handled))
    # 3분마다 도는 작업이라 매번 로그를 남기면 cron.log가 무의미하게 커진다.
    # 실제로 뭔가 한 경우에만 기록한다.
    if replied:
        log(f"댓글 응대 완료: 답글 {replied}건")
    return replied
