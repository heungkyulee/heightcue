#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import build_journey


def test_build_uses_live_packet_loader_and_returns_written_manifest(tmp_path):
    packet = {
        "product_key": "us-bin", "product_name": "Front-opening bin", "market": "US",
        "category": "storage", "friction_id": "fr-bin", "mechanism": "front opening",
        "failure_mode": "top lid gets blocked", "skip_if": "the shelf is too shallow",
        "workflow_state": "active", "evidence_revision": 1, "approved_revision": 1,
        "landing_verified": True, "offer_active": True, "offer_id": "offer-bin",
        "tracking_key": "amazon-us-bin", "landing_url": "https://heightcue.lifoli.co.kr/us/bin.html",
        "affiliate_url": "https://www.amazon.com/dp/TEST?tag=heightcue-20",
        "source_pointers": ["https://manufacturer.test/bin"],
    }
    calls = []

    def loader(root):
        calls.append(root)
        return [packet]

    manifest = build_journey.build(tmp_path / "public", tmp_path / "lifoli", packet_loader=loader)
    assert calls == [tmp_path / "lifoli"]
    assert manifest["products"][0]["product_key"] == "us-bin"
    assert (tmp_path / "public/journey-manifest.json").is_file()
    retired = tmp_path / "public/kr/p/153976571-444051272.html"
    assert retired.is_file()
    text = retired.read_text(encoding="utf-8")
    assert "판매 판정에서 퇴역" in text
    assert "link.coupang.com" not in text
    assert "kr/p/153976571-444051272.html" not in (tmp_path / "public/sitemap.xml").read_text(encoding="utf-8")
