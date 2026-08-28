# -*- coding: utf-8 -*-
"""파이프라인 B: Threads 공식 API 발행 계층.

- 2단계 발행: 컨테이너 생성 → publish. 링크는 link_attachment로 첨부(본문 텍스트를 아끼는 방식).
- reply_to_id로 첫 답글(링크 답글 A/B)과 댓글 답글을 발행.
- 장기 토큰은 60일 유효 — refresh()를 주 1회 돌려 갱신하고 config에 반영하는 것은 운영 세션이 담당.
- dry_run이면 호출 없이 로그만 남긴다.
"""
import re
import time

import requests

from common import append_jsonl, log, state_path

API = "https://graph.threads.net/v1.0"


def _account(cfg, country):
    p = "kr" if country == "KR" else "us"
    return cfg["threads"].get(f"{p}_user_id"), cfg["threads"].get(f"{p}_access_token")


def publish_text(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
    """텍스트 포스트(또는 답글) 발행. 반환: media_id 또는 None."""
    record = {"country": country, "text": text, "link": link, "reply_to": reply_to, "meta": meta or {}}
    # 모든 발행 경로(메인 글·첫 답글·댓글 답글)의 최종 언어 게이트.
    # run._gate_and_publish는 메인 글을 검사하지만 comments.run은 이 계층을
    # 직접 호출하므로, API 경계에서도 US 한글 혼입을 차단한다.
    if country == "US" and re.search(r"[가-힣]", text or ""):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "language_fail", "stage": "publish_boundary", **record})
        log("발행 차단(US): 한글 혼입 — 보류함 기록")
        return None
    if country == "KR" and not re.search(r"[가-힣]", text or ""):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "language_fail", "stage": "publish_boundary", **record})
        log("발행 차단(KR): 한국어 없음 — 보류함 기록")
        return None
    if dry_run:
        record["media_id"] = f"DRY-{int(time.time())}"
        append_jsonl(state_path(cfg, "published.jsonl"), record)
        log(f"발행(dry, {country}): {text.splitlines()[0][:48]}...")
        return record["media_id"]

    # 리허설 모드: 실제 생성·검사는 다 하되 발행만 안 함. config에 "publish": true 를 넣어야 실발행.
    if not cfg["mode"].get("publish", False):
        record["media_id"] = f"PREVIEW-{int(time.time())}"
        append_jsonl(state_path(cfg, "preview.jsonl"), record)
        log(f"리허설(발행 안 함, {country}): {text.splitlines()[0][:48]}... → state/preview.jsonl")
        return record["media_id"]

    user_id, token = _account(cfg, country)
    if not (user_id and token):
        log(f"발행 불가({country}): Threads 토큰 없음 → 보류함으로")
        append_jsonl(state_path(cfg, "holdbox.jsonl"), {"why": "no_token", **record})
        return None

    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if link:
        params["link_attachment"] = link
    if reply_to:
        params["reply_to_id"] = reply_to
    r = requests.post(f"{API}/{user_id}/threads", data=params, timeout=30)
    r.raise_for_status()
    creation_id = r.json()["id"]
    time.sleep(2)
    r2 = requests.post(f"{API}/{user_id}/threads_publish",
                       data={"creation_id": creation_id, "access_token": token}, timeout=30)
    r2.raise_for_status()
    media_id = r2.json()["id"]
    record["media_id"] = media_id
    append_jsonl(state_path(cfg, "published.jsonl"), record)
    log(f"발행({country}): media_id={media_id}")
    return media_id


def delete_media(cfg, country, media_id, dry_run=False):
    """기존 Threads 게시물 삭제. 삭제 후 상태 검증은 호출자가 수행한다."""
    if dry_run:
        log(f"삭제(dry, {country}): media_id={media_id}")
        return True
    user_id, token = _account(cfg, country)
    if not (user_id and token):
        raise RuntimeError(f"삭제 불가({country}): Threads 토큰 없음")
    r = requests.delete(f"{API}/{media_id}", params={"access_token": token}, timeout=30)
    r.raise_for_status()
    log(f"삭제({country}): media_id={media_id}")
    return True


def refresh_token(cfg, country):
    """장기 토큰 갱신(60일 만료 전 주기 실행). 새 토큰을 반환 — config 반영은 호출자가."""
    _, token = _account(cfg, country)
    if not token:
        return None
    r = requests.get(f"{API.replace('/v1.0','')}/refresh_access_token",
                     params={"grant_type": "th_refresh_token", "access_token": token}, timeout=30)
    r.raise_for_status()
    return r.json().get("access_token")


_REPLY_FIELDS = "id,text,username,timestamp,replied_to,is_reply_owned_by_me"

_USERNAME_CACHE = {}


def fetch_username(cfg, country, dry_run=False):
    """계정 자기 핸들. 자기 댓글에 자기가 답글 다는 루프를 막는 데 쓴다."""
    if dry_run:
        return "dry_self"
    if country in _USERNAME_CACHE:
        return _USERNAME_CACHE[country]
    user_id, token = _account(cfg, country)
    if not (user_id and token):
        return None
    try:
        r = requests.get(f"{API}/{user_id}",
                         params={"fields": "username", "access_token": token}, timeout=30)
        r.raise_for_status()
        name = r.json().get("username")
    except Exception as e:  # 조회 실패해도 파이프라인은 계속 — 다른 자기필터가 있다
        log(f"username 조회 실패({country}): {e}")
        return None
    _USERNAME_CACHE[country] = name
    return name


def fetch_replies(cfg, country, media_id, dry_run=False):
    """게시물의 직속 답글만. 대댓글까지 필요하면 fetch_conversation을 쓸 것."""
    if dry_run:
        return [{"id": f"DRYC-{media_id}", "text": "저도 반에서 제일 작았는데 이 글 너무 공감돼요",
                 "username": "dry_user", "replied_to": {"id": media_id}}]
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{media_id}/replies",
                     params={"fields": _REPLY_FIELDS, "access_token": token}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_conversation(cfg, country, media_id, dry_run=False):
    """게시물 스레드 전체(대댓글 포함). 실패 시 호출자가 fetch_replies로 폴백한다."""
    if dry_run:
        return fetch_replies(cfg, country, media_id, dry_run=True)
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{media_id}/conversation",
                     params={"fields": _REPLY_FIELDS, "reverse": "false",
                             "access_token": token}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_insights(cfg, country, media_id, dry_run=False):
    if dry_run:
        return {"views": 1200, "likes": 34, "replies": 3, "reposts": 2, "quotes": 0, "shares": 1}
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{media_id}/insights",
                     params={"metric": "views,likes,replies,reposts,quotes,shares",
                             "access_token": token}, timeout=30)
    r.raise_for_status()
    out = {}
    for item in r.json().get("data", []):
        vals = item.get("values") or [{}]
        out[item.get("name")] = vals[0].get("value")
    return out


def fetch_link_clicks(cfg, country, dry_run=False):
    """계정 단위 clicks 지표 — URL별 분해값. 게시물별 고유 링크로 게시물 클릭을 추적한다."""
    if dry_run:
        return {"https://link.coupang.com/DRYRUN": 18}
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{user_id}/threads_insights",
                     params={"metric": "clicks", "access_token": token}, timeout=30)
    r.raise_for_status()
    out = {}
    for item in r.json().get("data", []):
        for lv in item.get("link_total_values", []):
            if "link_url" in lv:
                out[lv["link_url"]] = lv.get("value", 0)
        for v in item.get("values", []):
            out[str(v.get("dimension_values", v.get("end_time", "total")))] = v.get("value")
    return out
