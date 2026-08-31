import generate


def test_stage_contract_rejects_commercial_coupling_and_biography():
    discovery = {"text": "매일 책상 밑 연필 줍는 데 5분", "friction_id": "fr-1", "stage": "discovery", "market": "KR", "source_pointers": ["signal:s1"]}
    assert generate.validate_friction_candidate(discovery) == discovery
    for bad in (
        {**discovery, "text": "브랜드 제품 https://shop.test"},
        {**discovery, "text": "제가 아이를 키워보니 이 제품을 썼어요"},
        {**discovery, "friction_id": ""},
    ):
        try:
            generate.validate_friction_candidate(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid candidate accepted: {bad}")


def test_bridge_and_verdict_have_stage_specific_requirements():
    bridge = {"text": "책을 눈높이까지 올리면 고개 숙이는 각도가 줄어듭니다", "friction_id": "fr-2", "stage": "bridge", "market": "US", "source_pointers": ["signal:s2"]}
    assert generate.validate_friction_candidate(bridge)["stage"] == "bridge"
    verdict = {"text": "#ad\nFront opening removes the restack. Bad reviews report weak latches. Skip if your shelf is shallow. https://heightcue.test/p/1", "friction_id": "fr-2", "stage": "verdict", "market": "US", "source_pointers": ["product:p1", "review:r1"], "mechanism": "front opening", "failure_mode": "weak latches", "skip_if": "shelf is shallow", "attributable_route": "https://heightcue.test/p/1"}
    assert generate.validate_friction_candidate(verdict)["stage"] == "verdict"
    del verdict["failure_mode"]
    try:
        generate.validate_friction_candidate(verdict)
    except ValueError as exc:
        assert "failure_mode" in str(exc)
    else:
        raise AssertionError("incomplete verdict accepted")


def test_value_generation_emits_friction_identity_and_stage():
    cfg = {"mode": {}}
    result = generate.make_value_post(cfg, "info", topic="정리", dry_run=True, country="KR",
                                      input_ids=["friction:fr-9"])
    assert result["friction_id"] == "fr-9"
    assert result["stage"] == "discovery"
    assert result["market"] == "KR"
    assert result["source_pointers"] == ["friction:fr-9"]


def test_blind_critic_payload_sees_text_and_hard_stage_contract_only():
    draft = {"id": "v1", "text": "scene", "angle_used": "price_math", "rationale": "secret", "friction_id": "fr-1", "stage": "discovery", "market": "KR"}
    payload = generate.viral_intelligence.build_value_critic_payload([draft])
    assert payload == {"drafts": [{"id": "v1", "text": "scene", "friction_id": "fr-1", "stage": "discovery", "market": "KR"}]}
