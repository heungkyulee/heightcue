#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import run
import companyos
import sourcing
import validate
import analytics
import execution_contract
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


def test_main_validates_execution_contract_before_content_dispatch(monkeypatch):
    cfg = {"mode": {"dry_run": False}}
    monkeypatch.setattr(run.sys, "argv", ["run.py", "daily"])
    with patch.object(run, "load_config", return_value=cfg), \
         patch.object(run.execution_contract, "validate_runtime", side_effect=RuntimeError("bad contract")) as validate_contract, \
         patch.object(run, "daily") as daily:
        try:
            run.main()
        except RuntimeError as exc:
            assert str(exc) == "bad contract"
        else:
            raise AssertionError("invalid contract did not stop startup")
    validate_contract.assert_called_once_with(cfg)
    daily.assert_not_called()


def test_rehearsal_stops_on_failed_validation():
    cfg = {"mode": {"publish": False}}
    with patch.object(run.subprocess, "call", return_value=1), patch.object(run, "daily") as daily:
        assert run.rehearsal(cfg) == 1
        daily.assert_not_called()


def test_rehearsal_entrypoint_runs_real_daily_us_sales_and_reads_attested_preview():
    """No-cost harness: invokes run.rehearsal, real daily, and real _us_sales."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "context").mkdir()
        (root / "autopilot/state").mkdir(parents=True)
        for relative in ("context/user-intent-contract.md", "context/compliance.md",
                         "context/persona.md", "context/voice-kr.md",
                         "context/voice-us.md", "heightcue-gemini-skills.md"):
            source = Path(run.__file__).parent.parent / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        manifest = json.loads((Path(run.__file__).parent.parent / "context/execution-contract.json").read_text())
        (root / "context/execution-contract.json").write_text(json.dumps(manifest))
        (root / "autopilot/config.json").write_text(json.dumps({"openrouter": {"model": "fixture-model"}}))
        (root / "autopilot/state/insight_atoms.json").write_text("[]")
        keys = root / "keys"
        fixture = str(Path(run.__file__).with_name("generation_test_fixture.py"))
        old_root, old_keys, old_mode = execution_contract.PROJECT_ROOT, execution_contract.DEFAULT_KEY_DIR, os.environ.get("HEIGHTCUE_TEST_FIXTURE_MODE")
        os.environ["HEIGHTCUE_TEST_FIXTURE_MODE"] = "us-sales"
        try:
            master = execution_contract.request_authoritative_generation(
                "sales_master", "US", ["product:us-ddrops-kids-600iu"],
                project_root=str(root), key_dir=str(keys),
                test_fixture_executable=fixture, rehearsal=True)
            sales = execution_contract.request_authoritative_generation(
                "sales_post", "US", ["product:us-ddrops-kids-600iu"],
                project_root=str(root), key_dir=str(keys),
                test_fixture_executable=fixture, rehearsal=True)
            # Verify the attested fixture against the same temporary trust root
            # only after the fixture has passed the production-boundary check.
            execution_contract.PROJECT_ROOT = str(root)
            execution_contract.DEFAULT_KEY_DIR = str(keys)
            cfg = {"mode": {"publish": False, "us_sales_posts": True,
                             "us_value_posts": False, "_testing": True},
                   "threads": {"us_access_token": "fixture"},
                   "paths": {"state_dir": str(root / "autopilot/state")},
                   "openrouter": {"model": "fixture-model"}}
            with patch.object(run.subprocess, "call", return_value=0), \
                 patch.object(run, "_kr_sales"), \
                 patch.object(run, "make_and_publish_value"), \
                 patch.object(run.analytics, "collect"), \
                 patch("digest.run_digest"), \
                 patch.object(run.evidence, "promote_pending"), \
                 patch.object(run.improve, "playbook_hint", return_value=""), \
                 patch("briefing.build", return_value="# fixture"), \
                 patch.object(run.sourcing, "top_up_requests"), \
                 patch.object(run.generate, "make_master", return_value=master), \
                 patch.object(run.generate, "make_sales_post", return_value=sales):
                assert run.rehearsal(cfg) == 0
            rows = read_jsonl(root / "autopilot/state/preview.jsonl")
            us = [row for row in rows if row.get("country") == "US" and row.get("meta", {}).get("post_type") == "sales"]
            assert len(us) == 1, read_jsonl(root / "autopilot/state/holdbox.jsonl")
            row = us[0]
            print("REHEARSAL_PREVIEW_ID", row["media_id"])
            assert row["meta"]["publish_status"] == "preview"
            assert row["meta"]["product_id"] == "us-front-open-storage"
            assert row["meta"]["generation_attestation"]["payload"]["execution_scope"] == "rehearsal"
            assert not (root / "autopilot/state/published.jsonl").exists()
        finally:
            execution_contract.stop_generation_service()
            execution_contract.PROJECT_ROOT, execution_contract.DEFAULT_KEY_DIR = old_root, old_keys
            if old_mode is None:
                os.environ.pop("HEIGHTCUE_TEST_FIXTURE_MODE", None)
            else:
                os.environ["HEIGHTCUE_TEST_FIXTURE_MODE"] = old_mode


def test_gate_does_not_report_published_when_boundary_returns_no_media():
    cfg = {"mode": {"auto_publish_clean": True}}
    clean = {"format_score": 100, "risk_notes": [], "format_tips": [], "verdict": "PASS"}
    with patch.object(run.post_check, "check_post", return_value=clean), \
         patch.object(run.publish, "publish_text", return_value=None):
        media, reason = run._gate_and_publish(cfg, "한국어 가치글", "KR", "value")
    assert media is None
    assert reason == "publish_failed"


def test_risk_hold_records_pipeline_identity_for_recovery():
    with tempfile.TemporaryDirectory() as tmp:
        cfg={'mode':{'hold_flagged':True},'paths':{'state_dir':tmp}}
        flagged={'format_score':92,'risk_notes':['boundary'],'format_tips':[],'verdict':'PASS'}
        with patch.object(run.post_check,'check_post',return_value=flagged):
            media,reason=run._gate_and_publish(cfg,'#ad\nEnglish sales copy','US','sales')
        assert media is None and reason=='risk_hold'
        row=read_jsonl(Path(tmp,'holdbox.jsonl'))[-1]
        assert row['country']=='US' and row['post_type']=='sales'
        assert row.get('ts')


def test_us_sales_preview_never_calls_threads_readback():
    product={'product_key':'us-dry-ddrops','country':'US','link':'https://example.com',
             '_workflow':{'workflow_id':'wf-1','tracking_key':'tk'}}
    result={'text':'#ad\nGrounded copy','_attestation':{}}
    with patch.object(run.sourcing,'pick_us',return_value=product), \
         patch.object(run.generate,'make_master',return_value={}), \
         patch.object(run.generate,'make_sales_post',return_value=result), \
         patch.object(run,'_publish_with_retry',return_value=('PREVIEW-1','published')), \
         patch.object(run.publish,'verified_publication_url',side_effect=AssertionError('preview readback called')) as verify, \
         patch.object(companyos,'record_product_publication') as record, \
         patch.object(companyos,'release_product_claim') as release:
        media,reason=run._us_sales({'mode':{'publish':False}},None,False)
    assert (media,reason)==('PREVIEW-1','published')
    verify.assert_not_called(); record.assert_not_called()
    assert release.call_args.args[1]=='preview_only'


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


def test_threads_readback_accepts_platform_stripped_ad_hash_only():
    expected = "Why one drop? #ad\n\nFull breakdown: https://example.com"
    observed = "Why one drop? ad\n\nFull breakdown: https://example.com"
    assert publish._threads_text_matches(expected, observed)
    assert not publish._threads_text_matches(expected, observed.replace("one drop", "two drops"))


def test_threads_canonical_permalink_accepts_threads_com():
    cfg = {"threads": {"us_access_token": "token", "us_user_id": "uid"}}
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"id": "media-1", "permalink": "https://www.threads.com/@heightcue_us/post/code"}
    with patch.object(publish.requests, "get", return_value=response):
        assert publish.verified_publication_url(cfg, "media-1") == "https://www.threads.com/@heightcue_us/post/code"


def test_publish_boundary_blocks_mixed_language_replies():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"mode": {"publish": True}, "paths": {"state_dir": tmp}}
        assert publish.publish_text(cfg, "US", "읽어주셔서 감사해요", dry_run=False) is None
        holds = read_jsonl(Path(tmp, "holdbox.jsonl"))
        assert len(holds) == 1
        assert holds[0]["why"] == "language_fail"
        assert holds[0]["stage"] == "publish_boundary"


def test_publish_boundary_hard_fails_sales_without_disclosure():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"mode": {"publish": True}, "paths": {"state_dir": tmp}}
        # KR 판매글 고지 누락 차단
        res_kr = publish.publish_text(
            cfg, "KR", "아이 운동템 리뷰입니다.\nhttps://heightcue.lifoli.co.kr/kr/p/1.html",
            dry_run=False, meta={"post_type": "sales"}
        )
        assert res_kr is None
        # US 판매글 #ad 누락 차단
        res_us = publish.publish_text(
            cfg, "US", "Vitamin D drops review.\nSkip if not needed.\nhttps://example.com",
            dry_run=False, meta={"post_type": "sales"}
        )
        assert res_us is None
        holds = read_jsonl(Path(tmp, "holdbox.jsonl"))
        assert len(holds) == 2
        assert all(h["why"] == "disclosure_missing_hard_fail" for h in holds)
        assert all(h["stage"] == "publish_boundary" for h in holds)


def test_us_voice_violations_lower_format_score():
    clean = "3 labels, one clear difference. #ad\n\nPlain facts.\n\nSkip if: not needed.\n\nFull breakdown: https://example.com"
    noisy = clean + "\n\n① First point 👉"
    clean_score = post_check.check_post({"country": "US", "post_type": "sales", "text": clean})["format_score"]
    noisy_score = post_check.check_post({"country": "US", "post_type": "sales", "text": noisy})["format_score"]
    assert noisy_score < clean_score


def test_small_study_cannot_support_absolute_individual_claim():
    text=('Deep sleep guilt is overblown.\n\nIn 14 pubertal kids, growth hormone pulses timed up with slow-wave sleep.\n\n'
          'It was a small trial.\n\nYour kid waking up at 2 AM changes nothing.')
    result=post_check.check_post({'country':'US','post_type':'value','text':text,'product':{}})
    assert any('small-study absolute' in note for note in result['risk_notes']), result


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
         patch.object(generate, "make_sales_post", return_value={
             "text": body,
             "_provenance": {"contract_id": "heightcue-content-v1", "model": "m"},
         }), \
         patch.object(run, "_publish_with_retry") as publish_with_retry:
        run._us_sales({"mode": {}}, "", False)
        built_text = publish_with_retry.call_args.args[1]()
    kwargs = publish_with_retry.call_args.kwargs
    assert kwargs["product"]["ad_mode"] == "on"
    assert kwargs["meta_extra"]["ad_mode"] == "on"
    assert kwargs["meta_extra"]["execution_contract"]["contract_id"] == "heightcue-content-v1"
    assert "#ad" in built_text.splitlines()[0]


def test_rehearsal_does_not_claim_or_read_legacy_us_rotation():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "us_products.json")
        original = [{"product_key": "x", "product_name": "Example", "site_url": "https://example.com", "last_used_ts": 0}]
        path.write_text(json.dumps(original))
        cfg = {"mode": {"_rehearsal": True, "publish": False}, "paths": {"state_dir": tmp}}
        assert sourcing.pick_us(cfg)["product_key"] == "us-front-open-storage"
        assert json.loads(path.read_text()) == original


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


def test_attribution_completeness_and_subid_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"paths": {"state_dir": tmp}}
        complete_row = {
            "media_id": "REAL-101", "country": "KR", "post_type": "sales",
            "hook_family": "F2", "angle_id": "a1", "product_id": "p1",
            "formfactor_id": "ff1", "ux_grade": "novel", "writer_variant": "d1",
            "sub_id": "hc-20260831-direct", "experiment_id": "kr_link_mode",
            "experiment_arm": "direct", "link_mode": "direct"
        }
        complete_row.update({"friction_id": "fr-1", "stage": "verdict",
                             "mechanism": "front_open", "price_band": "20k",
                             "affiliate_destination": "coupang", "market": "KR",
                             "source_pointers": ["review:r1"]})
        incomplete_row = {
            "media_id": "REAL-102", "country": "KR", "post_type": "sales",
            "hook_family": "F2", "angle_id": None, "product_id": "p1",
            "formfactor_id": "ff1", "ux_grade": "novel", "writer_variant": "d1",
        }
        assert analytics.attribution_gaps(complete_row) == []
        assert "angle_id" in analytics.attribution_gaps(incomplete_row)


if __name__ == "__main__":
    test_validate_exit_codes()
    test_rehearsal_stops_on_failed_validation()
    test_rehearsal_entrypoint_runs_real_daily_us_sales_and_reads_attested_preview()
    test_country_language_gate()
    test_us_dry_run_copy_stays_in_voice()
    test_publish_boundary_blocks_mixed_language_replies()
    test_publish_boundary_hard_fails_sales_without_disclosure()
    test_us_voice_violations_lower_format_score()
    test_disclosure_position_and_literal_are_enforced()
    test_site_landing_discloses_before_affiliate_cta()
    test_review_quote_must_exist_in_saved_source()
    test_us_source_deficient_phrases_are_blocked_before_reuse()
    test_ddrops_allows_only_saved_label_facts_and_requires_skip_if()
    test_us_sales_ad_mode_is_always_on_in_body_and_metadata()
    test_rehearsal_does_not_claim_or_read_legacy_us_rotation()
    test_dry_metrics_never_enter_weekly_summary()
    test_small_sample_cannot_change_playbook()
    test_compliance_replacement_is_audit_only_not_learning_sample()
    test_sales_arm_ignores_dry_publications()
    test_attribution_completeness_and_subid_mapping()
    print("ops safety tests: PASS")
