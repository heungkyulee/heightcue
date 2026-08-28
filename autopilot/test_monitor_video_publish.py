#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor_video_publish 회귀 — 결정성이 유일한 관심사.

Hermes 크론의 monitor 모드는 **stdout 바이트 해시**로 에이전트 기동 여부를
정한다. 출력에 타임스탬프·경과시간·무작위 순서가 섞이면 매 틱마다 '변경됨'이
되어 5분마다 LLM 이 깨어난다(비용·중복 발행 위험). 그래서 이 테스트가 지키는
것은 기능이 아니라 **같은 상태 → 같은 바이트**다.

네트워크·실제 원장 없이 돈다. 원장은 ``list_jobs`` 하나만 가진 스텁으로 대체한다
(video_handoff.list_ready 가 요구하는 유일한 표면).
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import monitor_video_publish as mon  # noqa: E402
from video_contracts import STATE_READY_TO_PUBLISH  # noqa: E402


def entry(job_id, *, market="kr", product_id="P-1", attempts=0,
          sha="a" * 64, recovered=None, attempted_at=None, state=None):
    """발행 대기 원장 엔트리 하나 (패킷 포함)."""
    return {
        "job_id": job_id,
        "state": state or STATE_READY_TO_PUBLISH,
        "attempts": attempts,
        "created_at": 1000.0,
        "updated_at": 2000.0,
        "recovered_from": recovered,
        "publish_attempted_at": attempted_at,
        "packet": {
            "job_id": job_id,
            "run_id": "R-" + job_id,
            "product_id": product_id,
            "market": market,
            "video_path": f"/tmp/{job_id}.mp4",
            "video_sha256": sha,
            "caption": "캡션",
            "idempotency_key": "K-" + job_id,
        },
    }


class StubLedger:
    """video_handoff.list_ready 가 쓰는 표면만 흉내낸다."""

    def __init__(self, entries):
        self._entries = list(entries)

    def list_jobs(self, state=None):
        return [dict(e) for e in self._entries
                if state is None or e["state"] == state]


def run(ledger):
    buf = io.StringIO()
    with redirect_stdout(buf):
        mon.emit(ledger)
    return buf.getvalue()


class TestNoOp(unittest.TestCase):
    def test_empty_ledger_gives_stable_noop_marker(self):
        out = run(StubLedger([]))
        self.assertEqual(out, mon.NO_READY_OUTPUT)
        self.assertEqual(out, run(StubLedger([])))

    def test_jobs_without_packet_are_not_ready(self):
        bad = entry("J-1")
        bad["packet"] = None
        self.assertEqual(run(StubLedger([bad])), mon.NO_READY_OUTPUT)


class TestDeterminism(unittest.TestCase):
    def test_same_state_twice_is_byte_identical(self):
        entries = [entry("J-2", market="us"), entry("J-1")]
        self.assertEqual(run(StubLedger(entries)), run(StubLedger(entries)))

    def test_ledger_ordering_does_not_change_output(self):
        a = entry("J-1")
        b = entry("J-2", market="us")
        c = entry("J-3", attempts=2)
        self.assertEqual(run(StubLedger([a, b, c])),
                         run(StubLedger([c, a, b])))

    def test_output_carries_no_timestamps(self):
        out = run(StubLedger([entry("J-1")]))
        for volatile in ("created_at", "updated_at", "1000", "2000"):
            self.assertNotIn(volatile, out)

    def test_output_carries_no_caption_or_secrets(self):
        e = entry("J-1")
        e["packet"]["access_token"] = "tok-should-never-appear"
        out = run(StubLedger([e]))
        self.assertNotIn("tok-should-never-appear", out)
        self.assertNotIn("캡션", out)

    def test_output_ends_with_single_newline(self):
        out = run(StubLedger([entry("J-1")]))
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))


class TestChangeDetection(unittest.TestCase):
    def test_newly_ready_video_changes_output(self):
        before = run(StubLedger([entry("J-1")]))
        after = run(StubLedger([entry("J-1"), entry("J-2")]))
        self.assertNotEqual(before, after)
        self.assertIn("J-2", after)

    def test_first_ready_video_wakes_agent(self):
        self.assertNotEqual(run(StubLedger([])),
                            run(StubLedger([entry("J-1")])))

    def test_attempt_change_changes_output(self):
        self.assertNotEqual(run(StubLedger([entry("J-1", attempts=0)])),
                            run(StubLedger([entry("J-1", attempts=1)])))

    def test_existence_check_flag_is_visible_and_changes_output(self):
        plain = run(StubLedger([entry("J-1")]))
        risky = run(StubLedger([entry("J-1", recovered="publishing",
                                      attempted_at=123.0)]))
        self.assertNotEqual(plain, risky)
        self.assertIn("existence_check=yes", risky)
        self.assertIn("existence_check=no", plain)

    def test_published_job_disappears_from_output(self):
        gone = entry("J-1", state="published")
        self.assertEqual(run(StubLedger([gone])), mon.NO_READY_OUTPUT)


class TestRowShape(unittest.TestCase):
    def test_row_reports_id_market_product_and_attempts(self):
        out = run(StubLedger([entry("J-9", market="us", product_id="B0ABC",
                                    attempts=3)]))
        self.assertIn("job=J-9", out)
        self.assertIn("market=us", out)
        self.assertIn("product=B0ABC", out)
        self.assertIn("attempts=3", out)
        self.assertIn("state=%s" % STATE_READY_TO_PUBLISH, out)

    def test_count_header_present(self):
        out = run(StubLedger([entry("J-1"), entry("J-2")]))
        self.assertTrue(out.startswith("video_publish_ready=2\n"), out)

    def test_sha_is_truncated_not_full(self):
        out = run(StubLedger([entry("J-1", sha="b" * 64)]))
        self.assertIn("sha=" + "b" * 12, out)
        self.assertNotIn("b" * 64, out)


class TestCli(unittest.TestCase):
    def test_main_on_empty_real_root_is_the_noop_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = mon.main(["--root", tmp])
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue(), mon.NO_READY_OUTPUT)

    def test_main_never_raises_on_broken_ledger(self):
        """모니터가 죽으면 크론은 매 틱 다른 stderr 로 시끄러워진다.
        읽을 수 없는 원장은 조용한 no-op 으로 떨어진다."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ledger.json"), "w") as fh:
                fh.write("{not json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = mon.main(["--root", tmp])
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue(), mon.NO_READY_OUTPUT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
