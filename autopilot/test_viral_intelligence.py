import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import analytics
import digest
import generate
import improve
import post_check
import run
import sourcing
import viral_intelligence
from common import is_real_publication, read_json, redact_secrets


ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "autopilot"


def test_generation_uses_openrouter_gemini_37_flash():
    cfg = json.loads((AUTOPILOT / "config.json").read_text(encoding="utf-8"))
    assert cfg["llm"]["provider"] == "openrouter"
    assert cfg["llm"]["model"] == "google/gemini-3.7-flash"
    assert cfg["llm"]["critic_model"] == "google/gemini-3.7-flash"


def test_real_publication_filter_rejects_dry_and_missing_ids():
    assert is_real_publication({"media_id": "181234", "meta": {"publish_status": "verified"}}) is True
    assert is_real_publication({"media_id": "181234", "meta": {"publish_status": "verification_pending"}}) is False
    assert is_real_publication({"media_id": "DRY-123"}) is False
    assert is_real_publication({}) is False


def test_error_redaction_removes_tokens_from_urls():
    text = "GET https://graph.threads.net/v1.0/me?access_token=secret-value&fields=id api_key=another-secret"
    redacted = redact_secrets(text)
    assert "secret-value" not in redacted
    assert "another-secret" not in redacted
    assert "access_token=[REDACTED]" in redacted


def test_sales_post_tries_lower_ranked_hooks_until_two_drafts_are_eligible():
    hooks = _six_hooks()
    hook_scores = [{"id": f"h{i}", "score": 100 - i} for i in range(1, 7)]
    responses = [
        {"hooks": hooks},
        {"scores": hook_scores},
        {"text": "본문 h1"},
        {"text": "본문 h2"},
        {"text": "본문 h3"},
        {"text": "본문 h4"},
        {"scores": [
            {"id": "d1", "score": 99}, {"id": "d2", "score": 98},
            {"id": "d3", "score": 80}, {"id": "d4", "score": 90},
        ]},
    ]
    weak = {"verdict": "PASS", "format_score": 100, "risk_notes": ["unsupported"]}
    clean = {"verdict": "PASS", "format_score": 100, "risk_notes": []}
    cfg = {"openrouter": {"model": "google/gemini-3.7-flash", "critic_model": "google/gemini-3.7-flash"}}
    product = {"country": "KR", "product_key": "p1", "link": "https://example.com"}
    with patch.object(generate, "_gemini", side_effect=responses), \
         patch("common.recent_context", return_value={"recent_posts": [], "my_recent_replies": [], "recent_comments_received": [], "my_bio": ""}), \
         patch.object(generate, "load_skill", return_value="writer-system"), \
         patch("post_check.check_post", side_effect=[weak, weak, clean, clean]):
        winner = generate.make_sales_post(cfg, {"hooks": ["legacy"]}, product)
    assert winner["hook_id"] == "h4"
    assert winner["writer_variant"] == "d4"


def test_viral_goldens_cover_user_rejection_categories():
    path = AUTOPILOT / "state" / "viral-goldens.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    categories = {case["category"] for case in payload["cases"]}
    assert {
        "generic_roundup",
        "medical_claim_framing",
        "ai_report_voice",
        "first_plausible_product",
        "compliance_dominates_copy",
        "unsupported_viral_claim",
    } <= categories
    assert all(case["failure"] and case["acceptance"] for case in payload["cases"])


def test_user_intent_contract_is_bound_to_aistudio_source():
    text = (ROOT / "context" / "user-intent-contract.md").read_text(encoding="utf-8")
    assert "1QNIpujCLuyjsLd8iRGespi9Sv6vVe8kE" in text
    assert "Aside CLI" in text
    assert "Gemini 3.7 Flash" in text


def _queue_cfg(state_dir):
    return {
        "paths": {"state_dir": state_dir},
        "coupang": {"sub_id_prefix": "hc"},
        "mode": {},
    }


def test_queue_fails_closed_without_validated_friction_signal():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _queue_cfg(tmp)
        added = sourcing.top_up_requests(cfg, buffer_target=2)
        requests = read_json(Path(tmp, "browser-queue", "requests.json"), [])
        assert added == 0
        assert requests == []


