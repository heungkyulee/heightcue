#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py 영상 명령 배선 테스트 (Task 16) — 실행:

    cd ~/heightcue-autopilot/autopilot
    ../.venv/bin/python -m unittest -v test_video_run.py

네트워크를 쓰지 않는다. 유료 호출(fal.ai)·발행(Threads)은 **호출되면 즉시 실패**하도록
지뢰를 깔아 두고, 그 지뢰가 밟히지 않는 것을 증명한다.

여기서 느슨하게 넘어가면 안 되는 것들:
* 기존 명령 4개(daily/post/comments/weekly)의 디스패치가 **한 글자도** 달라지지 않는다.
  이 파이프라인은 크론 5개로 매일 실발행 중이다 — 영상 기능보다 이쪽이 훨씬 비싸다.
* `video process` 는 production_generation_enabled 가 꺼져 있으면 **생성하지 않는다**.
  기본값이 꺼짐이므로, 실수로 켜지 않는 한 돈이 나가지 않는다.
* `rehearsal` 은 유료 호출 0건 · 발행 0건으로 전 과정을 돈다.
* `video status` 는 빈 원장에서도 죽지 않는다.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import run  # noqa: E402
import video_contracts as vc  # noqa: E402
import video_queue as vq  # noqa: E402

SHA_A = "a" * 64


def make_job(job_id="job-1", run_id="run-1", product_id="p-1", market="KR"):
    evidence = vc.ProductEvidence(
        product_id=product_id, market=market,
        source_urls=["https://www.coupang.com/vp/products/123"],
        source_sha256=[SHA_A],
        rights={"basis": "제품 상세 공개 정보", "holder": "쿠팡 판매자",
                "source_url": "https://www.coupang.com/vp/products/123",
                "captured_at": "2026-08-28T09:00:00+09:00"},
        provenance=[{"quote": "고밀도 폼",
                     "source_url": "https://www.coupang.com/vp/products/123",
                     "original_location": "상품 상세 > 제품 사양"}],
        captured_at="2026-08-28T09:00:00+09:00",
    )
    storyboard = vc.Storyboard(
        storyboard_id="sb-1", run_id=run_id, product_id=product_id, market=market,
        viral_pattern_ids=["vp-1"], content_draft_id="draft-1",
        cuts=[vc.CutPrompt(index=i, prompt=f"컷-{i} 장면") for i in (1, 2)],
    )
    return vc.VideoJob(job_id=job_id, run_id=run_id, product_id=product_id,
                       market=market, state=vc.STATE_QUEUED,
                       evidence=evidence, storyboard=storyboard)


