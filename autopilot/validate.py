#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""셋업 검증 스크립트 — 운영 머신(맥/서버)에서 실행: python3 validate.py

config.json에 채워진 자격 정보들이 실제로 동작하는지 하나씩 확인하고 ✓/✗로 출력한다.
어떤 값도 화면에 그대로 출력하지 않는다(마지막 4자리만 표시).
"""
import json
import sys

import requests

import sourcing
from common import load_config, load_skill


def check_prompts(cfg):
    """v2.2 프롬프트 합성 검증 — context/ 파일 + 모든 스킬 섹션이 정상 합성되는지."""
    combos = [("A2", "KR"), ("A2", "US"), ("A3-KR", "KR"), ("A3-US", "US"),
              ("V1", "KR"), ("V1", "US"), ("A5", "KR"), ("A4", None)]
    try:
        sizes = []
        for name, country in combos:
            prompt = load_skill(cfg, name, country=country)
            if "절대 금지 목록" not in prompt:
                return f"✗ {name}: 합성 프롬프트에 공통 금지 목록 누락"
            sizes.append(len(prompt))
        return f"✓ 정상 ({len(combos)}종 합성, {min(sizes)}~{max(sizes)}자)"
    except Exception as e:
        return f"✗ 실패: {type(e).__name__} — {str(e)[:120]}"


def check_openrouter(cfg):
    key = cfg.get("openrouter", {}).get("api_key", "")
    if not key or "여기에" in key:
        return "✗ 키 미설정"
    try:
        r = requests.get("https://openrouter.ai/api/v1/key",
                         headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code != 200:
            return f"✗ 키 인증 실패 (HTTP {r.status_code})"
        body = {"model": cfg["openrouter"]["model"],
                "messages": [{"role": "user", "content": "answer with exactly: ok"}],
                "max_tokens": 5}
        r2 = requests.post("https://openrouter.ai/api/v1/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {key}"}, timeout=60)
        if r2.status_code != 200:
            return f"✗ 모델 호출 실패 (HTTP {r2.status_code}): {r2.text[:120]}"
        return f"✓ 정상 (모델 {cfg['openrouter']['model']} 응답 확인)"
    except Exception as e:
        return f"✗ 연결 실패: {type(e).__name__} — 네트워크/방화벽 확인"


def check_threads(cfg, country):
    p = "kr" if country == "KR" else "us"
    uid = cfg["threads"].get(f"{p}_user_id", "")
    tok = cfg["threads"].get(f"{p}_access_token", "")
    if not tok:
        return "— 토큰 미설정 (건너뜀)"
    try:
        r = requests.get("https://graph.threads.net/v1.0/me",
                         params={"fields": "id,username", "access_token": tok}, timeout=20)
        if r.status_code != 200:
            return f"✗ 토큰 무효 (HTTP {r.status_code}): {r.text[:120]}"
        d = r.json()
        note = "" if (not uid or uid == d.get("id")) else f" ⚠ config의 user_id({uid})와 다름 → {d.get('id')}로 교체 필요"
        # 발행 권한(threads_content_publish)까지 확인 — 게시 쿼터 조회가 되면 발행 스코프 보유
        try:
            r2 = requests.get(f"https://graph.threads.net/v1.0/{d.get('id')}/threads_publishing_limit",
                              params={"fields": "quota_usage", "access_token": tok}, timeout=20)
            pub = "발행권한 ✓" if r2.status_code == 200 else f"발행권한 확인 실패(HTTP {r2.status_code})"
        except Exception:
            pub = "발행권한 확인 실패(연결)"
        return f"✓ @{d.get('username')} (user_id {d.get('id')}, {pub}){note}"
    except Exception as e:
        return f"✗ 연결 실패: {type(e).__name__}"


def check_coupang(cfg):
    if not cfg["coupang"].get("access_key"):
        return "— API 키 미설정 (수동 소싱 모드로 동작, 최종 승인 후 키 추가)"
    try:
        items = sourcing.search_products(cfg, "어린이 줄넘기", limit=1)
        return f"✓ 정상 (검색 응답 {len(items)}건)"
    except Exception as e:
        return f"✗ 실패: {type(e).__name__} — {str(e)[:120]}"


def main():
    cfg = load_config()
    results = {
        "프롬프트 합성": check_prompts(cfg),
        "OpenRouter": check_openrouter(cfg),
        "KR Threads": check_threads(cfg, "KR"),
        "US Threads": check_threads(cfg, "US"),
        "쿠팡 파트너스": check_coupang(cfg),
        "Amazon tracking": ("✓ " + cfg.get("amazon", {}).get("tracking_id", "")
                            if cfg.get("amazon", {}).get("tracking_id") else "✗ 미설정"),
    }
    print("heightcue 셋업 검증\n" + "=" * 40)
    for name, result in results.items():
        print(f"{name:<16}: {result}")
    print("=" * 40)
    required = ("프롬프트 합성", "OpenRouter", "KR Threads", "US Threads", "Amazon tracking")
    failed = [name for name in required if not results[name].startswith("✓")]
    if failed:
        print("✗ 가동 불가: " + ", ".join(failed))
        return 1
    print("✓ KR·US 트랙 필수 자격 정상")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
