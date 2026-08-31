#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-bound external reply generation."""

from __future__ import annotations

import hashlib
import json

import generation_worker
import journey_policy
import outreach


class ReplyGenerationError(RuntimeError):
    pass


_SOURCE_FIELDS = (
    "market", "source_post_id", "source_post_url", "source_author", "source_text", "source_published_at",
)


def generate(source: dict, category: str, cfg: dict, invoke=None) -> dict:
    missing = [field for field in _SOURCE_FIELDS if not source.get(field)]
    if missing:
        raise ReplyGenerationError("missing_source_fields:" + ",".join(missing))
    category = journey_policy.map_category(category)
    if not category:
        raise ReplyGenerationError("unsupported_friction_category")
    market = str(source["market"]).upper()
    if market not in {"KR", "US"}:
        raise ReplyGenerationError("unsupported_market")
    model = str((cfg.get("openrouter") or {}).get("model") or "")
    if model != "google/gemini-3.7-flash":
        raise ReplyGenerationError("reply_model_must_be_openrouter_gemini_3_7_flash")

    canonical_source = {field: source[field] for field in _SOURCE_FIELDS}
    source_digest = hashlib.sha256(
        json.dumps(canonical_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    options = journey_policy.GENERIC_REPLY_MECHANISMS[category]
    mechanism = options[int(source_digest[:8], 16) % len(options)]
    payload = {
        "source": canonical_source,
        "source_digest": source_digest,
        "mechanism": mechanism,
        "allowed_mechanisms": [mechanism],
        "market": market,
    }
    system = (
        "Write one useful Threads reply grounded only in the supplied source post and the single approved generic mechanism. "
        "Use the source language. Select one short exact source_anchor substring from source_text and use it naturally in reply_text. "
        "No brand, product, link, affiliate disclosure, profile instruction, medical advice, diagnosis, invented experience, or caregiver blame. "
        "Do not claim an outcome; explain only how the approved arrangement can reduce a repeated step. "
        "Return JSON only: source_digest, source_anchor, mechanism_id, reply_text."
    )
    if invoke is None:
        result = generation_worker._api_call(cfg, model, system, payload)
    else:
        result = invoke(cfg, system, payload)
    if not isinstance(result, dict):
        raise ReplyGenerationError("reply_output_not_object")
    if result.get("source_digest") != source_digest:
        raise ReplyGenerationError("source_digest_mismatch")
    if result.get("mechanism_id") != mechanism["id"]:
        raise ReplyGenerationError("mechanism_mismatch")
    anchor = str(result.get("source_anchor") or "").strip()
    reply_text = str(result.get("reply_text") or "").strip()
    if not anchor or anchor not in source["source_text"]:
        raise ReplyGenerationError("source_anchor_not_observed")
    if anchor not in reply_text:
        raise ReplyGenerationError("source_anchor_missing_from_reply")

    row = {
        **source,
        "market": market,
        "friction_id": source.get("friction_id") or f"outreach:{market.lower()}:{category}:{source_digest[:12]}",
        "friction_category": category,
        "mechanism_id": mechanism["id"],
        "source_digest": source_digest,
        "source_anchor": anchor,
        "reply_text": reply_text,
        "reply_model": model,
    }
    gate = outreach.validate_candidate(row)
    if not gate["eligible"]:
        raise ReplyGenerationError("reply_gate_failed:" + ",".join(gate["reasons"]))
    return row
