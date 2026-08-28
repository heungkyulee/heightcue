# -*- coding: utf-8 -*-
"""파이프라인 A-2/A-3 + V1 + A5: LLM 호출 계층 (OpenRouter 경유, 모델은 Gemini 계열 — SSOT 원칙).

- system 프롬프트는 load_skill이 context/(compliance·persona·voice) + gemini-skills.md 스킬 본문을 합성 (v2.2, SSOT 동기화).
- 기본은 JSON 응답 강제, 파싱 실패 시 1회 재시도.
- dry_run이면 LLM 없이 결정적(canned) 출력으로 파이프라인을 관통시킨다.
"""
import json
import re

import requests

from common import load_skill, log
import viral_intelligence

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def llm_call(cfg, system_prompt, user_content, json_mode=True, temperature=0.7, retry=1):
    """OpenRouter chat completions 호출. json_mode면 dict, 아니면 텍스트를 반환."""
    if not isinstance(user_content, str):
        user_content = json.dumps(user_content, ensure_ascii=False)
    body = {
        "model": cfg["openrouter"]["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(
        OPENROUTER_URL, json=body, timeout=90,
        headers={"Authorization": f"Bearer {cfg['openrouter']['api_key']}",
                 "HTTP-Referer": "https://heightcue.local", "X-Title": "heightcue-autopilot"},
    )
    r.raise_for_status()
    payload = r.json()
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        if retry > 0:
            return llm_call(cfg, system_prompt, user_content, json_mode, temperature, retry - 1)
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else "missing choices"
        raise ValueError(f"OpenRouter 응답 오류: {message}")
    if text is None:
        # 모델이 빈 응답을 주는 케이스(필터·리즌닝 오류) — 1회 재시도 후 실패 처리
        if retry > 0:
            return llm_call(cfg, system_prompt, user_content, json_mode, temperature, retry - 1)
        raise ValueError("LLM 응답 content가 None")
    if not json_mode:
        return text
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        if retry > 0:
            return llm_call(cfg, system_prompt, user_content, json_mode, temperature, retry - 1)
        raise


def _gemini(cfg, system_prompt, user_payload, retry=1):
    """(하위 호환) 기존 호출부가 쓰는 이름 — OpenRouter로 위임."""
    return llm_call(cfg, system_prompt, user_payload, json_mode=True, retry=retry)


# ── 판매글 체인: A2 → A3-KR ─────────────────────────────────────────────────

def make_master(cfg, product, playbook_hint="", dry_run=False):
    if dry_run:
        return {
            "hooks": [
                "'하루 300개 미션'으로 소파에서 아이를 떼어냈다는 집들의 공통 아이템 🤸",
                "층간소음 걱정 없이 집에서 하는 점프 운동, 조합이 따로 있습니다",
                "실내 줄넘기 리뷰 4천 개를 훑고 알게 된 것",
            ],
            "competitor_pain_points": ["줄이 있는 줄넘기는 층간소음 불만이 도배", "조립이 복잡한 제품 후회 리뷰 다수"],
            "verified_points": ["무소음 볼 방식이라 줄이 없음", "층간소음 매트 포함 옵션", "실내 전용 설계"],
            "review_summary": "리뷰 4천여 건에서 \"넷플릭스 틀어놓고 하루 300개\" 하는 집이 많다는 얘기가 반복됨",
            "usage_caveat": "매트 포함 여부는 옵션에서 확인",
            "risk_notes": [], "missing_data": [],
        }
    payload = dict(product)
    if playbook_hint:
        payload["playbook_hint"] = playbook_hint
    result = _gemini(cfg, load_skill(cfg, "A2", country=product.get("country", "KR")), payload)
    # 방어: LLM이 JSON 배열로 응답하면 dict를 기대하는 후속 코드가 죽는다 (2026-08-27 02:10 kr_sales 크래시)
    if isinstance(result, list):
        result = next((x for x in result if isinstance(x, dict)), {})
    return result


def generate_hooks(cfg, master, product, playbook_hint="", attempts=3):
    """Accumulate six unique, evidence-safe hooks with bounded semantic retries."""
    import post_check

    last_error = None
    safe_hooks = []
    seen_texts = set()
    country = str(product.get("country", "KR")).upper()
    for _ in range(attempts):
        response = _gemini(cfg, """You are HeightCue's Gemini hook generator.
Return JSON with exactly six unique hooks. Each hook needs id h1-h6, text, hook_family F1-F4, angle_id, and rationale.
Use concrete parent friction or an honest reversal grounded only in the supplied product evidence.
If compared_candidates/rejected_candidates are absent, never mention another form, brand, serving pattern, ingredient pattern, or competitor.
No medical framing, generic roundup, AI-report voice, fabricated review quote, or unsupported viral claim.
""", {"master_note": master, "product": product, "playbook_hint": playbook_hint})
        try:
            bundle = viral_intelligence.validate_hooks(
                viral_intelligence.extract_hook_rows(response)
            )
        except ValueError as exc:
            last_error = exc
            continue
        for hook in bundle:
            text_key = str(hook["text"]).strip().casefold()
            if text_key in seen_texts:
                continue
            if post_check.evidence_boundary_notes(hook["text"], country, product):
                continue
            seen_texts.add(text_key)
            safe_hooks.append(hook)
        if len(safe_hooks) >= 6:
            selected = [{**hook, "id": f"h{index}"} for index, hook in enumerate(safe_hooks[:6], 1)]
            return viral_intelligence.validate_hooks(selected)
        last_error = ValueError("hook bundle exceeds supplied product evidence")
    raise last_error or ValueError("hook tournament generation failed")


def make_sales_post(cfg, master, product, playbook_hint="", dry_run=False):
    if dry_run:
        if product.get("country") == "US":
            facts = product.get("spec_facts") or []
            label_line = facts[0] if facts else "Read the exact product label"
            if len(facts) > 1:
                label_line += f"; the other listed ingredient is {facts[1]}"
            skip_line = ("Skip if: the exact label or fractionated coconut oil does not fit your household."
                         if len(facts) > 1 else "Skip if: the exact label does not fit your household.")
            text = (
                "2 label facts—not vitamin hype. #ad\n\n"
                "I stopped growing at 5'6, so I read the label before the pitch.\n\n"
                f"{label_line}.\n\n"
                f"{skip_line}\n\n"
                f"Full breakdown and where to buy: {product['link']}"
            )
        else:
            text = (
                f"{master['hooks'][0].replace(' 🤸', '')}\n"
                "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.\n\n"
                "키 작았던 사람이라 애들 물건은 스펙부터 뜯어봅니다.\n\n"
                f"{master['verified_points'][0]}. {master['verified_points'][1]}.\n\n"
                f"{master['review_summary']}.\n\n"
                "비추천: 이미 집에서 잘 쓰는 운동 도구가 있는 집. 또 살 이유 없습니다.\n"
                f"{master['usage_caveat']}. 자세한 구성은 링크에서 확인하세요.\n"
                f"{product['link']}"
            )
        return {"text": text, "char_count": len(text), "self_check": {}}
    country = product.get("country", "KR")
    skill = "A3-KR" if country == "KR" else "A3-US"
    system = load_skill(cfg, skill, country=country) + "\n\n" + viral_intelligence.DRAFT_GATE
    import post_check
    from common import recent_context
    ctx = recent_context(cfg, country)
    log(f"컨텍스트({country}/판매글): 글 {len(ctx['recent_posts'])}건, 내 답글 {len(ctx['my_recent_replies'])}건, 받은 댓글 {len(ctx['recent_comments_received'])}건, 바이오 {'O' if ctx['my_bio'] else 'X'}")

    hooks = generate_hooks(cfg, master, product, playbook_hint)
    hook_scores = _gemini(
        cfg,
        viral_intelligence.HOOK_CRITIC_SYSTEM,
        viral_intelligence.build_hook_critic_payload(hooks),
    )
    ranked_hooks = viral_intelligence.select_hook_candidates(
        hooks, (hook_scores or {}).get("scores"), top_n=6
    )

    drafts = []
    for index, hook in enumerate(ranked_hooks, 1):
        payload = {
            "master_note": {**master, "preferred_hook": hook["text"]},
            "product_evidence": product,
            "evidence_contract": (
                "Use only literal facts present in product_evidence; facts absent from product_evidence are forbidden. "
                "When compared_candidates/rejected_candidates are absent, do not mention other formats, brands, "
                "servings, ingredients, measuring tools, filler lists, intake sufficiency, or competitor behavior. "
                "For Ddrops, skip if must mention the exact label or fractionated coconut oil."
            ),
            "hook_metadata": {key: hook[key] for key in ("id", "hook_family", "angle_id")},
            "link": product.get("link"), "playbook_hint": playbook_hint,
            "link_mode": product.get("link_mode", "direct" if country == "KR" else "site"),
            "ad_mode": product.get("ad_mode", "on"),
            **ctx,
        }
        candidate = _gemini(cfg, system, payload)
        if not isinstance(candidate, dict) or not candidate.get("text"):
            continue
        check = post_check.check_post({
            "country": country, "post_type": "sales",
            "text": candidate["text"], "product": product,
        })
        eligible = viral_intelligence.draft_is_eligible(check)
        draft = {
            **candidate,
            "id": f"d{index}",
            "hook_id": hook["id"],
            "hook_family": hook["hook_family"],
            "angle_id": hook["angle_id"],
            "eligible": eligible,
            "format_score": check.get("format_score", 0),
        }
        drafts.append(draft)
        log(f"  본문 {index}: 포맷 {draft['format_score']}점, eligible={eligible}, 훅='{hook['text'][:40]}'")
        if sum(1 for item in drafts if item.get("eligible")) >= 2:
            break

    draft_scores = _gemini(
        cfg,
        viral_intelligence.DRAFT_CRITIC_SYSTEM,
        viral_intelligence.build_draft_critic_payload(drafts),
    )
    winner = viral_intelligence.select_draft_winner(drafts, (draft_scores or {}).get("scores"))
    winner.update({
        "product_id": product.get("product_key"),
        "formfactor_id": product.get("formfactor_id"),
        "ux_grade": product.get("ux_grade"),
        "critic_model": cfg["openrouter"].get("critic_model", cfg["openrouter"]["model"]),
    })
    return winner


# ── 가치글 (V1) ─────────────────────────────────────────────────────────────

def make_value_post(cfg, kind, episode=None, topic=None, recent=None, dry_run=False, country="KR", angle_override=None):
    import random
    
    angles = ["rant", "shower_thought", "raw_memory", "myth_bust", "community_qa"]
    selected_angle = angle_override if angle_override else random.choice(angles)

    if dry_run:
        if country == "US":
            text = (
                f"[Angle: {selected_angle}] Doesn't matter how many miracle pills you buy. I'm 5'6 and fact-checking these labels so you don't waste your money."
            )
        else:
            text = (
                f"[{selected_angle} 앵글] 기적의 영양제요? 167cm에서 멈춘 제가 장담하는데 그런 거 없습니다. 지갑 털리지 마세요."
            )
        return {"text": text, "kind": "story", "angle_used": selected_angle,
                "self_check": {"링크_제품_없음": True, "각색_없음": True, "480자_이내": True, "신파_없음": True}}
    from common import recent_context
    ctx = recent_context(cfg, country)
    log(f"컨텍스트({country}/가치글): 글 {len(ctx['recent_posts'])}건, ACP 텐션: {ctx['account_memory'].get('current_tension', 'none')}")
    payload = {"kind": kind, "country": country,
               "language_requirement": "English only; no Korean characters" if country == "US" else "Korean only",
               "angle": selected_angle,
               "topic": topic, **ctx}
    return _gemini(cfg, load_skill(cfg, "V1", country=country), payload)


def make_value_thread(cfg, topic, parts=3, recent=None, dry_run=False, country="KR"):
    """가치글 타래 생성 (V2). 반환: {"parts": [본문, ...], ...}

    증거 원자 1건에는 사실·반론·실행이 함께 들어있어 단편보다 타래에 맞다.
    각 편은 run.py에서 개별로 검사기를 통과해야 발행된다.
    """
    parts = max(2, min(4, int(parts)))
    if dry_run:
        if country == "US":
            sample = ["Melatonin gummies aren't the fix.",
                      "Deep sleep total matters more than the clock on the wall.",
                      "That said, genetics is still the biggest factor. No routine changes that."]
        else:
            sample = ["10시 취침 강박, 내려놓으세요.",
                      "시계 바늘보다 깊은 잠을 얼마나 자느냐가 더 관련이 큽니다.",
                      "물론 최종 키는 유전이 가장 큰 변수입니다. 이건 어떤 습관으로도 안 바뀝니다."]
        return {"parts": sample[:parts],
                "self_check": {"각편_480자_이내": True, "링크_제품_없음": True,
                               "반론_포함": True, "AI결론_없음": True, "1편_결론_선공개": True}}

    from common import recent_context
    ctx = recent_context(cfg, country)
    log(f"컨텍스트({country}/타래 {parts}편): 글 {len(ctx['recent_posts'])}건")
    payload = {"country": country, "topic": topic, "parts": parts,
               "language_requirement": "English only; no Korean characters"
               if country == "US" else "Korean only", **ctx}
    return _gemini(cfg, load_skill(cfg, "V2", country=country), payload)


# ── 댓글 답글 (A5) ──────────────────────────────────────────────────────────

def make_reply(cfg, comment, post_summary, post_type, story_facts, dry_run=False, country="KR",
               thread_context=None, is_nested=False):
    """댓글/대댓글 답글 생성.

    thread_context: 원글→...→직전 댓글까지의 대화 체인
        [{"speaker": "me|them", "username": str, "text": str}, ...]
        대댓글("그럼 몇 개월이요?")은 단독으로 의미가 안 서므로 이 체인이 필수다.
    """
    if dry_run:
        if country == "US":
            return {"category": "empathy", "action": "reply",
                    "text": "Thanks for reading. Glad this was useful.",
                    "reason": "dry-run canned"}
        return {"category": "empathy", "action": "reply",
                "text": "읽어주셔서 감사해요. 같은 마음인 분들이 계셔서 이 계정 할 맛이 납니다.",
                "reason": "dry-run canned"}
    payload = {"comment": comment, "post_summary": post_summary,
               "post_type": post_type, "story_bank_facts": story_facts,
               "thread_context": thread_context or [], "is_nested": bool(is_nested)}
    return _gemini(cfg, load_skill(cfg, "A5", country=country), payload)