def test_friction_lane_preserves_validated_source_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _queue_cfg(tmp)
        signal = {"friction_id": "fr-1", "lifecycle": "validated", "market": "KR",
                  "domain": "storage", "source_type": "external_complaint",
                  "source_pointer": "https://source.test/1", "verbatim": "아래 통을 꺼낼 때 전부 내린다",
                  "recurrence": 3, "intensity": 4, "mechanisms": ["front_open"],
                  "sourcing_keyword": "앞으로 여는 수납함", "is_food": False}
        Path(tmp, "friction_signals.jsonl").write_text(
            json.dumps(signal, ensure_ascii=False) + "\n", encoding="utf-8")
        sourcing.top_up_requests(cfg, buffer_target=1)
        request = read_json(Path(tmp, "browser-queue", "requests.json"), [])[0]
        assert request["lane"] == "friction"
        assert request["friction_id"] == "fr-1"
        assert request["source_pointers"] == ["https://source.test/1"]


def test_sourcing_result_requires_five_to_three_to_one_comparison():
    incomplete = {
        "candidate_pool": [{"name": f"candidate-{i}"} for i in range(4)],
        "compared_candidates": [{"name": "a"}, {"name": "b"}],
        "rejected_candidates": [{"name": "b", "reason": "근거"}],
        "winner_reasons": [],
    }
    reasons = sourcing.comparison_readiness_reasons(incomplete)
    assert "candidate_pool_lt_5" in reasons
    assert "compared_candidates_lt_3" in reasons
    assert "rejected_candidates_lt_2" in reasons
    assert "winner_reasons_missing" in reasons


def test_discovery_result_never_invents_demand_provenance():
    reasons = sourcing.audit_readiness_reasons({
        "lane": "discovery",
        "formfactor_id": "ff-posture-angle-board",
        "friction_solved": "고개 숙임을 줄이는 각도 구조",
    })
    assert not any(reason.startswith("demand_") for reason in reasons)


def test_malformed_friction_provenance_is_held_instead_of_crashing():
    reasons = sourcing.audit_readiness_reasons({"lane": "friction", "source_pointers": "legacy free text"})
    assert "friction_source_pointers_invalid" in reasons


def test_malformed_price_provenance_is_held_instead_of_crashing():
    # 워커가 dict 대신 list/str을 제출해도 크래시 대신 보류 사유로 처리한다.
    for bad in ([{"regular_price": "1원"}], "24,000원", 24000):
        reasons = sourcing.audit_readiness_reasons({"lane": "discovery", "price_provenance": bad})
        assert "price_provenance_invalid" in reasons, bad


def test_malformed_review_and_official_provenance_are_held_instead_of_crashing():
    # list 안에 dict가 아닌 원소가 섞여도 크래시하지 않는다.
    reasons = sourcing.audit_readiness_reasons({
        "lane": "discovery",
        "review_provenance": ["리뷰 원문 문자열"],
        "official_provenance": ["스펙 문자열"],
    })
    assert "review_0_invalid" in reasons
    assert "official_0_invalid" in reasons

    # list가 아니라 dict로 제출된 경우도 방어한다.
    reasons = sourcing.audit_readiness_reasons({
        "lane": "discovery",
        "review_provenance": {"review_id": "r1"},
        "official_provenance": {"quote": "q"},
    })
    assert "review_provenance_invalid" in reasons
    assert "official_provenance_invalid" in reasons


def test_wellformed_provenance_produces_no_invalid_reasons():
    # 정상 형태는 새 방어 로직 때문에 반려되지 않아야 한다(거짓 양성 방지).
    reasons = sourcing.audit_readiness_reasons({
        "lane": "discovery",
        "price_provenance": {"regular_price": "24,000원", "variable_price": "17,390원",
                             "source_url": "https://www.coupang.com/vp/products/1"},
        "review_provenance": [{"review_id": "r1", "quote": "좋아요",
                               "source_url": "https://www.coupang.com/vp/products/1?review=r1",
                               "original_location": "상품평 > 베스트순"}],
        "official_provenance": [{"quote": "1200mm", "source_url": "https://www.coupang.com/vp/products/1",
                                 "original_location": "상세 > 사양"}],
    })
    assert not any("invalid" in reason for reason in reasons)


