# -*- coding: utf-8 -*-
"""가치글 바이럴 토너먼트 회귀.

배경(2026-08-29 발견): 토너먼트(viral_intelligence)는 판매글에만 배선돼 있었고
가치글 `make_value_post`는 단일 _gemini 호출이었다. 발행의 74%가 가치글인데
필력·어그로 비평을 한 번도 안 거치고 나갔다. 사용자 평가: "내용 구성과 필력이 영 별로".

여기서 지키는 것:
  1. 가치글도 후보 여러 개 → 블라인드 비평 → 승자 선발을 거친다
  2. 비평가는 생성자 근거를 못 본다(블라인드)
  3. 승자의 viral_score·writer_variant가 발행 meta까지 살아남는다
  4. 비평 실패 시 조용히 죽지 않고 첫 후보로 폴백한다

실행: ../.venv/bin/python test_value_tournament.py
"""
import os
import pathlib
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import generate  # noqa: E402
import viral_intelligence as vi  # noqa: E402

CFG = {
    "_testing": True,
    "openrouter": {"model": "google/gemini-3.7-flash",
                   "critic_model": "google/gemini-3.7-flash"},
    "llm": {"model": "google/gemini-3.7-flash"},
    "paths": {"skills": "x", "story_bank": "x"},
}

CTX = {"recent_posts": [], "my_recent_replies": [], "recent_comments_received": [],
       "my_bio": "", "account_memory": {"current_tension": "none"}}


def _fake_ctx(cfg, country, **kw):
    return dict(CTX)


def test_value_post_runs_tournament():
    """가치글도 후보 N개를 만들고 비평가가 고른다."""
    calls = []
    cfg = {**CFG, "openrouter": {**CFG["openrouter"], "critic_model": "google/critic-test"}}

    def fake_gemini(cfg, system, payload, **kwargs):
        calls.append({"system": system, "payload": payload, "kwargs": kwargs})
        if "critic" in system.lower():
            return {"scores": [{"id": "v1", "score": 40, "reason": "밋밋"},
                               {"id": "v2", "score": 88, "reason": "훅이 셈"},
                               {"id": "v3", "score": 61, "reason": "보통"}]}
        n = sum(1 for c in calls if "critic" not in c["system"].lower())
        return {"text": f"후보 {n} 본문입니다. 키 이야기.", "kind": "story",
                "angle_used": "rant", "self_check": {}}

    with patch.object(generate, "_gemini", side_effect=fake_gemini), \
         patch.object(generate, "load_skill", return_value="V1"), \
         patch("common.recent_context", _fake_ctx):
        out = generate.make_value_post(
            cfg, "story", topic="sleep", country="KR", input_ids=["friction:sleep"])

    writer_calls = [c for c in calls if "critic" not in c["system"].lower()]
    critic_calls = [c for c in calls if "critic" in c["system"].lower()]
    assert critic_calls[0]["kwargs"]["model"] == "google/critic-test"
    assert len(writer_calls) >= 3, f"후보가 {len(writer_calls)}개뿐 — 토너먼트가 아니다"
    assert len(critic_calls) == 1, f"비평 호출 {len(critic_calls)}회"
    assert out["text"] == "후보 2 본문입니다. 키 이야기.", out["text"]   # 88점 승자
    assert out["viral_score"] == 88, out
    assert out["writer_variant"] == "v2", out
    print(f"ok: 가치글 토너먼트 (후보 {len(writer_calls)} → 승자 {out['writer_variant']})")


def test_value_critic_is_blind():
    """비평가는 생성자의 근거·앵글 라벨을 보면 안 된다."""
    payload = vi.build_value_critic_payload([
        {"id": "v1", "text": "본문1", "angle_used": "rant",
         "self_check": {"x": True}, "kind": "story"},
        {"id": "v2", "text": "본문2", "angle_used": "myth_bust",
         "self_check": {"y": True}, "kind": "info"},
    ])
    blob = str(payload)
    for leak in ("rant", "myth_bust", "self_check", "kind"):
        assert leak not in blob, f"비평가에게 '{leak}'이 새어나감: {blob}"
    assert "본문1" in blob and "본문2" in blob
    print("ok: 비평 블라인드 (생성자 근거 차단)")


def test_value_winner_falls_back_when_critic_fails():
    """비평이 죽어도 발행은 계속돼야 한다 — 단 조용히 넘어가지 않는다."""
    drafts = [{"id": "v1", "text": "첫 후보"}, {"id": "v2", "text": "둘째"}]
    winner = vi.select_value_winner(drafts, None)
    assert winner["text"] == "첫 후보", winner
    assert winner.get("viral_score") is None, winner
    assert winner.get("tournament_fallback") is True, winner
    print("ok: 비평 실패 시 첫 후보 폴백 (플래그 기록)")


