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
import json
import os
import subprocess
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


class _StubCtx:
    """여러 mock.patch 를 하나의 with 로 묶는다 (+ 관찰값 seen 노출)."""

    def __init__(self, *patches):
        self._patches = patches
        self.seen = {}

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


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
        cfg = cfg or {"mode": {"dry_run": True}, "threads": {}, "paths": {},
                      "openrouter": {"model": "test-model"}}
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
                                                 "threads": {}, "paths": {},
                                                 "openrouter": {"model": "test-model"}}), \
                    mock.patch.object(sys, "argv", ["run.py"]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    run.main()
        d.assert_called_once()

    def test_video_command_does_not_shadow_existing(self):
        """`run.py video` 는 기존 4개 핸들러를 **한 번도** 부르지 않는다."""
        called = []
        with mock.patch.object(run, "video_command", return_value=0) as vid, \
                mock.patch.object(run, "daily",
                                  side_effect=lambda *a, **k: called.append("daily")), \
                mock.patch.object(run, "make_and_publish_value",
                                  side_effect=lambda *a, **k: called.append("post")), \
                mock.patch.object(run.comments_mod, "run",
                                  side_effect=lambda *a, **k: called.append("comments")), \
                mock.patch.object(run.improve, "run",
                                  side_effect=lambda *a, **k: called.append("weekly")):
            with self.assertRaises(SystemExit):
                self._main(["video", "status"])
        self.assertEqual(called, [])
        vid.assert_called_once()
        self.assertEqual(vid.call_args[0][1], ["status"])


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

    def test_money_flag_rejects_every_non_true_value(self):
        """돈 게이트는 **리터럴 True 만** 연다 — 나머지는 전부 닫힘(fail-closed).

        bool() 강제였을 때 실제로 이랬다: "false"→True, "off"→True, "no"→True,
        1→True. 사람이 OFF 를 뜻해 쓴 세 문자열이 전부 돈을 열었다.
        """
        for value in ("false", "off", "no", 1, "true", "True", "yes",
                      0, "", None, [], "1"):
            with self.subTest(value=value):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    s = run.video_settings(
                        {"paths": {"state_dir": "/tmp"},
                         "video": {"production_generation_enabled": value}})
                self.assertIs(s["production_generation_enabled"], False)

    def test_money_flag_opens_only_for_literal_true(self):
        s = run.video_settings({"paths": {"state_dir": "/tmp"},
                                "video": {"production_generation_enabled": True}})
        self.assertIs(s["production_generation_enabled"], True)

    def test_money_flag_refuses_loudly(self):
        """조용히 False 로 깎지 않는다 — 운영자가 켰다고 믿으면 안 되므로 말한다."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            run.video_settings({"paths": {"state_dir": "/tmp"},
                                "video": {"production_generation_enabled": "true"}})
        out = buf.getvalue()
        self.assertIn("production_generation_enabled", out)
        self.assertIn("true", out)

    def test_other_gates_are_also_strict(self):
        """enabled 는 애매하면 꺼짐으로, kill_switch 는 애매하면 **걸림**으로 떨어진다.

        방향이 반대인 게 핵심이다 — 둘 다 '안전한 쪽'이 서로 다르다.
        """
        buf = io.StringIO()
        with redirect_stdout(buf):
            s = run.video_settings({"paths": {"state_dir": "/tmp"},
                                    "video": {"enabled": "false"}})
        self.assertIs(s["enabled"], False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            s = run.video_settings({"paths": {"state_dir": "/tmp"},
                                    "video": {"kill_switch": "false"}})
        self.assertIs(s["kill_switch"], True)
        self.assertIn("kill_switch", buf.getvalue())

    def test_bare_string_market_becomes_one_element_list(self):
        """markets="KR" 이 ['K','R'] 로 쪼개지면 모든 market 게이트가 조용히 막힌다."""
        s = run.video_settings({"paths": {"state_dir": "/tmp"},
                                "video": {"markets": "KR"}})
        self.assertEqual(s["markets"], ["KR"])

    def test_ledger_root_is_always_concrete(self):
        """paths 가 없어도 원장 경로는 None 이 아니라 실제 경로여야 한다."""
        s = run.video_settings({})
        self.assertIsInstance(s["ledger_root"], str)
        self.assertTrue(s["ledger_root"])
        self.assertEqual(s["ledger_root"], vq.default_root())

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

    def test_dry_run_is_blocked_by_kill_switch(self):
        """README §5-2 는 kill_switch=true 가 enqueue·process 를 '즉시' 막는다고
        약속한다. --dry-run 도 process 다 — 문서와 코드가 어긋나면 안 된다."""
        self.cfg["video"]["kill_switch"] = True
        code, out = self.run_video("process", "--dry-run")
        self.assertNotEqual(code, 0)
        self.assertIn("킬스위치", out)
        self.assertNotIn("job-1", out)


# ---------------------------------------------------------------------------
# 6. video rehearsal — 유료 0 · 발행 0
# ---------------------------------------------------------------------------


class TestVideoRehearsal(VideoRunTestCase):
    """리허설은 전제조건 충족 여부에 따라 종료코드가 갈린다(0=충족, 1=미충족).

    이 머신에 OpenMontage 가 있는지에 테스트가 좌우되면 안 되므로,
    '유료 0/발행 0' 같은 불변식은 프로브를 고정해 놓고 검증한다."""

    def _met(self):
        return mock.patch.object(run, "_probe_openmontage_transcriber",
                                 return_value=(True, "고정: 충족"))

    def _unmet(self):
        return mock.patch.object(run, "_probe_openmontage_transcriber",
                                 return_value=(False, "고정: 미충족"))

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
        with self._unmet():
            code, out = self.run_video("rehearsal")
        self.assertIn("유료 호출 0건", out)
        self.assertIn("발행 0건", out)
        self.assertNotEqual(code, 0)

    def test_rehearsal_does_not_mutate_ledger_state(self):
        vq.VideoLedger(self.ledger_root).enqueue(make_job())
        before = vq.VideoLedger(self.ledger_root).get("job-1")
        with self._met():
            self.run_video("rehearsal")
        after = vq.VideoLedger(self.ledger_root).get("job-1")
        self.assertEqual(before["state"], after["state"])
        self.assertEqual(before["attempts"], after["attempts"])

    def test_rehearsal_reports_transcriber_prerequisite(self):
        """QA 게이트는 fail-closed 다 — 전사기가 없으면 모든 실영상이 QA 실패한다.
        운영자가 유료 실행 중에 발견하면 안 되므로 리허설이 먼저 말해야 한다."""
        with self._met():
            _, out = self.run_video("rehearsal")
        self.assertIn("OpenMontage", out)
        self.assertIn("transcriber", out)

    def test_rehearsal_marks_missing_transcriber_as_blocking(self):
        with self._unmet():
            code, out = self.run_video("rehearsal")
        self.assertIn("미충족", out)
        self.assertNotEqual(code, 0)

    def test_prereq_probes_openmontage_not_the_local_venv(self):
        """전사기는 autopilot venv 가 아니라 OpenMontage 를 통해 돈다.

        로컬 venv 의 faster_whisper 설치 여부로 판정하면 거짓 초록이 난다 —
        OpenMontage 가 없는데 [충족] 을 찍고 유료 실행을 승인해버린다."""
        import video_qa
        with mock.patch.object(video_qa, "DEFAULT_OPENMONTAGE_ROOT",
                               os.path.join(self.tmp, "no-such-openmontage")):
            ok, detail = run._probe_openmontage_transcriber()
        self.assertFalse(ok)
        self.assertIn("no-such-openmontage", detail)

    def test_prereq_fails_when_transcriber_entrypoint_absent(self):
        """루트만 있고 transcriber 가 없으면 미충족이다(디렉터리 존재≠실행가능)."""
        import video_qa
        fake = os.path.join(self.tmp, "om")
        os.makedirs(os.path.join(fake, "tools", "analysis"))
        with mock.patch.object(video_qa, "DEFAULT_OPENMONTAGE_ROOT", fake):
            ok, detail = run._probe_openmontage_transcriber()
        self.assertFalse(ok)
        self.assertIn("transcriber", detail)

    # -- 러너빌리티(파일 존재 ≠ 실행 가능) ---------------------------------
    #
    # transcriber.py 는 있는데 faster_whisper/whisperx 가 어디에도 없으면
    # QA 전사는 100% 실패한다. 파일 존재만 보는 프로브는 그 상태에서
    # [충족] 을 찍어 유료 실행을 승인해버린다 — 막으려던 사고 그 자체다.
    # 아래 테스트들은 이 머신의 실제 패키지 상태에 의존하지 않도록
    # 인터프리터 프로브(subprocess)를 고정한다.

    #: 스텁 인터프리터 경로 — 이 머신의 실제 패키지/venv 상태에 의존하지 않는다.
    FAKE_OM_PYTHON = "/fake/openmontage/.venv/bin/python"

    def _fake_om(self, payload=None, returncode=0, stdout=None, exc=None):
        """OpenMontage 루트를 만들고 인터프리터·subprocess 를 모두 고정한다.

        인터프리터 해석까지 스텁해야 오프라인·결정적이다 (실제 OpenMontage
        venv 가 있든 없든 같은 결과가 나와야 한다).
        """
        fake = os.path.join(self.tmp, "om-run")
        os.makedirs(os.path.join(fake, "tools", "analysis"), exist_ok=True)
        with open(os.path.join(fake, "tools", "analysis", "transcriber.py"),
                  "w") as fh:
            fh.write("# stub\n")
        if stdout is None:
            stdout = json.dumps(payload or {})

        seen = {}

        def fake_run(*a, **kw):
            seen["cmd"] = a[0] if a else kw.get("args")
            seen["cwd"] = kw.get("cwd")
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(a[0] if a else [], returncode,
                                               stdout=stdout, stderr="boom")
        import video_qa
        stub = _StubCtx(
            mock.patch.object(video_qa, "_openmontage_python",
                              return_value=self.FAKE_OM_PYTHON),
            mock.patch.object(run.subprocess, "run", side_effect=fake_run))
        stub.seen = seen
        return (fake,
                mock.patch.object(video_qa, "DEFAULT_OPENMONTAGE_ROOT", fake),
                stub)

    # -- 인터프리터 일치 (거짓 빨강 방지) ----------------------------------
    #
    # 프로브는 자기가 대변하는 코드(video_qa)와 **같은 인터프리터**를 봐야
    # 한다. sys.executable 을 보면 OpenMontage venv 에 전사기가 설치돼
    # 있어도 [미충족] 을 찍어 정당한 유료 실행을 막는다 (거짓 빨강).

    def test_prereq_probes_the_interpreter_video_qa_actually_uses(self):
        """프로브 인터프리터 == video_qa._openmontage_python 결과."""
        fake, root_patch, stub = self._fake_om(
            {"backends": {"faster_whisper": True, "whisperx": False}})
        with root_patch, stub:
            ok, detail = run._probe_openmontage_transcriber()
        self.assertTrue(ok)
        self.assertEqual(stub.seen["cmd"][0], self.FAKE_OM_PYTHON)
        self.assertNotEqual(stub.seen["cmd"][0], sys.executable)
        self.assertIn(self.FAKE_OM_PYTHON, detail)

    def test_prereq_unmet_message_names_the_probed_interpreter(self):
        """미충족 사유는 실제로 프로브한 인터프리터를 적어야 한다."""
        fake, root_patch, stub = self._fake_om(
            {"backends": {"faster_whisper": False, "whisperx": False}})
        with root_patch, stub:
            ok, detail = run._probe_openmontage_transcriber()
        self.assertFalse(ok)
        self.assertIn(self.FAKE_OM_PYTHON, detail)
        self.assertNotIn(sys.executable, detail)

    def test_prereq_unmet_when_openmontage_interpreter_missing(self):
        """OpenMontage 인터프리터가 없으면 fail closed + 경로를 명시한다."""
        import video_qa
        fake = os.path.join(self.tmp, "om-nopy")
        os.makedirs(os.path.join(fake, "tools", "analysis"), exist_ok=True)
        open(os.path.join(fake, "tools", "analysis", "transcriber.py"),
             "w").close()
        missing = os.path.join(fake, ".venv", "bin", "python")
        with mock.patch.object(video_qa, "DEFAULT_OPENMONTAGE_ROOT", fake):
            ok, detail = run._probe_openmontage_transcriber()
        self.assertFalse(ok, "인터프리터가 없는데 충족으로 보고했다")
        self.assertIn(missing, detail)

    def test_install_hint_names_the_openmontage_venv_only(self):
        """설치 안내는 OpenMontage venv 만 가리킨다 (구식 괄호 제거)."""
        self.assertIn("OpenMontage", run.TRANSCRIBER_INSTALL_HINT)
        self.assertNotIn("heightcue-autopilot/.venv",
                         run.TRANSCRIBER_INSTALL_HINT)

    def test_prereq_unmet_when_no_transcription_backend_imports(self):
        """전사 백엔드가 하나도 import 되지 않으면 [미충족] 이어야 한다."""
        fake, root_patch, run_patch = self._fake_om(
            {"backends": {"faster_whisper": False, "whisperx": False}})
        with root_patch, run_patch:
            ok, detail = run._probe_openmontage_transcriber()
        self.assertFalse(ok, "백엔드가 없는데 충족으로 보고했다 (거짓 초록)")
        self.assertIn("faster_whisper", detail)

    def test_prereq_met_when_a_backend_imports(self):
        fake, root_patch, run_patch = self._fake_om(
            {"backends": {"faster_whisper": True, "whisperx": False}})
        with root_patch, run_patch:
            ok, detail = run._probe_openmontage_transcriber()
        self.assertTrue(ok)
        self.assertIn("faster_whisper", detail)

    def test_prereq_met_with_whisperx_alternative(self):
        fake, root_patch, run_patch = self._fake_om(
            {"backends": {"faster_whisper": False, "whisperx": True}})
        with root_patch, run_patch:
            ok, _ = run._probe_openmontage_transcriber()
        self.assertTrue(ok)

    def test_prereq_unmet_when_probe_output_unparseable(self):
        """해석 못 한 출력은 통과가 아니라 실패다 (fail closed)."""
        fake, root_patch, run_patch = self._fake_om(stdout="not json")
        with root_patch, run_patch:
            ok, _ = run._probe_openmontage_transcriber()
        self.assertFalse(ok)

    def test_prereq_unmet_when_probe_process_fails(self):
        fake, root_patch, run_patch = self._fake_om(stdout="", returncode=1)
        with root_patch, run_patch:
            ok, _ = run._probe_openmontage_transcriber()
        self.assertFalse(ok)

    def test_prereq_unmet_when_probe_raises(self):
        fake, root_patch, run_patch = self._fake_om(
            exc=subprocess.TimeoutExpired("py", 1))
        with root_patch, run_patch:
            ok, _ = run._probe_openmontage_transcriber()
        self.assertFalse(ok)

    def test_prereq_probe_runs_in_the_openmontage_root(self):
        """autopilot venv 가 아니라 OpenMontage 쪽에서 돌아야 한다."""
        fake, root_patch, stub = self._fake_om({"backends": {}})
        with root_patch, stub:
            run._probe_openmontage_transcriber()
        self.assertEqual(stub.seen["cwd"], fake)
        self.assertEqual(stub.seen["cmd"][0], self.FAKE_OM_PYTHON)

    def test_prereq_probe_makes_no_network_call(self):
        """프로브는 파일시스템 + 로컬 import 만 본다."""
        import socket
        with mock.patch.object(socket, "socket",
                               side_effect=AssertionError("네트워크 호출 발생")):
            run._probe_openmontage_transcriber()

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
