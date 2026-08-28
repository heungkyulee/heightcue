#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""증거 수집 워커 — Aside CLI를 호출해 원장(evidence.jsonl)을 채운다.

설계 원칙: Aside(브라우저 에이전트)는 **수집만** 한다. 검증·승격은
`evidence.claim_gate`가 파이썬 쪽에서 수행하므로, 워커가 게이트를
우회하거나 insight_atoms.json을 직접 건드릴 수 없다.

흐름:
    harvest_once()
      → 재고 부족한 거리(distance)를 계산
      → 그 거리의 topic으로 Aside 수집 프롬프트 생성
      → aside --account u0 exec "<프롬프트>"  (JSON stdout 요구)
      → 파싱 → evidence.record_evidence() → evidence.jsonl

실행:
    python3 harvest.py            # 부족한 거리 자동 판단, 1배치 수집
    python3 harvest.py --topic discipline
    python3 harvest.py --dry-run  # Aside 호출 없이 프롬프트만 출력
"""
import argparse
import json
import re
import subprocess
import sys

import evidence
from common import load_config, log, record_error

ASIDE_TIMEOUT = 900  # 브라우저 조사는 느리다. 15분.

# 거리별 수집 가이드 — Aside에게 "어디를 뒤질지" 알려준다.
TOPIC_SOURCES = {
    "sleep": "PubMed에서 sleep duration/slow-wave sleep와 growth hormone, 아동 수면 가이드라인",
    "nutrition": "아동 영양 섭취 기준(한국인 영양소 섭취기준), 칼슘·비타민D 관련 메타분석",
    "posture": "소아 자세·척추측만 관련 학회 가이드라인",
    "exercise": "아동·청소년 신체활동 지침(WHO, 보건복지부)",
    "checkup": "질병관리청 소아청소년 성장도표, 영유아 건강검진 지침",
    "growth_data": "질병관리청 성장도표 해석, 백분위·성장속도 관련 공식 자료",
    "eating_habits": "아동 편식·식습관 형성 관련 소아과학회 자료 및 연구",
    "stress_growth": "만성 스트레스와 성장(psychosocial short stature) 관련 논문",
    "screen_time": "AAP 스크린타임 권고, 수면·신체활동 영향 연구",
    "discipline": "AAP 훈육 정책성명(Effective Discipline), 체벌 대안 연구",
    "self_regulation": "아동 자기조절·실행기능 발달 연구(마시멜로 후속 재현 연구 포함)",
    "manners": "친사회적 행동 발달, 또래 관계 형성 관련 발달심리 논문",
    "mindset": "성장 마인드셋 개입 연구 및 그 재현성 논쟁(양쪽 다)",
    "emotional_dev": "정서조절·애착 발달 관련 학회 자료",
    "sibling_peer": "형제 비교·또래 비교가 아동 자존감에 미치는 영향 연구",
    "social_gaze": "외모·신체 관련 사회적 시선과 청소년 자존감 연구",
    "body_image": "아동·청소년 신체상(body image) 형성 연구",
    "operator_story": "(수집 대상 아님 — story-bank.md 소관)",
}

PROMPT_TEMPLATE = """너는 HeightCue의 증거 수집 워커다. 키·성장 고민을 가진 부모를 위한
정보 콘텐츠의 **근거**를 수집한다. 너는 수집만 한다 — 판단·발행은 하지 않는다.

## 이번 배치에서 수집할 주제
{topic_block}

## 반드시 지킬 것

1. **1차 출처 필수.** 논문(PubMed/DOI), 정부·공공기관(질병관리청·보건복지부·WHO),
   학회 가이드라인(AAP·대한소아청소년과학회), 공식 통계 중 **최소 1개**가 없으면
   그 항목은 제출하지 마라. 뉴스·블로그만으로는 무효다.
2. **URL 또는 DOI를 실제로 열어보고** 확인한 것만 기록한다. 존재하지 않는
   DOI/URL을 지어내면 그 배치 전체가 폐기된다.
3. **반론(counter_claim) 필수.** 그 주장의 한계·반대 근거·개인차를 반드시 함께 적는다.
   특히 키와 관련해서는 "최종 성인 키의 최대 결정 요인은 유전"이라는 사실을 회피하지 마라.
   이 정직함이 이 채널의 핵심 자산이다.
4. **인과 단정 금지.** "~하면 키가 큰다", "○cm 더 큰다", "보장" 같은 표현을 쓰지 마라.
   상관을 인과로 바꾸는 순간 그 항목은 자동 반려된다. "~와 연관이 보고된다" 수준으로 적어라.
5. **제품·브랜드·링크 금지.** 이건 비상업 정보 레이어다.
6. claim은 200자 이내. 원문이 말한 범위를 넘지 마라.

## 출력 형식 — 오직 JSON 배열만. 설명·마크다운 코드펜스 금지.

[
  {{
    "evidence_id": "ev-{stamp}-01",
    "topic": "{topic}",
    "claim": "원문이 말한 사실을 200자 이내로",
    "counter_claim": "한계·반론·개인차",
    "confidence": "strong|moderate|weak",
    "sources": [
      {{"type": "paper|gov|guideline|official_stat", "url": "실제 확인한 URL",
        "doi": "있으면", "year": 2024, "excerpt": "원문 인용 한 문장"}}
    ],
    "parent_emotion": "이 사실이 건드리는 부모의 감정 지점(죄책감 해소 방향)",
    "hook_seeds": ["이 사실로 만들 수 있는 훅 1~2개"]
  }}
]

confidence 기준: 메타분석·공식 가이드라인=strong, 다수 관찰연구=moderate,
단일 소규모 연구=weak. 모르면 낮게 잡아라.