def test_value_winner_picks_highest_score():
    drafts = [{"id": "v1", "text": "a"}, {"id": "v2", "text": "b"}, {"id": "v3", "text": "c"}]
    w = vi.select_value_winner(drafts, [{"id": "v1", "score": 10},
                                        {"id": "v2", "score": 95},
                                        {"id": "v3", "score": 50}])
    assert w["text"] == "b" and w["viral_score"] == 95, w
    assert w["writer_variant"] == "v2"
    print("ok: 최고점 선발")


def test_empty_drafts_raises():
    try:
        vi.select_value_winner([], [{"id": "v1", "score": 90}])
    except ValueError:
        print("ok: 후보 0건은 명시적 에러")
        return
    raise AssertionError("후보가 없는데 통과됐다")


def test_critic_axes_are_revenue_shaped():
    """비평 축이 '수익 퍼널 기여 및 사람다운 말빨'을 재야 한다."""
    sys_prompt = vi.VALUE_CRITIC_SYSTEM
    for token in ("commission", "follow_pull", "harvest_trust", "human_cadence", "DEAD END"):
        assert token in sys_prompt, f"비평 프롬프트에 '{token}'이 없다"
    assert "parent_voice" not in sys_prompt, "문체 축이 아직 채점 기준으로 남아있다"
    assert "purchase" in sys_prompt and "sales post" in sys_prompt.lower()
    print("ok: 비평 축이 수익 퍼널 + 사람다운 말빨 기준")


def test_critic_enforces_house_frame():
    """컴플라이언스 제1원칙(지갑 FOMO)과 자기모순 금지가 채점에 박혀 있어야 한다.

    2026-08-29 사용자 지적 2건:
    ① "구매자에 대한 fomo 마케팅이 주된 거여야" — SSOT 제1원칙과 동일
       (공포는 아이 '몸'이 아니라 부모의 '지갑·시간·정보'에 건다)
    ② 우리도 제휴 수수료로 파는 쪽이라 판매자 전체를 매도하면 자기 발등을 찍는다.
    실제 Gemini 재채점: 판매자 매도 글 88점 -> 46점 ("자해 행위").
    """
    sys_prompt = vi.VALUE_CRITIC_SYSTEM
    # 지갑 FOMO가 주 엔진으로 명시
    assert "wallet_fomo" in sys_prompt
    assert "WALLET" in sys_prompt and "never at the child's body" in sys_prompt
    # 규제 적발 프레임은 0점 처리
    assert "골든타임" in sys_prompt and "score 0 if present" in sys_prompt
    # 자기모순(판매자 매도) 금지
    assert "we ALSO recommend products" in sys_prompt
    assert "seller-bashing" in sys_prompt
    print("ok: 지갑 FOMO + 자기모순 금지가 채점 기준에 박힘")


def test_generator_prompt_carries_friction_stage_contract():
    """생성 프롬프트가 persona-free 단계 분리와 source 계약을 가져야 한다."""
    skills = pathlib.Path(__file__).resolve().parent.parent / "heightcue-gemini-skills.md"
    text = skills.read_text(encoding="utf-8")
    v1 = text.split("SKILL V1")[1].split("SKILL V2")[0]
    for token in ("friction_id", "stage", "source_pointers", "discovery", "bridge"):
        assert token in v1
    assert "No creator-centered narrative" in v1


def test_dead_end_loses_to_funnel_post():
    """감상만 남기는 글은 퍼널 기여 글에 져야 한다 (점수 고정 시뮬레이션).

    실제 Gemini 판정(2026-08-29): 막다른 글 49점 / 퍼널 기여 글 90점.
    여기서는 선발 로직이 그 판정을 올바르게 반영하는지만 결정적으로 검증한다.
    """
    dead_end = {"id": "v1", "text": "밤 9시 59분, 숨죽이던 방. ... 피가 말랐는지 모르겠습니다."}
    funnel = {"id": "v2", "text": '"10시 전에 재우세요"라는 말, 어디서 나온 건지 아세요? ... 계속 뜯어보겠습니다.'}
    scores = [{"id": "v1", "score": 49, "follow_pull": 6, "harvest_authority": 7},
              {"id": "v2", "score": 90, "follow_pull": 23, "harvest_authority": 23}]
    w = vi.select_value_winner([dead_end, funnel], scores)
    assert w["id"] == "v2", w
    assert w["viral_score"] == 90
    print("ok: 막다른 글이 퍼널 기여 글에 패배")


