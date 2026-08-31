import json
from pathlib import Path

import analytics
import generate
import generation_ssot
import run
import sourcing
import viral_intelligence


def _cfg(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return {
        "paths": {"state_dir": str(state)},
        "mode": {"_rehearsal": True, "auto_publish_clean": True, "publish": False},
        "coupang": {"sub_id_prefix": "hc", "access_key": "", "secret_key": ""},
    }


def _friction(fid="fr-1", market="KR"):
    return {
        "friction_id": fid, "market": market, "domain": "storage",
        "source_type": "external_complaint", "source_pointer": "https://source.test/1",
        "verbatim": "아래 장난감을 꺼낼 때마다 전부 쏟아요", "recurrence": 5,
        "intensity": 4, "mechanisms": ["front_open"], "lifecycle": "validated",
        "sourcing_keyword": "앞으로 여는 장난감 수납함", "is_food": False,
    }


def _scored_product(fid="fr-1"):
    return {
        "product_key": "approved-kr-1", "approved_product_id": "approved-kr-1",
        "product_name": "앞으로 여는 수납함", "country": "KR", "friction_id": fid,
        "source_pointers": ["https://source.test/1", "review:r1"],
        "scores": {name: 3 for name in sourcing.CANDIDATE_SCORE_FIELDS},
        "wrong_purchase_reversible": True,
        "mechanism": "front_open", "failure_mode": "weak_latch",
        "skip_if": "선반 깊이가 얕은 집", "link": "https://link.test/hc-fr-1",
        "sub_id": "hc-fr-1", "price_info": "20000", "category": "storage",
        "review_provenance": [{"review_id": "r1", "quote": "잠금이 약함", "source_url": "https://review.test/r1", "original_location": "review"}],
    }


def test_top_up_requests_reads_only_validated_friction_ledger(tmp_path):
    cfg = _cfg(tmp_path)
    ledger = Path(cfg["paths"]["state_dir"]) / "friction_signals.jsonl"
    ledger.write_text(json.dumps(_friction(), ensure_ascii=False) + "\n", encoding="utf-8")
    assert sourcing.top_up_requests(cfg, buffer_target=1) == 1
    request = json.loads((Path(cfg["paths"]["state_dir"]) / "browser-queue/requests.json").read_text())[0]
    assert request["friction_id"] == "fr-1"
    assert request["source_pointers"] == ["https://source.test/1"]
    assert request["lane"] == "friction"


def test_top_up_requests_does_not_create_category_or_discovery_request_without_friction(tmp_path):
    cfg = _cfg(tmp_path)
    assert sourcing.top_up_requests(cfg, buffer_target=1) == 0


def test_every_product_selection_entry_point_fails_closed_without_score_and_friction(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    state = Path(cfg["paths"]["state_dir"])
    (state / "manual_products.json").write_text(json.dumps([{"product_key": "legacy", "product_name": "legacy"}]), encoding="utf-8")
    monkeypatch.setattr(sourcing, "search_products", lambda *_: [{"productId": "api", "productName": "api"}])
    assert sourcing.pick(cfg) is None


def test_publication_gate_validates_candidate_and_propagates_metadata(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    seen = {}
    monkeypatch.setattr(run.post_check, "check_post", lambda _: {"format_score": 100, "risk_notes": [], "format_tips": [], "verdict": "PASS"})
    monkeypatch.setattr(run.publish, "publish_text", lambda *a, **kw: seen.update(kw) or "PREVIEW-1")
    candidate = {"text": "장난감 통을 매번 뒤집는 데 5분", "friction_id": "fr-1", "stage": "discovery", "market": "KR", "source_pointers": ["https://source.test/1"]}
    media, reason = run._gate_and_publish(cfg, candidate["text"], "KR", "friction", dry_run=False, candidate=candidate)
    assert (media, reason) == ("PREVIEW-1", "published")
    assert seen["meta"]["friction_id"] == "fr-1"
    assert seen["meta"]["source_pointers"] == ["https://source.test/1"]
    bad = {**candidate, "text": "제품 https://shop.test"}
    assert run._gate_and_publish(cfg, bad["text"], "KR", "friction", candidate=bad)[1] == "candidate_fail"


def test_thread_gate_validates_each_part_with_shared_friction_metadata(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(run.post_check, "check_post", lambda _: {"risk_notes": [], "verdict": "PASS"})
    monkeypatch.setattr(run.publish, "publish_text", lambda *a, **kw: "PREVIEW-1")
    candidate = {"friction_id": "fr-1", "stage": "discovery", "market": "KR", "source_pointers": ["https://source.test/1"]}
    assert run._publish_thread(cfg, ["장난감 정리에 5분", "통을 비우는 동작이 반복됩니다"], "KR", candidate=candidate)[1] == "published"
    assert run._publish_thread(cfg, ["장난감 정리에 5분", "제품 https://shop.test"], "KR", candidate=candidate)[1] == "candidate_fail"


def test_weekly_summary_uses_revenue_hierarchy_and_requires_friction_attribution(tmp_path):
    cfg = _cfg(tmp_path)
    rows = [
        {"media_id": "viral", "country": "KR", "post_type": "friction", "friction_id": "fr-v", "stage": "discovery", "mechanism": "front", "price_band": "none", "affiliate_destination": "none", "hook_family": "scene", "angle_id": "scene", "product_id": "none", "formfactor_id": "front", "ux_grade": "observed", "writer_variant": "v1", "insights": {"views": 999999}, "link_clicks": 0},
        {"media_id": "sale", "country": "KR", "post_type": "friction", "friction_id": "fr-s", "stage": "verdict", "mechanism": "front", "price_band": "20k", "affiliate_destination": "coupang", "hook_family": "scene", "angle_id": "scene", "product_id": "p", "formfactor_id": "front", "ux_grade": "observed", "writer_variant": "v2", "insights": {"views": 10}, "link_clicks": 1, "orders": 1, "commission": 2.0},
    ]
    (Path(cfg["paths"]["state_dir"]) / "metrics.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    summary = analytics.weekly_summary(cfg)
    assert summary["top_posts"][0]["media_id"] == "sale"
    assert summary["friction_funnel"]["revenue_winner"]["friction_id"] == "fr-s"
    assert analytics.attribution_gaps({"country": "KR", "post_type": "friction"})


def test_authoritative_generation_rejects_episode_and_atom_as_friction():
    try:
        generation_ssot.resolve_inputs(Path(__file__).parents[1], "value_post", ["episode:E01"])
    except ValueError:
        pass
    else:
        raise AssertionError("retired episode resolver remains active")
    cfg = {"mode": {}}
    try:
        generate.make_value_post(cfg, "info", dry_run=True, country="KR", input_ids=["atom:a1"])
    except ValueError:
        pass
    else:
        raise AssertionError("atom was relabeled as friction")


def test_hook_critic_is_blind_to_angle_metadata():
    hooks = [{"id": f"h{i}", "text": f"hook {i}", "hook_family": "scene", "angle_id": "secret"} for i in range(1, 7)]
    assert viral_intelligence.build_hook_critic_payload(hooks) == {"hooks": [{"id": f"h{i}", "text": f"hook {i}"} for i in range(1, 7)]}