def test_audit_approval_requires_fresh_aside_reverification_marker():
    result = {
        "audit_status": "approved",
        "audited_by": "haneul-proof",
        "source_reverified_via": "stored-json-only",
    }
    reasons = sourcing.audit_readiness_reasons(result)
    assert "audit_source_not_reverified_with_aside_u0" in reasons
    assert "audit_source_reverified_at_missing" in reasons


def test_audit_owner_accepts_current_and_legacy_proof_bot_handles():
    # 봇 프로필명이 mungchi-proof -> haneul-proof로 바뀌었다.
    # 현행 핸들은 반드시 통과해야 하고(파이프라인 정지 방지),
    # 개명 전 승인된 기존 큐 항목도 계속 유효해야 한다.
    for handle in ("haneul-proof", "mungchi-proof"):
        reasons = sourcing.audit_readiness_reasons({
            "audit_status": "approved",
            "audited_by": handle,
            "source_reverified_via": "aside:u0",
            "source_reverified_at": "2026-08-28T13:15:19+09:00",
        })
        assert "audit_owner_mismatch" not in reasons, handle

    # 승인 권한이 없는 다른 봇은 여전히 거부한다.
    reasons = sourcing.audit_readiness_reasons({
        "audit_status": "approved",
        "audited_by": "jaehyun-publisher",
        "source_reverified_via": "aside:u0",
        "source_reverified_at": "2026-08-28T13:15:19+09:00",
    })
    assert "audit_owner_mismatch" in reasons


def test_digest_builds_acp_from_real_posts_and_ignores_dry_records():
    records = [
        {"country": "KR", "text": "가짜 고정 훅\n본문", "media_id": "DRY-1", "meta": {"post_type": "value"}},
        {"country": "KR", "text": "실제 반복 훅\n첫 본문", "media_id": "real-1", "meta": {"post_type": "value", "hook_pattern": "팩트체크", "publish_status": "verified"}},
        {"country": "KR", "text": "실제 반복 훅\n둘째 본문", "media_id": "real-2", "meta": {"post_type": "sales", "hook_pattern": "팩트체크", "publish_status": "verified"}},
        {"country": "US", "text": "Real US hook\nBody", "media_id": "real-us", "meta": {"post_type": "value", "hook_pattern": "myth_bust", "publish_status": "verified"}},
        {"country": "KR", "text": "댓글", "media_id": "reply-1", "meta": {"kind": "reply"}},
    ]
    acp = digest.build_account_memory(records)
    assert acp["KR"]["source_post_count"] == 2
    assert acp["US"]["source_post_count"] == 1
    assert acp["KR"]["overused_hooks"] == ["실제 반복 훅"]
    assert "가짜 고정 훅" not in json.dumps(acp, ensure_ascii=False)
    assert acp["KR"]["source_post_ids"] == ["real-1", "real-2"]


def test_digest_refresh_is_skipped_when_brand_lead_wrote_newer_acp():
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        published = state / "published.jsonl"
        acp = state / "account_memory.json"
        published.write_text('{}\n', encoding="utf-8")
        acp.write_text('{}', encoding="utf-8")
        published.touch()
        acp.touch()
        assert digest.needs_refresh({"paths": {"state_dir": tmp}}) is False


def test_sourcing_winner_requires_gemini_37_judgment_signature():
    reasons = sourcing.comparison_readiness_reasons({
        "candidate_pool": [{"name": str(i)} for i in range(5)],
        "compared_candidates": [{"name": str(i)} for i in range(3)],
        "rejected_candidates": [
            {"name": "1", "reason": "근거 1"},
            {"name": "2", "reason": "근거 2"},
        ],
        "winner_reasons": ["근거 3"],
        "winner_count": 1,
        "judgment_status": "pending",
    })
    assert "gemini_judgment_not_approved" in reasons
    assert "gemini_judgment_model_mismatch" in reasons


