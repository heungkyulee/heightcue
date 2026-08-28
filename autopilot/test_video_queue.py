#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""영상 잡 원장(video_queue) 테스트 — 실행:

    cd ~/heightcue-autopilot/autopilot
    ../.venv/bin/python -m unittest -v test_video_queue.py

네트워크를 전혀 쓰지 않는다. 모든 테스트는 임시 디렉터리에서 돌며 실제 state/ 를 건드리지 않는다.

여기서 느슨하게 넘어가면 안 되는 것들(실제로 증명한다):
* 동시 claim 두 개가 같은 잡을 소유할 수 없다 — 스레드/서브프로세스로 진짜 경합을 만든다.
* 중복 enqueue 는 새 잡을 만들지 않고 기존 잡을 돌려준다.
* 오래된 리스는 회수되고, 살아 있는 리스는 훔칠 수 없다.
* published 잡은 다시 claim/발행될 수 없다.
* 락 보유자가 죽어도 데드락/누수가 없다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import video_contracts as vc
import video_queue as vq

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_OUT = "c" * 64


def make_evidence(product_id="p-1", market="KR", source_sha=None):
    return vc.ProductEvidence(
        product_id=product_id,
        market=market,
        source_urls=["https://www.coupang.com/vp/products/123"],
        source_sha256=list(source_sha or [SHA_A]),
        rights={"basis": "제품 상세 공개 정보", "holder": "쿠팡 판매자",
                "source_url": "https://www.coupang.com/vp/products/123",
                "captured_at": "2026-08-28T09:00:00+09:00"},
        provenance=[{"quote": "고밀도 폼", "source_url": "https://www.coupang.com/vp/products/123",
                     "original_location": "상품 상세 > 제품 사양"}],
        captured_at="2026-08-28T09:00:00+09:00",
    )


def make_storyboard(storyboard_id="sb-1", run_id="run-1", product_id="p-1",
                    market="KR", n_cuts=2, prompt_prefix="컷"):
    return vc.Storyboard(
        storyboard_id=storyboard_id,
        run_id=run_id,
        product_id=product_id,
        market=market,
        viral_pattern_ids=["vp-1"],
        content_draft_id="draft-1",
        cuts=[vc.CutPrompt(index=i, prompt=f"{prompt_prefix}-{i} 장면")
              for i in range(1, n_cuts + 1)],
    )


def make_job(job_id="job-1", run_id="run-1", product_id="p-1", market="KR",
             source_sha=None, n_cuts=2, prompt_prefix="컷", storyboard_id="sb-1"):
    return vc.VideoJob(
        job_id=job_id, run_id=run_id, product_id=product_id, market=market,
        state=vc.STATE_QUEUED,
        evidence=make_evidence(product_id, market, source_sha),
        storyboard=make_storyboard(storyboard_id, run_id, product_id, market,
                                   n_cuts, prompt_prefix),
    )


def make_manifest(job_id="job-1", run_id="run-1", storyboard_id="sb-1",
                  product_id="p-1", market="KR", n_cuts=2):
    return vc.GenerationManifest(
        job_id=job_id, run_id=run_id, storyboard_id=storyboard_id,
        product_id=product_id, market=market,
        image_model_alias=vc.IMAGE_MODEL_ALIAS,
        image_hermes_provider=vc.IMAGE_HERMES_PROVIDER,
        image_hermes_model=vc.IMAGE_HERMES_MODEL,
        image_provider_model=vc.IMAGE_PROVIDER_MODEL,
        video_endpoint=vc.VIDEO_ENDPOINT,
        resolution=vc.VIDEO_RESOLUTION,
        aspect_ratio=vc.VIDEO_ASPECT_RATIO,
        cuts=[vc.CutGeneration(index=i, prompt=f"컷-{i} 장면", duration_seconds=5,
                               provider_request_id=f"req-{i}", cost_usd=0.25,
                               output_path=f"/tmp/cut{i}.mp4", output_sha256=SHA_OUT)
              for i in range(1, n_cuts + 1)],
    )


