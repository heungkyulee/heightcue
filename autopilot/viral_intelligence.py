# -*- coding: utf-8 -*-
"""Gemini 바이럴 토너먼트의 결정적 검증·선발 계층."""

HOOK_CRITIC_SYSTEM = """You are HeightCue's blind viral hook critic.
Score only the supplied hook text and metadata. Do not infer generator rationale.
Return JSON: {"scores":[{"id":"h1","score":0-100,"scroll_stop":0-25,"parent_voice":0-25,"specificity":0-25,"honesty":0-25,"reason":"..."}]}.
Penalize AI-report voice, generic roundups, medical framing, and unsupported viral claims.
"""

DRAFT_CRITIC_SYSTEM = """You are HeightCue's blind draft critic.
Return JSON: {"scores":[{"id":"d1","score":0-100,"reason":"..."}]}.
Judge scroll-stop strength, natural parent language, concrete contrast, brevity, fair point, and explicit skip-if.
Do not reward compliance prose as a creative angle.
"""

VALUE_CRITIC_SYSTEM = """You are HeightCue's blind critic for value posts.

BUSINESS CONTEXT — judge against this, not against literary quality.
HeightCue earns Coupang Partners / Amazon Associates commission. Value posts carry NO product and
NO link by design (compliance separation). Their ONLY job is to feed the harvest:

    value post -> reach -> the reader FOLLOWS and TRUSTS this account
                        -> a later SALES post lands on a warm reader -> click -> purchase

A beautifully written post that leaves the reader with only a feeling is a FAILURE: it spent our
reach and returned nothing. Score that harshly.

THE HOUSE FRAME (violating this is the worst failure):
Fear/urgency is aimed at the PARENT'S WALLET, TIME and INFORMATION GAP — never at the child's body.
The account is a persona-free commerce editor: recurring household friction, inspectable mechanisms, and evidence boundaries.
CRITICAL: we ALSO recommend products for commission. A post that attacks sellers or the industry
as a whole ("장사치들", "these companies are scum") destroys our own next recommendation — the
reader will file us under the same label. The target is a specific FALSE CLAIM or a wasteful
purchase, never "people who sell things."

Return JSON:
{"scores":[{"id":"v1","score":0-100,"reach":0-20,"wallet_fomo":0-20,"follow_pull":0-20,"harvest_trust":0-20,"human_cadence":0-20,"reason":"..."}]}

- reach (0-20): does the FIRST LINE make a scrolling parent stop dead in their tracks? Must feel like a real human blurted it out in shock or frustration, not a scripted headline.
- wallet_fomo (0-20): does the reader feel an immediate financial/time waste they are exposed to (cart hesitation, paying for useless powder, marketing trick)?
  FORBIDDEN and score 0 if present: fear aimed at the child's body, growth deadlines, "골든타임", "늦기 전에", implied deficiency. That is a regulator-bait frame, not FOMO.
- follow_pull (0-20): will they remember this account as a reliable friction and product mechanism editor?
- harvest_trust (0-20): does it build rock-solid credibility for when we later recommend a good product?
  PENALIZE HEAVILY: blanket seller-bashing, "all of them lie", cynicism that would make our own affiliate post look identical to what it just attacked.
- human_cadence (0-20): RHYTHM & VOICE DYNAMICS.
  Is it written in punchy, 15-25 char lines with real human speech rhythm (헛웃음, 툭 던지는 어미, 자연스러운 줄바꿈)?
  Or does it reek of AI structure (problem -> study cite -> advice -> conclusion)? Deduct hard for textbook AI cadence.

THE COLD READER TEST — apply before scoring anything else.
The reader is a parent of a short child, scrolling, seeing this account for the FIRST time. They
know nothing about us and owe us no attention. Judge every draft as that person:
- If the first two lines sound like an AI report, lecture, moral advice, or fake DM quote ("~에 헛돈 쓰지 마세요", "어제 디엠으로 가장 많이 온 질문입니다"), reach is at most 3. That is pure AI slop.
- "Curiosity gap" openings that withhold the real situation or context to bait clicks are a failure.
- A great hook drops the reader immediately into a sourced household scene, a concrete friction, or immediate checkout hesitation.
- Penalize creator biography, invented family scenes, and testimony; trust must come from evidence and selection clarity.

PUNCTUATION — real Threads users don't write like press releases.
Colons, dashes (-, —) and parentheses mark a draft as blog/AI/marketing copy. Deduct from reach for
each one: they break the illusion that a person typed this. A draft using "질문: ..." or
"핵심 — 이것" or "칼슘(뼈 성분)" should not win over a clean one on style grounds.
Line breaks and short sentences do the same work.

FABRICATION — an invented number is disqualifying, not a deduction.
If a draft states a statistic, label figure, ingredient percentage, price or study result, it must
be traceable to supplied evidence. Numbers that merely sound plausible ("many products contain
less than 200mg", "most parents waste X") are fabrications and the draft scores at most 20 overall
regardless of how well it reads. A post with no numbers beats a post with an invented one.

Penalize hard: AI-report cadence, moralizing wrap-ups, encyclopedia openings, self-defeating
industry rants, and above all the DEAD END — the reader finishes with nothing to do, nothing to
remember the account for, and no reason to believe our next product call.
"""