def test_value_post_has_fact_gate():
    """가치글에도 사실 게이트가 걸려야 한다 (2026-08-29 실사고).

    판매글만 evidence_contract를 걸고 있었다. 그 결과 Gemini가
    "겉면 1,000mg 배합인데 뒷면 실제 칼슘은 200mg도 안 된다"는 수치를 지어내
    93점 승자로 뽑혔다. 화학적으로 불가능한 수치다 —
    탄산칼슘 40%, 인산칼슘 38%, 구연산칼슘 21%로 1000mg 원료의 칼슘은
    최소 210mg이다. 증거 원장 10건에 칼슘·함량 원자는 0건이었다.
    컴플라이언스 절대금지 6번(가짜 수치 창작) 위반.
    """
    captured = {}

    def fake_gemini(cfg, system, payload, **kwargs):
        if "critic" in system.lower():
            return {"scores": [{"id": "v1", "score": 80}]}
        captured.update(payload)
        return {"text": "본문입니다.", "kind": "info", "angle_used": "rant", "self_check": {}}

    with patch.object(generate, "_gemini", side_effect=fake_gemini), \
         patch.object(generate, "load_skill", return_value="V1"), \
         patch("common.recent_context", _fake_ctx):
        generate.make_value_post(
            CFG, "info", topic="영양제 함량", country="KR", candidates=1,
            input_ids=["friction:supplement-content"])

    assert "evidence_contract" in captured, "가치글에 사실 게이트가 없다"
    contract = captured["evidence_contract"]
    assert "FACT GATE" in contract
    assert "DO NOT WRITE IT" in contract
    assert "Never invent label figures" in contract
    # 원장이 유일한 출처임이 명시돼야 한다
    assert "evidence_atoms" in contract and "ONLY source" in contract
    # 원장 내용 자체도 전달돼야 한다 (계약만 주고 원자를 안 주면 쓸 게 없다)
    assert "evidence_atoms" in captured, "증거 원자 목록이 전달되지 않았다"
    assert isinstance(captured["evidence_atoms"], list)
    print("ok: 가치글에 사실 게이트 + 증거 원자 전달")


def test_critic_disqualifies_fabricated_numbers():
    """지어낸 수치는 감점이 아니라 실격이어야 한다."""
    sys_prompt = vi.VALUE_CRITIC_SYSTEM
    assert "FABRICATION" in sys_prompt
    assert "disqualifying" in sys_prompt
    assert "at most 20 overall" in sys_prompt, "실격 상한이 없다 — 잘 쓰면 통과해버린다"
    assert "no numbers beats a post with an invented one" in sys_prompt
    print("ok: 창작 수치 실격 규칙")


def test_critic_applies_cold_reader_and_punctuation():
    """콜드 리더 기준과 구두점이 채점에 반영돼야 한다 (2026-08-29 운영자 지적).

    "키 작은 아이를 키우는 학부모가 쓰레드에서 처음 본다면 바로 직관적으로
    이해하고 이 계정 콘텐츠는 신뢰할 수 있겠다고 느껴야" — 이게 유일한 시험이다.
    """
    sys_prompt = vi.VALUE_CRITIC_SYSTEM
    assert "COLD READER TEST" in sys_prompt
    assert "FIRST time" in sys_prompt
    assert "Curiosity gap" in sys_prompt, "떡밥형 훅을 기법으로 인정하면 안 된다"
    assert "PUNCTUATION" in sys_prompt
    assert "Colons, dashes" in sys_prompt and "parentheses" in sys_prompt
    print("ok: 콜드 리더 + 구두점 채점")


def test_punctuation_checker_catches_press_release_marks():
    """구두점 검사가 콜론·대시·괄호·천단위쉼표를 잡고, 고지문구·시각은 오탐하지 않는다."""
    import post_check
    bad = '어제 온 질문: "1,000mg 들었다는데요?"\n\n뒷면 표(성분표)를 보면 다릅니다 - 완전히요.'
    notes = post_check.punctuation_notes(bad)
    joined = " ".join(notes)
    assert "콜론" in joined and "괄호" in joined and "대시" in joined and "천단위" in joined, notes

    clean = "수면 영양제 사기 전에 30초만 봐주세요\n\n장바구니에 담으셨다면 잠깐요."
    assert post_check.punctuation_notes(clean) == [], post_check.punctuation_notes(clean)

    # 오탐 금지: 고지 불변 문구와 시각·URL
    disclosure = ("오늘 09:30에 정리했습니다. https://a.com/b?x=1\n"
                  "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.")
    assert post_check.punctuation_notes(disclosure) == [], post_check.punctuation_notes(disclosure)
    print("ok: 구두점 검사기 (오탐 없음)")


def test_thread_skill_has_commercial_separation_contract():
    skills = pathlib.Path(__file__).resolve().parent.parent / "heightcue-gemini-skills.md"
    v2 = skills.read_text(encoding="utf-8").split("SKILL V2")[1].split("SKILL A5")[0]
    for token in ("friction_id", "stage", "source_pointers", "Do not reply-chain"):
        assert token in v2
    assert "commercial verdict" in v2


