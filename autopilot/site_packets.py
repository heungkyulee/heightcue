#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adapters from product execution sources to immutable site packets."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


class SitePacketError(RuntimeError):
    pass


def _run_json(args, runner):
    result = runner(args, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SitePacketError(f"companyos_cli_failed:{result.returncode}")
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SitePacketError("companyos_cli_invalid_json") from exc


def load_companyos_packets(lifoli_root, runner=subprocess.run):
    script = Path(lifoli_root) / "scripts/heightcue-productctl.mjs"
    rows = _run_json(["node", str(script), "list"], runner)
    packets = []
    for row in rows:
        if row.get("current_state") not in {"active", "published"}:
            continue
        key = (row.get("hc_products") or {}).get("product_key")
        if not key:
            raise SitePacketError("companyos_product_key_missing")
        bundle = _run_json(["node", str(script), "get", key], runner)
        packets.append(from_companyos_bundle(bundle))
    return packets


def _pick(items, predicate):
    return next((item for item in items if predicate(str(item))), "")


_US_SKIP_TRANSLATIONS = {
    "사용 대상이 만 1세 미만인 경우": "the child is under age 1",
    "비건 제품이 필요한 경우": "you need a vegan product",
    "코코넛 성분에 대한 알레르기나 민감성이 있는 경우": "there is a coconut allergy or sensitivity",
    "의료진이 600 IU와 다른 용량을 지시한 경우": "a clinician directed a different dose",
    "다른 비타민 D 제품과 병용 중이라 총섭취량 확인이 필요한 경우": "another vitamin D product is already in use and total intake needs checking",
    "Amazon에서 ASIN, 판매자 또는 상품 옵션이 현재 확인 내용과 다르게 표시되는 경우": "the current ASIN, seller, or product option does not match this review",
}


def _localize_us(text):
    value = str(text or "")
    if not re.search(r"[가-힣]", value):
        return value
    translated = _US_SKIP_TRANSLATIONS.get(value)
    if not translated:
        raise SitePacketError("site_locale_mismatch")
    return translated


def from_companyos_bundle(bundle):
    product = bundle.get("product") or {}
    workflow = bundle.get("workflow") or {}
    landing = bundle.get("landing") or {}
    key = product.get("product_key", "")
    revision = workflow.get("approved_revision")
    evidence = next((row for row in bundle.get("evidence", []) if row.get("revision") == revision), None)
    if not evidence:
        raise SitePacketError("approved_evidence_missing")
    offer = next((row for row in bundle.get("offers", [])
                  if row.get("status") == "active" and row.get("market") == "US" and row.get("affiliate_url")), None)
    if not offer:
        raise SitePacketError("active_offer_missing")
    verification = landing.get("verification_evidence") or {}
    expected_tracking = landing.get("expected_tracking_key")
    if not (
        landing.get("deployment_status") == "verified"
        and landing.get("landing_verified_at")
        and verification.get("readback_verified") is True
        and verification.get("observed_product_key") == key
        and verification.get("observed_tracking_key") == expected_tracking
        and verification.get("observed_landing_url") == landing.get("landing_url")
    ):
        raise SitePacketError("landing_readback_mismatch")

    packet = evidence.get("packet") or {}
    decision = packet.get("decision") or {}
    allowed = workflow.get("claim_boundary", {}).get("allowed_claims", [])
    themes = workflow.get("claim_boundary", {}).get("allowed_review_themes", [])
    mechanism = _pick(allowed, lambda item: "format" in item.lower())
    failure_mode = _pick(themes, lambda item: "mixed" in item.lower())
    if not mechanism or not failure_mode or not decision.get("skip_if"):
        raise SitePacketError("site_decision_fields_missing")
    sources = [row.get("url") for row in packet.get("official_sources", []) if row.get("url")]
    if not sources:
        raise SitePacketError("source_pointers_missing")
    metadata = product.get("metadata") or {}
    return {
        "product_key": key,
        "product_name": product.get("name"),
        "market": product.get("country_code"),
        "category": metadata.get("category"),
        "friction_id": metadata.get("friction_id") or f"friction:{key}",
        "mechanism": mechanism,
        "failure_mode": failure_mode,
        "skip_if": _localize_us(decision["skip_if"][0]),
        "workflow_state": workflow.get("current_state"),
        "evidence_revision": workflow.get("evidence_revision"),
        "approved_revision": revision,
        "landing_verified": True,
        "offer_active": True,
        "offer_id": offer.get("id"),
        "tracking_key": expected_tracking,
        "landing_url": landing.get("landing_url"),
        "affiliate_url": offer.get("affiliate_url"),
        "source_pointers": sources,
    }
