#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path

import outreach_worker


def test_run_once_rotates_two_topics_per_market_and_verifies_each_reserved_reply(tmp_path):
    cfg = {
        "mode": {"publish": True},
        "outreach": {"enabled": True, "publish": True, "queries_per_market_per_run": 2, "max_sources_per_query": 2},
    }
    calls = {"discover": [], "generate": [], "publish": []}

    def discover(market, queries, limit):
        calls["discover"].append((market, list(queries), limit))
        return [{
            "market": market,
            "source_post_id": f"{market}-{index}",
            "source_post_url": f"https://www.threads.com/@parent/post/{market}{index}",
            "source_author": "parent",
            "source_text": f"observed scene {query}",
            "source_published_at": "2026-08-31T10:00:00Z",
            "discovery_query": query,
        } for index, query in enumerate(queries)]

    def generate(source, category, config):
        calls["generate"].append((source["market"], category))
        return {**source, "friction_id": f"fr-{source['source_post_id']}", "reply_text": f"specific help for {source['source_text']}"}

    def publish(ledger, reservation, now):
        calls["publish"].append(reservation["idempotency_key"])
        return {**reservation, "status": "verified"}

    result = outreach_worker.run_once(
        cfg, slot=1, ledger_path=tmp_path / "outreach.jsonl", now="2026-08-31T12:00:00Z",
        discover_fn=discover, generate_fn=generate, publish_fn=publish,
    )
    assert result["verified"] == 4
    assert result["held"] == 0
    assert [call[0] for call in calls["discover"]] == ["KR", "US"]
    assert all(len(call[1]) == 2 and call[2] == 2 for call in calls["discover"])
    assert len(calls["generate"]) == len(calls["publish"]) == 4


def test_run_once_only_discovers_markets_explicitly_enabled_for_the_aside_session(tmp_path):
    cfg = {
        "mode": {"publish": True},
        "outreach": {"enabled": True, "publish": True, "markets": ["KR"], "queries_per_market_per_run": 1, "max_sources_per_query": 1},
    }
    markets = []

    def discover(market, queries, limit):
        markets.append(market)
        return []

    result = outreach_worker.run_once(
        cfg, slot=0, ledger_path=tmp_path / "outreach.jsonl", now="2026-08-31T12:00:00Z",
        discover_fn=discover,
    )
    assert markets == ["KR"]
    assert result["verified"] == 0


def test_cli_run_uses_configured_state_ledger_and_emits_machine_json(tmp_path, capsys):
    cfg = {"paths": {"state_dir": str(tmp_path)}, "mode": {"publish": True}, "outreach": {"enabled": True, "publish": True}}
    seen = {}

    def fake_run(config, slot, ledger_path, now):
        seen.update({"cfg": config, "slot": slot, "ledger_path": ledger_path, "now": now})
        return {"verified": 2, "held": 0, "records": []}

    code = outreach_worker.cli(
        ["run", "2"], cfg_loader=lambda: cfg, run_fn=fake_run,
        now_fn=lambda: "2026-08-31T20:30:00Z",
    )
    assert code == 0
    assert seen["slot"] == 2
    assert str(seen["ledger_path"]).endswith("outreach.jsonl")
    assert json.loads(capsys.readouterr().out)["verified"] == 2


def test_example_config_matches_two_original_posts_and_three_aside_outreach_slots():
    cfg = json.loads((Path(__file__).parent / "config.example.json").read_text(encoding="utf-8"))
    assert cfg["cadence"]["sales_per_day"] == 1
    assert cfg["cadence"]["value_per_day"] == 1
    assert cfg["openrouter"]["model"] == "google/gemini-3.7-flash"
    assert cfg["outreach"] == {
        "enabled": False,
        "publish": False,
        "markets": ["KR", "US"],
        "queries_per_market_per_run": 2,
        "max_sources_per_query": 2,
        "runs_per_day": 3,
    }


def test_crontab_has_three_outreach_slots_and_only_one_original_content_run():
    text = (Path(__file__).parent.parent / "crontab.txt").read_text(encoding="utf-8")
    outreach_lines = [line for line in text.splitlines() if "outreach_worker.py run" in line and not line.startswith("#")]
    assert len(outreach_lines) == 3
    assert {line.split("outreach_worker.py run ", 1)[1].split()[0] for line in outreach_lines} == {"0", "1", "2"}
    assert text.count("heightcue-work.sh daily") == 1
    assert "heightcue-work.sh midday" not in text
    assert "heightcue-work.sh afternoon" not in text
    assert "heightcue-work.sh evening" not in text
