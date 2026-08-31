# -*- coding: utf-8 -*-
"""파이프라인 A-2/A-3 + V1 + A5: LLM 호출 계층 (OpenRouter 경유, 모델은 Gemini 계열 — SSOT 원칙).

- system 프롬프트는 load_skill이 context/(compliance·persona·voice) + gemini-skills.md 스킬 본문을 합성 (v2.2, SSOT 동기화).
- 기본은 JSON 응답 강제, 파싱 실패 시 1회 재시도.
- dry_run이면 LLM 없이 결정적(canned) 출력으로 파이프라인을 관통시킨다.
"""
import json
import math
import re

import requests

from common import load_skill, log
import execution_contract
import viral_intelligence

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

RETIRED_NARRATOR_PATTERNS = (
    r"167\s*cm", r"5\s*['’]\s*6", r"stopped growing", r"short uncle",
    r"제가 아이를 키워", r"우리 집 초\d", r"제가 .*썼", r"I used this",
)


def validate_friction_candidate(candidate):
    """Fail closed on identity, metadata, and stage-separation violations."""
    row = dict(candidate or {})
    missing = [key for key in ("text", "friction_id", "stage", "market", "source_pointers")
               if row.get(key) in (None, "", [])]
    if missing:
        raise ValueError("missing candidate fields: " + ",".join(missing))
    if row["stage"] not in {"discovery", "bridge", "verdict"}:
        raise ValueError("invalid stage")
    text = str(row["text"])
    if any(re.search(pattern, text, re.I) for pattern in RETIRED_NARRATOR_PATTERNS):
        raise ValueError("narrator biography or testimony forbidden")
    if row["stage"] in {"discovery", "bridge"}:
        if re.search(r"https?://|#ad|affiliate|쿠팡 파트너스|아마존 어소시에이트|\bbrand\b|\bproduct\b|제품", text, re.I):
            raise ValueError("non-commercial stage contains commercial coupling")
    if row["stage"] == "verdict":
        required = ("mechanism", "failure_mode", "skip_if", "attributable_route")
        absent = [key for key in required if not row.get(key)]
        if absent:
            raise ValueError("missing verdict fields: " + ",".join(absent))
    return row


def _contract(cfg, task, country, input_ids=None):
    contract_cfg = cfg
    if not _authoritative_mode(cfg):
        contract_cfg = dict(cfg)
        contract_cfg["_testing"] = True
    packet = execution_contract.build_context(contract_cfg, task, country)
    return packet, execution_contract.generation_provenance(packet, input_ids=input_ids)


def _validated_critic_scores(candidates, scores):
    expected = [str(item.get("id") or "") for item in candidates]
    if not expected or any(not item for item in expected) or len(set(expected)) != len(expected):
        return None
    if not isinstance(scores, list) or len(scores) != len(expected):
        return None
    parsed = {}
    for row in scores:
        if not isinstance(row, dict):
            return None
        candidate_id, score = row.get("id"), row.get("score")
        if (not isinstance(candidate_id, str) or candidate_id in parsed
                or candidate_id not in expected or isinstance(score, bool)
                or not isinstance(score, (int, float)) or not math.isfinite(score)):
            return None
        parsed[candidate_id] = float(score)
    return parsed if set(parsed) == set(expected) else None


def _critic_winner_agrees(winner, score_map):
    if not score_map or str(winner.get("id") or "") not in score_map:
        return False
    expected_id = max(score_map, key=lambda item: (score_map[item], item))
    return str(winner.get("id")) == expected_id