{count}개 항목을 수집해라. 서로 다른 사실이어야 한다."""


def _stale_topics(cfg, limit=2):
    """재고가 목표 배분에 못 미치는 거리의 topic을 고른다."""
    store = evidence.atom_store(cfg)
    atoms = store["atoms"]
    total = len(atoms) or 1
    by_dist = {}
    for a in atoms:
        by_dist[a.get("distance", 0)] = by_dist.get(a.get("distance", 0), 0) + 1
    # 목표 대비 부족분이 큰 거리부터
    deficit = {d: evidence.DISTANCE_MIX[d] - (by_dist.get(d, 0) / total)
               for d in evidence.DISTANCE_MIX}
    order = sorted(deficit, key=lambda d: -deficit[d])

    covered = {a.get("topic") for a in atoms}
    picks = []
    for dist in order:
        topics = [t for t, d in evidence.TOPIC_DISTANCE.items()
                  if d == dist and t != "operator_story"]
        # 아직 한 번도 다루지 않은 주제 우선
        topics.sort(key=lambda t: (t in covered, t))
        for t in topics:
            if t not in picks:
                picks.append(t)
                break
        if len(picks) >= limit:
            break
    return picks


def build_prompt(topics, count=3, stamp=""):
    block = "\n".join(
        f"- `{t}` (D{evidence.TOPIC_DISTANCE[t]}): {TOPIC_SOURCES.get(t, '')}"
        for t in topics)
    return PROMPT_TEMPLATE.format(topic_block=block, topic=topics[0],
                                  stamp=stamp, count=count)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _balanced_arrays(raw):
    """문자열 안의 균형 잡힌 [...] 블록을 뒤에서부터 순서대로 내놓는다.

    Aside는 JSON 앞에 진행 로그('[tool] openTab ...')를 흘리므로,
    단순 find('[')~rfind(']') 슬라이스는 로그의 대괄호를 시작점으로 잡아
    파싱이 깨진다. 실제 배열 후보만 골라내야 한다.
    (2026-08-28 수집 1·2회차 연속 실패 원인)
    """
    starts = []
    spans = []
    in_str = False
    esc = False
    for i, ch in enumerate(raw):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "[":
            starts.append(i)
        elif ch == "]" and starts:
            spans.append((starts.pop(), i + 1))
    # 바깥쪽·뒤쪽 블록부터 시도 (본문 JSON은 대개 마지막에 온다)
    spans.sort(key=lambda s: (s[0] - s[1], -s[0]))
    return [raw[a:b] for a, b in spans]


def _extract_json(raw):
    """Aside 출력에서 증거 배열을 건져낸다.

    코드펜스·진행 로그·ANSI 색상코드가 섞여도 견딘다. 배열이 여럿이면
    '증거 레코드처럼 생긴 것'(dict 원소 + topic/claim 보유)을 우선한다.
    """
    if not raw:
        return []
    raw = ANSI_RE.sub("", raw)

    candidates = []
    fenced = re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    candidates.extend(fenced)
    candidates.extend(_balanced_arrays(raw))

    fallback = None
    for blob in candidates:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list) or not data:
            continue
        dicts = [d for d in data if isinstance(d, dict)]
        if dicts and any(("claim" in d or "topic" in d) for d in dicts):
            return data
        if fallback is None:
            fallback = data
    return fallback or []


def run_aside(prompt, account="u0", timeout=ASIDE_TIMEOUT):
    """Aside CLI 호출. 브라우저 작업은 전부 Aside를 경유한다(사용자 표준)."""
    proc = subprocess.run(
        ["aside", "--account", account, "exec", prompt],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"aside exit={proc.returncode}: {proc.stderr[-500:]}")
    return proc.stdout


def harvest_once(cfg, topics=None, count=3, dry_run=False, account="u0"):
    """1배치 수집 → evidence.jsonl 적재. 승격은 run.py daily가 한다."""
    import time
    stamp = time.strftime("%Y%m%d%H%M")
    topics = topics or _stale_topics(cfg)
    if not topics:
        log("수집 대상 주제 없음")
        return {"harvested": 0, "topics": []}

    prompt = build_prompt(topics, count=count, stamp=stamp)
    if dry_run:
        print(prompt)
        return {"harvested": 0, "topics": topics, "dry_run": True}

    log(f"증거 수집 시작: {topics} (목표 {count}건)")
    raw = run_aside(prompt, account=account)
    items = _extract_json(raw)
    if not items:
        log("수집 실패: JSON 파싱 불가")
        return {"harvested": 0, "topics": topics, "parse_failed": True,
                "raw_tail": raw[-500:] if raw else ""}

    saved = 0
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        item.setdefault("evidence_id", f"ev-{stamp}-{i:02d}")
        evidence.record_evidence(cfg, item)
        saved += 1
    log(f"증거 수집 완료: {saved}건 적재 → evidence.jsonl")
    return {"harvested": saved, "topics": topics}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", action="append", help="수집할 topic (반복 가능)")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="프롬프트만 출력")
    ap.add_argument("--account", default="u0")
    ap.add_argument("--promote", action="store_true", help="수집 후 즉시 승격")
    args = ap.parse_args()

    cfg = load_config()
    try:
        res = harvest_once(cfg, topics=args.topic, count=args.count,
                           dry_run=args.dry_run, account=args.account)
    except Exception as e:
        record_error(cfg, "harvest.harvest_once", e)
        log(f"수집 오류: {e}")
        return 1
    print(json.dumps(res, ensure_ascii=False))
    if args.promote and res.get("harvested"):
        print(json.dumps(evidence.promote_pending(cfg), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
