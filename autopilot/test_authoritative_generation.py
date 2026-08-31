import json, os, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import execution_contract as ec
import publish
import generation_worker as gw

FIXTURE = str(Path(__file__).with_name("generation_test_fixture.py"))

class AuthoritativeBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.root = Path(self.t.name)
        (self.root/"context").mkdir(); (self.root/"autopilot/state").mkdir(parents=True)
        for p, body in {
            "context/user-intent-contract.md":"intent", "context/compliance.md":"rules",
            "context/persona.md":"persona", "context/voice-kr.md":"voice",
            "context/voice-us.md":"voice-us", "heightcue-gemini-skills.md":"skills"}.items():
            (self.root/p).write_text(body)
        (self.root/"story-bank.md").write_text(
            "### E5. 키 큰 사람들 사이에서\n"
            "남들이 하지 않는 사업을 진행 중이다.\n"
            "* 콘텐츠 각도: 성인이 된 후에도 남는 열등감\n",
            encoding="utf-8")
        manifest={"schema_version":1,"contract_id":"heightcue-content-v1","owner_profile":"jaehyun-publisher","execution_mode":"script_only","business_kpi":"revenue","intent_source":"context/user-intent-contract.md","prompt_sources":["context/compliance.md","context/persona.md","context/voice-kr.md","context/voice-us.md","heightcue-gemini-skills.md"],"model_source":"runtime_config:openrouter.model","validator":"post_check.check_post","publisher":"publish.publish_text","tasks":["sales_master","sales_hooks","sales_post","value_post","value_thread","comment_reply"],"countries":["KR","US"]}
        (self.root/"context/execution-contract.json").write_text(json.dumps(manifest))
        (self.root/"autopilot/config.json").write_text(json.dumps({"openrouter":{"model":"trusted-model", "critic_model":"trusted-critic"}}))
        (self.root/"autopilot/state/insight_atoms.json").write_text(json.dumps([{"atom_id":"a1","fact":"resolved fact"}]))
        (self.root/"autopilot/state/friction_signals.jsonl").write_text(json.dumps({"friction_id":"f1","lifecycle":"validated","market":"KR","source_pointer":"source:f1"})+"\n")
        self.keys=self.root/"keys"

    def call(self, ids=("friction:f1",), fixture=FIXTURE):
        return ec.request_authoritative_generation("value_post","KR",list(ids),project_root=str(self.root),key_dir=str(self.keys),test_fixture_executable=fixture)

    def test_high_level_service_returns_restart_verifiable_ed25519_attestation(self):
        result=self.call(); self.assertEqual(result["text"],"후보 둘")
        att=result["_attestation"]
        self.assertTrue(ec.verify_attestation(att,result,project_root=str(self.root),key_dir=str(self.keys)))
        ec.stop_generation_service(); self.assertTrue(ec.verify_attestation(att,result,project_root=str(self.root),key_dir=str(self.keys)))
        self.assertEqual(att["payload"]["critic_status"],"verified")
        self.assertEqual(att["payload"]["output_digests"], [ec.sha256_text("후보 둘")])
        self.assertIn("input_payload_digest",att["payload"]); self.assertIn("prompt_digest",att["payload"])

    def test_relabel_partial_reordered_and_arbitrary_importer_are_rejected(self):
        r=self.call(); a=r["_attestation"]
        self.assertFalse(ec.verify_attestation(a,{"text":"다른 글"},project_root=str(self.root),key_dir=str(self.keys)))
        bad=json.loads(json.dumps(a)); bad["payload"]["task"]="sales_post"
        self.assertFalse(ec.verify_attestation(bad,r,project_root=str(self.root),key_dir=str(self.keys)))
        thread=ec.request_authoritative_generation("value_thread","KR",["friction:f1"],project_root=str(self.root),key_dir=str(self.keys),test_fixture_executable=FIXTURE)
        self.assertFalse(ec.verify_attestation(thread["_attestation"],{"parts":list(reversed(thread["parts"]))},project_root=str(self.root),key_dir=str(self.keys)))
        probe=subprocess.run([sys.executable,"-c","import generation_worker as w; assert not hasattr(w,'sign') and not hasattr(w,'bind') and not hasattr(w,'handle'); print('blocked')"],cwd=Path(__file__).parent,text=True,capture_output=True)
        self.assertEqual((probe.returncode,probe.stdout.strip()),(0,"blocked"),probe.stderr)

    def test_pending_reconciler_exact_readback_without_repost(self):
        from unittest.mock import Mock, patch
        state=self.root/"autopilot/state"
        row={"idempotency_key":"k1","status":"verification_pending","owner_id":"owner","media_id":"m1","text":"본문","country":"KR"}
        (state/"publication_reservations.jsonl").write_text(json.dumps(row,ensure_ascii=False)+"\n")
        pending_pub={"media_id":"m1","country":"KR","text":"본문","meta":{"publish_status":"verification_pending","idempotency_key":"k1"}}
        (state/"published.jsonl").write_text(json.dumps(pending_pub,ensure_ascii=False)+"\n")
        cfg={"paths":{"state_dir":str(state)},"threads":{"kr_access_token":"tok","kr_user_id":"u"}}
        response=Mock(); response.raise_for_status.return_value=None; response.json.return_value={"id":"m1","text":"본문"}
        with patch.object(publish.requests,"get",return_value=response), patch.object(publish.requests,"post") as post:
            self.assertEqual(publish.reconcile_pending(cfg),{"verified":1,"failed":0,"unchanged":0,"backfilled":0})
        post.assert_not_called()
        latest=publish._reservation_latest([json.loads(x) for x in (state/"publication_reservations.jsonl").read_text().splitlines()])
        self.assertEqual(latest["k1"]["status"],"verified")
        pubs=[json.loads(x) for x in (state/"published.jsonl").read_text().splitlines()]
        corrected=[x for x in pubs if x.get('media_id')=='m1' and (x.get('meta') or {}).get('publish_status')=='verified']
        self.assertEqual(len(corrected),1)
        self.assertEqual(corrected[0]['meta'].get('reconciled_from'),'verification_pending')

    def test_reconciler_backfills_published_ledger_from_verified_reservation_without_network(self):
        from unittest.mock import patch
        state=self.root/'autopilot/state'
        reservation={'idempotency_key':'k2','status':'verified','owner_id':'owner','media_id':'m2','text':'#ad\nBody','country':'US'}
        (state/'publication_reservations.jsonl').write_text(json.dumps(reservation)+'\n')
        pending={'media_id':'m2','country':'US','text':'#ad\nBody','meta':{'publish_status':'verification_pending','idempotency_key':'k2'}}
        (state/'published.jsonl').write_text(json.dumps(pending)+'\n')
        cfg={'paths':{'state_dir':str(state)},'threads':{'us_access_token':'tok','us_user_id':'u'}}
        with patch.object(publish.requests,'get') as get, patch.object(publish.requests,'post') as post:
            counts=publish.reconcile_pending(cfg)
        get.assert_not_called(); post.assert_not_called()
        self.assertEqual(counts['backfilled'],1)
        rows=[json.loads(x) for x in (state/'published.jsonl').read_text().splitlines()]
        self.assertEqual((rows[-1].get('meta') or {}).get('publish_status'),'verified')

    def test_rotation_keeps_old_public_key_and_corruption_fails_closed(self):
        first=self.call(); old=first["_attestation"]["key_id"]
        ec.rotate_attestation_key(str(self.keys)); second=self.call(); self.assertNotEqual(old,second["_attestation"]["key_id"])
        self.assertTrue(ec.verify_attestation(first["_attestation"],first,project_root=str(self.root),key_dir=str(self.keys)))
        ring=self.keys/"public_keys.json"; ring.write_text("corrupt")
        self.assertFalse(ec.verify_attestation(second["_attestation"],second,project_root=str(self.root),key_dir=str(self.keys)))

    def test_fixture_is_rejected_with_either_production_authority_path(self):
        with self.assertRaises(ec.ContractError):
            ec.request_authoritative_generation("value_post", "KR", ["friction:f1"],
                project_root=ec.PROJECT_ROOT, key_dir=str(self.keys), test_fixture_executable=FIXTURE)
        with self.assertRaises(ec.ContractError):
            ec.request_authoritative_generation("value_post", "KR", ["friction:f1"],
                project_root=str(self.root), key_dir=ec.DEFAULT_KEY_DIR, test_fixture_executable=FIXTURE)

    def test_input_row_mutation_invalidates_attestation(self):
        result = self.call()
        rows = [{"friction_id":"f1", "lifecycle":"validated", "market":"KR", "verbatim":"mutated after generation"}]
        (self.root/"autopilot/state/friction_signals.jsonl").write_text(json.dumps(rows[0])+"\n")
        self.assertFalse(ec.verify_attestation(result["_attestation"], result,
            project_root=str(self.root), key_dir=str(self.keys)))

    def test_undeclared_and_publication_mismatched_countries_are_rejected(self):
        with self.assertRaises(ec.ContractError):
            ec.request_authoritative_generation("value_post", "XX", ["friction:f1"],
                project_root=str(self.root), key_dir=str(self.keys),
                test_fixture_executable=FIXTURE)
        result = self.call()
        self.assertFalse(ec.verify_attestation(result["_attestation"], result,
            project_root=str(self.root), key_dir=str(self.keys), expected_country="US"))
        self.assertTrue(ec.verify_attestation(result["_attestation"], result,
            project_root=str(self.root), key_dir=str(self.keys), expected_country="KR"))

    def test_episode_identity_is_retired_even_when_story_exists(self):
        import generation_ssot
        with self.assertRaisesRegex(ValueError, "retired or unvalidated"):
            generation_ssot.resolve_inputs(self.root, "value_post", ["episode:E5"])

    def test_episode_identity_fails_closed_for_missing_or_unconfirmed_story(self):
        import generation_ssot
        with self.assertRaisesRegex(ValueError, "retired or unvalidated"):
            generation_ssot.resolve_inputs(self.root, "value_post", ["episode:E9"])
        (self.root/"story-bank.md").write_text(
            "### E5. 키 큰 사람들 사이에서 ⚠️(미확인)\n확인되지 않은 사실\n",
            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "retired or unvalidated"):
            generation_ssot.resolve_inputs(self.root, "value_post", ["episode:E5"])

    def test_sales_directive_requires_exact_resolved_landing_url(self):
        import generation_ssot
        directive = generation_ssot.TASK_DIRECTIVES["sales_post"]
        self.assertIn("resolved product link field", directive)
        self.assertIn("verbatim", directive)
        self.assertIn("placeholder", directive)

    def test_value_directive_forbids_teacher_wrap_up(self):
        import generation_ssot
        self.assertIn("No teacher wrap-up", generation_ssot.TASK_DIRECTIVES["value_post"])
        self.assertIn("No teacher wrap-up", generation_ssot.TASK_DIRECTIVES["value_thread"])

    def test_value_writer_directive_requires_explicit_multi_candidate_schema(self):
        import generation_ssot
        directive = generation_ssot.TASK_DIRECTIVES["value_post"]
        self.assertIn('{"candidates":[{"id":"a","text":"..."},{"id":"b","text":"..."}]}', directive)
        self.assertIn("at least two", directive)
        self.assertIn("unique IDs", directive)

    def test_value_writer_directive_prevents_critic_disqualifying_fabrication(self):
        import generation_ssot
        directive = generation_ssot.TASK_DIRECTIVES["value_post"]
        for boundary in ("numbers", "prevalence", "first-person", "family history"):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, directive)

    def test_writer_retries_semantically_invalid_candidate_bundle_with_repair_prompt(self):
        from unittest.mock import patch
        responses = iter([
            {"text": "A single direct draft"},
            {"candidates": [{"id": "a", "text": "Draft A"},
                            {"id": "b", "text": "Draft B"}]},
        ])
        calls = []

        def fake_invoke(fixture, cfg, phase, task, model, prompt, payload):
            calls.append((prompt, payload))
            return next(responses)

        with patch.object(gw, "invoke", side_effect=fake_invoke):
            raw = gw.invoke_writer_candidates(
                None, {}, "value_post", "model", "base prompt", {"resolved_payload": []})

        self.assertEqual(len(raw["candidates"]), 2)
        self.assertEqual(len(calls), 2)
        self.assertIn("previous response was invalid", calls[1][0].lower())
        self.assertNotIn("A single direct draft", calls[1][0])

    def test_rehearsal_product_fixture_is_trusted_only_when_rehearsal_mode_is_on(self):
        config_path=self.root/'autopilot/config.json'
        cfg=json.loads(config_path.read_text()); cfg['mode']={'_rehearsal': True}; config_path.write_text(json.dumps(cfg))
        result=ec.request_authoritative_generation('sales_post','US',['product:us-ddrops-kids-600iu'],
            project_root=str(self.root),key_dir=str(self.keys),test_fixture_executable=FIXTURE,
            rehearsal=True)
        self.assertTrue(ec.verify_attestation(result['_attestation'],result,
            project_root=str(self.root),key_dir=str(self.keys),expected_country='US',
            expected_rehearsal=True))
        self.assertFalse(ec.verify_attestation(result['_attestation'],result,
            project_root=str(self.root),key_dir=str(self.keys),expected_country='US'))
        ec.stop_generation_service()
        cfg['mode']['_rehearsal']=False; config_path.write_text(json.dumps(cfg))
        with self.assertRaises(ec.ContractError):
            ec.request_authoritative_generation('sales_post','US',['product:us-dry-ddrops'],
                project_root=str(self.root),key_dir=str(self.keys),test_fixture_executable=FIXTURE)

    def test_writer_and_trusted_critic_are_separate_observed_calls(self):
        log = self.root / "fixture-calls.jsonl"
        old = os.environ.get("HEIGHTCUE_TEST_FIXTURE_LOG")
        os.environ["HEIGHTCUE_TEST_FIXTURE_LOG"] = str(log)
        try:
            result = self.call()
        finally:
            if old is None: os.environ.pop("HEIGHTCUE_TEST_FIXTURE_LOG", None)
            else: os.environ["HEIGHTCUE_TEST_FIXTURE_LOG"] = old
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([x["phase"] for x in calls], ["writer", "critic"])
        self.assertEqual(calls[1]["model"], "trusted-critic")
        self.assertEqual(result["_attestation"]["payload"]["critic_model"], "trusted-critic")
        self.assertEqual(set(calls[0]["payload"]), {"task", "country", "stage", "resolved_payload"})

    def test_critic_cannot_select_a_high_scoring_fabricated_candidate(self):
        candidates = [
            {"id": "fabricated", "text": "I watched my folks blow entire paychecks on powders."},
            {"id": "grounded", "text": "The source says genetics is the largest contributor."},
        ]
        critic = {"scores": [
            {"id": "fabricated", "score": 99, "disqualified": True,
             "reason": "invented first-person family purchase history"},
            {"id": "grounded", "score": 70, "disqualified": False,
             "reason": "supported by the supplied source"},
        ]}
        self.assertEqual(gw.select_candidate(candidates, critic)["text"], candidates[1]["text"])

    def test_all_fabricated_candidates_fail_closed(self):
        candidates = [{"id": "a", "text": "fake"}, {"id": "b", "text": "also fake"}]
        critic = {"scores": [
            {"id": "a", "score": 90, "disqualified": True, "reason": "unsupported"},
            {"id": "b", "score": 80, "disqualified": True, "reason": "unsupported"},
        ]}
        with self.assertRaisesRegex(RuntimeError, "all candidates disqualified"):
            gw.select_candidate(candidates, critic)

    def test_all_task_result_schemas_and_task_specific_prompt_digests(self):
        state = self.root/"autopilot/state"
        (state/"browser-queue").mkdir()
        (state/"browser-queue/results.json").write_text(json.dumps([{"product_key":"p1","name":"P","friction_id":"f1","source_pointers":["source:f1"],"mechanism":"front_open","failure_mode":"weak_latch","skip_if":"shallow shelf","price_band":"KR_PRICE_UNAVAILABLE","link":"https://example.test/p1"}]))
        (state/"comments_log.jsonl").write_text(json.dumps({"comment_id":"c1","text":"hi"})+"\n")
        (state/"published.jsonl").write_text(json.dumps({"media_id":"m1","text":"post"})+"\n")
        cases = {
            "sales_master": ["product:p1"], "sales_hooks": ["product:p1"],
            "sales_post": ["product:p1"], "value_post": ["friction:f1"],
            "value_thread": ["friction:f1"], "comment_reply": ["comment:c1", "post:m1"],
        }
        from unittest import mock
        with mock.patch("companyos.get_product", return_value={"product_key": "p1", "approved_claims": ["fact"]}):
            results = {task: ec.request_authoritative_generation(task,"KR",ids,
                project_root=str(self.root),key_dir=str(self.keys),test_fixture_executable=FIXTURE)
                for task,ids in cases.items()}
        self.assertEqual(len({r["_attestation"]["payload"]["prompt_digest"] for r in results.values()}), 6)
        for task, result in results.items():
            self.assertTrue(ec.verify_attestation(result["_attestation"], result,
                project_root=str(self.root), key_dir=str(self.keys)), task)

    def test_malformed_writer_and_critic_outputs_fail_closed(self):
        for mode in ("bad-writer", "bad-critic"):
            old = os.environ.get("HEIGHTCUE_TEST_FIXTURE_MODE")
            os.environ["HEIGHTCUE_TEST_FIXTURE_MODE"] = mode
            try:
                ec.stop_generation_service()
                with self.assertRaises(ec.ContractError): self.call()
            finally:
                if old is None: os.environ.pop("HEIGHTCUE_TEST_FIXTURE_MODE", None)
                else: os.environ["HEIGHTCUE_TEST_FIXTURE_MODE"] = old

    def tearDown(self): ec.stop_generation_service(); self.t.cleanup()

if __name__ == '__main__': unittest.main()