def llm_call(cfg, system_prompt, user_content, json_mode=True, temperature=0.7, retry=1,
             model=None):
    """OpenRouter chat completions 호출. json_mode면 dict, 아니면 텍스트를 반환."""
    if not isinstance(user_content, str):
        user_content = json.dumps(user_content, ensure_ascii=False)
    body = {
        "model": model or cfg["openrouter"]["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg['openrouter']['api_key']}",
               "Content-Type": "application/json",
               "HTTP-Referer": "https://heightcue.local", "X-Title": "heightcue-autopilot"}
    try:
        response = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=90)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content) if json_mode else content
    except Exception:
        if retry > 0:
            return llm_call(cfg, system_prompt, user_content, json_mode, temperature,
                            retry - 1, model=model)
        raise


def _gemini(cfg, system_prompt, user_payload, retry=1, model=None):
    """(하위 호환) 기존 호출부가 쓰는 이름 — OpenRouter로 위임."""
    return llm_call(cfg, system_prompt, user_payload, json_mode=True, retry=retry, model=model)


def _authoritative_mode(cfg):
    """Route only a fully configured live runtime into the authority service."""
    return cfg.get("_testing") is not True and bool(
        str((cfg.get("openrouter") or {}).get("api_key") or "").strip()
    )


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
    if _authoritative_mode(cfg):
        return execution_contract.request_authoritative_generation(
            "sales_master", product.get("country", "KR"),
            [f"product:{product.get('product_key')}"],
            rehearsal=bool((cfg.get("mode") or {}).get("_rehearsal")))
    contract, provenance = _contract(
        cfg, "sales_master", product.get("country", "KR"),
        input_ids=[f"product:{product.get('product_key')}"])
    payload = {**dict(product), "execution_contract": contract}
    if playbook_hint:
        payload["playbook_hint"] = playbook_hint
    result = _gemini(cfg, load_skill(cfg, "A2", country=product.get("country", "KR")), payload)
    # 방어: LLM이 JSON 배열로 응답하면 dict를 기대하는 후속 코드가 죽는다 (2026-08-27 02:10 kr_sales 크래시)
    if isinstance(result, list):
        result = next((x for x in result if isinstance(x, dict)), {})
    return execution_contract._bind_generated_result(result, provenance)


def generate_hooks(cfg, master, product, playbook_hint="", attempts=3):
    """Accumulate six unique, evidence-safe hooks with bounded semantic retries."""
    if _authoritative_mode(cfg):
        result = execution_contract.request_authoritative_generation(
            "sales_hooks", product.get("country", "KR"),
            [f"product:{product.get('product_key')}"],
            rehearsal=bool((cfg.get("mode") or {}).get("_rehearsal")))
        return result.get("hooks", result)
    import post_check

    last_error = None
    safe_hooks = []
    seen_texts = set()
    country = str(product.get("country", "KR")).upper()
    contract, _ = _contract(cfg, "sales_hooks", country,
                            input_ids=[f"product:{product.get('product_key')}"])
    for _ in range(attempts):
        response = _gemini(cfg, """You are HeightCue's Gemini hook generator.
Return JSON with exactly six unique hooks. Each hook needs id h1-h6, text, hook_family F1-F4, angle_id, and rationale.
Use concrete parent friction or an honest reversal grounded only in the supplied product evidence.
If compared_candidates/rejected_candidates are absent, never mention another form, brand, serving pattern, ingredient pattern, or competitor.
No medical framing, generic roundup, AI-report voice, fabricated review quote, or unsupported viral claim.
""", {"master_note": master, "product": product, "playbook_hint": playbook_hint,
       "execution_contract": contract})
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
                "I checked the exact label before the pitch.\n\n"
                f"{label_line}.\n\n"
                f"{skip_line}\n\n"
                f"Full breakdown and where to buy: {product['link']}"
            )
        else:
            text = (
                f"{master['hooks'][0].replace(' 🤸', '')}\n"
                "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.\n\n"
                "리뷰와 스펙 원문부터 확인했습니다.\n\n"
                f"{master['verified_points'][0]}. {master['verified_points'][1]}.\n\n"
                f"{master['review_summary']}.\n\n"
                "비추천: 이미 집에서 잘 쓰는 운동 도구가 있는 집. 또 살 이유 없습니다.\n"
                f"{master['usage_caveat']}. 자세한 구성은 링크에서 확인하세요.\n"
                f"{product['link']}"
            )
        return {"text": text, "char_count": len(text), "self_check": {}}
    country = product.get("country", "KR")
    if _authoritative_mode(cfg):
        return execution_contract.request_authoritative_generation(
            "sales_post", country, [f"product:{product.get('product_key')}"],
            rehearsal=bool((cfg.get("mode") or {}).get("_rehearsal")))
    contract, provenance = _contract(
        cfg, "sales_post", country,
        input_ids=[f"product:{product.get('product_key')}"])
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
        model=cfg["openrouter"].get("critic_model", cfg["openrouter"]["model"]),
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
            "execution_contract": contract,
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
        model=cfg["openrouter"].get("critic_model", cfg["openrouter"]["model"]),
    )
    winner = viral_intelligence.select_draft_winner(drafts, (draft_scores or {}).get("scores"))
    critic_scores = (draft_scores or {}).get("scores") if isinstance(draft_scores, dict) else None
    validated_scores = _validated_critic_scores(drafts, critic_scores)
    critic_status = "verified" if _critic_winner_agrees(winner, validated_scores) else "failed"
    critic_model = cfg["openrouter"].get("critic_model", cfg["openrouter"]["model"])
    winner.update({
        "product_id": product.get("product_key"),
        "formfactor_id": product.get("formfactor_id"),
        "ux_grade": product.get("ux_grade"),
        "critic_status": critic_status,
    })
    if critic_status == "verified":
        winner["critic_model"] = critic_model
    else:
        winner.pop("critic_model", None)
    return execution_contract._bind_generated_result(winner, provenance, critic_status, critic_model)