class VideoRunTestCase(unittest.TestCase):
    """공통 준비 — 임시 state 디렉터리 + 유료/발행 경로 지뢰."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hc-video-run-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.ledger_root = os.path.join(self.tmp, "video")
        self.cfg = {
            "mode": {"dry_run": True, "publish": False},
            "cadence": {},
            "threads": {},
            "paths": {"state_dir": self.tmp},
            "video": {"ledger_root": self.ledger_root},
        }
        # 유료 호출·발행 지뢰: 밟히면 테스트가 즉사한다.
        def _paid(*a, **k):
            raise AssertionError("유료 provider 호출이 발생했다 — 리허설/차단이 새고 있다")

        def _published(*a, **k):
            raise AssertionError("발행 호출이 발생했다 — 리허설/차단이 새고 있다")

        for target, side in (("video_generate.generate_cuts", _paid),
                             ("video_generate.generate_first_frames", _paid),
                             ("publish.publish_text", _published)):
            mod, attr = target.rsplit(".", 1)
            patcher = mock.patch(f"{mod}.{attr}", side_effect=side)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_video(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run.video_command(self.cfg, list(args))
        return code, buf.getvalue()


# ---------------------------------------------------------------------------
# 1. 기존 명령 4개 — 절대 달라지면 안 된다
# ---------------------------------------------------------------------------


class TestExistingCommandsUnchanged(unittest.TestCase):
    """run.py 의 인자 파싱·디스패치가 영상 배선 후에도 동일한지 증명한다."""

    def _main(self, argv, cfg=None):
        cfg = cfg or {"mode": {"dry_run": True}, "threads": {}, "paths": {}}
        with mock.patch.object(run, "load_config", return_value=cfg), \
                mock.patch.object(sys, "argv", ["run.py"] + argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                run.main()
            return buf.getvalue()

    def test_daily_dispatches_with_dry_run_flag(self):
        with mock.patch.object(run, "daily") as m:
            self._main(["daily"])
        m.assert_called_once()
        self.assertIs(m.call_args.kwargs["dry_run"], True)

    def test_dryrun_dispatches_daily_comments_improve(self):
        with mock.patch.object(run, "daily") as d, \
                mock.patch.object(run.comments_mod, "run") as c, \
                mock.patch.object(run.improve, "run") as i:
            self._main(["dryrun"])
        d.assert_called_once()
        c.assert_called_once()
        i.assert_called_once()

    def test_post_defaults_to_kr_value(self):
        with mock.patch.object(run, "make_and_publish_value") as m:
            self._main(["post"])
        m.assert_called_once()
        self.assertEqual(m.call_args.kwargs["country"], "KR")

    def test_post_kr_sales_routes_to_kr_sales(self):
        with mock.patch.object(run, "_kr_sales") as kr, \
                mock.patch.object(run.improve, "playbook_hint", return_value="h"):
            self._main(["post", "KR", "sales"])
        kr.assert_called_once()

    def test_post_us_sales_routes_to_us_sales(self):
        with mock.patch.object(run, "_us_sales") as us, \
                mock.patch.object(run.improve, "playbook_hint", return_value="h"):
            self._main(["post", "us", "SALES"])
        us.assert_called_once()

    def test_post_us_value_routes_to_value_with_us(self):
        with mock.patch.object(run, "make_and_publish_value") as m:
            self._main(["post", "US", "value"])
        self.assertEqual(m.call_args.kwargs["country"], "US")

    def test_comments_dispatches(self):
        with mock.patch.object(run.comments_mod, "run") as c:
            self._main(["comments"])
        c.assert_called_once()

    def test_weekly_dispatches_improve_run(self):
        with mock.patch.object(run.improve, "run") as i:
            self._main(["weekly"])
        i.assert_called_once()

    def test_unknown_command_prints_usage(self):
        out = self._main(["nonsense-command"])
        self.assertIn("run.py daily", out)

    def test_no_args_defaults_to_dryrun(self):
        with mock.patch.object(run, "daily") as d, \
                mock.patch.object(run.comments_mod, "run"), \
                mock.patch.object(run.improve, "run"):
            with mock.patch.object(run, "load_config",
                                   return_value={"mode": {"dry_run": True},
                                                 "threads": {}, "paths": {}}), \
                    mock.patch.object(sys, "argv", ["run.py"]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    run.main()
        d.assert_called_once()

    def test_video_command_does_not_shadow_existing(self):
        """'video' 는 신규 명령이어야 하고 기존 4개와 겹치지 않는다."""
        self.assertNotIn("video", ("daily", "post", "comments", "weekly"))
        self.assertTrue(hasattr(run, "video_command"))


# ---------------------------------------------------------------------------
# 2. 설정 — 프로덕션 생성은 기본 꺼짐
# ---------------------------------------------------------------------------


class TestVideoSettings(unittest.TestCase):

    def test_production_generation_disabled_by_default(self):
        s = run.video_settings({"paths": {"state_dir": "/tmp"}})
        self.assertFalse(s["production_generation_enabled"])

    def test_kill_switch_off_by_default_but_present(self):
        s = run.video_settings({"paths": {"state_dir": "/tmp"}})
        self.assertIn("kill_switch", s)
        self.assertFalse(s["kill_switch"])

    def test_defaults_cover_markets_budget_and_caps(self):
        s = run.video_settings({"paths": {"state_dir": "/tmp"}})
        for key in ("markets", "daily_budget_usd", "max_jobs_per_run",
                    "max_attempts", "ledger_root", "enabled"):
            self.assertIn(key, s)
        self.assertFalse(s["enabled"])

    def test_config_example_ships_generation_disabled(self):
        import json
        with open(os.path.join(BASE, "config.example.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertIn("video", cfg)
        self.assertIs(cfg["video"]["production_generation_enabled"], False)
        self.assertIs(cfg["video"]["enabled"], False)


# ---------------------------------------------------------------------------
# 3. video status — 빈 원장에서도 동작
# ---------------------------------------------------------------------------


class TestVideoStatus(VideoRunTestCase):

    def test_status_on_empty_ledger_succeeds(self):
        code, out = self.run_video("status")
        self.assertEqual(code, 0)
        self.assertIn("총 0건", out)

    def test_status_reports_production_flag(self):
        _, out = self.run_video("status")
        self.assertIn("production_generation_enabled", out)
        self.assertIn("꺼짐", out)

    def test_status_json(self):
        import json
        code, out = self.run_video("status", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["ledger"]["total"], 0)
        self.assertFalse(payload["settings"]["production_generation_enabled"])

    def test_status_counts_enqueued_job(self):
        vq.VideoLedger(self.ledger_root).enqueue(make_job())
        _, out = self.run_video("status")
        self.assertIn("총 1건", out)


# ---------------------------------------------------------------------------
# 4. video enqueue
# ---------------------------------------------------------------------------


class TestVideoEnqueue(VideoRunTestCase):

    def _job_file(self, job_id="job-1"):
        path = os.path.join(self.tmp, f"{job_id}.json")
        vc.save_job(path, make_job(job_id=job_id))
        return path

    def test_enqueue_from_job_file_adds_job(self):
        code, out = self.run_video("enqueue", "--job-file", self._job_file())
        self.assertEqual(code, 0)
        self.assertEqual(vq.VideoLedger(self.ledger_root).stats()["total"], 1)
        self.assertIn("job-1", out)

    def test_enqueue_is_idempotent(self):
        path = self._job_file()
        self.run_video("enqueue", "--job-file", path)
        code, out = self.run_video("enqueue", "--job-file", path)
        self.assertEqual(code, 0)
        self.assertEqual(vq.VideoLedger(self.ledger_root).stats()["total"], 1)
        self.assertIn("기존", out)

    def test_enqueue_requires_job_file(self):
        code, _ = self.run_video("enqueue")
        self.assertNotEqual(code, 0)

    def test_enqueue_rejects_market_outside_config(self):
        self.cfg["video"]["markets"] = ["US"]
        code, out = self.run_video("enqueue", "--job-file", self._job_file())
        self.assertNotEqual(code, 0)
        self.assertIn("market", out.lower())
        self.assertEqual(vq.VideoLedger(self.ledger_root).stats()["total"], 0)

    def test_enqueue_blocked_by_kill_switch(self):
        self.cfg["video"]["kill_switch"] = True
        code, out = self.run_video("enqueue", "--job-file", self._job_file())
        self.assertNotEqual(code, 0)
        self.assertIn("킬스위치", out)
        self.assertEqual(vq.VideoLedger(self.ledger_root).stats()["total"], 0)


# ---------------------------------------------------------------------------
# 5. video process — 돈이 나가는 유일한 경로. 기본은 거부.
# ---------------------------------------------------------------------------


class TestVideoProcessRefusesByDefault(VideoRunTestCase):

    def setUp(self):
        super().setUp()
        vq.VideoLedger(self.ledger_root).enqueue(make_job())

    def test_process_refuses_when_production_flag_off(self):
        code, out = self.run_video("process")
        self.assertNotEqual(code, 0)
        self.assertIn("production_generation_enabled", out)

    def test_process_leaves_job_queued_when_refused(self):
        self.run_video("process")
        entry = vq.VideoLedger(self.ledger_root).get("job-1")
        self.assertEqual(entry["state"], vc.STATE_QUEUED)
        self.assertIsNone(entry["lease"])

    def test_process_refuses_when_kill_switch_on(self):
        self.cfg["video"]["production_generation_enabled"] = True
        self.cfg["video"]["enabled"] = True
        self.cfg["video"]["kill_switch"] = True
        code, out = self.run_video("process")
        self.assertNotEqual(code, 0)
        self.assertIn("킬스위치", out)
        self.assertEqual(vq.VideoLedger(self.ledger_root).get("job-1")["state"],
                         vc.STATE_QUEUED)

    def test_process_refuses_when_video_disabled(self):
        self.cfg["video"]["production_generation_enabled"] = True
        self.cfg["video"]["enabled"] = False
        code, out = self.run_video("process")
        self.assertNotEqual(code, 0)
        self.assertIn("enabled", out)

    def test_dry_run_process_does_not_generate(self):
        """--dry-run 은 플래그가 꺼져 있어도 통과하되 유료 호출은 하지 않는다."""
        code, out = self.run_video("process", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("job-1", out)
        self.assertEqual(vq.VideoLedger(self.ledger_root).get("job-1")["state"],
                         vc.STATE_QUEUED)


# ---------------------------------------------------------------------------
# 6. video rehearsal — 유료 0 · 발행 0
# ---------------------------------------------------------------------------


class TestVideoRehearsal(VideoRunTestCase):
    """리허설은 전제조건 충족 여부에 따라 종료코드가 갈린다(0=충족, 1=미충족).

    이 머신에 faster-whisper 가 설치돼 있는지에 테스트가 좌우되면 안 되므로,
    '유료 0/발행 0' 같은 불변식은 _has_module 을 고정해 놓고 검증한다."""

    def _met(self):
        return mock.patch.object(run, "_has_module", side_effect=lambda n: True)

    def test_rehearsal_succeeds_on_empty_ledger(self):
        with self._met():
            code, out = self.run_video("rehearsal")
        self.assertEqual(code, 0)
        self.assertIn("리허설", out)

    def test_rehearsal_makes_no_paid_calls_and_no_publishes(self):
        vq.VideoLedger(self.ledger_root).enqueue(make_job())
        with self._met():
            code, out = self.run_video("rehearsal", "--market", "KR")
        # setUp 의 지뢰가 밟혔다면 AssertionError 로 이미 죽었다.
        self.assertEqual(code, 0)
        self.assertIn("유료 호출 0건", out)
        self.assertIn("발행 0건", out)

    def test_rehearsal_reports_no_paid_calls_even_when_prereq_missing(self):
        """전제조건이 없어도 리허설 자체는 절대 돈을 쓰지 않는다."""
        with mock.patch.object(run, "_has_module", side_effect=lambda n: False):
            code, out = self.run_video("rehearsal")
        self.assertIn("유료 호출 0건", out)
        self.assertIn("발행 0건", out)
        self.assertNotEqual(code, 0)

    def test_rehearsal_does_not_mutate_ledger_state(self):
        vq.VideoLedger(self.ledger_root).enqueue(make_job())
        before = vq.VideoLedger(self.ledger_root).get("job-1")
        self.run_video("rehearsal")
        after = vq.VideoLedger(self.ledger_root).get("job-1")
        self.assertEqual(before["state"], after["state"])
        self.assertEqual(before["attempts"], after["attempts"])

    def test_rehearsal_reports_transcriber_prerequisite(self):
        """QA 게이트는 fail-closed 다 — 전사기가 없으면 모든 실영상이 QA 실패한다.
        운영자가 유료 실행 중에 발견하면 안 되므로 리허설이 먼저 말해야 한다."""
        _, out = self.run_video("rehearsal")
        self.assertIn("faster-whisper", out)

    def test_rehearsal_marks_missing_transcriber_as_blocking(self):
        with mock.patch.object(run, "_has_module", side_effect=lambda n: False):
            code, out = self.run_video("rehearsal")
        self.assertIn("미충족", out)
        self.assertNotEqual(code, 0)

    def test_rehearsal_passes_when_prerequisites_met(self):
        with self._met():
            code, out = self.run_video("rehearsal")
        self.assertEqual(code, 0)
        self.assertIn("충족", out)

    def test_rehearsal_reports_fixture_when_given(self):
        fixture = os.path.join(BASE, "fixtures", "viral_ugc_sample.jsonl")
        with self._met():
            code, out = self.run_video("rehearsal", "--market", "KR",
                                       "--fixture", fixture)
        self.assertEqual(code, 0)
        self.assertIn("관측", out)

    def test_rehearsal_reports_missing_fixture_without_crashing(self):
        code, out = self.run_video("rehearsal", "--fixture",
                                   os.path.join(self.tmp, "nope.jsonl"))
        self.assertNotEqual(code, 0)
        self.assertIn("픽스처", out)


class TestVideoCommandSurface(VideoRunTestCase):

    def test_unknown_subcommand_is_rejected(self):
        code, _ = self.run_video("teleport")
        self.assertNotEqual(code, 0)

    def test_no_subcommand_prints_help(self):
        code, out = self.run_video()
        self.assertNotEqual(code, 0)
        for name in ("enqueue", "process", "status", "rehearsal"):
            self.assertIn(name, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
