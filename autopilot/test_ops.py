#!/usr/bin/env python3
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import run
import sourcing
import validate
import analytics
import improve
import generate
import post_check
import publish
import sitegen_lt
from common import append_jsonl, read_jsonl


def test_validate_exit_codes():
    ok = "✓ 정상"
    with patch.object(validate, "load_config", return_value={"amazon": {"tracking_id": "heightcue-20"}}), \
         patch.object(validate, "check_prompts", return_value=ok), \
         patch.object(validate, "check_openrouter", return_value=ok), \
         patch.object(validate, "check_threads", return_value=ok), \
         patch.object(validate, "check_coupang", return_value="— API 키 미설정"):
        assert validate.main() == 0
    with patch.object(validate, "load_config", return_value={"amazon": {"tracking_id": "heightcue-20"}}), \
         patch.object(validate, "check_prompts", return_value=ok), \
         patch.object(validate, "check_openrouter", return_value="✗ 실패"), \
         patch.object(validate, "check_threads", return_value=ok), \
         patch.object(validate, "check_coupang", return_value="— API 키 미설정"):
        assert validate.main() == 1


def test_rehearsal_stops_on_failed_validation():
    cfg = {"mode": {"publish": False}}
    with patch.object(run.subprocess, "call", return_value=1), patch.object(run, "daily") as daily:
        assert run.rehearsal(cfg) == 1
        daily.assert_not_called()


def test_country_language_gate():
    cfg = {"mode": {}}
    assert run._gate_and_publish(cfg, "한글 문장", "US", "value")[1] == "language_fail"
    assert run._gate_and_publish(cfg, "English only", "KR", "value")[1] == "language_fail"


def test_us_dry_run_copy_stays_in_voice():
    product = {
        "country": "US",
        "spec_facts": ["600 IU vitamin D3 per labeled drop"],
        "link": "https://example.com/guide",
    }
    sales = generate.make_sales_post({}, {}, product, dry_run=True)["text"]
    reply = generate.make_reply({}, "Thanks", "", "value", {}, dry_run=True, country="US")["text"]
    assert not any("가" <= ch <= "힣" for ch in sales + reply)
    assert "①" not in sales and "👉" not in sales
    assert "#ad" in sales.splitlines()[0]
    assert "Skip if" in sales


def test_publish_boundary_blocks_mixed_language_replies():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"mode": {"publish": True}, "paths": {"state_dir": tmp}}
        assert publish.publish_text(cfg, "US", "읽어주셔서 감사해요", dry_run=False) is None
        holds = read_jsonl(Path(tmp, "holdbox.jsonl"))
        assert len(holds) == 1
        assert holds[0]["why"] == "language_fail"
        assert holds[0]["stage"] == "publish_boundary"


def test_us_voice_violations_lower_format_score():
    clean = "3 labels, one clear difference. #ad\n\nPlain facts.\n\nSkip if: not needed.\n\nFull breakdown: https://example.com"
    noisy = clean + "\n\n① First point 👉"
    clean_score = post_check.check_post({"country": "US", "post_type": "sales", "text": clean})["format_score"]
    noisy_score = post_check.check_post({"country": "US", "post_type": "sales", "text": noisy})["format_score"]
    assert noisy_score < clean_score


def test_disclosure_position_and_literal_are_enforced():
    kr = "훅입니다.\n이 포스팅은 쿠팡 파트너스 활동의 일환으로, 수수료를 받습니다.\nhttps://link.coupang.com/a/test"
    result = post_check.check_post({"country": "KR", "post_type": "sales", "text": kr})
    assert any("불변 문구" in note for note in result["risk_notes"])

    us = "A disclosure in the middle #ad is not enough. More hook text\nSkip if: not needed.\nhttps://example.com"
    result = post_check.check_post({"country": "US", "post_type": "sales", "text": us})
    assert any("훅 행 끝" in note for note in result["risk_notes"])


def test_site_landing_discloses_before_affiliate_cta():
    html = sitegen_lt.render_product(
        {"product_name": "테스트", "product_key": "x", "link": "https://link.coupang.com/a/x", "_slug": "x"},
        {},
    )
    disclosure = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
    assert disclosure in html
    assert html.index(disclosure) < html.index('data-track="coupang-x"')


def test_review_quote_must_exist_in_saved_source():
    base = {
        "country": "KR", "post_type": "sales",
        "product": {"product_key": "x", "review_quotes": ["커버 세탁이 편해요"]},
    }
    exact = {**base, "text": "리뷰에 “커버 세탁이 편해요”라고 적혀 있습니다."}
    invented = {**base, "text": "리뷰에 “아이가 먼저 찾습니다”라고 적혀 있습니다."}
    assert not any("리뷰 인용 원문 결손" in n for n in post_check.check_post(exact)["risk_notes"])
    assert any("리뷰 인용 원문 결손" in n for n in post_check.check_post(invented)["risk_notes"])


def test_us_source_deficient_phrases_are_blocked_before_reuse():
    phrases = [
        "Most of them are syrup.",
        "Most kids' vitamin D gummies are candy.",
        "Kids don't even notice.",
        "It builds bone foundation.",
        "Pure D3.",
        "Led the league in steals.",
    ]
    for phrase in phrases:
        result = post_check.check_post({"country": "US", "post_type": "value", "text": phrase})
        assert result["risk_notes"], phrase