def _six_hooks():
    return [
        {
            "id": f"h{i}",
            "text": f"서로 다른 훅 {i}",
            "hook_family": f"F{((i - 1) % 4) + 1}",
            "angle_id": f"angle-{i}",
            "rationale": f"생성자 내부 이유 {i}",
        }
        for i in range(1, 7)
    ]


def test_hook_response_accepts_openrouter_top_level_array():
    rows = _six_hooks()
    assert viral_intelligence.extract_hook_rows(rows) == rows
    assert viral_intelligence.extract_hook_rows({"hooks": rows}) == rows


def test_hook_tournament_requires_six_unique_hooks():
    assert len(viral_intelligence.validate_hooks(_six_hooks())) == 6
    duplicate = _six_hooks()
    duplicate[-1]["text"] = duplicate[0]["text"]
    try:
        viral_intelligence.validate_hooks(duplicate)
    except ValueError as exc:
        assert "six unique hooks" in str(exc)
    else:
        raise AssertionError("duplicate hooks must fail")


def test_hook_critic_payload_is_blind_to_generator_rationale():
    payload = viral_intelligence.build_hook_critic_payload(_six_hooks())
    assert len(payload["hooks"]) == 6
    assert all("rationale" not in hook for hook in payload["hooks"])
    assert set(payload["hooks"][0]) == {"id", "text"}


def test_hook_scores_select_exactly_top_two():
    scores = [{"id": f"h{i}", "score": score} for i, score in enumerate([60, 91, 72, 88, 70, 65], 1)]
    winners = viral_intelligence.select_hook_candidates(_six_hooks(), scores)
    assert [winner["id"] for winner in winners] == ["h2", "h4"]


def test_draft_winner_carries_attribution_metadata():
    drafts = [
        {"id": "d1", "hook_id": "h2", "text": "첫 글", "eligible": True},
        {"id": "d2", "hook_id": "h4", "text": "둘째 글", "eligible": True},
    ]
    scores = [{"id": "d1", "score": 83}, {"id": "d2", "score": 94}]
    winner = viral_intelligence.select_draft_winner(drafts, scores)
    assert winner["id"] == "d2"
    assert winner["writer_variant"] == "d2"
    assert winner["hook_id"] == "h4"
    assert winner["viral_score"] == 94


def test_draft_eligibility_rejects_format_tips_even_when_verdict_passes():
    weak = {"verdict": "PASS", "format_score": 92, "format_fails": [], "format_tips": ["weak hook"], "risk_notes": []}
    clean = {"verdict": "PASS", "format_score": 100, "format_fails": [], "format_tips": [], "risk_notes": []}
    assert viral_intelligence.draft_is_eligible(weak) is False
    assert viral_intelligence.draft_is_eligible(clean) is True


def test_real_post_check_keeps_format_quality_gate_enabled():
    text = (
        "숫자 1개가 있어도 이모지는 탈락입니다 🤸\n\n"
        "검증 근거를 충분히 설명하는 본문입니다. 비추천: 이미 잘 쓰는 제품이 있는 집.\n\n"
        "자세한 내용은 링크에서 확인하세요. https://example.com"
    )
    check = post_check.check_post({"text": text, "country": "KR", "post_type": "sales", "product": {}})
    assert check["format_score"] < 100
    assert any("이모지" in tip for tip in check["format_tips"])
    assert viral_intelligence.draft_is_eligible(check) is False


def test_dry_run_sales_fixtures_meet_the_same_quality_gate():
    cfg = {}
    for country in ("KR", "US"):
        product = {
            "country": country,
            "product_name": "테스트 제품",
            "price_info": "10,000원",
            "review_count": 1000,
            "review_rating": 4.8,
            "review_quotes": ["실제 저장 후기"],
            "spec_facts": ["600 IU of vitamin D3 per drop", "fractionated coconut oil"],
            "link": "https://example.com",
        }
        master = generate.make_master(cfg, product, dry_run=True)
        draft = generate.make_sales_post(cfg, master, product, dry_run=True)
        check = post_check.check_post({"text": draft["text"], "country": country, "post_type": "sales", "product": product})
        assert viral_intelligence.draft_is_eligible(check), (country, check)