# ── 가치글 (V1) ─────────────────────────────────────────────────────────────

def make_value_post(cfg, kind, episode=None, topic=None, recent=None, dry_run=False, country="KR",
                    angle_override=None, candidates=None, input_ids=None):
    """가치글 생성 — 서로 다른 앵글로 후보 N개를 만들어 블라인드 비평으로 고른다.

    2026-08-29 이전에는 앵글 하나를 무작위로 뽑아 단일 호출로 끝냈다. 발행의 74%가
    가치글인데 필력 검증을 전혀 안 거쳐 "내용 구성과 필력이 영 별로"라는 평가를 받았다.
    판매글에만 있던 토너먼트를 가치글에도 붙인다.

    후보는 서로 다른 앵글을 쓴다 — 같은 앵글 3개는 비슷한 글 3개일 뿐이라
    토너먼트의 의미가 없다.
    """
    import random

    angles = ["scene", "mechanism", "bad_review", "before_after", "price_math"]

    source_pointers = list(input_ids or [])
    friction_id = next((item.split(":", 1)[1] for item in source_pointers
                        if str(item).startswith("friction:")), None)
    if not friction_id and source_pointers:
        friction_id = str(source_pointers[0]).split(":", 1)[-1]
    friction_id = friction_id or "unresolved-friction"

    if dry_run:
        selected_angle = angle_override if angle_override else random.choice(angles)
        if country == "US":
            text = f"[Angle: {selected_angle}] Bedtime cleanup takes twelve minutes because every bin opens from the top."
        else:
            text = f"[{selected_angle} 앵글] 장난감 정리만 12분. 위로 여는 수납함이 매번 일을 두 번 만듭니다."
        return {"text": text, "kind": "info", "angle_used": selected_angle,
                "friction_id": friction_id, "stage": "discovery", "market": country,
                "source_pointers": source_pointers,
                "self_check": {"링크_제품_없음": True, "각색_없음": True, "480자_이내": True, "신파_없음": True}}

    if _authoritative_mode(cfg):
        return execution_contract.request_authoritative_generation(
            "value_post", country, list(input_ids or []),
            rehearsal=bool((cfg.get("mode") or {}).get("_rehearsal")))
    contract, provenance = _contract(cfg, "value_post", country, input_ids=input_ids)
    from common import recent_context
    ctx = recent_context(cfg, country)
    log(f"컨텍스트({country}/가치글): 글 {len(ctx['recent_posts'])}건, ACP 텐션: {ctx['account_memory'].get('current_tension', 'none')}")

    # 2026-08-29: 가치글에는 사실 게이트가 아예 없었다. 판매글만 evidence_contract를
    # 걸고 있었고, 그 결과 Gemini가 "1,000mg 배합인데 실제 칼슘은 200mg도 안 된다"는
    # 검증 불가 수치를 지어내 승자로 뽑혔다(탄산칼슘 40%·구연산칼슘 21% — 어떤 원료로도
    # 나오지 않는 숫자). 컴플라이언스 6번(가짜 수치 창작) 위반이다.
    # 원장의 원자를 주고, 원장에 없는 수치는 금지한다.
    evidence_atoms = []
    try:
        import evidence as evidence_mod
        store = evidence_mod.atom_store(cfg) or {}
        evidence_atoms = store.get("atoms", []) if isinstance(store, dict) else list(store)
    except Exception as e:
        log(f"  증거 원장 로드 실패({type(e).__name__}) — 수치 없는 글만 허용")
    atom_claims = [a.get("claim") or a.get("text") for a in evidence_atoms if (a.get("claim") or a.get("text"))]

    evidence_contract = (
        "FACT GATE (violating this is worse than a boring post):\n"
        "- The `evidence_atoms` list is the ONLY source of research findings, statistics and "
        "numeric claims about nutrition, sleep, growth or product composition.\n"
        "- If a number, percentage, dosage, study result or 'X out of Y products' claim is not "
        "literally supported by evidence_atoms, DO NOT WRITE IT. No exceptions, however good the hook.\n"
        "- Never invent label figures, ingredient percentages, market prices of specific products, "
        "review counts, or 'many products are like this' surveys you did not receive.\n"
        "- Wallet-FOMO must come from a VERIFIABLE mechanism the reader can check themselves "
        "(how to read a label, what a term legally means, what to compare before buying), "
        "not from a fabricated statistic.\n"
        "- Writing with no numbers at all is perfectly acceptable and preferred over inventing one."
    )

    n = int(candidates or cfg.get("mode", {}).get("value_candidates", 3))
    n = max(1, min(5, n))
    if angle_override:
        pool = [angle_override] * n
    else:
        pool = random.sample(angles, min(n, len(angles)))
        while len(pool) < n:
            pool.append(random.choice(angles))

    system = load_skill(cfg, "V1", country=country)
    lang = "English only; no Korean characters" if country == "US" else "Korean only"

    # 2026-08-29: 바이럴 말빨/리듬 시드 동적 주입 (사람다운 호흡 강제)
    style_seeds = []
    try:
        from common import read_json, state_path
        seeds_data = read_json(state_path(cfg, "viral_style_seeds.json"), {})
        style_seeds = seeds_data.get("viral_speech_patterns", [])
    except Exception as e:
        log(f"  바이럴 스타일 시드 로드 실패({e})")

    drafts = []
    for index, angle in enumerate(pool, 1):
        payload = {"kind": kind, "country": country, "language_requirement": lang,
                   "angle": angle, "topic": topic,
                   "evidence_atoms": atom_claims,
                   "evidence_contract": evidence_contract,
                   "viral_speech_seeds": style_seeds,
                   "execution_contract": contract, **ctx}
        try:
            result = _gemini(cfg, system, payload)
        except Exception as e:  # 후보 하나가 죽어도 토너먼트는 계속된다
            log(f"  가치글 후보 {index}({angle}) 생성 실패: {type(e).__name__}")
            continue
        if not isinstance(result, dict) or not (result.get("text") or "").strip():
            continue
        drafts.append({**result, "id": f"v{index}", "angle_used": result.get("angle_used", angle),
                       "friction_id": friction_id, "stage": result.get("stage", "discovery"),
                       "market": country, "source_pointers": source_pointers})
        log(f"  후보 {index}: 앵글={angle}, {len(result['text'])}자")

    if not drafts:
        raise ValueError("가치글 후보를 하나도 만들지 못했다")
    if len(drafts) == 1:
        return execution_contract._bind_generated_result(
            {**drafts[0], "writer_variant": drafts[0]["id"], "viral_score": None,
             "tournament_fallback": True, "critic_status": "not_run"}, provenance,
            "not_run")

    scores = None
    try:
        verdict = _gemini(
            cfg, viral_intelligence.VALUE_CRITIC_SYSTEM,
            viral_intelligence.build_value_critic_payload(drafts),
            model=cfg["openrouter"].get("critic_model", cfg["openrouter"]["model"]))
        scores = (verdict or {}).get("scores")
    except Exception as e:
        log(f"  가치글 비평 실패({type(e).__name__}) — 첫 후보로 폴백")

    validated_scores = _validated_critic_scores(drafts, scores)
    winner = viral_intelligence.select_value_winner(drafts, scores if validated_scores else None)
    if winner.get("viral_score") is not None:
        log(f"  ▶ 승자: {winner['writer_variant']} ({winner['angle_used']}) "
            f"바이럴 {winner['viral_score']}점 / 후보 {len(drafts)}개")
    else:
        log(f"  ▶ 비평 없이 {winner['writer_variant']} 채택 (토너먼트 폴백)")
    critic_status = "verified" if _critic_winner_agrees(winner, validated_scores) else "failed"
    critic_model = cfg["openrouter"].get("critic_model", cfg["openrouter"]["model"])
    winner["critic_status"] = critic_status
    if critic_status == "verified":
        winner["critic_model"] = critic_model
    else:
        winner.pop("critic_model", None)
    return execution_contract._bind_generated_result(winner, provenance, critic_status, critic_model)


