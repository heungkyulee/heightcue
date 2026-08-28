#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_viral_ugc 테스트 — 전부 오프라인. 네트워크 호출 0건.

증명 대상:
  1. dry-run 산출물이 viral_ugc 의 Observation 으로 그대로 수용된다.
  2. 미관측 지표는 0 이 아니라 부재(None) 로 남는다.
  3. 미디어 사본 키는 절대 저장되지 않는다.
  4. 바운드(건수·쿼리·월클럭)가 실제로 실행을 멈춘다.
  5. dry-run 경로에서 외부 명령이 단 한 번도 실행되지 않는다 (runner 시임).
  6. 읽기 전용 가드가 쓰기/다운로드성 명령을 거부한다.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collect_viral_ugc as cvu  # noqa: E402
import viral_ugc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "viral_ugc_sample.jsonl")


class RecordingRunner:
    """runner 시임 — 호출된 argv 를 기록만 하고 아무것도 실행하지 않는다."""

    def __init__(self, stdout="{}", returncode=0):
        self.calls = []
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, argv, timeout=None):
        self.calls.append({"argv": list(argv), "timeout": timeout})
        return {"argv": list(argv), "returncode": self.returncode,
                "stdout": self.stdout, "stderr": ""}


class ExplodingRunner:
    """호출되면 즉시 테스트를 실패시키는 runner — '네트워크 0건' 증명용."""

    def __call__(self, argv, timeout=None):
        raise AssertionError(
            f"dry-run 경로에서 외부 명령이 실행됐다 (네트워크 금지): {argv!r}")


def _fake_clock(*values):
    """호출될 때마다 다음 값을 돌려주는 단조 시계 (마지막 값 유지)."""
    seq = list(values)

    def clock():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return clock


def _write_lines(directory, name, rows):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


class TestBoundsAreNamedAndSane(unittest.TestCase):
    def test_module_declares_named_bounds(self):
        for const in ("MAX_QUERIES_PER_RUN", "MAX_PAGES_PER_QUERY",
                      "MAX_POSTS_PER_QUERY", "MAX_OBSERVATIONS_PER_RUN",
                      "WALL_CLOCK_BUDGET_SECONDS", "STEP_TIMEOUT_SECONDS"):
            self.assertTrue(hasattr(cvu, const), f"상수 누락: {const}")
            self.assertGreater(getattr(cvu, const), 0, const)

    def test_wall_clock_budget_is_below_the_prior_timeout(self):
        # 이전 라이브 시도는 300초에서 죽었다. 예산은 그보다 확실히 작아야 한다.
        self.assertLess(cvu.WALL_CLOCK_BUDGET_SECONDS, 300)

    def test_bounds_reject_nonpositive_values(self):
        with self.assertRaises(cvu.BoundsError):
            cvu.CollectionBounds(max_observations_per_run=0).validate()
        with self.assertRaises(cvu.BoundsError):
            cvu.CollectionBounds(wall_clock_budget_seconds=-1).validate()

    def test_bounds_cannot_exceed_module_ceilings(self):
        with self.assertRaises(cvu.BoundsError):
            cvu.CollectionBounds(
                wall_clock_budget_seconds=cvu.WALL_CLOCK_BUDGET_SECONDS + 1
            ).validate()


class TestDryRunProducesLedgerReadyObservations(unittest.TestCase):
    def test_dry_run_emits_valid_observations(self):
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             runner=ExplodingRunner())
        self.assertEqual(result.source, "fixture")
        self.assertTrue(result.observations)
        for obs in result.observations:
            self.assertIsInstance(obs, viral_ugc.Observation)
            obs.validate()

    def test_observations_are_accepted_by_the_pattern_ledger(self):
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             runner=ExplodingRunner())
        kr = [o for o in result.observations if o.market == "KR"]
        self.assertTrue(kr)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = viral_ugc.PatternLedger(tmp, "KR")
            for obs in kr:
                ledger.record_observation(obs)
            self.assertEqual(len(ledger.observations()), len(kr))

    def test_every_observation_carries_provenance(self):
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             runner=ExplodingRunner())
        for obs in result.observations:
            self.assertTrue(obs.source_url.startswith("https://"))
            self.assertTrue(obs.observed_at)
            self.assertTrue(obs.engagement.observed_at)

    def test_dry_run_is_deterministic(self):
        a = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                        runner=ExplodingRunner())
        b = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                        runner=ExplodingRunner())
        self.assertEqual([o.to_dict() for o in a.observations],
                         [o.to_dict() for o in b.observations])

    def test_market_filter(self):
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             markets=("US",), runner=ExplodingRunner())
        self.assertTrue(result.observations)
        self.assertEqual({o.market for o in result.observations}, {"US"})


