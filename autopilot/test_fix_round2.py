import json
from pathlib import Path
from unittest.mock import patch

import execution_contract as ec
import generation_ssot
import generate
import run
import sourcing


def test_authoritative_worker_returns_friction_stage_metadata_for_discovery_and_bridge(tmp_path):
    root = tmp_path
    (root / "context").mkdir()
    (root / "autopilot/state").mkdir(parents=True)
    for path, body in {
        "context/user-intent-contract.md": "intent",
        "context/compliance.md": "rules",
        "context/persona.md": "editorial",
        "context/voice-kr.md": "voice",
        "context/voice-us.md": "voice-us",
        "heightcue-gemini-skills.md": "skills",
    }.items():
        (root / path).write_text(body, encoding="utf-8")
    manifest = {
        "schema_version": 1, "contract_id": "heightcue-content-v1",
        "owner_profile": "jaehyun-publisher", "execution_mode": "script_only",
        "business_kpi": "revenue", "intent_source": "context/user-intent-contract.md",
        "prompt_sources": ["context/compliance.md", "context/persona.md", "context/voice-kr.md", "context/voice-us.md", "heightcue-gemini-skills.md"],
        "model_source": "runtime_config:openrouter.model", "validator": "post_check.check_post",
        "publisher": "publish.publish_text", "tasks": ["sales_master", "sales_hooks", "sales_post", "value_post", "value_thread", "comment_reply"],
        "countries": ["KR", "US"],
    }
    (root / "context/execution-contract.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "autopilot/config.json").write_text(json.dumps({"openrouter": {"model": "m", "critic_model": "c"}}), encoding="utf-8")
    friction = {"friction_id": "fr-kr-storage", "lifecycle": "validated", "market": "KR", "source_pointer": "rehearsal:kr-storage", "verbatim": "아래 통을 꺼낼 때 위 통을 모두 내린다"}
    (root / "autopilot/state/friction_signals.jsonl").write_text(json.dumps(friction, ensure_ascii=False) + "\n", encoding="utf-8")
    fixture = str(Path(__file__).with_name("generation_test_fixture.py"))
    keys = root / "keys"
    try:
        for stage in ("discovery", "bridge"):
            result = ec.request_authoritative_generation("value_post", "KR", ["friction:fr-kr-storage"], project_root=str(root), key_dir=str(keys), test_fixture_executable=fixture, stage=stage)
            assert result["friction_id"] == "fr-kr-storage"
            assert result["stage"] == stage
            assert result["market"] == "KR"
            assert result["source_pointers"] == ["rehearsal:kr-storage"]
            generate.validate_friction_candidate(result)
    finally:
        ec.stop_generation_service()


def test_authoritative_worker_returns_complete_verdict_metadata(tmp_path):
    product = generation_ssot.REHEARSAL_PRODUCTS["us-front-open-storage"]
    result = {"text": "#ad\nA front-opening bin removes the unstack-and-restack step.\nSkip if: your shelf is too shallow.\nFull breakdown and current listing: https://heightcue.lifoli.co.kr/us/ (paid link)"}
    enriched = __import__("generation_worker").bind_friction_contract("sales_post", "US", [product], result, None)
    assert enriched["stage"] == "verdict"
    for field in ("friction_id", "market", "source_pointers", "mechanism", "failure_mode", "skip_if", "attributable_route", "disclosure"):
        assert enriched[field]
    generate.validate_friction_candidate(enriched)


def test_rehearsal_returns_nonzero_when_daily_records_any_stage_error(tmp_path):
    cfg = {"mode": {"publish": False}, "paths": {"state_dir": str(tmp_path)}}
    def failing_daily(config, dry_run=False):
        from common import record_error
        record_error(config, "bridge_US", RuntimeError("stage failed"))
    with patch.object(run.subprocess, "call", return_value=0), patch.object(run, "daily", side_effect=failing_daily):
        assert run.rehearsal(cfg) == 1


def test_rehearsal_products_are_explicit_complete_fixtures_and_production_has_no_fallback():
    for market, key in (("KR", "kr-front-open-storage"), ("US", "us-front-open-storage")):
        cfg = {"mode": {"_rehearsal": True, "publish": False}}
        product = sourcing.pick(cfg) if market == "KR" else sourcing.pick_us(cfg)
        assert product["product_key"] == key
        assert product["rehearsal_fixture"] is True
        assert product["country"] == market
        assert product["friction_id"]
        assert product["source_pointers"]
        assert sourcing.score_candidate(product)["eligible"]