def test_ddrops_allows_only_saved_label_facts_and_requires_skip_if():
    product = {"product_key": "us-ddrops-kids-600iu", "is_food": True}
    clean = (
        "The label is shorter than the pitch. #ad\n\n"
        "600 IU vitamin D3 per labeled drop; the other listed ingredient is fractionated coconut oil.\n\n"
        "Skip if: the exact label or fractionated coconut oil does not fit your household.\n\n"
        "Full breakdown: https://example.com"
    )
    assert not post_check.check_post({"country": "US", "post_type": "sales", "text": clean, "product": product})["risk_notes"]
    unsupported = clean.replace("fractionated coconut oil.", "fractionated coconut oil. Tasteless and pure.", 1)
    assert post_check.check_post({"country": "US", "post_type": "sales", "text": unsupported, "product": product})["risk_notes"]
    missing_skip = clean.replace("Skip if:", "Consider whether")
    assert any("skip if" in n.lower() for n in post_check.check_post({"country": "US", "post_type": "sales", "text": missing_skip, "product": product})["risk_notes"])


def test_us_sales_ad_mode_is_always_on_in_body_and_metadata():
    product = {"product_key": "x", "country": "US", "spec_facts": ["fact"], "link": "https://example.com"}
    body = "Verified label fact. #ad\n\nSkip if: this is not a fit.\n\nFull breakdown: https://example.com"
    with patch.object(sourcing, "pick_us", return_value=product), \
         patch.object(generate, "make_master", return_value={}), \
         patch.object(generate, "make_sales_post", return_value={"text": body}), \
         patch.object(run, "_publish_with_retry") as publish_with_retry:
        run._us_sales({"mode": {}}, "", False)
        built_text = publish_with_retry.call_args.args[1]()
    kwargs = publish_with_retry.call_args.kwargs
    assert kwargs["product"]["ad_mode"] == "on"
    assert kwargs["meta_extra"]["ad_mode"] == "on"
    assert "#ad" in built_text.splitlines()[0]


def test_rehearsal_does_not_consume_us_rotation():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "us_products.json")
        path.write_text(json.dumps([{"product_key": "x", "product_name": "Example", "site_url": "https://example.com", "last_used_ts": 0}]))
        cfg = {"mode": {"_rehearsal": True}, "paths": {"state_dir": tmp}}
        assert sourcing.pick_us(cfg)["product_key"] == "x"
        assert json.loads(path.read_text())[0]["last_used_ts"] == 0


def test_dry_metrics_never_enter_weekly_summary():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"paths": {"state_dir": tmp}}
        append_jsonl(Path(tmp, "metrics.jsonl"), {
            "media_id": "DRY-1", "post_type": "sales",
            "insights": {"views": 1200}, "link_clicks": 18,
        })
        append_jsonl(Path(tmp, "metrics.jsonl"), {
            "media_id": "REAL-1", "post_type": "sales",
            "insights": {"views": 5}, "link_clicks": None,
        })
        summary = analytics.weekly_summary(cfg)
        assert summary["posts_total"] == 1
        by_type = summary.get("by_type")
        assert isinstance(by_type, dict)
        assert by_type["sales"]["views"] == 5
        assert by_type["sales"]["clicks"] is None
        assert analytics.collect(cfg, dry_run=True) == 0


def test_small_sample_cannot_change_playbook():
    summary = {"posts_total": 6, "by_hook_pattern": {"A": {"posts": 2}, "B": {"posts": 2}}}
    assert not improve._decision_sample_ready(summary)
    playbook = improve._insufficient_playbook(summary)
    assert "승자를 선언하지 않습니다" in playbook
    assert "kr_link_mode" in playbook


def test_compliance_replacement_is_audit_only_not_learning_sample():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"paths": {"state_dir": tmp}}
        append_jsonl(Path(tmp, "metrics.jsonl"), {
            "media_id": "BAD-1", "post_type": "sales", "insights": {"views": 99},
        })
        append_jsonl(Path(tmp, "metrics.jsonl"), {
            "media_id": "GOOD-1", "post_type": "sales", "insights": {"views": 7},
        })
        append_jsonl(Path(tmp, "deletions.jsonl"), {
            "media_id": "BAD-1", "status": "failed", "reason": "compliance replacement requested",
        })
        summary = analytics.weekly_summary(cfg)
        assert summary["posts_total"] == 1
        by_type = summary.get("by_type")
        assert isinstance(by_type, dict)
        assert by_type["sales"]["views"] == 7
        assert summary["analysis_excluded"] == [
            {"media_id": "BAD-1", "reason": "compliance_replacement"}
        ]


def test_sales_arm_ignores_dry_publications():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"mode": {}, "paths": {"state_dir": tmp}}
        append_jsonl(Path(tmp, "published.jsonl"), {
            "media_id": "REAL-1", "country": "KR", "meta": {"post_type": "sales"},
        })
        append_jsonl(Path(tmp, "published.jsonl"), {
            "media_id": "DRY-1", "country": "KR", "meta": {"post_type": "sales"},
        })
        assert run._sales_arm(cfg, "KR", "kr_link_mode") == "site"


if __name__ == "__main__":
    test_validate_exit_codes()
    test_rehearsal_stops_on_failed_validation()
    test_country_language_gate()
    test_us_dry_run_copy_stays_in_voice()
    test_publish_boundary_blocks_mixed_language_replies()
    test_us_voice_violations_lower_format_score()
    test_disclosure_position_and_literal_are_enforced()
    test_site_landing_discloses_before_affiliate_cta()
    test_review_quote_must_exist_in_saved_source()
    test_us_source_deficient_phrases_are_blocked_before_reuse()
    test_ddrops_allows_only_saved_label_facts_and_requires_skip_if()
    test_us_sales_ad_mode_is_always_on_in_body_and_metadata()
    test_rehearsal_does_not_consume_us_rotation()
    test_dry_metrics_never_enter_weekly_summary()
    test_small_sample_cannot_change_playbook()
    test_compliance_replacement_is_audit_only_not_learning_sample()
    test_sales_arm_ignores_dry_publications()
    print("ops safety tests: PASS")