class TestUnseenMetricsStayAbsent(unittest.TestCase):
    def test_unobserved_counters_are_none_not_zero(self):
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             runner=ExplodingRunner())
        shorts = [o for o in result.observations
                  if o.observation_id == "obs-kr-004"]
        self.assertEqual(len(shorts), 1)
        eng = shorts[0].engagement
        self.assertIsNone(eng.reposts)
        self.assertIsNone(eng.shares)
        self.assertNotEqual(eng.reposts, 0)
        # 직렬화에도 키 자체가 없어야 한다.
        self.assertNotIn("reposts", shorts[0].to_dict()["engagement"])
        self.assertNotIn("shares", shorts[0].to_dict()["engagement"])

    def test_collector_never_backfills_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_lines(tmp, "partial.jsonl", [{
                "observation_id": "obs-kr-900", "market": "KR",
                "platform": "threads",
                "source_url": "https://example.invalid/x",
                "observed_at": "2026-08-20T10:00:00+09:00",
                "product_id": "p1", "category": "sleep",
                "engagement": {"observed_at": "2026-08-20T10:00:00+09:00",
                               "likes": 5},
            }])
            result = cvu.collect(dry_run=True, fixture_path=path,
                                 runner=ExplodingRunner())
            eng = result.observations[0].engagement
            self.assertEqual(eng.likes, 5)
            for absent in ("replies", "reposts", "shares", "views"):
                self.assertIsNone(getattr(eng, absent), absent)


class TestMediaIsNeverStored(unittest.TestCase):
    def _media_row(self, key):
        return {
            "observation_id": "obs-kr-901", "market": "KR",
            "platform": "threads", "source_url": "https://example.invalid/y",
            "observed_at": "2026-08-20T10:00:00+09:00",
            "product_id": "p1", "category": "sleep",
            "engagement": {"observed_at": "2026-08-20T10:00:00+09:00",
                           "likes": 5},
            key: "/tmp/stolen.mp4",
        }

    def test_media_key_is_rejected_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_lines(tmp, "media.jsonl",
                                [self._media_row("video_path")])
            with self.assertRaises(viral_ugc.MediaPolicyError):
                cvu.collect(dry_run=True, fixture_path=path,
                            runner=ExplodingRunner())

    def test_media_key_is_dropped_in_lenient_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_lines(tmp, "media.jsonl",
                                [self._media_row("image_bytes")])
            result = cvu.collect(dry_run=True, fixture_path=path,
                                 strict=False, runner=ExplodingRunner())
            self.assertEqual(result.observations, [])
            self.assertEqual(len(result.rejected), 1)
            self.assertIn("image_bytes", result.rejected[0]["error"])

    def test_written_output_contains_no_media_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.jsonl")
            result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                                 runner=ExplodingRunner())
            cvu.write_observations(out, result.observations)
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
            for key in viral_ugc.FORBIDDEN_MEDIA_KEYS:
                self.assertNotIn(key, text)
            rows = [json.loads(l) for l in text.splitlines() if l.strip()]
            self.assertEqual(len(rows), len(result.observations))


