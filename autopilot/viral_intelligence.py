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
    visible = ("id", "text", "hook_family", "angle_id")
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


def draft_is_eligible(check):
    """Only pristine format/risk results may enter the final Gemini ranking."""
    return (
        check.get("verdict") == "PASS"
        and check.get("format_score") == 100
        and not check.get("format_fails")
        and not check.get("format_tips")
        and not check.get("risk_notes")
    )
