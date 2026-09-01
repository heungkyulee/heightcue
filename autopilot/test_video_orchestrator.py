#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py 영상 종단 오케스트레이터 회귀 테스트.

모든 유료/네트워크/렌더 경계는 주입한다. 실제 원장과 계약만 사용한다.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

import run
import video_compose as vcomp
import video_contracts as vc
import video_handoff as vh
import video_queue as vq
import video_storyboard as vs
from test_video_queue import make_job


DISCLOSURE = vs.DISCLOSURE_TEXT["KR"]
AFFILIATE_LINK = "https://link.coupang.com/a/heightcue-test"


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class VideoOrchestratorTest(unittest.TestCase):

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="hc-video-orchestrator-")
        self.tmp = self.tmp_ctx.name
        self.settings = dict(
            run.VIDEO_DEFAULTS,
            enabled=True,
            production_generation_enabled=True,
            kill_switch=False,
            max_jobs_per_run=1,
            daily_budget_usd=2.0,
            ledger_root=os.path.join(self.tmp, "ledger"),
        )
        self.cfg = {"paths": {"state_dir": self.tmp}, "video": {}}
        self.args = types.SimpleNamespace(dry_run=False)
        self.ledger = vq.VideoLedger(self.settings["ledger_root"])
        self.ledger.enqueue(make_job(job_id="job-1", n_cuts=3))

        self.product_still = self._write("real-product.png", b"real product pixels")
        self.motion_paths = [
            self._write(f"motion-{i}.mp4", b"\x00\x00\x00\x18ftypmp42motion" + bytes([i]))
            for i in (2, 3)
        ]
        self.calls = []

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _storyboard(self):
        kinds = vs.cut_kinds_for(3)
        cuts = []
        for index, kind in enumerate(kinds, 1):
            cuts.append(vs.StoryboardCut(
                index=index,
                duration_seconds=5,
                action="실제 제품을 보여준다" if kind == vs.CUT_KIND_STILL
                       else "손이 제품을 들어 올린다",
                benefit="표시를 직접 확인한다",
                claim="고밀도 폼",
                evidence_id="ev1",
                evidence_quote="고밀도 폼",
                evidence_source_url="https://example.com/product",
                voice_line="" if kind == vs.CUT_KIND_STILL
                           else f"표시를 직접 확인해 보세요 {index}",
                first_frame_prompt="세로 화면의 제품 한 개",
                motion_prompt="느린 밀어넣기" if kind == vs.CUT_KIND_STILL
                              else "손이 제품을 들어 올린다",
                cut_kind=kind,
                still_plan=({"move": vs.DEFAULT_KEN_BURNS_MOVE}
                            if kind == vs.CUT_KIND_STILL else None),
                generation_prompt=("" if kind == vs.CUT_KIND_STILL
                                   else f"S1이 제품을 든다 <d>확인해 보세요 {index}</d>"),
            ))
        return vs.GroundedStoryboard(
            storyboard_id="sb-1", run_id="run-1", product_id="p-1",
            market="KR", content_draft_id="draft-1",
            viral_pattern_ids=["vp-1"], complexity="complex", cuts=cuts,
            disclosure=vs.disclosure_for("KR"), evidence_ids=["ev1"],
        )

    def _deps(self, *, qa_passes=True, explode_at=None):
        board = self._storyboard()

        def storyboard(*args, **kwargs):
            self.calls.append("storyboard")
            self.assertEqual(kwargs["storyboard_id"], "sb-1")
            self.assertEqual(kwargs["complexity"], "complex")
            return board

        def first_frames(received, asset_manifest, **kwargs):
            self.calls.append("first_frames")
            if explode_at == "first_frames":
                raise RuntimeError("first-frame transport down")
            self.assertIs(received, board)
            self.assertEqual(kwargs["asset_sha256"], "a" * 64)
            return {
                "frames": [
                    {"cut_index": i, "output_path": self._write(
                        f"frame-{i}.png", f"frame-{i}".encode()),
                     "output_sha256": hashlib.sha256(f"frame-{i}".encode()).hexdigest()}
                    for i in (2, 3)
                ],
                "still_cuts": [{
                    "cut_index": 1,
                    "cut_kind": vs.CUT_KIND_STILL,
                    "source_path": self.product_still,
                    "source_sha256": _sha(self.product_still),
                    "ken_burns_move": vs.DEFAULT_KEN_BURNS_MOVE,
                    "generated": False,
                    "paid": False,
                }],
            }

        def cuts(received, frames_manifest, **kwargs):
            self.calls.append("cuts")
            self.assertIs(received, board)
            self.assertEqual([f["cut_index"] for f in frames_manifest["frames"]], [2, 3])
            self.assertEqual(kwargs["daily_cap_usd"], 2.0)
            generated = [
                vc.CutGeneration(
                    index=i, prompt=f"motion prompt {i}", duration_seconds=5,
                    provider_request_id=f"fal-{i}", cost_usd=0.2,
                    output_path=path, output_sha256=_sha(path),
                )
                for i, path in zip((2, 3), self.motion_paths)
            ]
            manifest = vc.GenerationManifest(
                job_id="job-1", run_id="run-1", storyboard_id="sb-1",
                product_id="p-1", market="KR",
                image_model_alias=vc.IMAGE_MODEL_ALIAS,
                image_hermes_provider=vc.IMAGE_HERMES_PROVIDER,
                image_hermes_model=vc.IMAGE_HERMES_MODEL,
                image_provider_model=vc.IMAGE_PROVIDER_MODEL,
                video_endpoint=vc.VIDEO_ENDPOINT, resolution=vc.VIDEO_RESOLUTION,
                aspect_ratio=vc.VIDEO_ASPECT_RATIO, cuts=generated,
            ).validate()
            lineage = [dict(c.to_dict(), cut_index=c.index,
                            cut_kind=vs.CUT_KIND_MOTION)
                       for c in generated]
            return {"state": vc.STATE_READY_TO_PUBLISH,
                    "manifest": manifest.to_dict(), "cut_lineage": lineage}

        def master(*, storyboard, cut_lineage, output_path, **kwargs):
            self.calls.append("master")
            self.assertEqual([c["cut_index"] for c in cut_lineage], [1, 2, 3])
            still = cut_lineage[0]
            self.assertEqual(still["cut_kind"], vs.CUT_KIND_STILL)
            self.assertEqual(still["output_path"], self.product_still)
            self.assertEqual(still["output_sha256"], _sha(self.product_still))
            self._write(os.path.relpath(output_path, self.tmp),
                        b"\x00\x00\x00\x18ftypmp42clean-master")
            srt = os.path.splitext(output_path)[0] + ".srt"
            with open(srt, "w", encoding="utf-8") as fh:
                fh.write("1\n00:00:05,000 --> 00:00:10,000\n확인해 보세요\n")
            return {"stage": vcomp.STAGE_MASTER, "output_path": output_path,
                    "output_sha256": _sha(output_path),
                    "subtitle_sidecar_path": srt,
                    "expected_duration_seconds": 15,
                    "run_id": "run-1", "storyboard_id": "sb-1",
                    "product_id": "p-1", "market": "KR"}

        def subtitled(*, master, output_path, **kwargs):
            self.calls.append("subtitled")
            self.assertNotEqual(master["output_path"], output_path)
            self._write(os.path.relpath(output_path, self.tmp),
                        b"\x00\x00\x00\x18ftypmp42subtitled")
            return {"stage": vcomp.STAGE_SUBTITLED, "output_path": output_path,
                    "output_sha256": _sha(output_path),
                    "expected_duration_seconds": 15}

        def qa(**kwargs):
            self.calls.append("qa")
            self.assertIn("subtitled", kwargs["video_path"])
            self.assertIn("clean-master", kwargs["master_path"])
            self.assertTrue(os.path.isfile(kwargs["identity_signoff"]["artifact_path"]))
            if qa_passes:
                return vc.QAReport(job_id="job-1", run_id="run-1", passed=True,
                                   checks={"both_artifacts": {"passed": True}})
            return vc.QAReport(job_id="job-1", run_id="run-1", passed=False,
                               checks={"both_artifacts": {"passed": False}},
                               failures=["both_artifacts: mismatch"])

        def promote(*args, **kwargs):
            self.calls.append("handoff")
            return vh.promote_to_ready(*args, **kwargs)

        return {
            "worker_id": "test-worker",
            "generate_storyboard": storyboard,
            "generate_first_frames": first_frames,
            "generate_cuts": cuts,
            "compose_master": master,
            "compose_subtitled": subtitled,
            "run_qa": qa,
            "promote_to_ready": promote,
            "load_asset_manifest": lambda job: {"product_id": job.product_id},
            "resolve_asset_sha256": lambda job, manifest: "a" * 64,
            "resolve_affiliate_link": lambda job: AFFILIATE_LINK,
            "resolve_account": lambda job: "heightcue",
            "load_identity_signoff": lambda job, master_path: {
                "signed_off_by": "qa-human",
                "signed_off_at": "2026-08-30T10:00:00+09:00",
                "artifact_sha256": _sha(master_path),
                "artifact_path": master_path,
            },
            "cut_client": lambda request: self.fail("fake cuts must own this seam"),
            "image_url_for": lambda frame: "https://example.com/frame.png",
            "renderer": lambda request: self.fail("fake compose must own this seam"),
            "projects_root": self.tmp,
        }

    def _process(self, deps):
        with mock.patch.object(run, "_video_prereq_report",
                               return_value=(True, ["  [충족] fake"])):
            return run._video_process(self.cfg, self.settings, self.args, deps=deps)

    def test_process_runs_all_stages_once_and_stops_at_ready(self):
        self.ledger.enqueue(make_job(job_id="job-2", product_id="p-2",
                                     prompt_prefix="다른 컷", n_cuts=3))

        code = self._process(self._deps())

        self.assertEqual(code, 0)
        self.assertEqual(self.calls,
                         ["storyboard", "first_frames", "cuts", "master",
                          "subtitled", "qa", "handoff"])
        ready = self.ledger.get("job-1")
        self.assertEqual(ready["state"], vc.STATE_READY_TO_PUBLISH)
        self.assertIsNone(ready["lease"])
        self.assertEqual(ready["packet"]["duration_seconds"], 15)
        self.assertEqual(ready["job"]["handoff"]["duration_seconds"], 15)
        self.assertTrue(os.path.isfile(ready["packet"]["video_path"]))
        self.assertTrue(os.path.isfile(os.path.join(
            self.tmp, "heightcue_run-1", "renders", "job-1_clean-master.srt")))
        self.assertEqual(self.ledger.get("job-2")["state"], vc.STATE_QUEUED)
        self.assertNotEqual(ready["state"], vc.STATE_PUBLISHED)

    def test_qa_failure_is_terminal_for_this_run_and_never_promoted(self):
        deps = self._deps(qa_passes=False)
        deps["promote_to_ready"] = lambda *a, **kw: self.fail("failed QA was promoted")

        code = self._process(deps)

        self.assertNotEqual(code, 0)
        entry = self.ledger.get("job-1")
        self.assertEqual(entry["state"], vc.STATE_QA_FAILED)
        self.assertIsNone(entry["lease"])
        self.assertFalse(entry["job"]["qa_report"]["passed"])
        self.assertTrue(os.path.isfile(os.path.join(
            self.tmp, "heightcue_run-1", "qa", "job-1_qa.json")))

    def test_unexpected_stage_error_requeues_without_losing_job(self):
        code = self._process(self._deps(explode_at="first_frames"))

        self.assertNotEqual(code, 0)
        entry = self.ledger.get("job-1")
        self.assertEqual(entry["state"], vc.STATE_QUEUED)
        self.assertEqual(entry["attempts"], 1)
        self.assertIsNone(entry["lease"])
        self.assertIn("first-frame transport down", entry["last_error"])


