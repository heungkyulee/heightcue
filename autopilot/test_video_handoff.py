#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""발행 핸드오프(video_handoff) 테스트 — 실행:

    cd ~/heightcue-autopilot/autopilot
    ../.venv/bin/python -m unittest -v test_video_handoff.py

네트워크를 전혀 쓰지 않는다. 모든 테스트는 임시 디렉터리에서 돌며 실제 state/ 를 건드리지 않는다.

여기서 느슨하게 넘어가면 안 되는 것들(실제로 증명한다):

* **QA 게이트는 구조적으로 필수다.** 계약의 TRANSITIONS 는 generating -> ready_to_publish
  직행 간선을 허용하므로, QA 를 한 번도 안 돌린 호출자가 발행까지 걸어갈 수 있다.
  이 모듈이 그 간선 위에 `qa_report is not None and passed` 를 못 박는다.
* **중복 발행 위험.** Threads 발행 호출과 publish_done 사이에서 죽으면 큐가
  `recovered_from="publishing"` + `publish_attempted_at` 를 찍고 잡을 되살린다.
  그 신호를 달고 있는 잡은 존재 확인 없이 절대 재발행되지 않는다.
* **원자적 클레임.** 두 발행 워커가 같은 영상을 동시에 가져갈 수 없다 —
  진짜 스레드로 경합을 만든다.
* **자격증명 유출 금지.** 핸드오프 패킷에 토큰/시크릿류가 실리면 죽는다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

import analytics
import video_contracts as vc
import video_handoff as vh
import video_queue as vq
# 픽스처는 원장 테스트의 것을 재사용한다 — 같은 계약을 두 번 정의하지 않는다.
from test_video_queue import (SHA_OUT, make_handoff, make_job, make_manifest,
                              make_storyboard)

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

AFFILIATE_LINK = "https://link.coupang.com/a/abcdef"
DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
CAPTION = f"아이 키 고민 3년차가 고른 것\n\n{DISCLOSURE}"


def passing_qa(job_id="job-1", run_id="run-1"):
    return vc.QAReport(job_id=job_id, run_id=run_id, passed=True,
                       checks={"technical_container": {"passed": True}}, failures=[])


def failing_qa(job_id="job-1", run_id="run-1"):
    return vc.QAReport(job_id=job_id, run_id=run_id, passed=False, checks={},
                       failures=["technical_container: 길이 불일치"])