def test_us_unsourced_other_options_comparison_is_ineligible():
    product = {
        "product_key": "us-ddrops-kids-600iu",
        "spec_facts": ["600 IU vitamin D3 per labeled drop", "fractionated coconut oil"],
    }
    text = (
        "Other options require full droppers—not this 1 labeled drop. #ad\n\n"
        "This label lists 600 IU vitamin D3 per labeled drop and fractionated coconut oil.\n\n"
        "Skip if: the exact label does not fit your household.\n\n"
        "Full breakdown: https://example.com"
    )
    check = post_check.check_post({"text": text, "country": "US", "post_type": "sales", "product": product})
    assert any("source-deficient market comparison" in note for note in check["risk_notes"])
    assert viral_intelligence.draft_is_eligible(check) is False


def test_ddrops_unsourced_formfactor_comparisons_are_ineligible():
    product = {
        "product_key": "us-ddrops-kids-600iu",
        "spec_facts": [
            "600 IU vitamin D3 per labeled drop",
            "manufacturer audience: age 1+",
            "single tasteless oil drop format",
            "fractionated coconut oil",
        ],
    }
    text = (
        "Skip measuring 1 ml syrups. Look at 1 drop instead. #ad\n\n"
        "No multi-milliliter syringes or complex additives. Just 600 IU vitamin D3 per labeled drop "
        "in fractionated coconut oil for age 1+.\n\n"
        "To be fair, gummies win if you prefer flavored options.\n\n"
        "skip if: your kid already gets enough vitamin D from food.\n\n"
        "Full breakdown: https://example.com"
    )
    check = post_check.check_post({"text": text, "country": "US", "post_type": "sales", "product": product})
    assert any("Ddrops evidence boundary" in note for note in check["risk_notes"])
    assert viral_intelligence.draft_is_eligible(check) is False


def test_ddrops_chewable_and_multivitamin_comparison_is_ineligible():
    product = {
        "product_key": "us-ddrops-kids-600iu",
        "spec_facts": ["600 IU vitamin D3 per labeled drop", "fractionated coconut oil"],
    }
    text = (
        "Why buy bulky chewables instead of 1 simple drop? #ad\n\n"
        "This label lists 600 IU vitamin D3 per labeled drop in fractionated coconut oil.\n\n"
        "skip if: your child already takes an all-in-one multivitamin.\n\n"
        "Full breakdown: https://example.com"
    )
    check = post_check.check_post({"text": text, "country": "US", "post_type": "sales", "product": product})
    assert any("competitor form-factor claims" in note for note in check["risk_notes"])
    assert viral_intelligence.draft_is_eligible(check) is False


def test_ddrops_other_formats_multiple_servings_are_ineligible():
    product = {
        "product_key": "us-ddrops-kids-600iu",
        "spec_facts": ["600 IU vitamin D3 per labeled drop", "fractionated coconut oil"],
    }
    text = (
        "Why measure multiple doses when 1 drop gives 600 IU? #ad\n\n"
        "Other formats require multiple servings and complex extras. This label lists 600 IU vitamin D3 "
        "per labeled drop in fractionated coconut oil.\n\n"
        "skip if: the exact label does not fit your household.\n\n"
        "Full breakdown: https://example.com"
    )
    check = post_check.check_post({"text": text, "country": "US", "post_type": "sales", "product": product})
    assert any("Ddrops evidence boundary" in note for note in check["risk_notes"])
    assert viral_intelligence.draft_is_eligible(check) is False


def test_ddrops_unstored_convenience_and_intake_phrases_are_blocked():
    product = {"product_key": "us-ddrops-kids-600iu"}
    phrases = [
        "long filler list", "measuring spoons needed", "daily vitamin D intake",
        "another routine", "already gets adequate vitamin D3", "bloated formulas",
    ]
    for phrase in phrases:
        notes = post_check.evidence_boundary_notes(phrase, "US", product)
        assert notes, phrase