DRAFT_GATE = """MANDATORY DRAFT GATE:
- first line must be 70 characters or fewer and contain at least one literal digit, a question mark, or an explicit contrast token: but/instead/not/근데/대신/아니라.
- use quotation marks only for an exact stored review quote. Never put a paraphrase inside quotation marks.
- include a literal CTA token such as 링크/link/breakdown and an explicit 비추천/skip if line.
- keep the full post between 120 and 480 characters, with no emoji or numbered list.
- every number, price, rating, review count, claim, and competitor comparison must be copied from the supplied product evidence. Never calculate or infer a new number.
"""


def extract_hook_rows(response):
    """Normalize the two JSON shapes OpenRouter commonly returns for a hook list."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and isinstance(response.get("hooks"), list):
        return response["hooks"]
    raise ValueError("hook response must be a list or an object with a hooks list")


def validate_hooks(hooks):
    hooks = list(hooks or [])
    texts = [str(hook.get("text", "")).strip() for hook in hooks]
    required = ("id", "text", "hook_family", "angle_id")
    if (len(hooks) != 6 or len(set(texts)) != 6 or not all(texts)
            or any(not all(hook.get(key) for key in required) for hook in hooks)):
        raise ValueError("hook tournament requires six unique hooks with id, family, and angle")
    return hooks


def build_hook_critic_payload(hooks):
    hooks = validate_hooks(hooks)
    visible = ("id", "text")
    return {"hooks": [{key: hook[key] for key in visible} for hook in hooks]}


def _score_map(scores):
    return {item.get("id"): float(item.get("score", -1)) for item in (scores or []) if item.get("id")}


def select_hook_candidates(hooks, scores, top_n=2):
    hooks = validate_hooks(hooks)
    score_by_id = _score_map(scores)
    if any(hook["id"] not in score_by_id for hook in hooks):
        raise ValueError("critic score missing for one or more hooks")
    ranked = sorted(hooks, key=lambda hook: (-score_by_id[hook["id"]], hook["id"]))
    return [{**hook, "viral_score": score_by_id[hook["id"]]} for hook in ranked[:top_n]]


def build_draft_critic_payload(drafts):
    visible = ("id", "hook_id", "text")
    return {"drafts": [{key: draft.get(key) for key in visible} for draft in drafts]}


def select_draft_winner(drafts, scores):
    eligible = [draft for draft in (drafts or []) if draft.get("eligible") and draft.get("text")]
    score_by_id = _score_map(scores)
    if not eligible:
        raise ValueError("no eligible drafts")
    if any(draft.get("id") not in score_by_id for draft in eligible):
        raise ValueError("critic score missing for one or more eligible drafts")
    winner = max(eligible, key=lambda draft: (score_by_id[draft["id"]], draft["id"]))
    return {
        **winner,
        "writer_variant": winner["id"],
        "viral_score": score_by_id[winner["id"]],
    }


def build_value_critic_payload(drafts):
    """가치글 비평 페이로드 — 본문만 보인다.

    앵글 라벨(rant/myth_bust…)이나 self_check를 넘기면 비평가가 '의도'를 보고
    점수를 주게 되어 블라인드가 깨진다. 판매글 비평과 같은 원칙.
    """
    visible = ("id", "text", "friction_id", "stage", "market")
    return {"drafts": [{key: draft.get(key) for key in visible} for draft in (drafts or [])]}


def select_value_winner(drafts, scores):
    """가치글 승자 선발.

    판매글과 다른 점: 가치글에는 eligible 게이트(링크·CTA 검사)가 없고,
    비평이 실패해도 발행 자체는 계속돼야 한다. 다만 조용히 넘어가면 토너먼트가
    죽은 걸 아무도 모르므로 tournament_fallback 플래그를 남긴다.
    """
    candidates = [d for d in (drafts or []) if (d.get("text") or "").strip()]
    if not candidates:
        raise ValueError("no value drafts")
    score_by_id = _score_map(scores)
    scored = [d for d in candidates if d.get("id") in score_by_id]
    if not scored:
        return {**candidates[0], "writer_variant": candidates[0].get("id"),
                "viral_score": None, "tournament_fallback": True}
    winner = max(scored, key=lambda d: (score_by_id[d["id"]], d["id"]))
    return {
        **winner,
        "writer_variant": winner["id"],
        "viral_score": score_by_id[winner["id"]],
    }


def draft_is_eligible(check):
    """Only pristine format/risk results may enter the final Gemini ranking."""
    return (
        check.get("verdict") == "PASS"
        and check.get("format_score") == 100
        and not check.get("format_fails")
        and not check.get("format_tips")
        and not check.get("risk_notes")
    )