class TestBoundsActuallyStopTheRun(unittest.TestCase):
    def test_observation_cap_truncates(self):
        bounds = cvu.CollectionBounds(max_observations_per_run=2)
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             bounds=bounds, runner=ExplodingRunner())
        self.assertEqual(len(result.observations), 2)
        self.assertIn("max_observations_per_run", result.bounds_hit)

    def test_wall_clock_budget_stops_the_run(self):
        bounds = cvu.CollectionBounds(wall_clock_budget_seconds=10)
        clock = _fake_clock(0.0, 1.0, 2.0, 99.0, 99.0, 99.0, 99.0, 99.0)
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             bounds=bounds, clock=clock,
                             runner=ExplodingRunner())
        self.assertIn("wall_clock_budget_seconds", result.bounds_hit)
        self.assertLess(len(result.observations), 8)

    def test_no_bounds_hit_on_a_small_complete_run(self):
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             runner=ExplodingRunner())
        self.assertEqual(result.bounds_hit, [])

    def test_plan_respects_query_and_page_caps(self):
        plan = cvu.build_plan(markets=("KR", "US"),
                              bounds=cvu.CollectionBounds(
                                  max_queries_per_run=2,
                                  max_pages_per_query=1))
        self.assertEqual(len(plan), 2)
        for step in plan:
            self.assertEqual(step["pages"], 1)
            self.assertLessEqual(step["max_posts"],
                                 cvu.MAX_POSTS_PER_QUERY)


class TestNoNetworkAndReadOnly(unittest.TestCase):
    def test_dry_run_invokes_no_command_at_all(self):
        runner = RecordingRunner()
        cvu.collect(dry_run=True, fixture_path=FIXTURE, runner=runner)
        self.assertEqual(runner.calls, [])

    def test_preflight_and_postflight_are_skippable(self):
        runner = RecordingRunner(stdout='{"ok": true}')
        result = cvu.collect(dry_run=True, fixture_path=FIXTURE,
                             runner=runner, preflight=True, postflight=True)
        cmds = [" ".join(c["argv"]) for c in runner.calls]
        self.assertEqual(cmds, ["agent-reach doctor --json",
                                "agent-reach check-update"])
        self.assertIsNotNone(result.preflight)
        self.assertIsNotNone(result.postflight)
        for call in runner.calls:
            self.assertIsNotNone(call["timeout"])
            self.assertLessEqual(call["timeout"], cvu.STEP_TIMEOUT_SECONDS)

    def test_read_only_guard_rejects_write_commands(self):
        for argv in (["aside", "--account", "u0", "exec", "like the post"],
                     ["aside", "--account", "u0", "exec", "post a reply"],
                     ["aside", "--account", "u0", "exec", "follow @someone"],
                     ["yt-dlp", "https://example.invalid/v"],
                     ["curl", "-X", "POST", "https://example.invalid"]):
            with self.assertRaises(cvu.ReadOnlyViolation, msg=str(argv)):
                cvu.assert_read_only(argv)

    def test_read_only_guard_allows_metadata_commands(self):
        cvu.assert_read_only(["agent-reach", "doctor", "--json"])
        cvu.assert_read_only(["yt-dlp", "--skip-download", "--dump-json",
                              "https://example.invalid/v"])
        cvu.assert_read_only(["aside", "--account", "u0", "exec",
                              "Read-only: observe and return structured data"])

    def test_runner_defaults_are_not_invoked_without_live_flag(self):
        # 라이브 수집은 명시적으로 켜야만 한다.
        with self.assertRaises(cvu.LiveCollectionDisabled):
            cvu.collect(dry_run=False, runner=RecordingRunner())


class TestCLI(unittest.TestCase):
    def test_cli_dry_run_writes_output_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "obs.jsonl")
            rc = cvu.main(["--dry-run", "--fixture", FIXTURE, "--out", out],
                          runner=ExplodingRunner())
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh.read().splitlines() if l.strip()]
            self.assertEqual(len(rows), 8)
            for row in rows:
                viral_ugc.Observation.from_dict(row).validate()

    def test_cli_requires_dry_run_or_explicit_live(self):
        rc = cvu.main(["--fixture", FIXTURE], runner=ExplodingRunner())
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