def test_ddrops_skip_if_must_be_exact_label_nonfit():
    product = {"product_key": "us-ddrops-kids-600iu"}
    text = (
        "1 labeled drop gives 600 IU. #ad\n\n"
        "This label lists fractionated coconut oil.\n\n"
        "skip if: your kid has no need for extra D3.\n\n"
        "Full breakdown: https://example.com"
    )
    notes = post_check.risk_notes(text, "US", "sales", product)
    assert any("skip if must be based on the exact label" in note for note in notes)


def test_kr_unsourced_other_brand_claim_is_blocked():
    notes = post_check.evidence_boundary_notes(
        "솔직히 패키지 디자인은 타사가 낫습니다", "KR", {"product_key": "kr-product"}
    )
    assert any("source-deficient competitor comparison" in note for note in notes)


def test_single_quoted_review_paraphrase_is_ineligible():
    product = {
        "product_key": "kr-product",
        "review_quotes": ["저장된 실제 후기 원문"],
    }
    text = (
        "리뷰 4,500개에서 제일 자주 나오는 말: '아이가 알아서 먼저 찾아 먹어요'\n\n"
        "비추천: 이미 잘 먹는 집.\n\n"
        "링크: https://example.com"
    )
    check = post_check.check_post({"text": text, "country": "KR", "post_type": "sales", "product": product})
    assert any("리뷰 인용 원문 결손" in note for note in check["risk_notes"])
    assert viral_intelligence.draft_is_eligible(check) is False


