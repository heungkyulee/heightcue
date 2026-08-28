import os
import time
from collections import Counter
from typing import Any

from common import BASE, is_real_publication, read_json, read_jsonl, state_path, write_json


def _real_posts(records, country):
    posts = []
    for record in records:
        meta = record.get("meta") or {}
        if record.get("country") != country or meta.get("kind") == "reply":
            continue
        if not is_real_publication(record):
            continue
        if not record.get("text"):
            continue
        posts.append(record)
    return posts[-12:]


def _country_packet(records, country):
    posts = _real_posts(records, country)
    hooks = [post["text"].splitlines()[0].strip().strip('"') for post in posts]
    hook_counts = Counter(hook for hook in hooks if hook)
    overused = [hook for hook, count in hook_counts.most_common() if count >= 2]

    angles = []
    for post in posts:
        meta = post.get("meta") or {}
        angle = meta.get("hook_pattern") or meta.get("post_type")
        if angle and angle not in angles:
            angles.append(angle)
    angles = angles[-4:]

    if country == "KR":
        tension = (
            f"최근 실발행 {len(posts)}건 기준. "
            + (f"반복 훅 {len(overused)}개를 오늘 피한다." if overused else "반복 훅은 없지만 최근 앵글을 그대로 재사용하지 않는다.")
        )
        audience_vibe = "실측 댓글 데이터는 이 패킷에 없으므로 독자 감정을 추정하지 않는다."
        do_not = "AI식 교훈 마무리 금지. " + ("반복 훅: " + " | ".join(overused) if overused else "최근 훅의 문장 구조 복제 금지.")
    else:
        tension = (
            f"Based on {len(posts)} real published posts. "
            + (f"Avoid {len(overused)} repeated hook(s) today." if overused else "Do not reuse the recent angle structure verbatim.")
        )
        audience_vibe = "No measured comment sentiment is present in this packet; do not invent audience mood."
        do_not = "No AI wrap-up. " + ("Repeated hooks: " + " | ".join(overused) if overused else "Do not clone the recent hook structure.")

    return {
        "current_tension": tension,
        "recent_angles_used": angles,
        "overused_hooks": overused,
        "audience_vibe": audience_vibe,
        "do_not_do_today": do_not,
        "source_post_count": len(posts),
        "source_post_ids": [str(post.get("media_id")) for post in posts],
        "generated_from_real_data": True,
    }


def build_account_memory(records):
    return {"KR": _country_packet(records, "KR"), "US": _country_packet(records, "US")}


def needs_refresh(cfg):
    published = state_path(cfg, "published.jsonl")
    account_memory = state_path(cfg, "account_memory.json")
    if not os.path.exists(account_memory):
        return True
    if not os.path.exists(published):
        return False
    return os.path.getmtime(account_memory) < os.path.getmtime(published)


def run_digest(cfg, force=False):
    if not force and not needs_refresh(cfg):
        print("[autopilot/digest] account_memory.json is newer than published history; keeping brand-lead ACP.")
        return False
    records = read_jsonl(state_path(cfg, "published.jsonl"))
    acp: dict[str, Any] = build_account_memory(records)
    acp["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out_path = state_path(cfg, "account_memory.json")
    write_json(out_path, acp)
    print(f"[autopilot/digest] ACP rebuilt from real published records at {out_path}")
    return True


if __name__ == "__main__":
    cfg = read_json(os.path.join(BASE, "config.json"), {})
    run_digest(cfg, force=True)
