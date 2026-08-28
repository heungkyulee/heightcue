#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue 영상 잡·발행 핸드오프 계약 테스트.

네트워크 호출 없음. 임시 디렉터리만 사용한다.
실행: cd autopilot && ../.venv/bin/python -m unittest -v test_video_contracts.py
"""

import json
import os
import shutil
import tempfile
import unittest

import video_contracts as vc


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

RUN_ID = "run-2026-08-28-01"
JOB_ID = "vjob-0001"
PRODUCT_ID = "kr-sleepcomfort-junior-pillow"


def evidence(**over):
    base = dict(
        product_id=PRODUCT_ID,
        market="KR",
        source_urls=["https://www.coupang.com/vp/products/123"],
        source_sha256=["a" * 64],
        rights={
            "basis": "official_product_page",
            "holder": "SleepComfort",
            "source_url": "https://www.coupang.com/vp/products/123",
            "captured_at": "2026-08-28T09:00:00+09:00",
        },
        provenance=[{
            "quote": "고밀도 폼",
            "source_url": "https://www.coupang.com/vp/products/123",
            "original_location": "상품 상세 > 제품 사양",
        }],
        captured_at="2026-08-28T09:00:00+09:00",
    )
    base.update(over)
    return vc.ProductEvidence(**base)


def storyboard(**over):
    base = dict(
        storyboard_id="sb-0001",
        run_id=RUN_ID,
        product_id=PRODUCT_ID,
        market="KR",
        viral_pattern_ids=["vp-hook-question", "vp-parent-relief"],
        content_draft_id="draft-0001",
        cuts=[
            vc.CutPrompt(index=1, prompt="아이가 책상에 앉아 고개를 숙인다", duration_seconds=5),
            vc.CutPrompt(index=2, prompt="베개를 놓자 자세가 펴진다", duration_seconds=5),
        ],
    )
    base.update(over)
    return vc.Storyboard(**base)


def manifest(**over):
    base = dict(
        job_id=JOB_ID,
        run_id=RUN_ID,
        storyboard_id="sb-0001",
        product_id=PRODUCT_ID,
        market="KR",
        image_model_alias=vc.IMAGE_MODEL_ALIAS,
        image_hermes_provider=vc.IMAGE_HERMES_PROVIDER,
        image_hermes_model=vc.IMAGE_HERMES_MODEL,
        image_provider_model=vc.IMAGE_PROVIDER_MODEL,
        video_endpoint=vc.VIDEO_ENDPOINT,
        resolution=vc.VIDEO_RESOLUTION,
        aspect_ratio=vc.VIDEO_ASPECT_RATIO,
        cuts=[
            vc.CutGeneration(index=1, prompt="아이가 책상에 앉아 고개를 숙인다",
                             duration_seconds=5, provider_request_id="fal-req-1",
                             cost_usd=0.42, output_path="/tmp/cut1.mp4",
                             output_sha256="b" * 64),
            vc.CutGeneration(index=2, prompt="베개를 놓자 자세가 펴진다",
                             duration_seconds=5, provider_request_id="fal-req-2",
                             cost_usd=0.42, output_path="/tmp/cut2.mp4",
                             output_sha256="c" * 64),
        ],
    )
    base.update(over)
    return vc.GenerationManifest(**base)


def qa(**over):
    base = dict(job_id=JOB_ID, run_id=RUN_ID, passed=True,
                checks={"aspect_ratio": True, "duration": True, "disclosure": True},
                failures=[])
    base.update(over)
    return vc.QAReport(**base)


def handoff(**over):
    base = dict(
        job_id=JOB_ID,
        run_id=RUN_ID,
        product_id=PRODUCT_ID,
        market="KR",
        state=vc.STATE_READY_TO_PUBLISH,
        content_draft_id="draft-0001",
        video_path="/tmp/final.mp4",
        video_sha256="d" * 64,
        duration_seconds=10,
        aspect_ratio=vc.VIDEO_ASPECT_RATIO,
        caption="키 고민 부모를 위한 자세 루틴",
        disclosure_included=True,
    )
    base.update(over)
    return vc.PublishingHandoff(**base)


# ---------------------------------------------------------------------------
# pinned upstream constants
# ---------------------------------------------------------------------------


class TestPinnedConstants(unittest.TestCase):
    def test_image_chain(self):
        self.assertEqual(vc.IMAGE_MODEL_ALIAS, "gpt-image-gen-2")
        self.assertEqual(vc.IMAGE_HERMES_PROVIDER, "openai-codex")
        self.assertEqual(vc.IMAGE_HERMES_MODEL, "gpt-image-2-medium")
        self.assertEqual(vc.IMAGE_PROVIDER_MODEL, "gpt-image-2")

    def test_video_chain(self):
        self.assertEqual(vc.VIDEO_ENDPOINT, "minimax/h3-max/image-to-video")
        self.assertEqual(vc.VIDEO_RESOLUTION, "768P")
        self.assertEqual(vc.VIDEO_ASPECT_RATIO, "9:16")
        self.assertEqual(vc.CUT_DURATION_SECONDS, 5)
        self.assertEqual(vc.MAX_CUTS, 3)
        self.assertEqual(sorted(vc.MARKETS), ["KR", "US"])

    def test_states(self):
        self.assertEqual(
            sorted(vc.STATES),
            sorted(["queued", "generating", "qa_failed", "ready_to_publish",
                    "publishing", "published", "retryable_failed", "dead_letter"]))


# ---------------------------------------------------------------------------
# product evidence
# ---------------------------------------------------------------------------


class TestProductEvidence(unittest.TestCase):
    def test_valid_roundtrip(self):
        e = evidence()
        e.validate()
        again = vc.ProductEvidence.from_dict(json.loads(json.dumps(e.to_dict())))
        self.assertEqual(again.to_dict(), e.to_dict())

    def test_missing_product_id_raises(self):
        with self.assertRaises(vc.LineageError):
            evidence(product_id="").validate()

    def test_unknown_market_raises(self):
        with self.assertRaises(vc.LineageError):
            evidence(market="JP").validate()

    def test_missing_rights_raises(self):
        with self.assertRaises(vc.RightsError):
            evidence(rights={}).validate()

    def test_rights_missing_basis_raises(self):
        with self.assertRaises(vc.RightsError):
            evidence(rights={"holder": "X", "source_url": "https://x",
                             "captured_at": "2026-08-28T09:00:00+09:00"}).validate()

    def test_missing_provenance_raises(self):
        with self.assertRaises(vc.RightsError):
            evidence(provenance=[]).validate()

    def test_provenance_entry_without_source_url_raises(self):
        with self.assertRaises(vc.RightsError):
            evidence(provenance=[{"quote": "x", "original_location": "y"}]).validate()

    def test_source_hash_shape_enforced(self):
        with self.assertRaises(vc.RightsError):
            evidence(source_sha256=["nope"]).validate()

    def test_source_urls_required(self):
        with self.assertRaises(vc.RightsError):
            evidence(source_urls=[]).validate()


# ---------------------------------------------------------------------------
# storyboard
# ---------------------------------------------------------------------------


class TestStoryboard(unittest.TestCase):
    def test_valid_roundtrip(self):
        sb = storyboard()
        sb.validate()
        again = vc.Storyboard.from_dict(json.loads(json.dumps(sb.to_dict())))
        self.assertEqual(again.to_dict(), sb.to_dict())
        self.assertEqual(sb.total_duration_seconds(), 10)

    def test_total_duration_allowed_values(self):
        for n, total in ((1, 5), (2, 10), (3, 15)):
            cuts = [vc.CutPrompt(index=i + 1, prompt=f"컷 {i+1}", duration_seconds=5)
                    for i in range(n)]
            sb = storyboard(cuts=cuts)
            sb.validate()
            self.assertEqual(sb.total_duration_seconds(), total)

    def test_zero_cuts_raises(self):
        with self.assertRaises(vc.DurationError):
            storyboard(cuts=[]).validate()

    def test_four_cuts_raises(self):
        cuts = [vc.CutPrompt(index=i + 1, prompt=f"컷 {i+1}", duration_seconds=5)
                for i in range(4)]
        with self.assertRaises(vc.DurationError):
            storyboard(cuts=cuts).validate()

    def test_non_five_second_cut_raises(self):
        with self.assertRaises(vc.DurationError):
            storyboard(cuts=[vc.CutPrompt(index=1, prompt="컷", duration_seconds=6)]).validate()

    def test_missing_run_id_raises(self):
        with self.assertRaises(vc.LineageError):
            storyboard(run_id="").validate()

    def test_missing_content_draft_id_raises(self):
        with self.assertRaises(vc.LineageError):
            storyboard(content_draft_id="").validate()

    def test_missing_viral_pattern_ids_raises(self):
        with self.assertRaises(vc.LineageError):
            storyboard(viral_pattern_ids=[]).validate()

    def test_empty_prompt_raises(self):
        with self.assertRaises(vc.ContractError):
            storyboard(cuts=[vc.CutPrompt(index=1, prompt="   ", duration_seconds=5)]).validate()

    def test_non_sequential_cut_index_raises(self):
        cuts = [vc.CutPrompt(index=1, prompt="a", duration_seconds=5),
                vc.CutPrompt(index=3, prompt="b", duration_seconds=5)]
        with self.assertRaises(vc.ContractError):
            storyboard(cuts=cuts).validate()


# ---------------------------------------------------------------------------
# generation manifest
# ---------------------------------------------------------------------------


class TestGenerationManifest(unittest.TestCase):
    def test_valid_roundtrip(self):
        m = manifest()
        m.validate()
        again = vc.GenerationManifest.from_dict(json.loads(json.dumps(m.to_dict())))
        self.assertEqual(again.to_dict(), m.to_dict())
        self.assertAlmostEqual(m.total_cost_usd(), 0.84)
        self.assertEqual(m.total_duration_seconds(), 10)

    def test_image_provider_model_mismatch_raises(self):
        with self.assertRaises(vc.ModelMismatchError):
            manifest(image_provider_model="gpt-image-1").validate()

    def test_image_hermes_model_mismatch_raises(self):
        with self.assertRaises(vc.ModelMismatchError):
            manifest(image_hermes_model="gpt-image-2-high").validate()

    def test_image_provider_mismatch_raises(self):
        with self.assertRaises(vc.ModelMismatchError):
            manifest(image_hermes_provider="openai").validate()

    def test_image_alias_mismatch_raises(self):
        with self.assertRaises(vc.ModelMismatchError):
            manifest(image_model_alias="gpt-image-gen-1").validate()

    def test_video_endpoint_mismatch_raises(self):
        with self.assertRaises(vc.ModelMismatchError):
            manifest(video_endpoint="minimax/hailuo-02/image-to-video").validate()

    def test_resolution_mismatch_raises(self):
        with self.assertRaises(vc.ModelMismatchError):
            manifest(resolution="1080P").validate()

    def test_wrong_aspect_ratio_raises(self):
        with self.assertRaises(vc.AspectRatioError):
            manifest(aspect_ratio="16:9").validate()

    def test_missing_provider_request_id_raises(self):
        cuts = [vc.CutGeneration(index=1, prompt="컷", duration_seconds=5,
                                 provider_request_id="", cost_usd=0.4,
                                 output_path="/tmp/a.mp4", output_sha256="e" * 64)]
        with self.assertRaises(vc.LineageError):
            manifest(cuts=cuts).validate()

    def test_negative_cost_raises(self):
        cuts = [vc.CutGeneration(index=1, prompt="컷", duration_seconds=5,
                                 provider_request_id="r1", cost_usd=-1.0,
                                 output_path="/tmp/a.mp4", output_sha256="e" * 64)]
        with self.assertRaises(vc.ContractError):
            manifest(cuts=cuts).validate()

    def test_bad_output_hash_raises(self):
        cuts = [vc.CutGeneration(index=1, prompt="컷", duration_seconds=5,
                                 provider_request_id="r1", cost_usd=0.4,
                                 output_path="/tmp/a.mp4", output_sha256="short")]
        with self.assertRaises(vc.ContractError):
            manifest(cuts=cuts).validate()

    def test_unsupported_cut_count_raises(self):
        cuts = [vc.CutGeneration(index=i + 1, prompt="컷", duration_seconds=5,
                                 provider_request_id=f"r{i}", cost_usd=0.4,
                                 output_path=f"/tmp/{i}.mp4", output_sha256="e" * 64)
                for i in range(4)]
        with self.assertRaises(vc.DurationError):
            manifest(cuts=cuts).validate()

    def test_unsupported_cut_duration_raises(self):
        cuts = [vc.CutGeneration(index=1, prompt="컷", duration_seconds=10,
                                 provider_request_id="r1", cost_usd=0.4,
                                 output_path="/tmp/a.mp4", output_sha256="e" * 64)]
        with self.assertRaises(vc.DurationError):
            manifest(cuts=cuts).validate()


# ---------------------------------------------------------------------------
# QA report + handoff
# ---------------------------------------------------------------------------


class TestQAReport(unittest.TestCase):
    def test_valid_roundtrip(self):
        r = qa()
        r.validate()
        again = vc.QAReport.from_dict(json.loads(json.dumps(r.to_dict())))
        self.assertEqual(again.to_dict(), r.to_dict())

    def test_passed_with_failures_raises(self):
        with self.assertRaises(vc.ContractError):
            qa(passed=True, failures=["disclosure missing"]).validate()

    def test_failed_without_failures_raises(self):
        with self.assertRaises(vc.ContractError):
            qa(passed=False, failures=[]).validate()

    def test_missing_job_id_raises(self):
        with self.assertRaises(vc.LineageError):
            qa(job_id="").validate()


class TestPublishingHandoff(unittest.TestCase):
    def test_valid_roundtrip(self):
        h = handoff()
        h.validate()
        again = vc.PublishingHandoff.from_dict(json.loads(json.dumps(h.to_dict())))
        self.assertEqual(again.to_dict(), h.to_dict())

    def test_wrong_aspect_ratio_raises(self):
        with self.assertRaises(vc.AspectRatioError):
            handoff(aspect_ratio="1:1").validate()

    def test_unsupported_duration_raises(self):
        with self.assertRaises(vc.DurationError):
            handoff(duration_seconds=12).validate()

    def test_missing_disclosure_raises(self):
        with self.assertRaises(vc.RightsError):
            handoff(disclosure_included=False).validate()

    def test_unknown_state_raises(self):
        with self.assertRaises(vc.StateError):
            handoff(state="uploading").validate()

    def test_non_handoff_state_raises(self):
        with self.assertRaises(vc.StateError):
            handoff(state="queued").validate()

    def test_missing_video_hash_raises(self):
        with self.assertRaises(vc.ContractError):
            handoff(video_sha256="").validate()


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------


class TestStateMachine(unittest.TestCase):
    def test_happy_path(self):
        path = ["queued", "generating", "ready_to_publish", "publishing", "published"]
        for a, b in zip(path, path[1:]):
            vc.assert_transition(a, b)

    def test_qa_failure_and_requeue(self):
        vc.assert_transition("generating", "qa_failed")
        vc.assert_transition("qa_failed", "queued")
        vc.assert_transition("qa_failed", "dead_letter")

    def test_retry_paths(self):
        vc.assert_transition("generating", "retryable_failed")
        vc.assert_transition("publishing", "retryable_failed")
        vc.assert_transition("retryable_failed", "queued")
        vc.assert_transition("retryable_failed", "dead_letter")

    def test_unknown_state_raises(self):
        with self.assertRaises(vc.StateError):
            vc.assert_transition("queued", "uploading")
        with self.assertRaises(vc.StateError):
            vc.assert_transition("uploading", "queued")

    def test_illegal_transition_raises(self):
        with self.assertRaises(vc.StateError):
            vc.assert_transition("queued", "published")
        with self.assertRaises(vc.StateError):
            vc.assert_transition("published", "queued")
        with self.assertRaises(vc.StateError):
            vc.assert_transition("dead_letter", "queued")

    def test_terminal_states(self):
        self.assertTrue(vc.is_terminal("published"))
        self.assertTrue(vc.is_terminal("dead_letter"))
        self.assertFalse(vc.is_terminal("queued"))


# ---------------------------------------------------------------------------
# VideoJob aggregate
# ---------------------------------------------------------------------------


class TestVideoJob(unittest.TestCase):
    def _job(self, **over):
        base = dict(job_id=JOB_ID, run_id=RUN_ID, product_id=PRODUCT_ID, market="KR",
                    state=vc.STATE_QUEUED, evidence=evidence(), storyboard=storyboard(),
                    manifest=None, qa_report=None, handoff=None)
        base.update(over)
        return vc.VideoJob(**base)

    def test_valid_roundtrip_full(self):
        job = self._job(state=vc.STATE_READY_TO_PUBLISH, manifest=manifest(),
                        qa_report=qa(), handoff=handoff())
        job.validate()
        again = vc.VideoJob.from_dict(json.loads(json.dumps(job.to_dict())))
        self.assertEqual(again.to_dict(), job.to_dict())
        self.assertEqual(again.manifest.cuts[1].provider_request_id, "fal-req-2")
        self.assertEqual(again.evidence.rights["basis"], "official_product_page")

    def test_valid_roundtrip_minimal(self):
        job = self._job()
        job.validate()
        again = vc.VideoJob.from_dict(json.loads(json.dumps(job.to_dict())))
        self.assertEqual(again.to_dict(), job.to_dict())
        self.assertIsNone(again.manifest)

    def test_lineage_mismatch_between_job_and_storyboard_raises(self):
        with self.assertRaises(vc.LineageError):
            self._job(storyboard=storyboard(run_id="run-other")).validate()

    def test_market_mismatch_raises(self):
        with self.assertRaises(vc.LineageError):
            self._job(evidence=evidence(market="US")).validate()

    def test_ready_to_publish_requires_manifest_and_qa(self):
        with self.assertRaises(vc.ContractError):
            self._job(state=vc.STATE_READY_TO_PUBLISH).validate()

    def test_ready_to_publish_requires_passing_qa(self):
        with self.assertRaises(vc.ContractError):
            self._job(state=vc.STATE_READY_TO_PUBLISH, manifest=manifest(),
                      qa_report=qa(passed=False, failures=["blurry"]),
                      handoff=handoff()).validate()

    def test_transition_updates_state(self):
        job = self._job()
        job.transition(vc.STATE_GENERATING)
        self.assertEqual(job.state, vc.STATE_GENERATING)
        with self.assertRaises(vc.StateError):
            job.transition(vc.STATE_PUBLISHED)

    def test_handoff_state_must_match_job_state(self):
        # 잡은 publishing 인데 핸드오프는 ready_to_publish 로 굳어 있으면 두 개의 진실이 된다.
        with self.assertRaises(vc.StateError):
            self._job(state=vc.STATE_PUBLISHING, manifest=manifest(), qa_report=qa(),
                      handoff=handoff(state=vc.STATE_READY_TO_PUBLISH)).validate()

    def test_handoff_state_matching_passes(self):
        self._job(state=vc.STATE_PUBLISHING, manifest=manifest(), qa_report=qa(),
                  handoff=handoff(state=vc.STATE_PUBLISHING)).validate()

    def test_qa_failed_requires_qa_report(self):
        with self.assertRaises(vc.ContractError):
            self._job(state=vc.STATE_QA_FAILED, manifest=manifest()).validate()

    def test_qa_failed_with_failing_report_passes(self):
        self._job(state=vc.STATE_QA_FAILED, manifest=manifest(),
                  qa_report=qa(passed=False, failures=["blurry"])).validate()

    def test_lineage_missing_attribute_fails_loudly(self):
        class Stub:
            market = "KR"
            run_id = RUN_ID
        job = self._job()
        with self.assertRaises(vc.LineageError):
            job._require_lineage("stub", Stub())


# ---------------------------------------------------------------------------
# persistence: atomic JSON + append-only JSONL
# ---------------------------------------------------------------------------


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hc-video-contracts-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_atomic_write_json_roundtrip(self):
        p = os.path.join(self.tmp, "nested", "job.json")
        vc.atomic_write_json(p, {"job_id": JOB_ID, "한글": "보존"})
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"job_id": JOB_ID, "한글": "보존"})

    def test_atomic_write_leaves_no_tmp_files(self):
        p = os.path.join(self.tmp, "job.json")
        vc.atomic_write_json(p, {"a": 1})
        vc.atomic_write_json(p, {"a": 2})
        self.assertEqual(os.listdir(self.tmp), ["job.json"])

    def test_atomic_write_preserves_original_on_failure(self):
        p = os.path.join(self.tmp, "job.json")
        vc.atomic_write_json(p, {"a": 1})
        with self.assertRaises(TypeError):
            vc.atomic_write_json(p, {"a": object()})
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"a": 1})
        self.assertEqual(os.listdir(self.tmp), ["job.json"])

    def test_save_and_load_job(self):
        job = vc.VideoJob(job_id=JOB_ID, run_id=RUN_ID, product_id=PRODUCT_ID,
                          market="KR", state=vc.STATE_QUEUED, evidence=evidence(),
                          storyboard=storyboard())
        p = os.path.join(self.tmp, "jobs", f"{JOB_ID}.json")
        vc.save_job(p, job)
        loaded = vc.load_job(p)
        self.assertEqual(loaded.to_dict(), job.to_dict())

    def test_save_job_rejects_invalid(self):
        job = vc.VideoJob(job_id=JOB_ID, run_id=RUN_ID, product_id=PRODUCT_ID,
                          market="KR", state=vc.STATE_QUEUED,
                          evidence=evidence(rights={}), storyboard=storyboard())
        p = os.path.join(self.tmp, "bad.json")
        with self.assertRaises(vc.RightsError):
            vc.save_job(p, job)
        self.assertFalse(os.path.exists(p))

    def test_append_event_is_append_only(self):
        p = os.path.join(self.tmp, "video_events.jsonl")
        vc.append_event(p, {"job_id": JOB_ID, "event": "queued"})
        vc.append_event(p, {"job_id": JOB_ID, "event": "generating"})
        with open(p, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual([r["event"] for r in rows], ["queued", "generating"])
        self.assertTrue(all(r.get("ts") for r in rows))

    def test_append_event_redacts_secrets(self):
        p = os.path.join(self.tmp, "video_events.jsonl")
        vc.append_event(p, {"job_id": JOB_ID, "event": "generating",
                            "detail": "GET https://fal.run/x?api_key=sk-live-12345"})
        with open(p, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertNotIn("sk-live-12345", row["detail"])
        self.assertIn("[REDACTED]", row["detail"])

    def test_append_event_redacts_nested_secrets(self):
        p = os.path.join(self.tmp, "video_events.jsonl")
        vc.append_event(p, {"job_id": JOB_ID, "event": "generating",
                            "request": {"url": "https://fal.run/x?api_key=sk-live-99",
                                        "headers": ["authorization=sk-live-88"]}})
        with open(p, encoding="utf-8") as fh:
            raw = fh.readline()
        self.assertNotIn("sk-live-", raw)
        row = json.loads(raw)
        self.assertIn("[REDACTED]", row["request"]["url"])
        self.assertIn("[REDACTED]", row["request"]["headers"][0])

    def test_append_event_ts_is_timezone_aware(self):
        import datetime as _dt
        p = os.path.join(self.tmp, "video_events.jsonl")
        row = vc.append_event(p, {"job_id": JOB_ID, "event": "queued"})
        parsed = _dt.datetime.fromisoformat(row["ts"])
        self.assertIsNotNone(parsed.tzinfo)

    def test_atomic_write_tmp_name_is_unique(self):
        p = os.path.join(self.tmp, "job.json")
        names = set()
        real_replace = vc.os.replace

        def spy(src, dst):
            names.add(src)
            return real_replace(src, dst)

        vc.os.replace = spy
        try:
            vc.atomic_write_json(p, {"a": 1})
            vc.atomic_write_json(p, {"a": 2})
        finally:
            vc.os.replace = real_replace
        self.assertEqual(len(names), 2)

    def test_transition_event_records_both_states(self):
        p = os.path.join(self.tmp, "video_events.jsonl")
        vc.append_transition_event(p, job_id=JOB_ID, run_id=RUN_ID,
                                   from_state=vc.STATE_QUEUED,
                                   to_state=vc.STATE_GENERATING)
        with open(p, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row["from_state"], "queued")
        self.assertEqual(row["to_state"], "generating")

    def test_transition_event_rejects_illegal(self):
        p = os.path.join(self.tmp, "video_events.jsonl")
        with self.assertRaises(vc.StateError):
            vc.append_transition_event(p, job_id=JOB_ID, run_id=RUN_ID,
                                       from_state=vc.STATE_QUEUED,
                                       to_state=vc.STATE_PUBLISHED)
        self.assertFalse(os.path.exists(p))


class TestNoNetwork(unittest.TestCase):
    def test_module_imports_no_http_client(self):
        import inspect
        src = inspect.getsource(vc)
        for banned in ("import requests", "urllib.request", "http.client", "socket."):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