def make_value_thread(cfg, topic, parts=3, recent=None, dry_run=False, country="KR", input_ids=None):
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

    if _authoritative_mode(cfg):
        return execution_contract.request_authoritative_generation(
            "value_thread", country, list(input_ids or []),
            rehearsal=bool((cfg.get("mode") or {}).get("_rehearsal")))
    contract, provenance = _contract(cfg, "value_thread", country, input_ids=input_ids)
    from common import recent_context
    ctx = recent_context(cfg, country)
    log(f"컨텍스트({country}/타래 {parts}편): 글 {len(ctx['recent_posts'])}건")
    payload = {"country": country, "topic": topic, "parts": parts,
               "language_requirement": "English only; no Korean characters"
               if country == "US" else "Korean only",
               "execution_contract": contract, **ctx}
    return execution_contract._bind_generated_result(
        _gemini(cfg, load_skill(cfg, "V2", country=country), payload), provenance)


# ── 댓글 답글 (A5) ──────────────────────────────────────────────────────────

def make_reply(cfg, comment, post_summary, post_type, story_facts, dry_run=False, country="KR",
               thread_context=None, is_nested=False, input_ids=None):
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
    if _authoritative_mode(cfg):
        return execution_contract.request_authoritative_generation(
            "comment_reply", country, list(input_ids or []),
            rehearsal=bool((cfg.get("mode") or {}).get("_rehearsal")))
    contract, provenance = _contract(cfg, "comment_reply", country, input_ids=input_ids)
    payload = {"comment": comment, "post_summary": post_summary,
               "post_type": post_type, "story_bank_facts": story_facts,
               "thread_context": thread_context or [], "is_nested": bool(is_nested),
               "execution_contract": contract}
    return execution_contract._bind_generated_result(
        _gemini(cfg, load_skill(cfg, "A5", country=country), payload), provenance)
