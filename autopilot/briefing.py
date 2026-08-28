#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""콘텐츠 브리프 — 증거 원자를 사람이 읽는 소재 카드로 내보낸다.

`evidence.to_generation_topic()`이 봇(LLM)용 직렬화라면, 이 모듈은
**콘텐츠 팀(나영·유진·다은·서연)용** 렌더링이다. 같은 원자를 사람이
집어갈 수 있는 소재로 펼친다.

핵심 관점: 원자 1건 = 콘텐츠 1건이 아니다.
    원자 1건 × 채널 4개 × 앵글 6종 = 최대 24개 소재.
원자는 채널을 모르므로(멀티채널 설계) 어떤 포맷으로도 렌더링된다.

실행:
    python3 briefing.py                 # 전체 브리프 → state/content-brief.md
    python3 briefing.py --topic sleep   # 특정 주제만
    python3 briefing.py --gaps          # 재고 격차만(무엇을 더 모을지)
    python3 briefing.py --stdout        # 파일 대신 화면 출력
"""
import argparse
import sys
import time

import evidence
from common import load_config, log, state_path

# ── 앵글 어휘 — 봇(SKILL V1)과 팀이 같은 말을 쓰도록 맞춘 사전 ──────────────
# 실제 카드에 실리는 앵글은 _suggest_angles()가 원자별로 고른다.
ANGLE_NAMES = {
    "myth_bust": "통념 파괴", "rant": "팩트폭격", "community_qa": "질문 응답",
    "shower_thought": "단상", "raw_memory": "기억", "reassurance": "죄책감 해소",
}

# ── 채널별 포맷 사양 — 원자 하나가 어떤 그릇에 담기는가 ─────────────────────
CHANNELS = [
    ("threads_single", "Threads 단문", "480자 이내 1편",
     "결론 선공개. 훅 1행 20자 이내."),
    ("threads_thread", "Threads 타래", "각 편 480자 · 3~4편",
     "1편 결론 → 2편 근거 → 3편 반론 → 4편 실행. (자동화 가동 중)"),
    ("reels_script", "릴스/틱톡 대본", "20~30초 · 세로 9:16",
     "0~2초 훅 자막, 3~15초 근거, 15~25초 반론, 끝은 툭 끊기."),
    ("carousel", "카드뉴스", "5장 내외",
     "1장 훅, 2~3장 근거, 4장 반론, 5장 실행. 장당 문장 2개 이하."),
    ("yt_short", "유튜브 쇼츠", "45~60초",
     "릴스보다 설명 여유. 자막 필수, 얼굴 없이 텍스트+B롤."),
    ("blog", "블로그/사이트 글", "800~1200자",
     "SEO용. 출처 링크를 실제로 걸 수 있는 유일한 포맷."),
]

CONFIDENCE_GUIDE = {
    "strong": "메타분석·공식 가이드라인. **단정적으로 써도 되는 유일한 등급** "
              "(그래도 '연관이 보고된다' 어법 유지)",
    "moderate": "다수 관찰연구. '~와 연관이 있다고 보고됩니다' 수준까지만.",
    "weak": "단일·소규모 연구. **'이런 연구도 있다' 이상 나가지 말 것.** "
            "단독 주장 금지, 반론과 반드시 함께.",
}


def _fmt_sources(atom):
    out = []
    for s in atom.get("sources", []):
        bits = [s.get("type", "?")]
        if s.get("year"):
            bits.append(str(s["year"]))
        loc = s.get("doi") or s.get("url") or ""
        line = f"`{'/'.join(bits)}` {loc}"
        if s.get("excerpt"):
            line += f"\n    > {s['excerpt'][:200]}"
        out.append(line)
    return out


def _used_summary(atom):
    used = atom.get("used_in") or {}
    if not any(used.values()):
        return "아직 어느 채널에도 안 씀 — **전 채널 신규**"
    parts = []
    for ck, ids in sorted(used.items()):
        if ids:
            parts.append(f"{ck} {len(ids)}편")
    fresh = [c for c in ("threads_kr", "threads_us", "tiktok_kr",
                         "reels_kr", "shorts_kr") if not used.get(c)]
    s = "발행: " + ", ".join(parts)
    if fresh:
        s += f" / **아직 신선: {', '.join(fresh)}**"
    return s


def _live_links(atom):
    """이미 발행된 편의 실물 링크 — 팀이 톤을 눈으로 확인하는 용도."""
    used = atom.get("used_in") or {}
    out = []
    for ck, ids in sorted(used.items()):
        for mid in ids[:4]:
            out.append(f"  - `{ck}` https://www.threads.net/t/{mid} (`{mid}`)")
    return out


def _suggest_angles(atom):
    """이 원자에 실제로 맞는 앵글만 고른다.

    표를 카드마다 똑같이 찍으면 읽히지 않는다. 확신도·반론 성격·감정
    지점에 따라 어울리는 앵글이 달라지므로 그것만 남긴다.
    """
    conf = atom.get("confidence")
    counter = atom.get("counter_claim") or ""
    emotion = atom.get("parent_emotion") or ""
    dist = atom.get("distance", 0)
    picks = []

    # 반론이 본론을 뒤집는 형태면 통념 파괴가 가장 강하다
    if any(k in counter for k in ("바꾸지 않", "않았다", "약하", "불확실", "단정할 수 없")):
        picks.append(("myth_bust", "통념 파괴",
                      "반론 자체가 반전이다. '흔히 이렇게 믿는다' → "
                      "'같은 연구에서 이랬다'로 뒤집어라.",
                      "Threads 타래 · 릴스 · 카드뉴스"))
    # 죄책감·불안을 건드리는 감정 지점이면 해소 앵글
    if any(k in emotion for k in ("죄책감", "불안", "자책", "강박", "걱정")):
        picks.append(("reassurance", "죄책감 해소",
                      f"부모가 이미 하는 걱정({emotion[:24]}…)을 근거로 덜어준다. "
                      "우리 채널이 갈리는 지점.",
                      "Threads 타래 · 카드뉴스 · 블로그"))
    # 강한 근거는 단정적 팩트폭격이 가능
    if conf == "strong":
        picks.append(("rant", "팩트폭격",
                      "근거가 강하니 세게 나가도 된다. 단 분노 대상은 "
                      "'속은 부모'가 아니라 '불안으로 장사하는 쪽'.",
                      "Threads 단문 · 릴스"))
    # 약한 근거는 단정 금지 — 질문 형식이 안전하다
    if conf == "weak":
        picks.append(("community_qa", "질문 응답",
                      "근거가 약하므로 단정하지 말고 '이런 연구도 있다'로. "
                      "질문에 답하는 형식이 가장 안전하다.",
                      "Threads 단문 · 쇼츠"))
    # 거리가 먼 주제(훈육·마인드셋)는 서사·단상이 잘 붙는다
    if dist >= 2:
        picks.append(("raw_memory", "기억",
                      "제품과 먼 주제라 서사를 붙이기 좋다. 구체적 장면으로 "
                      "시작하고 교훈 없이 끝낸다.",
                      "Threads 단문 · 릴스"))
    if len(picks) < 3:
        picks.append(("shower_thought", "단상",
                      "100자 내외 초단문. 설명하지 말고 그냥 끝낸다.",
                      "Threads 단문"))
    return picks[:4]


def atom_card(atom, index=None):
    """원자 1건 → 소재 카드 (마크다운)."""
    d = atom.get("distance", 0)
    conf = atom.get("confidence", "?")
    head = f"### {index}. {atom['claim'][:60]}" if index else f"### {atom['claim'][:60]}"
    lines = [
        head, "",
        f"- **주제** `{atom['topic']}` (D{d}) · **확신도** `{conf}`",
        f"- **근거** {atom['claim']}",
        f"- **반드시 함께 말할 것** {atom['counter_claim']}",
    ]
    if atom.get("parent_emotion"):
        lines.append(f"- **부모의 감정 지점** {atom['parent_emotion']}")
    lines.append(f"- **확신도 취급법** {CONFIDENCE_GUIDE.get(conf, '?')}")
    lines.append(f"- **소진 상태** {_used_summary(atom)}")

    srcs = _fmt_sources(atom)
    if srcs:
        lines += ["- **출처**"] + [f"  - {s}" for s in srcs]

    seeds = atom.get("hook_seeds") or []
    if seeds:
        lines += ["", "**훅 씨앗** (그대로 쓰지 말고 변형할 것)"]
        lines += [f"- {s}" for s in seeds]

    links = _live_links(atom)
    if links:
        lines += ["", "**이미 나간 글** (톤 참고용)"] + links

    picks = _suggest_angles(atom)
    lines += ["", "**이 근거에 맞는 접근** (아무 앵글이나 되는 게 아니다)", "",
              "| 앵글 | 왜 이게 맞나 | 포맷 |", "|---|---|---|"]
    for key, name, why, fits in picks:
        lines.append(f"| **{name}** `{key}` | {why} | {fits} |")
    lines.append("")
    return lines


def gap_lines(cfg):
    """재고 격차 — 무엇을 더 모아야 하는가."""
    inv = evidence.inventory(cfg)
    store = evidence.atom_store(cfg)
    total = inv["total"] or 1
    covered = {a["topic"] for a in store["atoms"]}

    lines = ["## 재고 격차 — 다음에 모을 것", "",
             "| 거리 | 목표 비중 | 현재 | 상태 |", "|---|---|---|---|"]
    for dist, target in sorted(evidence.DISTANCE_MIX.items()):
        have = inv["by_distance"].get(dist, 0)
        cur = have / total
        gap = target - cur
        mark = "🔴 비어 있음" if have == 0 else ("⚠️ 부족" if gap > 0.1 else "✅ 충분")
        lines.append(f"| D{dist} | {target:.0%} | {have}건 ({cur:.0%}) | {mark} |")

    missing = [(evidence.TOPIC_DISTANCE[t], t) for t in evidence.TOPIC_DISTANCE
               if t not in covered and t != "operator_story"]
    if missing:
        lines += ["", "**아직 한 번도 안 다룬 주제**", ""]
        by_d = {}
        for d, t in sorted(missing):
            by_d.setdefault(d, []).append(f"`{t}`")
        for d in sorted(by_d):
            lines.append(f"- **D{d}**: {', '.join(by_d[d])}")
        lines += ["", "수집 명령: "
                  "`cd ~/heightcue-autopilot/autopilot && "
                  "../.venv/bin/python harvest.py --topic <주제> --count 2`"]
    return lines


def build(cfg, topic=None, gaps_only=False):
    store = evidence.atom_store(cfg)
    atoms = store["atoms"]
    if topic:
        atoms = [a for a in atoms if a.get("topic") == topic]
    # 거리 → 확신도 순으로 정렬 (판매 직결 + 근거 강한 것 먼저)
    conf_rank = {"strong": 0, "moderate": 1, "weak": 2}
    atoms.sort(key=lambda a: (a.get("distance", 0),
                              conf_rank.get(a.get("confidence"), 3)))

    out = [
        f"# HeightCue 콘텐츠 소재 브리프 — {time.strftime('%Y-%m-%d')}",
        "",
        "> 검증된 근거만 실립니다. 1차 출처(논문·공공기관) 없는 주장, 반론 없는 주장,",
        "> 인과 단정은 게이트에서 자동 반려되므로 여기 올라오지 않습니다.",
        "",
        "**쓰는 법**",
        "",
        "1. 아래 카드에서 근거를 고른다 (거리 D0~D3 섞어서).",
        "2. 앵글 × 포맷을 골라 조합한다 — 같은 근거도 앵글이 다르면 다른 콘텐츠다.",
        "3. **반론은 생략 금지.** 그게 우리가 다른 계정과 갈리는 지점이다.",
        "4. 제품·링크·브랜드는 넣지 않는다 (가치글은 비상업 레이어).",
        "5. `~하면 큰다`, `○cm 더`, `보장` 같은 인과 단정은 검사기가 잡는다.",
        "",
    ]
    if not gaps_only:
        out += ["---", "", f"## 소재 카드 ({len(atoms)}건)", ""]
        if not atoms:
            out += ["_해당 조건의 원자가 없습니다. 아래 격차를 참고해 수집하세요._", ""]
        for i, a in enumerate(atoms, 1):
            out += atom_card(a, index=i) + ["---", ""]

    out += gap_lines(cfg)
    out += ["", "---", "",
            "## 포맷 사양 (참고)", "",
            "| 포맷 | 분량 | 구조 |", "|---|---|---|"]
    for key, name, size, how in CHANNELS:
        out.append(f"| **{name}** | {size} | {how} |")
    out += ["",
            "> Threads KR/US는 자동 발행 중(타래 포함). 릴스·틱톡·쇼츠·카드뉴스는",
            "> 아직 수동 — 같은 원자를 그대로 재활용하면 되고, 채널별 소진은",
            "> `insight_atoms.json`의 `used_in`이 따로 추적합니다.", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--gaps", action="store_true", help="재고 격차만")
    ap.add_argument("--stdout", action="store_true", help="파일 대신 화면 출력")
    ap.add_argument("--out", help="출력 경로 (기본 state/content-brief.md)")
    args = ap.parse_args()

    cfg = load_config()
    text = build(cfg, topic=args.topic, gaps_only=args.gaps)
    if args.stdout:
        print(text)
        return 0
    path = args.out or state_path(cfg, "content-brief.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    log(f"콘텐츠 브리프 생성: {path}")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
