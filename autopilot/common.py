# -*- coding: utf-8 -*-
"""공통: 설정 로드, 상태 저장, 스킬·스토리뱅크 파서."""
import json
import os
import re
import time

BASE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    path = os.path.join(BASE, "config.json")
    if not os.path.exists(path):
        path = os.path.join(BASE, "config.example.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["paths"]["state_dir"] = os.path.join(BASE, cfg["paths"].get("state_dir", "state"))
    os.makedirs(cfg["paths"]["state_dir"], exist_ok=True)
    return cfg


def state_path(cfg, name):
    """Return a state-file path for complete and lightweight test configs.

    Callers such as the comments lock need the directory to exist even when a
    mocked config omits ``paths.state_dir``.  Production configs still use the
    normalized path prepared by ``load_config``.
    """
    paths = cfg.setdefault("paths", {})
    state_dir = paths.get("state_dir")
    if not state_dir:
        state_dir = os.path.join(BASE, "state")
        paths["state_dir"] = state_dir
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, name)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    path = os.fspath(path)
    if os.path.exists(path):
        os.chmod(path, 0o600)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_jsonl(path, record):
    record = dict(record)
    record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path = os.fspath(path)
    if os.path.exists(path):
        os.chmod(path, 0o600)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def is_real_publication(record):
    """정확한 read-back까지 끝난 게시만 분석·댓글의 성공 입력으로 인정한다."""
    media_id = str(record.get("media_id") or "")
    status = str((record.get("meta") or {}).get("publish_status") or "")
    return bool(media_id) and status == "verified"


def is_possible_live_publication(record):
    """중복 차단에는 verified와 verification_pending을 모두 live 점유로 본다."""
    media_id = str(record.get("media_id") or "")
    status = str((record.get("meta") or {}).get("publish_status") or "")
    return bool(media_id) and status in ("verified", "verification_pending")


def redact_secrets(text):
    """Remove secrets from query strings, mappings, and HTTP-style headers."""
    keys = r"access_token|api_key|apikey|authorization|client_secret|secret_key|token"
    clean = str(text)
    clean = re.sub(
        rf"(?i)(\b(?:{keys})\b\s*=\s*)[^&\s\"']+",
        r"\1[REDACTED]",
        clean,
    )
    clean = re.sub(
        rf"(?i)(['\"]?(?:{keys})['\"]?\s*:\s*['\"]?)(?:Bearer\s+)?[^,\s'\"}}]+",
        r"\1[REDACTED]",
        clean,
    )
    return clean


def load_skill(cfg, name, country=None):
    """context/(compliance·persona[·voice-국가]) + gemini-skills.md의 '## SKILL <name>' 본문을 합성한다.

    v2.2: 이전에는 스킬 섹션 하나만 주입되어 공통 금지 목록이 모델에게 보이지 않았다.
    이제 모든 호출이 컴플라이언스·페르소나를 직접 보고, country가 있으면 해당 보이스도 본다.
    """
    path = os.path.join(BASE, cfg["paths"]["skills"])
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"## SKILL {re.escape(name)}[^\n]*\n(.*?)(?=\n## |\Z)", text, re.S)
    if not m:
        raise ValueError(f"스킬 {name} 을 찾을 수 없음: {path}")
    blocks = re.findall(r"```\n(.*?)```", m.group(1), re.S)
    body = blocks[0].strip() if blocks else m.group(1).strip()

    ctx_dir = os.path.join(os.path.dirname(path), "context")  # 스킬 파일과 같은 디렉터리(레포 루트)
    files = ["user-intent-contract.md", "compliance.md", "persona.md"]
    if country == "KR":
        files.append("voice-kr.md")
    elif country == "US":
        files.append("voice-us.md")
    parts = []
    for fn in files:
        p = os.path.join(ctx_dir, fn)
        if not os.path.exists(p):
            raise ValueError(f"공통 컨텍스트 파일 없음: {p} — context/ 디렉터리를 확인하라")
        with open(p, encoding="utf-8") as f:
            parts.append(f.read().strip())
    
    # Inject ACP into context if running in a context where it's loaded
    # Usually handled by the payload, but we can add a placeholder in the system prompt
    parts.append("## ACCOUNT CONTEXT PACKET (ACP)\nApply the 'account_memory' instructions from the JSON payload strictly.")

    parts.append(body)
    return "\n\n---\n\n".join(parts)