class HandoffTestCase(unittest.TestCase):
    """임시 원장 + 임시 mp4 를 쓰는 공통 베이스."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hc-video-handoff-test-")
        self.ledger = vq.VideoLedger(self.tmp)
        self.video_path = os.path.join(self.tmp, "final.mp4")
        with open(self.video_path, "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42fake-mp4-bytes")
        self.qa_path = os.path.join(self.tmp, "qa-job-1.json")
        with open(self.qa_path, "w", encoding="utf-8") as fh:
            json.dump(passing_qa().to_dict(), fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 헬퍼 ---------------------------------------------------------------

    def _job(self, job_id="job-1", run_id="run-1", **kw):
        return make_job(job_id=job_id, run_id=run_id, **kw)

    def _ready(self, job_id="job-1", run_id="run-1", worker="gen-1",
               qa=None, **job_kw):
        """queued -> generating -> ready_to_publish 까지 끌고 간다."""
        job = self._job(job_id, run_id, **job_kw)
        self.ledger.enqueue(job)
        self.ledger.claim(worker)
        report = passing_qa(job_id, run_id) if qa is None else qa
        return vh.promote_to_ready(
            self.ledger, job_id=job_id, worker_id=worker,
            manifest=make_manifest(job_id=job_id, run_id=run_id,
                                   storyboard_id=job_kw.get("storyboard_id", "sb-1"),
                                   n_cuts=job_kw.get("n_cuts", 2)),
            qa_report=report,
            video_path=self.video_path,
            caption=CAPTION,
            disclosure=DISCLOSURE,
            affiliate_link=AFFILIATE_LINK,
            qa_report_path=self.qa_path,
            account="heightcue",
        )


# ---------------------------------------------------------------------------
# 1. QA 게이트 — 이 태스크가 닫아야 하는 이월 지적사항
# ---------------------------------------------------------------------------


class QAGateTest(HandoffTestCase):

    def test_contract_still_allows_the_raw_edge(self):
        """계약 자체는 여전히 generating -> ready_to_publish 직행을 허용한다.

        이 테스트는 '왜 이 모듈에 게이트가 필요한가'의 근거다. 계약이 바뀌어
        간선이 사라지면 여기서 실패해 이 게이트를 다시 검토하게 만든다.
        """
        self.assertIn(vc.STATE_READY_TO_PUBLISH,
                      vc.TRANSITIONS[vc.STATE_GENERATING])

    def test_no_qa_report_cannot_hand_off(self):
        job = self._job()
        job.state = vc.STATE_GENERATING
        job.manifest = make_manifest()
        job.qa_report = None
        with self.assertRaises(vh.QAGateError) as ctx:
            vh.assert_qa_gate(job)
        self.assertIn("qa_report", str(ctx.exception))

    def test_failing_qa_report_cannot_hand_off(self):
        job = self._job()
        job.state = vc.STATE_GENERATING
        job.manifest = make_manifest()
        job.qa_report = failing_qa()
        with self.assertRaises(vh.QAGateError):
            vh.assert_qa_gate(job)

    def test_qa_report_lineage_must_match(self):
        job = self._job()
        job.state = vc.STATE_GENERATING
        job.manifest = make_manifest()
        job.qa_report = passing_qa(job_id="other-job")
        with self.assertRaises(vc.LineageError):
            vh.assert_qa_gate(job)

    def test_promote_without_qa_report_raises_and_leaves_job_generating(self):
        job = self._job()
        self.ledger.enqueue(job)
        self.ledger.claim("gen-1")
        with self.assertRaises(vh.QAGateError):
            vh.promote_to_ready(
                self.ledger, job_id="job-1", worker_id="gen-1",
                manifest=make_manifest(), qa_report=None,
                video_path=self.video_path, caption=CAPTION,
                disclosure=DISCLOSURE, affiliate_link=AFFILIATE_LINK,
                qa_report_path=self.qa_path, account="heightcue")
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_GENERATING)
        self.assertEqual(vh.list_ready(self.ledger), [])

    def test_promote_with_failing_qa_report_raises_and_does_not_publish(self):
        job = self._job()
        self.ledger.enqueue(job)
        self.ledger.claim("gen-1")
        with self.assertRaises(vh.QAGateError):
            vh.promote_to_ready(
                self.ledger, job_id="job-1", worker_id="gen-1",
                manifest=make_manifest(), qa_report=failing_qa(),
                video_path=self.video_path, caption=CAPTION,
                disclosure=DISCLOSURE, affiliate_link=AFFILIATE_LINK,
                qa_report_path=self.qa_path, account="heightcue")
        self.assertEqual(vh.list_ready(self.ledger), [])

    def test_promote_with_passing_qa_reaches_ready(self):
        entry = self._ready()
        self.assertEqual(entry["state"], vc.STATE_READY_TO_PUBLISH)
        self.assertEqual([p["job_id"] for p in vh.list_ready(self.ledger)],
                         ["job-1"])


# ---------------------------------------------------------------------------
# 2. 패킷 내용 — 필요한 건 다, 자격증명은 절대
# ---------------------------------------------------------------------------


class PacketTest(HandoffTestCase):

    def test_packet_carries_everything_publisher_needs(self):
        self._ready()
        packet = vh.list_ready(self.ledger)[0]
        for field in vh.REQUIRED_PACKET_FIELDS:
            self.assertIn(field, packet, f"패킷에 {field} 가 없다")
            self.assertTrue(packet[field] not in (None, "", [], {}),
                            f"패킷의 {field} 가 비어 있다")
        self.assertEqual(packet["video_path"], os.path.abspath(self.video_path))
        self.assertEqual(packet["video_sha256"], vh.sha256_file(self.video_path))
        self.assertEqual(packet["market"], "KR")
        self.assertEqual(packet["affiliate_link"], AFFILIATE_LINK)
        self.assertEqual(packet["disclosure"], DISCLOSURE)
        self.assertTrue(packet["qa_report"]["passed"])

    def test_packet_carries_full_lineage_back_to_source_asset(self):
        self._ready()
        lineage = vh.list_ready(self.ledger)[0]["lineage"]
        self.assertEqual(lineage["storyboard_id"], "sb-1")
        self.assertEqual(lineage["content_draft_id"], "draft-1")
        self.assertEqual(lineage["viral_pattern_ids"], ["vp-1"])
        self.assertTrue(lineage["source_urls"])
        self.assertTrue(lineage["source_sha256"])
        self.assertEqual(lineage["provider_request_ids"], ["req-1", "req-2"])

    def test_packet_never_contains_credentials(self):
        self._ready()
        packet = vh.list_ready(self.ledger)[0]
        blob = json.dumps(packet, ensure_ascii=False).lower()
        for needle in ("access_token", "app_secret", "password", "api_key",
                       "cookie", "bearer "):
            self.assertNotIn(needle, blob, f"패킷에 {needle} 가 실렸다")

    def test_credential_bearing_extras_are_rejected(self):
        with self.assertRaises(vh.CredentialLeak):
            vh.assert_no_credentials({"access_token": "abc"})
        with self.assertRaises(vh.CredentialLeak):
            vh.assert_no_credentials({"nested": [{"threads_app_secret": "x"}]})
        vh.assert_no_credentials({"affiliate_link": AFFILIATE_LINK})

    def test_missing_disclosure_is_refused(self):
        job = self._job()
        self.ledger.enqueue(job)
        self.ledger.claim("gen-1")
        with self.assertRaises(vc.RightsError):
            vh.promote_to_ready(
                self.ledger, job_id="job-1", worker_id="gen-1",
                manifest=make_manifest(), qa_report=passing_qa(),
                video_path=self.video_path, caption="고지 없는 캡션",
                disclosure="", affiliate_link=AFFILIATE_LINK,
                qa_report_path=self.qa_path, account="heightcue")

    def test_video_must_be_a_local_file(self):
        job = self._job()
        self.ledger.enqueue(job)
        self.ledger.claim("gen-1")
        with self.assertRaises(vh.HandoffError):
            vh.promote_to_ready(
                self.ledger, job_id="job-1", worker_id="gen-1",
                manifest=make_manifest(), qa_report=passing_qa(),
                video_path="https://cdn.example.com/final.mp4",
                caption=CAPTION, disclosure=DISCLOSURE,
                affiliate_link=AFFILIATE_LINK, qa_report_path=self.qa_path,
                account="heightcue")

    def test_missing_local_file_is_refused(self):
        job = self._job()
        self.ledger.enqueue(job)
        self.ledger.claim("gen-1")
        with self.assertRaises(vh.HandoffError):
            vh.promote_to_ready(
                self.ledger, job_id="job-1", worker_id="gen-1",
                manifest=make_manifest(), qa_report=passing_qa(),
                video_path=os.path.join(self.tmp, "nope.mp4"),
                caption=CAPTION, disclosure=DISCLOSURE,
                affiliate_link=AFFILIATE_LINK, qa_report_path=self.qa_path,
                account="heightcue")


# ---------------------------------------------------------------------------
# 3. 원자적 클레임 — 진짜 경합으로 증명한다
# ---------------------------------------------------------------------------


class ClaimTest(HandoffTestCase):

    def test_only_ready_jobs_are_claimable(self):
        self.ledger.enqueue(self._job("job-q", "run-1"))
        self.assertIsNone(vh.claim(self.ledger, "pub-1"),
                          "queued 잡이 발행 클레임에 잡혔다")

    def test_claim_moves_to_publishing_and_returns_packet(self):
        self._ready()
        claimed = vh.claim(self.ledger, "pub-1")
        self.assertEqual(claimed["job_id"], "job-1")
        self.assertEqual(claimed["state"], vc.STATE_PUBLISHING)
        self.assertEqual(claimed["packet"]["video_sha256"],
                         vh.sha256_file(self.video_path))
        self.assertFalse(claimed["requires_existence_check"])
        self.assertIsNone(vh.claim(self.ledger, "pub-2"),
                          "이미 소유된 잡이 두 번째 워커에게도 잡혔다")

    def test_concurrent_claims_never_both_win(self):
        """진짜 스레드 경합. 순차 호출로는 잡히지 않는 버그를 노린다."""
        self._ready()
        barrier = threading.Barrier(8)
        winners, errors = [], []

        def worker(i):
            try:
                barrier.wait(timeout=10)
                got = vh.claim(self.ledger, f"pub-{i}")
                if got:
                    winners.append(got["job_id"])
            except Exception as exc:            # pragma: no cover - 진단용
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(winners, ["job-1"],
                         f"단독 소유가 깨졌다: {winners}")

    def test_concurrent_claims_across_processes(self):
        """서브프로세스 경합 — 스레드 GIL 뒤에 숨는 경쟁을 배제한다."""
        self._ready()
        procs = [
            subprocess.Popen(
                [PY, os.path.join(BASE, "video_handoff.py"), "--root", self.tmp,
                 "claim", "--worker", f"proc-{i}", "--json"],
                cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            for i in range(4)
        ]
        wins = 0
        for p in procs:
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, f"stderr={err}")
            payload = json.loads(out)
            if payload.get("claimed"):
                wins += 1
        self.assertEqual(wins, 1, "서브프로세스 둘 이상이 같은 영상을 가져갔다")

    def test_published_job_cannot_be_reclaimed(self):
        self._ready()
        vh.claim(self.ledger, "pub-1")
        vh.mark_published(self.ledger, "job-1", "pub-1", media_id="MID-1",
                          post_url="https://www.threads.net/@heightcue/post/1")
        self.assertIsNone(vh.claim(self.ledger, "pub-2"))
        self.assertEqual(vh.list_ready(self.ledger), [])


# ---------------------------------------------------------------------------
# 4. 발행 확정 · 재발행 차단
# ---------------------------------------------------------------------------


class MarkPublishedTest(HandoffTestCase):

    def _claimed(self):
        self._ready()
        return vh.claim(self.ledger, "pub-1")

    def test_requires_real_post_reference(self):
        self._claimed()
        with self.assertRaises(vh.HandoffError):
            vh.mark_published(self.ledger, "job-1", "pub-1", media_id="",
                              post_url="https://www.threads.net/@heightcue/post/1")
        with self.assertRaises(vh.HandoffError):
            vh.mark_published(self.ledger, "job-1", "pub-1", media_id="MID-1",
                              post_url="not-a-url")

    def test_marks_published_and_records_evidence(self):
        self._claimed()
        result = vh.mark_published(
            self.ledger, "job-1", "pub-1", media_id="MID-1",
            post_url="https://www.threads.net/@heightcue/post/1")
        self.assertEqual(result["state"], vc.STATE_PUBLISHED)
        self.assertEqual(result["media_id"], "MID-1")
        with open(os.path.join(self.tmp, "publish_evidence.jsonl"),
                  encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual(rows[-1]["media_id"], "MID-1")
        self.assertEqual(rows[-1]["post_type"], analytics.VIDEO_POST_TYPE)

    def test_repeated_acknowledgement_is_idempotent(self):
        self._claimed()
        vh.mark_published(self.ledger, "job-1", "pub-1", media_id="MID-1",
                          post_url="https://www.threads.net/@heightcue/post/1")
        again = vh.mark_published(
            self.ledger, "job-1", "pub-1", media_id="MID-1",
            post_url="https://www.threads.net/@heightcue/post/1")
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["media_id"], "MID-1")

    def test_conflicting_second_media_id_is_refused(self):
        self._claimed()
        vh.mark_published(self.ledger, "job-1", "pub-1", media_id="MID-1",
                          post_url="https://www.threads.net/@heightcue/post/1")
        with self.assertRaises(vh.DuplicatePublishRisk):
            vh.mark_published(
                self.ledger, "job-1", "pub-1", media_id="MID-2",
                post_url="https://www.threads.net/@heightcue/post/2")

    def test_published_job_cannot_be_republished_by_worker(self):
        self._claimed()
        vh.mark_published(self.ledger, "job-1", "pub-1", media_id="MID-1",
                          post_url="https://www.threads.net/@heightcue/post/1")
        calls = []

        def publisher(packet):
            calls.append(packet["job_id"])
            return {"media_id": "MID-9", "post_url": "https://x/9"}

        with self.assertRaises(vh.HandoffError):
            vh.publish_video(self.ledger, "job-1", "pub-1", publisher=publisher)
        self.assertEqual(calls, [], "종결된 잡에 발행 호출이 나갔다")


# ---------------------------------------------------------------------------
# 5. 크래시 복구 잡 — 존재 확인 없이 재발행 금지 (이월 리스크)
# ---------------------------------------------------------------------------


class RecoveredJobTest(HandoffTestCase):

    def _crash_during_publishing(self):
        """publishing 중 워커가 죽은 상황을 만든다 (리스 만료 → 큐 회수)."""
        self._ready()
        vh.claim(self.ledger, "pub-dead", lease_seconds=0.01)
        import time as _t
        _t.sleep(0.05)
        recovered = self.ledger.recover_stale()
        self.assertEqual(recovered, ["job-1"])
        entry = self.ledger.get("job-1")
        self.assertEqual(entry["recovered_from"], vc.STATE_PUBLISHING)
        self.assertTrue(entry["publish_attempted_at"])
        return entry

    def test_queue_stamps_the_duplicate_publish_signals(self):
        entry = self._crash_during_publishing()
        self.assertTrue(vh.needs_existence_check(entry))

    def test_recovered_job_flags_existence_check_on_claim(self):
        self._crash_during_publishing()
        # 다시 생성 경로를 거쳐 ready 로 돌아온다 (계약이 강제하는 보수적 경로).
        self.ledger.claim("gen-2")
        vh.promote_to_ready(
            self.ledger, job_id="job-1", worker_id="gen-2",
            manifest=make_manifest(), qa_report=passing_qa(),
            video_path=self.video_path, caption=CAPTION, disclosure=DISCLOSURE,
            affiliate_link=AFFILIATE_LINK, qa_report_path=self.qa_path,
            account="heightcue")
        claimed = vh.claim(self.ledger, "pub-2")
        self.assertTrue(claimed["requires_existence_check"],
                        "복구된 잡이 존재 확인 플래그 없이 넘어왔다")

    def test_recovered_job_cannot_silently_double_publish(self):
        self._crash_during_publishing()
        self.ledger.claim("gen-2")
        vh.promote_to_ready(
            self.ledger, job_id="job-1", worker_id="gen-2",
            manifest=make_manifest(), qa_report=passing_qa(),
            video_path=self.video_path, caption=CAPTION, disclosure=DISCLOSURE,
            affiliate_link=AFFILIATE_LINK, qa_report_path=self.qa_path,
            account="heightcue")
        vh.claim(self.ledger, "pub-2")
        calls = []

        def publisher(packet):
            calls.append(packet["job_id"])
            return {"media_id": "MID-DUP", "post_url": "https://x/dup"}

        with self.assertRaises(vh.DuplicatePublishRisk):
            vh.publish_video(self.ledger, "job-1", "pub-2", publisher=publisher)
        self.assertEqual(calls, [], "존재 확인 없이 발행 호출이 나갔다")

    def test_existence_check_finding_a_post_adopts_it_instead_of_republishing(self):
        self._crash_during_publishing()
        self.ledger.claim("gen-2")
        vh.promote_to_ready(
            self.ledger, job_id="job-1", worker_id="gen-2",
            manifest=make_manifest(), qa_report=passing_qa(),
            video_path=self.video_path, caption=CAPTION, disclosure=DISCLOSURE,
            affiliate_link=AFFILIATE_LINK, qa_report_path=self.qa_path,
            account="heightcue")
        vh.claim(self.ledger, "pub-2")
        calls = []

        def publisher(packet):                  # pragma: no cover - 불려선 안 된다
            calls.append(packet["job_id"])
            return {"media_id": "MID-DUP", "post_url": "https://x/dup"}

        def existence_checker(packet):
            return {"media_id": "MID-EXISTING",
                    "post_url": "https://www.threads.net/@heightcue/post/7"}

        result = vh.publish_video(self.ledger, "job-1", "pub-2",
                                  publisher=publisher,
                                  existence_checker=existence_checker)
        self.assertEqual(calls, [], "이미 있는 글인데 또 발행했다")
        self.assertTrue(result["deduplicated"])
        self.assertEqual(result["media_id"], "MID-EXISTING")
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_PUBLISHED)

    def test_existence_check_finding_nothing_allows_one_publish(self):
        self._crash_during_publishing()
        self.ledger.claim("gen-2")
        vh.promote_to_ready(
            self.ledger, job_id="job-1", worker_id="gen-2",
            manifest=make_manifest(), qa_report=passing_qa(),
            video_path=self.video_path, caption=CAPTION, disclosure=DISCLOSURE,
            affiliate_link=AFFILIATE_LINK, qa_report_path=self.qa_path,
            account="heightcue")
        vh.claim(self.ledger, "pub-2")
        calls = []

        def publisher(packet):
            calls.append(packet["job_id"])
            return {"media_id": "MID-NEW",
                    "post_url": "https://www.threads.net/@heightcue/post/8"}

        result = vh.publish_video(self.ledger, "job-1", "pub-2",
                                  publisher=publisher,
                                  existence_checker=lambda packet: None)
        self.assertEqual(calls, ["job-1"])
        self.assertFalse(result["deduplicated"])
        self.assertEqual(result["media_id"], "MID-NEW")

    def test_clean_job_does_not_need_existence_check(self):
        self._ready()
        vh.claim(self.ledger, "pub-1")
        calls = []

        def publisher(packet):
            calls.append(packet["job_id"])
            return {"media_id": "MID-OK",
                    "post_url": "https://www.threads.net/@heightcue/post/9"}

        result = vh.publish_video(self.ledger, "job-1", "pub-1",
                                  publisher=publisher)
        self.assertEqual(calls, ["job-1"])
        self.assertEqual(result["media_id"], "MID-OK")


# ---------------------------------------------------------------------------
# 6. 실패 처리 — 계약에 있는 간선만 쓴다
# ---------------------------------------------------------------------------


class MarkFailedTest(HandoffTestCase):

    def test_failed_publishing_job_routes_through_contract_edges(self):
        self._ready()
        vh.claim(self.ledger, "pub-1")
        entry = vh.mark_failed(self.ledger, "job-1", "pub-1",
                               reason="threads 5xx")
        self.assertIn(entry["state"], (vc.STATE_QUEUED, vc.STATE_DEAD_LETTER))
        with open(os.path.join(self.tmp, "events.jsonl"),
                  encoding="utf-8") as fh:
            events = [json.loads(l) for l in fh]
        transitions = [(e["from_state"], e["to_state"]) for e in events
                       if e.get("event") == "transition"]
        for frm, to in transitions:
            self.assertIn(to, vc.TRANSITIONS[frm],
                          f"계약에 없는 간선이 기록됐다: {frm} -> {to}")

    def test_mark_failed_can_dead_letter_directly(self):
        self._ready()
        vh.claim(self.ledger, "pub-1")
        entry = vh.mark_failed(self.ledger, "job-1", "pub-1",
                               reason="정책 위반", dead_letter=True)
        self.assertEqual(entry["state"], vc.STATE_DEAD_LETTER)

    def test_mark_failed_refuses_published_job(self):
        self._ready()
        vh.claim(self.ledger, "pub-1")
        vh.mark_published(self.ledger, "job-1", "pub-1", media_id="MID-1",
                          post_url="https://www.threads.net/@heightcue/post/1")
        with self.assertRaises(vc.ContractError):
            vh.mark_failed(self.ledger, "job-1", "pub-1", reason="늦은 실패")


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------


class CLITest(HandoffTestCase):

    def _run(self, *args):
        proc = subprocess.run(
            [PY, os.path.join(BASE, "video_handoff.py"), "--root", self.tmp,
             *args],
            cwd=BASE, capture_output=True, text=True, timeout=60)
        return proc

    def test_list_ready_json_is_empty_without_qa_pass(self):
        self.ledger.enqueue(self._job())
        proc = self._run("list-ready", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [])

    def test_full_cli_round_trip(self):
        self._ready()
        proc = self._run("list-ready", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        packets = json.loads(proc.stdout)
        self.assertEqual(packets[0]["job_id"], "job-1")

        proc = self._run("claim", "--worker", "pub-cli", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["claimed"])

        proc = self._run("mark-published", "job-1", "--worker", "pub-cli",
                         "--media-id", "MID-CLI", "--post-url",
                         "https://www.threads.net/@heightcue/post/5", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["state"], vc.STATE_PUBLISHED)

    def test_cli_mark_failed(self):
        self._ready()
        self._run("claim", "--worker", "pub-cli", "--json")
        proc = self._run("mark-failed", "job-1", "--worker", "pub-cli",
                         "--reason", "api down", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(json.loads(proc.stdout)["state"],
                      (vc.STATE_QUEUED, vc.STATE_DEAD_LETTER))

    def test_cli_reports_contract_errors_without_traceback(self):
        proc = self._run("mark-published", "nope", "--worker", "w",
                         "--media-id", "M", "--post-url", "https://x/1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)


# ---------------------------------------------------------------------------
# 8. analytics 는 텍스트 파이프라인을 깨지 않는다
# ---------------------------------------------------------------------------


class AnalyticsCompatTest(unittest.TestCase):

    def test_text_row_attribution_unchanged(self):
        row = {"hook_family": "a", "angle_id": "b", "product_id": "c",
               "formfactor_id": "d", "ux_grade": "proven", "country": "KR",
               "post_type": "sales", "writer_variant": "v1"}
        self.assertEqual(analytics.attribution_gaps(row), [])
        self.assertEqual(analytics.attribution_gaps({}),
                         list(analytics.REQUIRED_ATTRIBUTION_FIELDS))

    def test_video_row_uses_video_attribution_fields(self):
        row = {"post_type": analytics.VIDEO_POST_TYPE, "country": "KR",
               "product_id": "p-1", "video_job_id": "job-1",
               "video_run_id": "run-1", "qa_report_ref": "/tmp/qa.json",
               "media_id": "MID-1"}
        self.assertTrue(analytics.is_video_row(row))
        self.assertEqual(analytics.attribution_gaps(row), [])
        self.assertIn("qa_report_ref",
                      analytics.attribution_gaps(
                          {"post_type": analytics.VIDEO_POST_TYPE}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