def make_handoff(job_id="job-1", run_id="run-1", product_id="p-1", market="KR",
                 state=vc.STATE_READY_TO_PUBLISH, duration=10):
    return vc.PublishingHandoff(
        job_id=job_id, run_id=run_id, product_id=product_id, market=market,
        state=state, content_draft_id="draft-1",
        video_path="/tmp/final.mp4", video_sha256=SHA_OUT,
        duration_seconds=duration, aspect_ratio=vc.VIDEO_ASPECT_RATIO,
        caption="캡션 본문", disclosure_included=True,
    )


class LedgerTestCase(unittest.TestCase):
    """임시 원장 디렉터리를 쓰는 공통 베이스."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hc-video-queue-test-")
        self.ledger = vq.VideoLedger(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drive_to_ready(self, ledger, job_id, worker="w-1"):
        """queued -> generating -> ready_to_publish 까지 정상 경로로 민다."""
        ledger.claim(worker_id=worker)
        ledger.complete(job_id, worker_id=worker,
                        manifest=make_manifest(job_id=job_id),
                        qa_report=vc.QAReport(job_id=job_id, run_id="run-1", passed=True),
                        handoff=make_handoff(job_id=job_id))
        return ledger.get(job_id)


# ---------------------------------------------------------------------------
# 멱등 키
# ---------------------------------------------------------------------------


class TestIdempotencyKey(LedgerTestCase):

    def test_key_is_sha256_hex(self):
        key = vq.idempotency_key(make_job())
        self.assertEqual(len(key), 64)
        self.assertTrue(set(key) <= set("0123456789abcdef"), key)

    def test_key_is_stable_across_calls_and_job_id(self):
        """job_id/run_id 는 키에 들어가지 않는다 — 같은 소재+계획이면 같은 키."""
        a = vq.idempotency_key(make_job(job_id="job-1", run_id="run-1"))
        b = vq.idempotency_key(make_job(job_id="job-2", run_id="run-9"))
        self.assertEqual(a, b)

    def test_key_varies_by_market_product_sources_storyboard_and_version(self):
        base = vq.idempotency_key(make_job())
        self.assertNotEqual(base, vq.idempotency_key(
            make_job(market="US", product_id="p-1")))
        self.assertNotEqual(base, vq.idempotency_key(make_job(product_id="p-2")))
        self.assertNotEqual(base, vq.idempotency_key(make_job(source_sha=[SHA_B])))
        self.assertNotEqual(base, vq.idempotency_key(
            make_job(prompt_prefix="다른컷")), "스토리보드 내용이 바뀌면 키도 바뀌어야 한다")
        self.assertNotEqual(base, vq.idempotency_key(
            make_job(), pipeline_version="v999"))

    def test_source_hash_order_does_not_change_key(self):
        """소스 해시 순서는 의미가 없다 — 정렬돼야 한다."""
        a = vq.idempotency_key(make_job(source_sha=[SHA_A, SHA_B]))
        b = vq.idempotency_key(make_job(source_sha=[SHA_B, SHA_A]))
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# enqueue / 중복 억제
# ---------------------------------------------------------------------------


class TestEnqueue(LedgerTestCase):

    def test_enqueue_creates_queued_entry(self):
        entry = self.ledger.enqueue(make_job())
        self.assertEqual(entry["state"], vc.STATE_QUEUED)
        self.assertEqual(entry["job_id"], "job-1")
        self.assertEqual(entry["attempts"], 0)
        self.assertIsNone(entry["lease"])
        self.assertEqual(len(self.ledger.list_jobs()), 1)

    def test_enqueue_persists_to_disk(self):
        self.ledger.enqueue(make_job())
        reopened = vq.VideoLedger(self.tmp)
        self.assertEqual(len(reopened.list_jobs()), 1)
        self.assertEqual(reopened.get("job-1")["state"], vc.STATE_QUEUED)

    def test_duplicate_enqueue_returns_existing_job(self):
        first = self.ledger.enqueue(make_job(job_id="job-1"))
        second = self.ledger.enqueue(make_job(job_id="job-2"))  # 같은 소재/계획
        self.assertEqual(second["job_id"], first["job_id"],
                         "중복 enqueue 는 기존 잡을 돌려줘야 한다")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(self.ledger.list_jobs()), 1,
                         "중복 enqueue 가 두 번째 잡을 만들면 안 된다")

    def test_duplicate_enqueue_survives_reopen(self):
        self.ledger.enqueue(make_job(job_id="job-1"))
        again = vq.VideoLedger(self.tmp).enqueue(make_job(job_id="job-2"))
        self.assertEqual(again["job_id"], "job-1")
        self.assertEqual(len(self.ledger.list_jobs()), 1)

    def test_distinct_material_creates_distinct_jobs(self):
        self.ledger.enqueue(make_job(job_id="job-1", product_id="p-1"))
        self.ledger.enqueue(make_job(job_id="job-2", product_id="p-2",
                                     storyboard_id="sb-2"))
        self.assertEqual(len(self.ledger.list_jobs()), 2)

    def test_enqueue_rejects_invalid_job(self):
        bad = make_job()
        bad.market = "JP"
        with self.assertRaises(vc.ContractError):
            self.ledger.enqueue(bad)
        self.assertEqual(len(self.ledger.list_jobs()), 0,
                         "검증 실패한 잡이 원장에 남으면 안 된다")

    def test_enqueue_after_publish_does_not_resurrect(self):
        """published 소재를 다시 enqueue 해도 새 잡이 생기지 않는다."""
        self.ledger.enqueue(make_job())
        self.drive_to_ready(self.ledger, "job-1")
        self.ledger.claim(worker_id="pub-1", states=(vc.STATE_READY_TO_PUBLISH,))
        self.ledger.publish_done("job-1", worker_id="pub-1", media_id="media-9")
        again = self.ledger.enqueue(make_job(job_id="job-2"))
        self.assertEqual(again["job_id"], "job-1")
        self.assertEqual(again["state"], vc.STATE_PUBLISHED)
        self.assertFalse(again["created"])
        self.assertEqual(len(self.ledger.list_jobs()), 1)


# ---------------------------------------------------------------------------
# claim / 리스
# ---------------------------------------------------------------------------


class TestClaim(LedgerTestCase):

    def test_claim_moves_to_generating_with_lease(self):
        self.ledger.enqueue(make_job())
        claimed = self.ledger.claim(worker_id="w-1", lease_seconds=60)
        self.assertEqual(claimed["job_id"], "job-1")
        self.assertEqual(claimed["state"], vc.STATE_GENERATING)
        self.assertEqual(claimed["lease"]["worker_id"], "w-1")
        self.assertGreater(claimed["lease"]["expires_at"], time.time())

    def test_claim_returns_none_when_empty(self):
        self.assertIsNone(self.ledger.claim(worker_id="w-1"))

    def test_second_claim_gets_nothing_while_lease_is_fresh(self):
        self.ledger.enqueue(make_job())
        self.assertIsNotNone(self.ledger.claim(worker_id="w-1", lease_seconds=300))
        self.assertIsNone(self.ledger.claim(worker_id="w-2"),
                          "살아 있는 리스를 훔칠 수 없어야 한다")

    def test_fresh_lease_is_not_stealable_by_recovery(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=300)
        self.assertEqual(self.ledger.recover_stale(), [])
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_GENERATING)
        self.assertEqual(self.ledger.get("job-1")["lease"]["worker_id"], "w-1")

    def test_claim_is_fifo_by_enqueue_order(self):
        self.ledger.enqueue(make_job(job_id="job-1", product_id="p-1"))
        self.ledger.enqueue(make_job(job_id="job-2", product_id="p-2",
                                     storyboard_id="sb-2"))
        self.assertEqual(self.ledger.claim(worker_id="w-1")["job_id"], "job-1")
        self.assertEqual(self.ledger.claim(worker_id="w-2")["job_id"], "job-2")

    def test_published_job_cannot_be_reclaimed(self):
        self.ledger.enqueue(make_job())
        self.drive_to_ready(self.ledger, "job-1")
        self.ledger.claim(worker_id="pub-1", states=(vc.STATE_READY_TO_PUBLISH,))
        self.ledger.publish_done("job-1", worker_id="pub-1", media_id="media-9")
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_PUBLISHED)
        self.assertIsNone(self.ledger.claim(worker_id="w-2"))
        self.assertIsNone(self.ledger.claim(worker_id="w-2",
                                            states=(vc.STATE_READY_TO_PUBLISH,)))

    def test_published_job_cannot_be_republished(self):
        self.ledger.enqueue(make_job())
        self.drive_to_ready(self.ledger, "job-1")
        self.ledger.claim(worker_id="pub-1", states=(vc.STATE_READY_TO_PUBLISH,))
        self.ledger.publish_done("job-1", worker_id="pub-1", media_id="media-9")
        with self.assertRaises(vc.StateError):
            self.ledger.publish_done("job-1", worker_id="pub-1", media_id="media-10")
        self.assertEqual(self.ledger.get("job-1")["media_id"], "media-9",
                         "재발행 시도가 원래 media_id 를 덮어쓰면 안 된다")

    def test_dead_letter_job_cannot_be_claimed(self):
        self.ledger.enqueue(make_job())
        self.ledger.dead_letter("job-1", reason="사람이 중단")
        self.assertIsNone(self.ledger.claim(worker_id="w-1"))


# ---------------------------------------------------------------------------
# 진짜 동시성 — 스레드와 서브프로세스 양쪽에서 증명한다
# ---------------------------------------------------------------------------


class TestConcurrentClaim(LedgerTestCase):

    def test_threads_racing_for_one_job_produce_exactly_one_winner(self):
        self.ledger.enqueue(make_job())
        winners, errors = [], []
        start = threading.Barrier(12)

        def worker(i):
            led = vq.VideoLedger(self.tmp)  # 워커마다 독립 핸들
            try:
                start.wait(timeout=10)
                got = led.claim(worker_id=f"w-{i}", lease_seconds=300)
                if got:
                    winners.append(got)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(winners), 1,
                         f"정확히 한 워커만 이겨야 한다, 실제 승자: {[w['lease']['worker_id'] for w in winners]}")
        final = self.ledger.get("job-1")
        self.assertEqual(final["state"], vc.STATE_GENERATING)
        self.assertEqual(final["lease"]["worker_id"], winners[0]["lease"]["worker_id"])

    def test_threads_racing_for_n_jobs_never_double_own(self):
        """잡 5개 · 워커 20개 — 어떤 잡도 두 워커가 소유할 수 없다."""
        for i in range(5):
            self.ledger.enqueue(make_job(job_id=f"job-{i}", product_id=f"p-{i}",
                                         storyboard_id=f"sb-{i}"))
        claimed, errors = [], []
        start = threading.Barrier(20)

        def worker(i):
            led = vq.VideoLedger(self.tmp)
            try:
                start.wait(timeout=10)
                got = led.claim(worker_id=f"w-{i}", lease_seconds=300)
                if got:
                    claimed.append(got["job_id"])
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(claimed), 5, f"잡 5개가 정확히 5번 claim 돼야 한다: {claimed}")
        self.assertEqual(sorted(claimed), sorted(set(claimed)),
                         f"같은 잡을 두 번 claim 했다: {claimed}")

    def test_separate_processes_racing_produce_exactly_one_winner(self):
        """스레드 GIL 이 가려줄 수 있는 경합을 진짜 프로세스로 다시 증명한다."""
        self.ledger.enqueue(make_job())
        script = (
            "import sys, time, json;"
            f"sys.path.insert(0, {BASE!r});"
            "import video_queue as vq;"
            f"led = vq.VideoLedger({self.tmp!r});"
            "t = float(sys.argv[1]);"
            "time.sleep(max(0.0, t - time.time()));"
            "got = led.claim(worker_id=sys.argv[2], lease_seconds=300);"
            "print(json.dumps({'won': bool(got)}))"
        )
        go_at = time.time() + 1.5
        procs = [subprocess.Popen([PY, "-c", script, str(go_at), f"proc-{i}"],
                                  cwd=BASE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for i in range(6)]
        wins = 0
        for p in procs:
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, f"자식 프로세스 실패: {err}")
            if json.loads(out.strip())["won"]:
                wins += 1
        self.assertEqual(wins, 1, "정확히 한 프로세스만 이겨야 한다")
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_GENERATING)

    def test_concurrent_enqueue_of_same_material_creates_one_job(self):
        """중복 억제도 경합 상황에서 성립해야 한다."""
        created, errors = [], []
        start = threading.Barrier(10)

        def worker(i):
            led = vq.VideoLedger(self.tmp)
            try:
                start.wait(timeout=10)
                entry = led.enqueue(make_job(job_id=f"job-{i}"))
                if entry["created"]:
                    created.append(entry["job_id"])
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(created), 1, f"단 한 번만 생성돼야 한다: {created}")
        self.assertEqual(len(vq.VideoLedger(self.tmp).list_jobs()), 1)


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat(LedgerTestCase):

    def test_heartbeat_extends_lease(self):
        self.ledger.enqueue(make_job())
        claimed = self.ledger.claim(worker_id="w-1", lease_seconds=30)
        before = claimed["lease"]["expires_at"]
        time.sleep(0.01)
        beat = self.ledger.heartbeat("job-1", worker_id="w-1", lease_seconds=300)
        self.assertGreater(beat["lease"]["expires_at"], before)

    def test_heartbeat_by_wrong_worker_is_rejected(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=300)
        with self.assertRaises(vq.LeaseError):
            self.ledger.heartbeat("job-1", worker_id="w-2")

    def test_heartbeat_on_unleased_job_is_rejected(self):
        self.ledger.enqueue(make_job())
        with self.assertRaises(vq.LeaseError):
            self.ledger.heartbeat("job-1", worker_id="w-1")

    def test_heartbeat_rescues_job_from_stale_recovery(self):
        """하트비트를 계속 치는 느린 워커는 회수 대상이 아니다."""
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=0.05)
        self.ledger.heartbeat("job-1", worker_id="w-1", lease_seconds=300)
        self.assertEqual(self.ledger.recover_stale(), [])
        self.assertEqual(self.ledger.get("job-1")["lease"]["worker_id"], "w-1")

    def test_heartbeat_after_lease_expired_is_rejected(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=0.05)
        time.sleep(0.1)
        self.ledger.recover_stale()
        with self.assertRaises(vq.LeaseError):
            self.ledger.heartbeat("job-1", worker_id="w-1")


# ---------------------------------------------------------------------------
# 리스 만료 회수
# ---------------------------------------------------------------------------


class TestStaleLeaseRecovery(LedgerTestCase):

    def test_stale_generating_claim_is_recovered_and_reclaimable(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=0.05)
        time.sleep(0.1)
        recovered = self.ledger.recover_stale()
        self.assertEqual(recovered, ["job-1"])
        entry = self.ledger.get("job-1")
        self.assertEqual(entry["state"], vc.STATE_QUEUED)
        self.assertIsNone(entry["lease"])
        again = self.ledger.claim(worker_id="w-2")
        self.assertEqual(again["job_id"], "job-1")
        self.assertEqual(again["lease"]["worker_id"], "w-2")

    def test_stale_publishing_claim_is_recovered(self):
        self.ledger.enqueue(make_job())
        self.drive_to_ready(self.ledger, "job-1")
        self.ledger.claim(worker_id="pub-1", states=(vc.STATE_READY_TO_PUBLISH,),
                          lease_seconds=0.05)
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_PUBLISHING)
        time.sleep(0.1)
        self.assertEqual(self.ledger.recover_stale(), ["job-1"])
        # 계약 전이표(video_contracts.TRANSITIONS)에 publishing -> ready_to_publish
        # 간선이 없다. publishing 에서 죽은 잡은 retryable_failed 를 거쳐 queued 로
        # 돌아간다 — 발행 성공 여부가 불확실하므로 재검증을 강제하는 보수적 경로다.
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_QUEUED)
        self.assertIsNone(self.ledger.get("job-1")["lease"])

    def test_claim_auto_recovers_stale_lease(self):
        """운영자가 recover_stale 을 안 불러도 claim 이 스스로 회수한다."""
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=0.05)
        time.sleep(0.1)
        got = vq.VideoLedger(self.tmp).claim(worker_id="w-2")
        self.assertIsNotNone(got, "만료된 리스는 다음 claim 에서 회수돼야 한다")
        self.assertEqual(got["lease"]["worker_id"], "w-2")

    def test_recovery_counts_as_attempt_and_dead_letters_at_limit(self):
        self.ledger.enqueue(make_job())
        for _ in range(vq.MAX_ATTEMPTS):
            self.assertIsNotNone(self.ledger.claim(worker_id="w-1", lease_seconds=0.02))
            time.sleep(0.05)
            self.ledger.recover_stale()
        entry = self.ledger.get("job-1")
        self.assertEqual(entry["state"], vc.STATE_DEAD_LETTER,
                         f"{vq.MAX_ATTEMPTS}회 리스 만료 후에는 데드레터여야 한다")
        self.assertIsNone(self.ledger.claim(worker_id="w-9"))

    def test_stale_worker_cannot_complete_after_recovery(self):
        """좀비 워커가 뒤늦게 돌아와 남의 잡을 완료 처리할 수 없다."""
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=0.05)
        time.sleep(0.1)
        self.ledger.recover_stale()
        self.ledger.claim(worker_id="w-2", lease_seconds=300)
        with self.assertRaises(vq.LeaseError):
            self.ledger.complete("job-1", worker_id="w-1",
                                 manifest=make_manifest(),
                                 qa_report=vc.QAReport(job_id="job-1", run_id="run-1",
                                                       passed=True),
                                 handoff=make_handoff())


# ---------------------------------------------------------------------------
# complete / retry / dead-letter
# ---------------------------------------------------------------------------


class TestCompleteRetryDeadLetter(LedgerTestCase):

    def test_complete_moves_to_ready_to_publish_and_clears_lease(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        entry = self.ledger.complete("job-1", worker_id="w-1",
                                     manifest=make_manifest(),
                                     qa_report=vc.QAReport(job_id="job-1",
                                                           run_id="run-1", passed=True),
                                     handoff=make_handoff())
        self.assertEqual(entry["state"], vc.STATE_READY_TO_PUBLISH)
        self.assertIsNone(entry["lease"])

    def test_complete_with_failed_qa_goes_to_qa_failed(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        entry = self.ledger.complete("job-1", worker_id="w-1",
                                     manifest=make_manifest(),
                                     qa_report=vc.QAReport(job_id="job-1", run_id="run-1",
                                                           passed=False,
                                                           failures=["워터마크 감지"]))
        self.assertEqual(entry["state"], vc.STATE_QA_FAILED)
        self.assertIsNone(entry["lease"])

    def test_complete_requires_the_lease_holder(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        with self.assertRaises(vq.LeaseError):
            self.ledger.complete("job-1", worker_id="intruder",
                                 manifest=make_manifest(),
                                 qa_report=vc.QAReport(job_id="job-1", run_id="run-1",
                                                       passed=True),
                                 handoff=make_handoff())

    def test_retry_returns_job_to_queue(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        entry = self.ledger.retry("job-1", worker_id="w-1", reason="fal 504")
        self.assertEqual(entry["state"], vc.STATE_QUEUED)
        self.assertIsNone(entry["lease"])
        self.assertEqual(entry["last_error"], "fal 504")
        self.assertEqual(self.ledger.claim(worker_id="w-2")["job_id"], "job-1")

    def test_retry_dead_letters_after_max_attempts(self):
        self.ledger.enqueue(make_job())
        for _ in range(vq.MAX_ATTEMPTS):
            self.assertIsNotNone(self.ledger.claim(worker_id="w-1"))
            self.ledger.retry("job-1", worker_id="w-1", reason="fal 504")
        entry = self.ledger.get("job-1")
        self.assertEqual(entry["state"], vc.STATE_DEAD_LETTER)
        self.assertGreaterEqual(entry["attempts"], vq.MAX_ATTEMPTS)
        self.assertIsNone(self.ledger.claim(worker_id="w-2"))

    def test_dead_letter_is_terminal(self):
        self.ledger.enqueue(make_job())
        entry = self.ledger.dead_letter("job-1", reason="권리 근거 철회")
        self.assertEqual(entry["state"], vc.STATE_DEAD_LETTER)
        self.assertEqual(entry["last_error"], "권리 근거 철회")
        with self.assertRaises(vc.StateError):
            self.ledger.retry("job-1")

    def test_qa_failed_can_be_requeued(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        self.ledger.complete("job-1", worker_id="w-1", manifest=make_manifest(),
                             qa_report=vc.QAReport(job_id="job-1", run_id="run-1",
                                                   passed=False, failures=["워터마크"]))
        self.assertEqual(self.ledger.requeue("job-1")["state"], vc.STATE_QUEUED)
        self.assertEqual(self.ledger.claim(worker_id="w-2")["job_id"], "job-1")

    def test_illegal_transition_is_rejected_by_contract(self):
        """계약의 전이 허용표를 원장이 우회할 수 없다."""
        self.ledger.enqueue(make_job())
        with self.assertRaises(vc.StateError):
            self.ledger.publish_done("job-1", worker_id="w-1", media_id="m-1")

    def test_unknown_job_id_raises(self):
        with self.assertRaises(KeyError):
            self.ledger.get("nope")
        with self.assertRaises(KeyError):
            self.ledger.retry("nope")


# ---------------------------------------------------------------------------
# 락 — 데드락/누수 없음
# ---------------------------------------------------------------------------


class TestLock(LedgerTestCase):

    def test_lock_file_is_released_after_operation(self):
        self.ledger.enqueue(make_job())
        self.assertFalse(os.path.exists(self.ledger.lock_path),
                         "정상 종료 후 락 파일이 남으면 안 된다")

    def test_lock_is_released_when_body_raises(self):
        with self.assertRaises(RuntimeError):
            with self.ledger._locked():
                raise RuntimeError("boom")
        self.assertFalse(os.path.exists(self.ledger.lock_path),
                         "예외가 나도 락이 누수되면 안 된다")
        self.ledger.enqueue(make_job())  # 여전히 동작해야 한다

    def test_stale_lock_from_dead_holder_is_broken(self):
        """락 보유자가 죽어 락 파일이 남아도 데드락에 빠지지 않는다."""
        with open(self.ledger.lock_path, "w", encoding="utf-8") as fh:
            json.dump({"pid": 999999999, "host": "ghost",
                       "acquired_at": time.time() - 3600}, fh)
        self.ledger.enqueue(make_job())  # 타임아웃 없이 통과해야 한다
        self.assertEqual(len(self.ledger.list_jobs()), 1)

    def test_fresh_lock_is_respected_until_timeout(self):
        """살아 있는 보유자의 락은 즉시 깨지 않고 대기하다 타임아웃한다."""
        led = vq.VideoLedger(self.tmp, lock_timeout=0.3)
        with open(led.lock_path, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "host": "self",
                       "acquired_at": time.time()}, fh)
        started = time.time()
        with self.assertRaises(vq.LockTimeout):
            led.enqueue(make_job())
        self.assertGreaterEqual(time.time() - started, 0.3)
        os.unlink(led.lock_path)

    def test_corrupt_lock_file_does_not_deadlock(self):
        with open(self.ledger.lock_path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        self.ledger.enqueue(make_job())
        self.assertEqual(len(self.ledger.list_jobs()), 1)


# ---------------------------------------------------------------------------
# 지속성 / 이벤트 로그
# ---------------------------------------------------------------------------


class TestPersistenceAndEvents(LedgerTestCase):

    def test_events_are_appended_for_lifecycle(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        self.ledger.retry("job-1", worker_id="w-1", reason="fal 504")
        events = [json.loads(l) for l in
                  open(self.ledger.events_path, encoding="utf-8") if l.strip()]
        kinds = [e.get("event") for e in events]
        self.assertIn("enqueue", kinds)
        self.assertIn("transition", kinds)
        self.assertTrue(all(e.get("job_id") == "job-1" for e in events))

    def test_ledger_file_is_valid_json_after_many_ops(self):
        for i in range(6):
            self.ledger.enqueue(make_job(job_id=f"job-{i}", product_id=f"p-{i}",
                                         storyboard_id=f"sb-{i}"))
            self.ledger.claim(worker_id=f"w-{i}")
        with open(self.ledger.ledger_path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["jobs"]), 6)

    def test_no_tmp_files_left_behind(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        leftovers = [f for f in os.listdir(self.tmp) if ".tmp" in f]
        self.assertEqual(leftovers, [], f"임시 파일이 남았다: {leftovers}")

    def test_stats_counts_states(self):
        self.ledger.enqueue(make_job(job_id="job-1", product_id="p-1"))
        self.ledger.enqueue(make_job(job_id="job-2", product_id="p-2",
                                     storyboard_id="sb-2"))
        self.ledger.claim(worker_id="w-1")
        stats = self.ledger.stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_state"][vc.STATE_QUEUED], 1)
        self.assertEqual(stats["by_state"][vc.STATE_GENERATING], 1)


# ---------------------------------------------------------------------------
# CLI — 크론 모니터·운영자용
# ---------------------------------------------------------------------------


class TestCLI(LedgerTestCase):

    def run_cli(self, *args):
        return subprocess.run([PY, os.path.join(BASE, "video_queue.py"),
                               "--root", self.tmp, *args],
                              cwd=BASE, capture_output=True, text=True, timeout=60)

    def test_status_on_empty_ledger_exits_zero(self):
        proc = self.run_cli("status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0", proc.stdout)

    def test_status_json_reports_counts(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1")
        proc = self.run_cli("status", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["by_state"][vc.STATE_GENERATING], 1)

    def test_status_uses_default_state_dir_without_root(self):
        """운영자가 그냥 `video_queue.py status` 를 쳐도 죽지 않아야 한다."""
        proc = subprocess.run([PY, os.path.join(BASE, "video_queue.py"), "status"],
                              cwd=BASE, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_list_shows_jobs(self):
        self.ledger.enqueue(make_job())
        proc = self.run_cli("list", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)[0]["job_id"], "job-1")

    def test_recover_cli_recovers_stale_leases(self):
        self.ledger.enqueue(make_job())
        self.ledger.claim(worker_id="w-1", lease_seconds=0.05)
        time.sleep(0.1)
        proc = self.run_cli("recover", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["recovered"], ["job-1"])
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_QUEUED)

    def test_show_reports_single_job(self):
        self.ledger.enqueue(make_job())
        proc = self.run_cli("show", "job-1", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["state"], vc.STATE_QUEUED)

    def test_show_unknown_job_exits_nonzero(self):
        proc = self.run_cli("show", "nope")
        self.assertNotEqual(proc.returncode, 0)

    def test_dead_letter_cli_marks_job(self):
        self.ledger.enqueue(make_job())
        proc = self.run_cli("dead-letter", "job-1", "--reason", "운영자 중단")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.ledger.get("job-1")["state"], vc.STATE_DEAD_LETTER)

    def test_status_exits_nonzero_with_flag_when_dead_letters_exist(self):
        """크론 모니터가 데드레터를 알아챌 수 있어야 한다."""
        self.ledger.enqueue(make_job())
        self.ledger.dead_letter("job-1", reason="x")
        self.assertNotEqual(self.run_cli("status", "--fail-on-dead-letter").returncode, 0)
        self.assertEqual(self.run_cli("status").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