def test_generator_forbids_press_release_punctuation():
    """말투 규칙에 구두점 금지가 있어야 한다 (공통 규칙이라 전 스킬 적용)."""
    voice = pathlib.Path(__file__).resolve().parent.parent / "context" / "voice-kr.md"
    text = voice.read_text(encoding="utf-8")
    assert "콜론(:)" in text and "괄호" in text and "대시" in text
    assert "타래로 넘겨라" in text or "타래로 빼야" in text, "부연은 타래로 빼라는 지시가 없다"
    print("ok: 말투 규칙에 구두점 금지 + 타래 유도")


def test_value_attribution_reaches_publication():
    """승자의 점수·앵글이 발행 meta까지 살아남아야 성과 귀속이 가능하다.

    2026-08-29 이전 발행 31건 전부 hook_family/viral_score가 None이었다 —
    토너먼트를 돌려도 결과를 안 실으면 무엇이 통했는지 영원히 알 수 없다.
    """
    import run

    captured = {}

    def fake_publish_text(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
        captured.update(meta or {})
        return "MID1"

    winner = {"text": "바닥에 작은 조각.\n가구 밑에도 하나.\n정리가 분류가 됩니다.\n이런 적 있나요?",
              "kind": "story", "angle_used": "myth_bust", "writer_variant": "v2",
              "viral_score": 91.0, "critic_model": "google/gemini-3.7-flash",
              "_provenance": {"contract_id": "heightcue-content-v1", "model": "m"}}

    cfg = {
        "mode": {"publish": True, "auto_publish_clean": True, "hold_flagged": False,
                 "value_thread_ratio": 0.0},
        "openrouter": {"model": "m", "critic_model": "m"},
        "paths": {"state_dir": "/tmp", "story_bank": "/tmp/sb.md"},
        "cadence": {},
    }

    signal = {"friction_id": "fr-1", "verbatim": "아래 통을 꺼낼 때 전부 내린다",
              "source_pointer": "source:1", "market": "KR"}
    winner.update({"friction_id": "fr-1", "stage": "discovery", "market": "KR",
                   "source_pointers": ["source:1"]})
    with patch.object(run.generate, "make_value_post", return_value=winner), \
         patch.object(run, "read_jsonl", return_value=[]), \
         patch("friction.pick_signal", return_value=signal), \
         patch.object(run.publish, "publish_text", side_effect=fake_publish_text), \
         patch.object(run, "append_jsonl"), \
         patch.object(run.post_check, "check_post",
                      return_value={"verdict": "PASS", "format_score": 95,
                                    "risk_notes": [], "format_tips": []}):
        run.make_and_publish_value(cfg, dry_run=False, country="KR")

    assert captured.get("viral_score") == 91.0, captured
    assert captured.get("writer_variant") == "v2", captured
    assert captured.get("angle_used") == "myth_bust", captured
    assert captured.get("critic_model"), captured
    assert captured["execution_contract"]["contract_id"] == "heightcue-content-v1", captured
    print("ok: 승자 귀속과 실행 계약이 발행 meta까지 전달")


def test_value_thread_contract_reaches_every_publication_part():
    import run

    captured = {}
    cfg = {"mode": {"auto_publish_clean": True}, "paths": {"state_dir": "/tmp"}}
    candidate = {"friction_id": "fr-1", "stage": "bridge", "market": "KR",
                 "source_pointers": ["source:1"]}
    def fake_publish(*args, **kwargs):
        captured.setdefault("rows", []).append(kwargs["meta"])
        return "MID"
    with patch.object(run.post_check, "check_post", return_value={"verdict": "PASS", "risk_notes": []}), \
         patch.object(run.publish, "publish_text", side_effect=fake_publish):
        assert run._publish_thread(cfg, ["첫 편 한국어", "둘째 편 한국어"], "KR",
                                   candidate=candidate)[1] == "published"
    assert all(row["friction_id"] == "fr-1" for row in captured["rows"])


if __name__ == "__main__":
    test_value_critic_is_blind()
    test_value_winner_falls_back_when_critic_fails()
    test_value_winner_picks_highest_score()
    test_empty_drafts_raises()
    test_value_post_runs_tournament()
    test_critic_axes_are_revenue_shaped()
    test_critic_enforces_house_frame()
    test_generator_prompt_carries_friction_stage_contract()
    test_dead_end_loses_to_funnel_post()
    test_value_post_has_fact_gate()
    test_critic_disqualifies_fabricated_numbers()
    test_critic_applies_cold_reader_and_punctuation()
    test_punctuation_checker_catches_press_release_marks()
    test_thread_skill_has_commercial_separation_contract()
    test_generator_forbids_press_release_punctuation()
    test_value_attribution_reaches_publication()
    print("\n가치글 토너먼트 회귀 16/16 통과")
