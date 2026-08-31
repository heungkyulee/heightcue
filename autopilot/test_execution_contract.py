#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""통합 실행 계약 회귀 — 모델·의도·프롬프트 provenance의 단일 정본."""
import json
import os
import sys
import tempfile
import threading
import subprocess
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_contract as ec


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "context").mkdir()
        (self.root / "context" / "user-intent-contract.md").write_text("매출이 최상위 KPI", encoding="utf-8")
        (self.root / "context" / "compliance.md").write_text("광고 고지", encoding="utf-8")
        (self.root / "context" / "persona.md").write_text("HeightCue 운영자", encoding="utf-8")
        (self.root / "context" / "voice-kr.md").write_text("덤덤한 존댓말", encoding="utf-8")
        (self.root / "heightcue-gemini-skills.md").write_text("## SKILL A5\nreply", encoding="utf-8")
        self.manifest = {
            "schema_version": 1,
            "contract_id": "heightcue-content-v1",
            "owner_profile": "jaehyun-publisher",
            "execution_mode": "script_only",
            "business_kpi": "affiliate revenue",
            "intent_source": "context/user-intent-contract.md",
            "prompt_sources": ["context/voice-kr.md", "heightcue-gemini-skills.md"],
            "model_source": "runtime_config:openrouter.model",
            "validator": "post_check.check_post",
            "publisher": "publish.publish_text",
            "tasks": ["sales_post", "value_post", "value_thread", "comment_reply"],
            "countries": ["KR", "US"],
        }
        (self.root / "context" / "execution-contract.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")
        self.cfg = {
            "_testing": True,
            "openrouter": {"model": "google/gemini-runtime", "api_key": "SECRET-DO-NOT-RECORD"},
            "paths": {"contract_manifest": "context/execution-contract.json"},
            "_project_root": str(self.root),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _value_meta(self, text="한국어 실발행 본문"):
        import generate
        from unittest.mock import patch
        with patch.object(generate, "load_skill", return_value="V1"), \
             patch("common.recent_context", return_value={
                 "recent_posts": [], "my_recent_replies": [],
                 "recent_comments_received": [], "my_bio": "",
                 "account_memory": {}}), \
             patch.object(generate, "_gemini", side_effect=[
                 {"text": text}, {"text": text + " 다른 후보"},
                 {"scores": [{"id": "v1", "score": 90}, {"id": "v2", "score": 80}]}
             ]):
            result = generate.make_value_post(
                self.cfg, "story", topic="x", country="KR", candidates=2,
                input_ids=["friction:test-friction"])
        return ec.merge_provenance({"post_type": "value"}, result["_provenance"])

    def test_contract_does_not_claim_critic_before_observed_success(self):
        self.cfg["openrouter"]["critic_model"] = "google/critic-runtime"
        provenance = ec.generation_provenance(
            ec.build_context(self.cfg, "value_post", "KR"),
            input_ids=["friction:test-friction"])
        self.assertEqual(provenance["model"], "google/gemini-runtime")
        self.assertNotIn("critic_model", provenance)
        self.assertEqual(provenance["critic_status"], "not_run")

    def test_content_importer_cannot_claim_or_call_attestor(self):
        self.assertFalse(hasattr(ec, "claim_generation_attestor"))
        self.assertFalse(any("seal" in name.lower() or "attestor" in name.lower()
                             for name in ec.__dict__ if not name.startswith("__")))
        probe_env = dict(os.environ)
        probe_env.pop("PYTEST_CURRENT_TEST", None)
        probe = subprocess.run(
            [sys.executable, "-c", (
                "import execution_contract as e\n"
                "for name in ('_BOOTSTRAP_GENERATION', '_BIND_GENERATED'):\n"
                "  assert not hasattr(e, name)\n"
                "fake = {'text': 'forged'}\n"
                "try: e._bind_generated_result(fake, {})\n"
                "except e.ContractError: pass\n"
                "else: raise SystemExit('forged receipt')\n"
                "import generate\n"
                "print('blocked')")],
            cwd=os.path.dirname(__file__), env=probe_env, text=True, capture_output=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout.strip(), "blocked")

    def test_content_importer_cannot_mint_receipt_via_generate_exports(self):
        probe = subprocess.run(
            [sys.executable, "-c", (
                "import generate, execution_contract as e\n"
                "assert not hasattr(generate, '_generated')\n"
                "danger = [n for n, v in vars(generate).items() "
                "if callable(v) and ('receipt' in n.lower() or 'attest' in n.lower() "
                "or 'bind' in n.lower() or 'generated' in n.lower())]\n"
                "assert not danger, danger\n"
                "print('blocked')")],
            cwd=os.path.dirname(__file__), text=True, capture_output=True)
        self.assertEqual(probe.returncode, 0, probe.stderr or probe.stdout)
        self.assertEqual(probe.stdout.strip(), "blocked")

    def test_compile_forged_generate_filename_cannot_mint_receipt(self):
        probe = subprocess.run(
            [sys.executable, "-c", (
                "import execution_contract as e, os\n"
                "src=\"def _bind_generated_result():\\n return e._BIND_GENERATED({}, {'text':'FORGED'})\\nresult=_bind_generated_result()\"\n"
                "try: exec(compile(src, os.path.join(os.path.dirname(e.__file__), 'generate.py'), 'exec'), {'e':e, '__name__':'generate'})\n"
                "except (AttributeError, e.ContractError): print('blocked')\n"
                "else: raise SystemExit('forged receipt')")],
            cwd=os.path.dirname(__file__), text=True, capture_output=True)
        self.assertEqual(probe.returncode, 0, probe.stderr or probe.stdout)
        self.assertEqual(probe.stdout.strip(), "blocked")

    def test_reservation_transition_table_rejects_illegal_missing_and_non_owner(self):
        import publish
        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        record = {"country": "KR", "meta": {}}
        provenance = {"task": "value_post", "input_ids": ["friction:f1"]}
        owner = publish._reserve_publication(self.cfg, record, provenance, "본문")
        self.assertIsInstance(owner, str)
        key = record["meta"]["idempotency_key"]
        with self.assertRaises(publish.PublicationStateError):
            publish._transition_publication(self.cfg, "missing", "reserved", "creating", owner)
        with self.assertRaises(publish.PublicationStateError):
            publish._transition_publication(self.cfg, key, "reserved", "verified", owner)
        with self.assertRaises(publish.PublicationStateError):
            publish._transition_publication(self.cfg, key, "reserved", "creating", "not-owner")
        with self.assertRaises(publish.PublicationStateError):
            publish._transition_publication(self.cfg, key, "reserved", "unknown", owner)

    def test_all_declared_task_input_schemas_fail_closed(self):
        valid = {
            "sales_master": ["product:p1"], "sales_hooks": ["product:p1"],
            "sales_post": ["product:p1"], "value_post": ["friction:f1"],
            "value_thread": ["friction:t1"],
            "comment_reply": ["comment:c1", "post:p1"],
        }
        for task in self.manifest["tasks"]:
            packet = ec.build_context(self.cfg, task, "KR")
            with self.subTest(task=task, case="valid"):
                ec.generation_provenance(packet, valid[task])
            for bad in ([], ["wrong:x"], valid[task] + ["extra:x"]):
                with self.subTest(task=task, bad=bad), self.assertRaises(ec.ContractError):
                    ec.generation_provenance(packet, bad)

    def test_manifest_declared_unknown_task_is_rejected_by_total_registry(self):
        self.manifest["tasks"].append("new_declared_task")
        (self.root / "context" / "execution-contract.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ec.ContractError, "unsupported.*new_declared_task"):
            ec.load_manifest(self.cfg)

    def test_verified_critic_model_is_bound_and_fallback_omits_it(self):
        import generate
        from unittest.mock import patch
        verdict = {"scores": [{"id": "v1", "score": 90}, {"id": "v2", "score": 80}]}
        calls = iter([{"text": "본문"}, {"text": "다른 본문"}, verdict])
        with patch.object(generate, "load_skill", return_value="V1"), \
             patch("common.recent_context", return_value={"recent_posts": [], "my_recent_replies": [], "recent_comments_received": [], "my_bio": "", "account_memory": {}}), \
             patch.object(generate, "_gemini", side_effect=lambda *a, **k: next(calls)):
            result = generate.make_value_post(self.cfg, "story", topic="x", country="KR", candidates=2, input_ids=["friction:f1"])
        verified = result["_provenance"]
        self.assertEqual(verified["critic_status"], "verified")
        self.assertEqual(verified["critic_model"], "google/gemini-runtime")
        ec.validate_provenance(self.cfg, verified, "KR", "본문")

    def test_value_critic_requires_exact_unique_finite_scores_and_winner_agreement(self):
        import generate
        drafts = [{"id": "v1", "text": "one"}, {"id": "v2", "text": "two"}]
        invalid = (
            [{"id": "v1", "score": 90}],
            [{"id": "v1", "score": 90}, {"id": "unknown", "score": 80}],
            [{"id": "v1", "score": 90}, {"id": "v1", "score": 80}],
            [{"id": "v1", "score": float("nan")}, {"id": "v2", "score": 80}],
            [{"id": "v1", "score": True}, {"id": "v2", "score": 80}],
            [{"id": "v1", "score": 90}, {"score": 80}],
        )
        for scores in invalid:
            with self.subTest(scores=scores):
                self.assertIsNone(generate._validated_critic_scores(drafts, scores))
        valid = generate._validated_critic_scores(
            drafts, [{"id": "v1", "score": 90}, {"id": "v2", "score": 80}])
        self.assertEqual(valid, {"v1": 90.0, "v2": 80.0})
        self.assertTrue(generate._critic_winner_agrees(drafts[0], valid))
        self.assertFalse(generate._critic_winner_agrees(drafts[1], valid))

    def test_reservation_is_single_winner_under_real_threads(self):
        import publish
        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        provenance = {"task": "value_post", "input_ids": ["friction:f1"]}
        barrier = threading.Barrier(8)
        results = []
        def attempt():
            record = {"country": "KR", "meta": {}}
            barrier.wait()
            results.append(publish._reserve_publication(
                self.cfg, record, provenance, "동일 본문"))
        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(isinstance(item, str) for item in results), 1)

    def test_crash_recovery_releases_only_pre_submit(self):
        import publish
        from common import append_jsonl, read_jsonl, state_path
        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        ledger = state_path(self.cfg, "publication_reservations.jsonl")
        append_jsonl(ledger, {"idempotency_key": "safe", "status": "reserved", "owner_pid": 99999999})
        append_jsonl(ledger, {"idempotency_key": "uncertain", "status": "submitting", "owner_pid": 99999999})
        publish.recover_publication_reservations(self.cfg)
        latest = publish._reservation_latest(read_jsonl(ledger))
        self.assertEqual(latest["safe"]["status"], "released")
        self.assertEqual(latest["uncertain"]["status"], "verification_pending")

    def test_recovery_preserves_live_owner_then_released_key_can_be_acquired(self):
        import publish
        from common import append_jsonl, read_jsonl, state_path
        from unittest.mock import patch
        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        ledger = state_path(self.cfg, "publication_reservations.jsonl")
        append_jsonl(ledger, {"idempotency_key": "live", "status": "reserved", "owner_pid": os.getpid()})
        self.assertEqual(publish.recover_publication_reservations(self.cfg), [])
        self.assertEqual(publish._reservation_latest(read_jsonl(ledger))["live"]["status"], "reserved")

        record = {"country": "KR", "meta": {}}
        provenance = {"task": "value_post", "input_ids": ["friction:f1"]}
        owner = publish._reserve_publication(self.cfg, record, provenance, "retry text")
        self.assertIsInstance(owner, str)
        key = record["meta"]["idempotency_key"]
        publish._transition_publication(self.cfg, key, "reserved", "released", owner)
        self.assertTrue(publish._reserve_publication(
            self.cfg, {"country": "KR", "meta": {}}, provenance, "retry text"))

    def test_runtime_model_is_authoritative(self):
        packet = ec.build_context(self.cfg, "comment_reply", "KR",
                                  runtime_context={"bot_model": "google/gemini-bot"})
        self.assertEqual(packet["model"], "google/gemini-runtime")
        self.assertEqual(packet["owner_profile"], "jaehyun-publisher")
        self.assertEqual(packet["execution_mode"], "script_only")

    def test_packet_has_deterministic_source_hashes(self):
        a = ec.build_context(self.cfg, "value_post", "KR")
        b = ec.build_context(self.cfg, "value_post", "KR")
        self.assertEqual(a["source_hashes"], b["source_hashes"])
        self.assertEqual(set(a["source_hashes"]), {
            "context/user-intent-contract.md", "context/voice-kr.md",
            "heightcue-gemini-skills.md"})
        self.assertTrue(all(len(v) == 64 for v in a["source_hashes"].values()))

    def test_provenance_is_compact_and_excludes_secrets_and_private_text(self):
        packet = ec.build_context(self.cfg, "comment_reply", "KR",
                                  runtime_context={"comment": "private message body"})
        provenance = ec.generation_provenance(
            packet, input_ids=["comment:comment-42", "post:post-1"])
        rendered = json.dumps(provenance)
        self.assertIn("comment-42", rendered)
        self.assertNotIn("SECRET-DO-NOT-RECORD", rendered)
        self.assertNotIn("private message body", rendered)
        self.assertNotIn("api_key", rendered)

    def test_canonical_provenance_wins_over_caller_metadata(self):
        packet = ec.build_context(self.cfg, "sales_post", "US")
        provenance = ec.generation_provenance(packet, input_ids=["product:test-product"])
        merged = ec.merge_provenance(
            {"post_type": "sales", "execution_contract": {"model": "forged"}},
            provenance,
        )
        self.assertEqual(merged["post_type"], "sales")
        self.assertEqual(merged["execution_contract"]["model"], "google/gemini-runtime")

    def test_missing_source_fails_closed(self):
        (self.root / "context" / "voice-kr.md").unlink()
        with self.assertRaisesRegex(ec.ContractError, "voice-kr.md"):
            ec.build_context(self.cfg, "value_post", "KR")

    def test_manifest_cannot_redefine_code_owned_execution_identity(self):
        self.manifest["owner_profile"] = "forged-bot"
        (self.root / "context" / "execution-contract.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ec.ContractError, "owner_profile"):
            ec.load_manifest(self.cfg)

    def test_active_runtime_rejects_forged_provenance(self):
        provenance = self._value_meta("본문")["execution_contract"]
        ec.validate_provenance(self.cfg, provenance, country="KR", text="본문")
        for key, forged in (
            ("model", "other-model"),
            ("country", "US"),
            ("task", "undeclared"),
            ("source_hashes", {"x": "0" * 64}),
        ):
            bad = {**provenance, key: forged}
            with self.subTest(key=key), self.assertRaises(ec.ContractError):
                ec.validate_provenance(self.cfg, bad, country="KR")

    def test_validate_runtime_covers_every_declared_task(self):
        summary = ec.validate_runtime(self.cfg)
        self.assertEqual(set(summary["tasks"]), set(self.manifest["tasks"]))
        self.assertEqual(summary["model"], "google/gemini-runtime")

    def test_missing_model_fails_closed(self):
        self.cfg["openrouter"].pop("model")
        with self.assertRaisesRegex(ec.ContractError, "model"):
            ec.build_context(self.cfg, "value_post", "KR")

    def test_load_skill_injects_user_intent_source_into_model_prompt(self):
        import common
        from unittest.mock import patch

        cfg = {"paths": {"skills": "../heightcue-gemini-skills.md"}}
        with patch.object(common, "BASE", str(self.root / "autopilot")):
            (self.root / "autopilot").mkdir()
            prompt = common.load_skill(cfg, "A5", country="KR")
        self.assertIn("매출이 최상위 KPI", prompt)
        self.assertIn("덤덤한 존댓말", prompt)

    def test_reply_generation_carries_shared_contract_in_and_out(self):
        import generate
        from unittest.mock import patch

        seen = {}

        def fake_gemini(cfg, system, payload):
            seen.update(payload)
            return {"action": "reply", "text": "감사합니다.", "reason": "ok"}

        with patch.object(generate, "load_skill", return_value="A5"), \
             patch.object(generate, "_gemini", side_effect=fake_gemini):
            result = generate.make_reply(
                self.cfg, comment="비공개 댓글 본문", post_summary="원글",
                post_type="value", story_facts=[], country="KR",
                input_ids=["comment:17884339329479178", "post:17900326344567271"])

        self.assertEqual(seen["execution_contract"]["task"], "comment_reply")
        self.assertEqual(result["_provenance"]["model"], "google/gemini-runtime")
        self.assertEqual(result["_provenance"]["input_ids"],
                         ["comment:17884339329479178", "post:17900326344567271"])
        rendered = json.dumps(result["_provenance"], ensure_ascii=False)
        self.assertNotIn("비공개 댓글 본문", rendered)

    def test_live_publish_without_execution_contract_is_held_before_api_call(self):
        import publish
        from unittest.mock import patch

        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        self.cfg["mode"] = {"publish": True}
        self.cfg["threads"] = {"kr_user_id": "u1", "kr_access_token": "token"}
        with patch.object(publish.requests, "post") as post:
            media = publish.publish_text(
                self.cfg, "KR", "한국어 실발행 본문", meta={"post_type": "value"})
        self.assertIsNone(media)
        post.assert_not_called()
        hold = json.loads((self.root / "state" / "holdbox.jsonl").read_text().splitlines()[-1])
        self.assertEqual(hold["why"], "execution_contract_invalid")

    def test_live_publish_readback_retries_eventual_consistency(self):
        import publish
        from unittest.mock import Mock, patch

        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        self.cfg["mode"] = {"publish": True}
        self.cfg["threads"] = {"kr_user_id": "u1", "kr_access_token": "token"}
        create, sent = Mock(), Mock()
        create.raise_for_status.return_value = sent.raise_for_status.return_value = None
        create.json.return_value = {"id": "creation-1"}
        sent.json.return_value = {"id": "media-1"}
        stale, current = Mock(), Mock()
        stale.raise_for_status.return_value = current.raise_for_status.return_value = None
        stale.json.return_value = {"id": "media-1", "text": ""}
        current.json.return_value = {"id": "media-1", "text": "한국어 실발행 본문"}

        meta = self._value_meta()
        with patch.object(publish.requests, "post", side_effect=[create, sent]), \
             patch.object(publish.requests, "get", side_effect=[stale, current, current]) as get, \
             patch.object(publish.time, "sleep", return_value=None):
            media = publish.publish_text(
                self.cfg, "KR", "한국어 실발행 본문", meta=meta)
        self.assertEqual(media, "media-1")
        self.assertEqual(get.call_count, 3)

    def test_definite_create_rejection_releases_reservation_and_allows_retry(self):
        import publish
        from common import read_jsonl, state_path
        from unittest.mock import Mock, patch
        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        self.cfg["mode"] = {"publish": True}
        self.cfg["threads"] = {"kr_user_id": "u1", "kr_access_token": "token"}
        rejected = Mock()
        rejected.raise_for_status.side_effect = publish.requests.HTTPError("400 definite")
        created, sent, readback = Mock(), Mock(), Mock()
        created.raise_for_status.return_value = sent.raise_for_status.return_value = None
        created.json.return_value = {"id": "creation-2"}
        sent.json.return_value = {"id": "media-2"}
        readback.raise_for_status.return_value = None
        readback.json.return_value = {"id": "media-2", "text": "한국어 실발행 본문"}
        meta = self._value_meta()

        with patch.object(publish.requests, "post", side_effect=[rejected, created, sent]), \
             patch.object(publish.requests, "get", return_value=readback), \
             patch.object(publish.time, "sleep", return_value=None):
            with self.assertRaises(publish.requests.HTTPError):
                publish.publish_text(self.cfg, "KR", "한국어 실발행 본문", meta=meta)
            media = publish.publish_text(self.cfg, "KR", "한국어 실발행 본문", meta=meta)

        self.assertEqual(media, "media-2")
        latest = publish._reservation_latest(read_jsonl(
            state_path(self.cfg, "publication_reservations.jsonl")))
        row = next(iter(latest.values()))
        self.assertEqual(row["status"], "verified")

    def test_unverified_live_publish_is_recorded_as_pending_to_prevent_duplicate(self):
        import publish
        from unittest.mock import Mock, patch

        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        self.cfg["mode"] = {"publish": True}
        self.cfg["threads"] = {"kr_user_id": "u1", "kr_access_token": "token"}
        create, sent, stale = Mock(), Mock(), Mock()
        create.raise_for_status.return_value = sent.raise_for_status.return_value = None
        stale.raise_for_status.return_value = None
        create.json.return_value = {"id": "creation-1"}
        sent.json.return_value = {"id": "media-uncertain"}
        stale.json.return_value = {"id": "media-uncertain", "text": ""}
        meta = self._value_meta()

        with patch.object(publish.requests, "post", side_effect=[create, sent]) as post, \
             patch.object(publish.requests, "get", return_value=stale), \
             patch.object(publish.time, "sleep", return_value=None):
            with self.assertRaises(publish.PublicationVerificationError):
                publish.publish_text(self.cfg, "KR", "한국어 실발행 본문", meta=meta)
            second = publish.publish_text(
                self.cfg, "KR", "한국어 실발행 본문", meta=meta)

        self.assertIsNone(second)
        self.assertEqual(post.call_count, 2)
        row = json.loads((self.root / "state" / "published.jsonl").read_text().splitlines()[-1])
        self.assertEqual(row["media_id"], "media-uncertain")
        self.assertEqual(row["meta"]["publish_status"], "verification_pending")

    def test_threads_readback_allows_only_platform_hashtag_marker_normalization(self):
        import publish
        local="Why measure 8 drops? #ad\n\nBody stays exact."
        remote="Why measure 8 drops? ad\n\nBody stays exact."
        self.assertTrue(publish._threads_text_matches(local,remote))
        self.assertFalse(publish._threads_text_matches(local,remote.replace('Body','Changed')))
        self.assertFalse(publish._threads_text_matches('price #10','price 10'))

    def test_live_publish_is_verified_by_readback_before_success_is_recorded(self):
        import publish
        from unittest.mock import Mock, patch

        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        self.cfg["mode"] = {"publish": True}
        self.cfg["threads"] = {"kr_user_id": "u1", "kr_access_token": "token"}
        create = Mock()
        create.raise_for_status.return_value = None
        create.json.return_value = {"id": "creation-1"}
        sent = Mock()
        sent.raise_for_status.return_value = None
        sent.json.return_value = {"id": "media-1"}
        readback = Mock()
        readback.raise_for_status.return_value = None
        readback.json.return_value = {"id": "media-1", "text": "한국어 실발행 본문"}

        meta = self._value_meta()
        with patch.object(publish.requests, "post", side_effect=[create, sent]), \
             patch.object(publish.requests, "get", return_value=readback), \
             patch.object(publish.time, "sleep", return_value=None):
            media = publish.publish_text(
                self.cfg, "KR", "한국어 실발행 본문", meta=meta)

        self.assertEqual(media, "media-1")
        row = json.loads((self.root / "state" / "published.jsonl").read_text().splitlines()[-1])
        self.assertEqual(row["meta"]["publish_status"], "verified")
        self.assertEqual(row["meta"]["published_media_id"], "media-1")
        self.assertIn("published_at", row["meta"])

    def test_publish_records_distinct_dry_run_status(self):
        import publish
        self.cfg["paths"]["state_dir"] = str(self.root / "state")
        self.cfg["mode"] = {"publish": False}
        packet = ec.build_context(self.cfg, "value_post", "KR")
        meta = ec.merge_provenance(
            {"post_type": "value"},
            ec.generation_provenance(packet, input_ids=["friction:dry-run"]))

        media = publish.publish_text(
            self.cfg, "KR", "한국어 가치글입니다.", dry_run=True, meta=meta)
        self.assertTrue(media.startswith("DRY-"))
        row = json.loads((self.root / "state" / "published.jsonl").read_text().splitlines()[-1])
        self.assertEqual(row["meta"]["publish_status"], "dry_run")
        self.assertEqual(row["meta"]["execution_contract"]["contract_id"],
                         "heightcue-content-v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