def load_story_episodes(cfg):
    """story-bank.md에서 ### E{n} 에피소드를 파싱. ⚠️(미확인) 표시 에피소드는 제외."""
    path = os.path.join(BASE, cfg["paths"]["story_bank"])
    with open(path, encoding="utf-8") as f:
        text = f.read()
    episodes = []
    for m in re.finditer(r"### (E\d+)\.\s*([^\n]+)\n(.*?)(?=\n### |\n## |\Z)", text, re.S):
        eid, title, body = m.group(1), m.group(2).strip(), m.group(3).strip()
        if "⚠️" in title or "⚠️" in body or "미확인" in title:
            continue
        episodes.append({"id": eid, "title": title, "body": body})
    return episodes


def log(msg):
    print(f"[autopilot] {redact_secrets(msg)}")


def record_error(cfg, where, err):
    """단계별 오류를 기록하고 실행은 계속한다 — 무인 운영에서 한 단계 실패가 전체를 죽이지 않게."""
    append_jsonl(state_path(cfg, "errors.jsonl"),
                 {"where": where, "error": f"{type(err).__name__}: {redact_secrets(str(err))[:300]}"})
    log(f"오류[{where}]: {type(err).__name__} — 기록 후 계속 진행")


def save_threads_tokens(updates):
    """config.json의 threads 토큰을 갱신 저장 (다른 키는 보존). updates: {'kr_access_token': ...}"""
    path = os.path.join(BASE, "config.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw.setdefault("threads", {}).update({k: v for k, v in updates.items() if v})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def set_mode_flags(**flags):
    """config.json의 mode 플래그를 갱신 저장 (golive 등에서 사용)."""
    path = os.path.join(BASE, "config.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw.setdefault("mode", {}).update(flags)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    return raw["mode"]


def recent_context(cfg, country, n_posts=10, n_replies=10, n_comments=10):
    """국가별 실시간 컨텍스트 — 해당 Threads 계정의 실제 글·내 답글·받은 댓글·바이오를 API로 조회.
    API 실패 시 로컬 기록으로 폴백. 리허설 미리보기는 항상 병합(중복 방지용).
    account_memory.json(ACP)의 브랜드 총괄 가이드를 포함한다."""
    p = "kr" if country == "KR" else "us"
    uid = cfg.get("threads", {}).get(f"{p}_user_id", "")
    tok = cfg.get("threads", {}).get(f"{p}_access_token", "")
    ctx = {"my_bio": "", "recent_posts": [], "my_recent_replies": [], "recent_comments_received": [], "account_memory": {}}
    
    # Load ACP
    acp_path = state_path(cfg, "account_memory.json")
    if os.path.exists(acp_path):
        acp_data = read_json(acp_path, {})
        ctx["account_memory"] = acp_data.get(country, {})
    if uid and tok:
        try:
            import requests as rq
            base = "https://graph.threads.net/v1.0"
            prof = rq.get(f"{base}/{uid}", params={
                "fields": "username,threads_biography", "access_token": tok}, timeout=15).json()
            ctx["my_bio"] = prof.get("threads_biography", "")
            posts = rq.get(f"{base}/{uid}/threads", params={
                "fields": "id,text", "limit": n_posts, "access_token": tok}, timeout=15).json().get("data", [])
            ctx["recent_posts"] = [x.get("text", "") for x in posts if x.get("text")]
            my_replies = rq.get(f"{base}/{uid}/replies", params={
                "fields": "id,text", "limit": n_replies, "access_token": tok}, timeout=15).json().get("data", [])
            ctx["my_recent_replies"] = [x.get("text", "") for x in my_replies if x.get("text")]
            comments = []
            for post in posts[:5]:
                try:
                    r = rq.get(f"{base}/{post['id']}/replies", params={
                        "fields": "id,text,username", "limit": 10, "access_token": tok}, timeout=15).json().get("data", [])
                    comments += [f"@{x.get('username', '?')}: {x.get('text', '')}" for x in r if x.get("text")]
                except Exception:
                    continue
            ctx["recent_comments_received"] = comments[-n_comments:]
        except Exception as e:
            log(f"Threads 컨텍스트 조회 실패({country}): {type(e).__name__} — 로컬 기록으로 폴백")
    if not ctx["recent_posts"]:
        for q in read_jsonl(state_path(cfg, "published.jsonl")):
            if q.get("country") != country or not is_real_publication(q):
                continue
            if (q.get("meta") or {}).get("kind") == "reply":
                ctx["my_recent_replies"].append(q.get("text", ""))
            else:
                ctx["recent_posts"].append(q.get("text", ""))
    # 리허설 미리보기 병합 — 아직 발행 전인 생성물과의 중복 방지
    ctx["recent_posts"] += [q.get("text", "") for q in read_jsonl(state_path(cfg, "preview.jsonl"))
                            if q.get("country") == country]
    ctx["recent_posts"] = ctx["recent_posts"][-n_posts:]
    ctx["my_recent_replies"] = ctx["my_recent_replies"][-n_replies:]
    return ctx
