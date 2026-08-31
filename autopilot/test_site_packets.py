#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import subprocess

import pytest

import site_packets


def companyos_bundle(state="published"):
    return {
        "product": {
            "product_key": "us-drops",
            "name": "One-drop format",
            "country_code": "US",
            "metadata": {"category": "nutrition"},
        },
        "workflow": {
            "current_state": state,
            "evidence_revision": 2,
            "approved_revision": 2,
            "claim_boundary": {
                "allowed_claims": ["one-drop liquid format", "600 IU per labeled drop"],
                "allowed_review_themes": ["mixed dispensing speed"],
            },
        },
        "evidence": [{
            "revision": 2,
            "packet": {
                "decision": {"skip_if": ["a different labeled dose is required"]},
                "official_sources": [{"url": "https://manufacturer.test/product"}],
                "review_evidence": [{"theme": "mixed dispensing speed", "direction": "mixed"}],
            },
        }],
        "landing": {
            "landing_url": "https://heightcue.lifoli.co.kr/us/drops.html",
            "expected_tracking_key": "amazon-us-drops",
            "deployment_status": "verified",
            "landing_verified_at": "2026-08-31T00:00:00Z",
            "verification_evidence": {
                "readback_verified": True,
                "observed_product_key": "us-drops",
                "observed_tracking_key": "amazon-us-drops",
                "observed_landing_url": "https://heightcue.lifoli.co.kr/us/drops.html",
            },
        },
        "offers": [{"id": "offer-us-drops", "status": "active", "market": "US", "affiliate_url": "https://www.amazon.com/dp/TEST?tag=heightcue-20"}],
    }


def test_companyos_bundle_becomes_evidence_bound_site_packet():
    packet = site_packets.from_companyos_bundle(companyos_bundle())
    assert packet == {
        "product_key": "us-drops",
        "product_name": "One-drop format",
        "market": "US",
        "category": "nutrition",
        "friction_id": "friction:us-drops",
        "mechanism": "one-drop liquid format",
        "failure_mode": "mixed dispensing speed",
        "skip_if": "a different labeled dose is required",
        "workflow_state": "published",
        "evidence_revision": 2,
        "approved_revision": 2,
        "landing_verified": True,
        "offer_active": True,
        "offer_id": "offer-us-drops",
        "tracking_key": "amazon-us-drops",
        "landing_url": "https://heightcue.lifoli.co.kr/us/drops.html",
        "affiliate_url": "https://www.amazon.com/dp/TEST?tag=heightcue-20",
        "source_pointers": ["https://manufacturer.test/product"],
    }


def test_companyos_bundle_rejects_mismatched_readback_and_evidence_revision():
    broken = companyos_bundle()
    broken["landing"]["verification_evidence"]["observed_tracking_key"] = "wrong"
    with pytest.raises(site_packets.SitePacketError, match="landing_readback_mismatch"):
        site_packets.from_companyos_bundle(broken)

    stale = companyos_bundle()
    stale["evidence"][0]["revision"] = 1
    with pytest.raises(site_packets.SitePacketError, match="approved_evidence_missing"):
        site_packets.from_companyos_bundle(stale)


def test_us_site_copy_translates_known_evidence_and_rejects_unknown_non_english_copy():
    known = companyos_bundle()
    known["evidence"][0]["packet"]["decision"]["skip_if"] = ["사용 대상이 만 1세 미만인 경우"]
    assert site_packets.from_companyos_bundle(known)["skip_if"] == "the child is under age 1"

    unknown = companyos_bundle()
    unknown["evidence"][0]["packet"]["decision"]["skip_if"] = ["확인되지 않은 한국어 조건"]
    with pytest.raises(site_packets.SitePacketError, match="site_locale_mismatch"):
        site_packets.from_companyos_bundle(unknown)


def test_loader_reads_only_live_companyos_workflows_and_adapts_their_exact_bundles(tmp_path):
    calls = []
    rows = [
        {"current_state": "published", "hc_products": {"product_key": "us-drops"}},
        {"current_state": "sourced", "hc_products": {"product_key": "us-unready"}},
    ]

    def runner(args, **kwargs):
        calls.append(args)
        payload = rows if args[-1] == "list" else companyos_bundle()
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    packets = site_packets.load_companyos_packets(tmp_path, runner=runner)
    assert [packet["product_key"] for packet in packets] == ["us-drops"]
    assert calls == [
        ["node", str(tmp_path / "scripts/heightcue-productctl.mjs"), "list"],
        ["node", str(tmp_path / "scripts/heightcue-productctl.mjs"), "get", "us-drops"],
    ]
