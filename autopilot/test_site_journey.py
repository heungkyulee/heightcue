#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static HeightCue friction-commerce journey tests."""

import hashlib
import json
from pathlib import Path

import pytest

import site_journey


CATEGORY_KEYS = ("sleep_morning", "meals_lunch", "play_movement", "study_routine", "storage_cleanup")


def read(root, relative):
    return (Path(root) / relative).read_text(encoding="utf-8")


def active_packet(market="KR", category="storage", key="front-bin"):
    return {
        "product_key": key,
        "product_name": "앞으로 여는 장난감 수납함" if market == "KR" else "Front-opening toy bin",
        "market": market,
        "category": category,
        "friction_id": f"fr-{key}",
        "mechanism": "front_open",
        "failure_mode": "weak_latch",
        "skip_if": "선반 깊이가 얕은 집" if market == "KR" else "your shelf is too shallow",
        "workflow_state": "active",
        "evidence_revision": 3,
        "approved_revision": 3,
        "landing_verified": True,
        "offer_active": True,
        "offer_id": f"offer-{key}",
        "tracking_key": f"hc-{market.lower()}-{key}",
        "landing_url": f"https://heightcue.lifoli.co.kr/{market.lower()}/p/{key}.html",
        "affiliate_url": "https://retailer.example/item",
        "source_pointers": ["https://source.example/item"],
    }


def test_empty_build_has_complete_locale_category_archive_and_recovery_routes(tmp_path):
    manifest = site_journey.build_site(tmp_path, products=[])

    expected = [
        "index.html", "kr/index.html", "us/index.html", "measurement/index.html",
        "disclosure.html", "privacy.html", "sitemap.xml",
    ]
    expected += [f"kr/c/{key}.html" for key in CATEGORY_KEYS]
    expected += [f"us/c/{key}.html" for key in CATEGORY_KEYS]
    for relative in expected:
        assert (tmp_path / relative).is_file(), relative

    kr = read(tmp_path, "kr/index.html")
    us = read(tmp_path, "us/index.html")
    assert "아이 키우는 집의 반복되는 귀찮음을 줄이는 제품 판정" in kr
    assert "Product verdicts for recurring parenting friction" in us
    assert "제품 제보" in kr and "Submit a product" in us
    assert "전체 카테고리" in kr and "All categories" in us
    assert "167cm" not in kr + us
    assert "5'6" not in kr + us

    assert manifest["routes"] == sorted(expected)
    on_disk = json.loads(read(tmp_path, "journey-manifest.json"))
    assert on_disk == manifest
    assert site_journey.validate_site(tmp_path) == []


def test_validator_reports_an_internal_dead_end(tmp_path):
    site_journey.build_site(tmp_path, products=[])
    (tmp_path / "kr/c/sleep_morning.html").unlink()
    assert site_journey.validate_site(tmp_path) == [
        "kr/index.html -> /kr/c/sleep_morning.html"
    ]


def test_only_current_approved_and_verified_product_packets_render_ctas(tmp_path):
    approved = active_packet()
    published = {**active_packet(key="published"), "workflow_state": "published"}
    held = {**active_packet(key="held"), "workflow_state": "held"}
    stale = {**active_packet(key="stale"), "approved_revision": 2}
    unverified = {**active_packet(key="unverified"), "landing_verified": False}

    manifest = site_journey.build_site(tmp_path, products=[approved, published, held, stale, unverified])
    page = read(tmp_path, "kr/c/storage_cleanup.html")
    for shown in (approved, published):
        assert shown["product_name"] in page
        assert shown["landing_url"].replace("https://heightcue.lifoli.co.kr", "") in page
    for blocked in (held, stale, unverified):
        assert blocked["product_name"] not in page or blocked["product_key"] not in page
    assert [p["product_key"] for p in manifest["products"]] == ["front-bin", "published"]


def test_us_active_pages_never_render_static_price_rating_or_review_counts(tmp_path):
    product = {
        **active_packet(market="US", category="nutrition", key="drops"),
        "product_name": "One-drop vitamin D format",
        "price_info": {"amount": 19.99, "currency": "USD"},
        "review_rating": 4.8,
        "review_count": 1234,
    }
    manifest = site_journey.build_site(tmp_path, products=[product])
    page = read(tmp_path, "us/c/meals_lunch.html")
    detail = read(tmp_path, "us/p/drops.html")
    assert product["product_name"] in page
    assert product["product_name"] in detail
    for text in (page, detail):
        assert "$19.99" not in text
        assert "4.8" not in text
        assert "1,234" not in text and "1234 reviews" not in text
    assert "Check current listing" in page
    assert product["affiliate_url"] in detail
    assert 'rel="sponsored nofollow noopener noreferrer"' in detail
    assert "#ad" in detail
    assert "As an Amazon Associate I earn from qualifying purchases." in page + detail
    assert "us/p/drops.html" in manifest["routes"]
    assert site_journey.validate_site(tmp_path) == []


def test_manifest_binds_exact_approval_offer_landing_and_packet_digest(tmp_path):
    packet = active_packet()
    manifest = site_journey.build_site(tmp_path, [packet])
    item = manifest["products"][0]
    expected = hashlib.sha256(
        json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert item == {
        "product_key": "front-bin",
        "market": "KR",
        "evidence_revision": 3,
        "approved_revision": 3,
        "offer_id": packet["offer_id"],
        "tracking_key": packet["tracking_key"],
        "landing_url": "https://heightcue.lifoli.co.kr/kr/p/front-bin.html",
        "packet_digest": expected,
    }
    assert json.loads(read(tmp_path, "journey-manifest.json")) == manifest


def test_measurement_archive_has_no_commerce_cta_or_affiliate_link(tmp_path):
    site_journey.build_site(tmp_path, products=[])
    page = read(tmp_path, "measurement/index.html").lower()
    assert "historical education" in page
    assert "amazon.com/dp" not in page
    assert "link.coupang.com" not in page
    assert "check price" not in page


def test_invalid_product_category_fails_closed(tmp_path):
    with pytest.raises(site_journey.SiteJourneyError, match="unsupported_friction_category"):
        site_journey.build_site(tmp_path, products=[active_packet(category="measurement")])


def test_visual_contract_includes_keyboard_touch_and_reduced_motion_guards(tmp_path):
    site_journey.build_site(tmp_path, products=[])
    css = read(tmp_path, "journey.css")
    assert ":focus-visible" in css
    assert "min-height:44px" in css
    assert "prefers-reduced-motion:reduce" in css
    assert "--inspection-cyan:#00b7c7" in css.lower()
    assert "font-weight:900;line-height:1.04" in css
    assert "padding-bottom:4px" in css
    assert ".top a,footer a,.sources a{min-height:44px" in css


def test_hub_all_categories_link_advances_to_the_category_section(tmp_path):
    site_journey.build_site(tmp_path, products=[])
    page = read(tmp_path, "kr/index.html")
    assert 'id="categories"' in page
    assert 'href="#categories"' in page
    assert 'href="/kr/#categories"' in page