def test_llm_call_retries_http_generation(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise ValueError("provider temporarily unavailable")
        return Response()

    monkeypatch.setattr(generate.requests, "post", fake_post)
    cfg = {"openrouter": {"model": "google/gemini-3.7-flash", "api_key": "test"}}
    result = generate.llm_call(cfg, "system", {"input": "test"}, retry=1)
    assert result["ok"] is True
    assert result == {"ok": True}
    assert len(calls) == 2


def test_hook_generation_retries_semantically_invalid_bundle(monkeypatch):
    valid = [
        {"id": f"h{i}", "text": f"unique hook {i}", "hook_family": "F1", "angle_id": f"a{i}"}
        for i in range(1, 7)
    ]
    responses = [[*valid[:5]], valid]
    calls = []

    def fake_gemini(*args, **kwargs):
        calls.append((args, kwargs))
        return responses[len(calls) - 1]

    monkeypatch.setattr(generate, "_gemini", fake_gemini)
    hooks = generate.generate_hooks({"openrouter": {"model": "test-model"}},
                                    {"master": "note"}, {"product_key": "p1"})
    assert len(hooks) == 6
    assert len(calls) == 2


def test_hook_generation_retries_bundle_outside_product_evidence(monkeypatch):
    safe = [
        {"id": f"h{i}", "text": f"Label fact hook {i}", "hook_family": "F1", "angle_id": f"a{i}"}
        for i in range(1, 7)
    ]
    unsafe = [dict(item) for item in safe]
    unsafe[0]["text"] = "Other formats require multiple servings"
    responses = [unsafe, safe]
    calls = []

    def fake_gemini(*args, **kwargs):
        calls.append((args, kwargs))
        return responses[len(calls) - 1]

    monkeypatch.setattr(generate, "_gemini", fake_gemini)
    product = {"country": "US", "product_key": "us-ddrops-kids-600iu"}
    hooks = generate.generate_hooks({"openrouter": {"model": "test-model"}},
                                    {"master": "note"}, product)
    assert len(hooks) == 6
    assert all(not post_check.evidence_boundary_notes(hook["text"], "US", product) for hook in hooks)
    assert len(calls) == 2


def test_hook_generation_accumulates_safe_hooks_across_bundles(monkeypatch):
    def bundle(prefix, safe_indexes):
        rows = []
        for i in range(1, 7):
            text = f"{prefix} safe label hook {i}" if i in safe_indexes else f"Other formats require multiple servings {prefix}{i}"
            rows.append({"id": f"h{i}", "text": text, "hook_family": "F1", "angle_id": f"{prefix}{i}"})
        return rows

    responses = [bundle("first", {1, 2, 3}), bundle("second", {4, 5, 6})]
    calls = []

    def fake_gemini(*args, **kwargs):
        calls.append((args, kwargs))
        return responses[len(calls) - 1]

    monkeypatch.setattr(generate, "_gemini", fake_gemini)
    product = {"country": "US", "product_key": "us-ddrops-kids-600iu"}
    hooks = generate.generate_hooks({"openrouter": {"model": "test-model"}},
                                    {"master": "note"}, product)
    assert [hook["id"] for hook in hooks] == [f"h{i}" for i in range(1, 7)]
    assert all(not post_check.evidence_boundary_notes(hook["text"], "US", product) for hook in hooks)
    assert len(calls) == 2


def test_sales_post_runs_blind_gemini_tournament():
    hooks = _six_hooks()
    hook_scores = [{"id": f"h{i}", "score": score} for i, score in enumerate([60, 95, 70, 90, 65, 55], 1)]
    responses = [
        {"hooks": hooks},
        {"scores": hook_scores},
        {"text": "본문 h2", "self_check": {}},
        {"text": "본문 h4", "self_check": {}},
        {"scores": [{"id": "d1", "score": 82}, {"id": "d2", "score": 96}]},
    ]
    cfg = {"openrouter": {"model": "google/gemini-3.7-flash", "critic_model": "google/gemini-3.7-flash"}}
    product = {"country": "KR", "product_key": "p1", "formfactor_id": "ff-1", "ux_grade": "novel", "link": "https://example.com"}
    with patch.object(generate, "_gemini", side_effect=responses) as gemini, \
         patch("common.recent_context", return_value={"recent_posts": [], "my_recent_replies": [], "recent_comments_received": [], "my_bio": ""}), \
         patch.object(generate, "load_skill", return_value="writer-system"), \
         patch("post_check.check_post", return_value={"verdict": "PASS", "format_score": 100, "risk_notes": []}):
        winner = generate.make_sales_post(cfg, {"hooks": ["legacy"]}, product)
    assert gemini.call_count == 5
    assert winner["text"] == "본문 h4"
    assert winner["hook_id"] == "h4"
    assert winner["hook_family"] == "F4"
    assert winner["angle_id"] == "angle-4"
    assert winner["writer_variant"] == "d2"
    writer_systems = [call.args[1] for call in gemini.call_args_list if "MANDATORY DRAFT GATE" in call.args[1]]
    writer_payloads = [call.args[2] for call in gemini.call_args_list if "MANDATORY DRAFT GATE" in call.args[1]]
    assert len(writer_systems) == 2
    assert all("first line must be 70 characters or fewer" in system for system in writer_systems)
    assert all("quotation marks only for an exact stored review quote" in system for system in writer_systems)
    assert all(payload["product_evidence"] == product for payload in writer_payloads)
    assert all("facts absent from product_evidence are forbidden" in payload["evidence_contract"] for payload in writer_payloads)
    assert all("skip if must mention the exact label or fractionated coconut oil" in payload["evidence_contract"] for payload in writer_payloads)


def test_metrics_contract_reports_missing_attribution_fields():
    gaps = analytics.attribution_gaps({"country": "KR", "post_type": "sales"})
    assert {
        "hook_family", "angle_id", "product_id", "formfactor_id",
        "ux_grade", "writer_variant",
    } <= set(gaps)
    complete = {
        "hook_family": "F1", "angle_id": "price-reversal", "product_id": "p1",
        "formfactor_id": "ff1", "ux_grade": "novel", "country": "KR",
        "post_type": "sales", "writer_variant": "d2",
        "friction_id": "fr-1", "stage": "verdict", "mechanism": "front_open",
        "price_band": "20k", "affiliate_destination": "coupang", "market": "KR",
        "source_pointers": ["review:r1"],
    }
    assert analytics.attribution_gaps(complete) == []


def _promotion_summary(measured=True):
    winner = {
        "posts": 5,
        "attributed_posts": 5,
        "clicks_measured_posts": 5 if measured else 0,
        "clicks": 12 if measured else None,
        "ctr": 0.04 if measured else None,
    }
    baseline = {
        "posts": 5,
        "attributed_posts": 5,
        "clicks_measured_posts": 5 if measured else 0,
        "clicks": 6 if measured else None,
        "ctr": 0.02 if measured else None,
    }
    return {"posts_total": 10, "by_hook_pattern": {"F1": winner, "F2": baseline}}


def test_playbook_promotion_requires_attributed_click_or_conversion_data():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _queue_cfg(tmp)
        playbook = Path(tmp, "playbook.md")
        playbook.write_text("기존 플레이북", encoding="utf-8")
        promoted = improve.promote_playbook_candidate(
            cfg, "새 플레이북", _promotion_summary(measured=False), regressions_passed=True
        )
        assert promoted is False
        assert playbook.read_text(encoding="utf-8") == "기존 플레이북"


def test_playbook_promotion_snapshots_and_rollback_restores_previous():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _queue_cfg(tmp)
        playbook = Path(tmp, "playbook.md")
        playbook.write_text("기존 플레이북", encoding="utf-8")
        assert improve.promote_playbook_candidate(
            cfg, "검증된 새 플레이북", _promotion_summary(), regressions_passed=True
        ) is True
        assert playbook.read_text(encoding="utf-8") == "검증된 새 플레이북"
        assert Path(tmp, "playbook.previous.md").read_text(encoding="utf-8") == "기존 플레이북"
        assert improve.rollback_playbook(cfg) is True
        assert playbook.read_text(encoding="utf-8") == "기존 플레이북"


def test_kr_publication_receives_tournament_attribution():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"mode": {"kr_link_mode": "direct"}, "paths": {"state_dir": tmp}}
        product = {
            "product_key": "p1", "country": "KR", "category": "posture",
            "formfactor_id": "ff1", "ux_grade": "novel", "sub_id": "hc-p1",
            "link": "https://example.com/p1",
        }
        tournament = {
            "text": "한국어 본문", "hook_family": "F2", "angle_id": "angle-2",
            "writer_variant": "d2", "viral_score": 94,
        }
        with patch.object(run.sourcing, "pick", return_value=product), \
             patch.object(run.generate, "make_master", return_value={}), \
             patch.object(run.generate, "make_sales_post", return_value=tournament), \
             patch.object(run, "_publish_with_retry") as publish_with_retry:
            run._kr_sales(cfg, "", False)
            publish_with_retry.call_args.args[1]()
        meta = publish_with_retry.call_args.kwargs["meta_extra"]
        assert meta["hook_family"] == "F2"
        assert meta["angle_id"] == "angle-2"
        assert meta["product_id"] == "p1"
        assert meta["formfactor_id"] == "ff1"
        assert meta["writer_variant"] == "d2"


