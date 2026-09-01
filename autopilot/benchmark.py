# -*- coding: utf-8 -*-
"""바이럴 쓰레드 벤치마킹 워커 (autopilot/benchmark.py).

Aside CLI 또는 벤치마크 피드를 통해 쓰레드에서 터진 날것의 글(말빨, 리듬, 훅)을 수집하고
'내용'이 아닌 '말맛/호흡/어투 템플릿'만 리버스 엔지니어링하여 state/playbook.md 및
state/viral_style_seeds.json에 반영한다.
"""
import json
import os
import re
import sys
from common import load_config, log, read_json, state_path, write_json

STYLE_DECONSTRUCT_PROMPT = """너는 쓰레드(Threads) 바이럴 포스팅의 말빨과 어투를 분석하는 수석 리서처다.
입력된 바이럴 쓰레드 글에서 '주제/내용'은 전부 버리고, 오직 **'사람을 빨아들이는 말맛, 문장 리듬, 어투 패턴'**만 역설계(Reverse-engineer)한다.

분석 기준:
1. 훅의 시작 호흡 (장면 제시, 헛웃음, 억울함, 결제 멈춤 등)
2. 문장 길이와 줄바꿈 호흡 (어디서 끊어 치는지)
3. 서술어 어투 특성 (툭 내뱉는 어미, 쿨한 종결 등)
4. AI 냄새 없는 인간 특유의 텐션 포인트

출력: JSON 객체
{
  "pattern_id": "영문 식별자 (예: midnight_doubt, checkout_halt 등)",
  "name": "한글 패턴 이름",
  "cadence": "서사 전개 공식 (예: 황당한 장면 -> 뒷면 확인 -> 허탈한 진실 -> 쿨한 대안)",
  "example_hook": "이 텐션을 167cm 영양/육아 페르소나에 적용한 1줄 훅 예시",
  "voice_traits": ["핵심 어투/서술어 3개"]
}
"""

def extract_style_from_post(cfg, raw_post_text):
    """주어진 원문 포스팅에서 스타일/말맛 패턴을 추출."""
    import generate
    if not raw_post_text or len(raw_post_text.strip()) < 30:
        return None
    try:
        res = generate._gemini(cfg, STYLE_DECONSTRUCT_PROMPT, {"viral_post": raw_post_text})
        if isinstance(res, dict) and res.get("pattern_id") and res.get("example_hook"):
            return res
    except Exception as e:
        log(f"스타일 분석 실패: {e}")
    return None

def update_style_seeds(cfg, new_pattern):
    """새로 발견된 바이럴 말빨 패턴을 seeds 파일에 추가/갱신."""
    if not new_pattern:
        return False
    seeds_file = state_path(cfg, "viral_style_seeds.json")
    seeds = read_json(seeds_file, {"version": "1.0", "viral_speech_patterns": []})
    patterns = seeds.get("viral_speech_patterns", [])

    # 중복 체크
    existing_ids = {p.get("pattern_id") for p in patterns}
    if new_pattern.get("pattern_id") not in existing_ids:
        patterns.append(new_pattern)
        seeds["viral_speech_patterns"] = patterns[-10:] # 최근 10개 유지
        write_json(seeds_file, seeds)
        log(f"새 바이럴 말빨 패턴 등록: {new_pattern.get('name')} ({new_pattern.get('pattern_id')})")
        return True
    return False

if __name__ == "__main__":
    cfg = load_config()
    print("벤치마크 모듈 정상 로드")