class AdapterTest(unittest.TestCase):

    class Response:
        def __init__(self, payload=None, content=b"", status=200):
            self.payload = payload
            self.content = content
            self.status_code = status
            self.text = json.dumps(payload) if payload is not None else ""

        def json(self):
            return self.payload

    def test_fal_client_keeps_exact_request_and_provider_request_id(self):
        with tempfile.TemporaryDirectory(prefix="hc-fal-client-") as tmp:
            output = os.path.join(tmp, "cut.mp4")
            request = {
                "url": "https://queue.fal.run/fal-ai/minimax/hailuo-02/standard/image-to-video",
                "payload": {"prompt": "approved prompt", "image_url": "https://example.com/f.png",
                            "duration": 5, "resolution": "768P", "aspect_ratio": "9:16",
                            "prompt_expansion_mode": "disabled"},
                "output_path": output,
            }
            session = mock.Mock()
            session.post.return_value = self.Response({
                "request_id": "fal-request-123",
                "status_url": "https://queue.fal.run/status/123",
                "response_url": "https://queue.fal.run/result/123",
            })
            session.get.side_effect = [
                self.Response({"status": "IN_PROGRESS"}),
                self.Response({"status": "COMPLETED"}),
                self.Response({"video": {"url": "https://cdn.example.com/cut.mp4"}}),
                self.Response(content=b"\x00\x00\x00\x18ftypmp42video"),
            ]

            result = run._video_fal_client(
                request, api_key="fake-secret", session=session,
                sleep=lambda _: None, max_polls=3)

            self.assertEqual(result["request_id"], "fal-request-123")
            self.assertEqual(result["output_path"], output)
            self.assertEqual(open(output, "rb").read(), b"\x00\x00\x00\x18ftypmp42video")
            session.post.assert_called_once_with(
                request["url"], json=request["payload"],
                headers={"Authorization": "Key fake-secret"}, timeout=60)
            self.assertEqual(session.call_count, 0)
            self.assertEqual(session.get.call_count, 4)

    def test_remotion_renderer_uses_only_registered_heightcue_composition(self):
        with tempfile.TemporaryDirectory(prefix="hc-remotion-renderer-") as tmp:
            props = os.path.join(tmp, "props.json")
            output = os.path.join(tmp, "out.mp4")
            with open(props, "w", encoding="utf-8") as fh:
                json.dump({"clips": []}, fh)
            request = {
                "composition_id": vcomp.COMPOSITION_ID,
                "render_runtime": vcomp.RENDER_RUNTIME,
                "props_path": props,
                "output_path": output,
                "overlay_plan": {"text_layers": [{"role": "disclosure", "text": DISCLOSURE}]},
            }
            observed = {}

            def runner(command, **kwargs):
                observed["command"] = command
                observed["kwargs"] = kwargs
                with open(output, "wb") as fh:
                    fh.write(b"\x00\x00\x00\x18ftypmp42render")
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")

            result = run._video_remotion_renderer(
                request, runner=runner, composer_root=tmp)

            command = observed["command"]
            self.assertIn(vcomp.COMPOSITION_ID, command)
            self.assertIn(f"--props={props}", command)
            self.assertNotIn("ffmpeg", command)
            self.assertEqual(result["runtime"], vcomp.RENDER_RUNTIME)
            self.assertEqual(result["text_layers"], [DISCLOSURE])

    def test_status_says_orchestrator_is_wired_and_process_never_publishes(self):
        with tempfile.TemporaryDirectory(prefix="hc-video-status-") as tmp:
            settings = dict(run.VIDEO_DEFAULTS, ledger_root=tmp)
            out = io.StringIO()
            with redirect_stdout(out):
                run._video_status({}, settings, types.SimpleNamespace(json=False))
            text = out.getvalue()
            self.assertIn("종단 오케스트레이터", text)
            self.assertIn("배선됨", text)
            self.assertIn("발행하지 않음", text)


if __name__ == "__main__":
    unittest.main()