def test_weekly_run_preserves_active_playbook_when_sample_is_not_ready():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"paths": {"state_dir": tmp}, "threads": {}}
        playbook = Path(tmp, "playbook.md")
        playbook.write_text("검증된 기존 플레이북", encoding="utf-8")
        audit = {"formfactors_total": 0, "proven_active": 0, "novel_active": 0,
                 "candidates": 0, "new_this_week": [], "retired": [], "alerts": []}
        with patch.object(improve.analytics, "weekly_summary", return_value={"posts_total": 1}), \
             patch.object(improve.sourcing, "update_ux_stats", return_value=audit), \
             patch.object(improve.publish, "refresh_token", return_value=None):
            active = improve.run(cfg, dry_run=False)
        assert active == "검증된 기존 플레이북"
        assert playbook.read_text(encoding="utf-8") == "검증된 기존 플레이북"


def test_weekly_dry_run_never_overwrites_operational_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"paths": {"state_dir": tmp}, "threads": {}}
        playbook = Path(tmp, "playbook.md")
        report = Path(tmp, "weekly_report.md")
        playbook.write_text("실운영 플레이북", encoding="utf-8")
        report.write_text("실운영 리포트", encoding="utf-8")
        with patch.object(improve.analytics, "weekly_summary") as summary, \
             patch.object(improve.sourcing, "update_ux_stats") as update:
            active = improve.run(cfg, dry_run=True)
        assert active == "실운영 플레이북"
        assert playbook.read_text(encoding="utf-8") == "실운영 플레이북"
        assert report.read_text(encoding="utf-8") == "실운영 리포트"
        summary.assert_not_called()
        update.assert_not_called()
