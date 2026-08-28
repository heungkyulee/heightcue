#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V UGC 파이프라인 — 멱등 영상 잡 원장(ledger).

파일 기반 원장 하나로 다음을 보장한다:

* **멱등성** — 같은 소재(시장·상품·소스 해시·스토리보드·파이프라인 버전)는
  몇 번 enqueue 해도 잡이 하나다. 중복 요청은 기존 잡을 그대로 돌려준다.
  fal/이미지 생성은 호출당 실비가 나가므로 중복 생성은 곧 돈이다.
* **리스 기반 단독 소유** — claim 은 잠금 파일 아래에서 read-modify-write 하므로
  두 워커가 같은 잡을 동시에 소유할 수 없다. 크론이 겹쳐 돌아도 안전하다.
* **좀비 회수** — 워커가 죽어 `generating`/`publishing` 에 박힌 잡은 리스 만료 후
  회수된다. 회수는 시도 횟수를 올리고, 한계를 넘으면 데드레터로 보낸다.
* **재발행 불가** — `published` 는 종결 상태다. 상태 전이는 전부
  ``video_contracts.assert_transition`` 을 통과해야 하므로 원장이 계약을 우회할 수 없다.

상태·전이표·데이터클래스·원자적 기록은 전부 ``video_contracts`` 것을 쓴다.
여기서 상태를 다시 정의하거나 원자적 쓰기를 재구현하지 않는다.

이 모듈은 네트워크를 호출하지 않는다. 신규 의존성도 없다(표준 라이브러리만).
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

import video_contracts as vc
from video_contracts import (STATE_DEAD_LETTER, STATE_GENERATING, STATE_PUBLISHED,
                             STATE_PUBLISHING, STATE_QA_FAILED, STATE_QUEUED,
                             STATE_READY_TO_PUBLISH, STATE_RETRYABLE_FAILED,
                             ContractError, StateError, VideoJob, append_event,
                             assert_transition, atomic_write_json)

# ---------------------------------------------------------------------------
# 튜닝 상수
# ---------------------------------------------------------------------------

#: 멱등 키에 섞이는 파이프라인 버전. 생성 로직이 실질적으로 바뀌어 같은 소재라도
#: 다시 만들어야 할 때만 올린다 (올리면 과거 잡과 키가 갈라진다).
PIPELINE_VERSION = "i2v-1"

#: 기본 리스 길이. 컷 3개 생성이 넉넉히 끝나는 시간.
DEFAULT_LEASE_SECONDS = 900.0

#: 시도 한계. 여기 도달하면 데드레터 — 무한 재시도로 돈을 태우지 않는다.
MAX_ATTEMPTS = 3

#: 락 획득 대기 한계.
DEFAULT_LOCK_TIMEOUT = 30.0

#: 이 시간보다 오래된 락은 보유자 생사와 무관하게 깬다 (다른 호스트 대비 최후 방어).
DEFAULT_LOCK_STALE_SECONDS = 300.0

LEDGER_FILENAME = "ledger.json"
EVENTS_FILENAME = "events.jsonl"
LOCK_FILENAME = "ledger.lock"

#: claim 이 잡는 상태 -> 소유 중 상태
CLAIMABLE: Dict[str, str] = {
    STATE_QUEUED: STATE_GENERATING,
    STATE_READY_TO_PUBLISH: STATE_PUBLISHING,
}

#: 리스가 만료되면 회수 대상이 되는 "소유 중" 상태.
#: 회수는 계약의 전이표를 따라 ``retryable_failed`` 를 경유한다 — 전이표에
#: generating/publishing -> queued 직행 간선이 없기 때문이다(계약이 권위이며,
#: 여기서 평행 전이표를 만들지 않는다). 결과적으로 publishing 에서 죽은 잡도
#: 다시 생성 경로를 타는데, 이는 계약이 의도한 보수적 동작이다.
RECOVERABLE: tuple = (STATE_GENERATING, STATE_PUBLISHING)


# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class QueueError(ContractError):
    """원장 계층 오류 공통 베이스 (계약 오류의 하위라 한 번에 잡을 수 있다)."""


class LeaseError(QueueError):
    """리스 미보유·만료·소유자 불일치."""


class LockTimeout(QueueError):
    """제한 시간 안에 원장 락을 얻지 못했다."""


class LedgerCorrupt(QueueError):
    """ledger.json 이 존재하지만 해석할 수 없다.

    '아직 없음'과 '찢어짐'을 뭉뚱그려 빈 원장으로 시작하면, 다음 쓰기가 큐
    전체를 조용히 지운다. 손상 파일은 옆으로 보존하고 반드시 소리를 낸다.
    """


# ---------------------------------------------------------------------------
# 멱등 키
# ---------------------------------------------------------------------------


def storyboard_fingerprint(storyboard: vc.Storyboard) -> str:
    """스토리보드의 *내용* 지문. storyboard_id/run_id 같은 계보 식별자는 뺀다 —
    같은 계획을 새 run 에서 다시 큐에 넣어도 중복으로 잡혀야 하기 때문이다."""
    payload = {
        "viral_pattern_ids": sorted(storyboard.viral_pattern_ids),
        "content_draft_id": storyboard.content_draft_id,
        "cuts": [[c.index, c.prompt, c.duration_seconds]
                 for c in sorted(storyboard.cuts, key=lambda c: c.index)],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def idempotency_key(job: VideoJob, pipeline_version: str = PIPELINE_VERSION) -> str:
    """시장 + 상품 ID + 소스 해시 + 스토리보드 해시 + 파이프라인 버전.

    소스 해시는 정렬한다 — 수집 순서는 소재의 정체성이 아니다.
    구분자를 넣어 필드 경계가 뭉개지지 않게 한다.
    """
    parts = [
        pipeline_version,
        job.market,
        job.product_id,
        ",".join(sorted(job.evidence.source_sha256)),
        storyboard_fingerprint(job.storyboard),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 프로세스 생존 확인 (락 회수용)
# ---------------------------------------------------------------------------


def _pid_alive(pid: Any) -> bool:
    """이 호스트에서 해당 pid 가 살아 있는가. 확신할 수 없으면 True(보수적)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:      # 그런 프로세스 없음 → 죽었다
            return False
        if exc.errno == errno.EPERM:      # 남의 프로세스지만 살아는 있다
            return True
        return True
    return True


# ---------------------------------------------------------------------------
# 원장
# ---------------------------------------------------------------------------


def default_root() -> str:
    """설정의 state_dir 아래 video/ — 설정을 못 읽으면 모듈 옆 state/video/."""
    try:
        from common import load_config, state_path
        return state_path(load_config(), "video")
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "state", "video")


class VideoLedger:
    """``<root>/ledger.json`` 을 락 + 원자적 교체로 다루는 파일 기반 잡 원장.

    핸들은 상태를 캐싱하지 않는다 — 모든 연산이 락 안에서 디스크를 다시 읽으므로
    여러 프로세스·스레드가 같은 원장을 동시에 열어도 일관성이 유지된다.
    """

    def __init__(self, root: Optional[str] = None,
                 lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
                 lock_stale_seconds: float = DEFAULT_LOCK_STALE_SECONDS,
                 pipeline_version: str = PIPELINE_VERSION):
        self.root = os.path.abspath(root or default_root())
        self.ledger_path = os.path.join(self.root, LEDGER_FILENAME)
        self.events_path = os.path.join(self.root, EVENTS_FILENAME)
        self.lock_path = os.path.join(self.root, LOCK_FILENAME)
        self.lock_timeout = float(lock_timeout)
        self.lock_stale_seconds = float(lock_stale_seconds)
        self.pipeline_version = pipeline_version
        os.makedirs(self.root, exist_ok=True)

    # -- 락 -----------------------------------------------------------------

    def _lock_is_stale(self) -> Tuple[bool, Optional[int]]:
        """락 파일이 죽은 보유자/깨진 내용/너무 오래된 것이면 (True, inode).

        inode 를 함께 돌려주는 이유: 판독과 파기 사이에 락이 교체될 수 있다.
        파기 직전에 재-stat 해서 같은 inode 인지 확인해야 *살아 있는* 락을
        지우지 않는다(그러지 않으면 다중 소유 버그가 회수 경로에서 부활한다).
        """
        try:
            ino = os.stat(self.lock_path).st_ino
        except FileNotFoundError:
            return False, None
        except OSError:
            return False, None
        try:
            with open(self.lock_path, encoding="utf-8") as fh:
                info = json.load(fh)
        except FileNotFoundError:
            return False, None
        except (ValueError, OSError):
            return True, ino   # 깨진 락은 붙잡고 있어봐야 영원히 안 풀린다
        if not isinstance(info, dict):
            return True, ino
        acquired = info.get("acquired_at")
        if isinstance(acquired, (int, float)) and \
                time.time() - acquired > self.lock_stale_seconds:
            return True, ino
        if info.get("host") == socket.gethostname():
            return (not _pid_alive(info.get("pid"))), ino
        return False, ino   # 다른 호스트는 판단 불가 — 시간 기준으로만 깬다

    def _break_stale_lock(self, ino: Optional[int]) -> None:
        """판독 시점과 **같은 inode 일 때만** 락을 파기한다.

        무조건 unlink 하면 그 사이에 락을 정상 획득한 다른 워커의 살아 있는
        락을 지워버린다. 그 결과 둘 이상이 원장을 동시에 소유하게 된다.
        """
        if ino is None:
            return
        try:
            if os.stat(self.lock_path).st_ino != ino:
                return                      # 이미 교체됐다 — 남의 락이다
            os.unlink(self.lock_path)
        except OSError:
            pass


    def _try_acquire(self) -> bool:
        """락 파일을 *내용까지 완성한 채* 원자적으로 만든다.

        O_CREAT|O_EXCL 로 빈 파일을 먼저 만들고 나중에 쓰면, 그 사이에 낀 경쟁자가
        빈 파일을 읽고 "깨진 락"으로 오판해 살아 있는 락을 훔쳐간다(실제로 동시성
        테스트가 이 버그를 잡았다). tmp 에 다 쓴 뒤 os.link 로 걸면 락 파일은
        부분 상태로 관측되지 않는다 — link 는 대상이 있으면 실패하는 원자 연산이다.
        """
        tmp = f"{self.lock_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                              "tid": threading.get_ident(),
                              "acquired_at": time.time()})
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, self.lock_path)
            return True
        except FileExistsError:
            return False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    @contextmanager
    def _locked(self):
        """배타 락. 본문이 예외로 죽어도 반드시 해제한다(누수 없음)."""
        deadline = time.time() + self.lock_timeout
        acquired = False
        while True:
            # 데드라인 검사는 루프 **맨 위**에 있어야 한다. stale 분기가 아래에서
            # continue 하면 깰 수 없는 락(읽기전용 디렉터리·EPERM)에서 무한 루프에
            # 빠져 크론 잡이 코어를 태운다.
            if time.time() >= deadline:
                if self._try_acquire():
                    acquired = True
                    break
                raise LockTimeout(
                    f"원장 락 획득 실패({self.lock_timeout}s): {self.lock_path}")
            if self._try_acquire():
                acquired = True
                break
            stale, ino = self._lock_is_stale()
            if stale:
                self._break_stale_lock(ino)
                continue
            time.sleep(0.01)
        try:
            yield
        finally:
            if acquired:
                try:
                    os.unlink(self.lock_path)
                except OSError:
                    pass

    # -- 저수준 상태 --------------------------------------------------------

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.ledger_path, encoding="utf-8") as fh:
                raw = fh.read()
        except FileNotFoundError:
            raw = None
        if raw is None:
            data: Any = {}                 # 아직 원장이 없다 — 정상적인 빈 시작
        elif not raw.strip():
            data = {}                      # 빈 파일도 빈 원장으로 본다
        else:
            try:
                data = json.loads(raw)
            except ValueError as exc:
                aside = f"{self.ledger_path}.corrupt.{int(time.time() * 1000)}"
                try:
                    os.replace(self.ledger_path, aside)
                except OSError:
                    aside = "(보존 실패)"
                raise LedgerCorrupt(
                    f"원장을 해석할 수 없다: {self.ledger_path} — {exc}. "
                    f"원본을 {aside} 로 보존했다. 사람이 확인해야 한다") from exc
            if not isinstance(data, dict):
                aside = f"{self.ledger_path}.corrupt.{int(time.time() * 1000)}"
                try:
                    os.replace(self.ledger_path, aside)
                except OSError:
                    aside = "(보존 실패)"
                raise LedgerCorrupt(
                    f"원장 최상위가 객체가 아니다: {self.ledger_path} "
                    f"({type(data).__name__}). 원본을 {aside} 로 보존했다")
        data.setdefault("version", 1)
        data.setdefault("jobs", [])
        data.setdefault("index", {})
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        atomic_write_json(self.ledger_path, data)

    def _event(self, **record: Any) -> None:
        append_event(self.events_path, record)

    @staticmethod
    def _find(data: Dict[str, Any], job_id: str) -> Dict[str, Any]:
        for entry in data["jobs"]:
            if entry.get("job_id") == job_id:
                return entry
        raise KeyError(f"알 수 없는 job_id: {job_id!r}")

    def _set_state(self, entry: Dict[str, Any], to_state: str,
                   **extra: Any) -> Dict[str, Any]:
        """계약의 전이표를 통과한 전이만 반영하고 이벤트로 남긴다."""
        from_state = entry["state"]
        assert_transition(from_state, to_state)
        entry["state"] = to_state
        entry["updated_at"] = time.time()
        self._event(job_id=entry["job_id"], run_id=entry.get("run_id", ""),
                    event="transition", from_state=from_state, to_state=to_state,
                    **extra)
        return entry

    # -- 리스 ---------------------------------------------------------------

    @staticmethod
    def _lease_is_live(entry: Dict[str, Any], now: Optional[float] = None) -> bool:
        lease = entry.get("lease")
        if not lease:
            return False
        return float(lease.get("expires_at", 0)) > (now if now is not None
                                                    else time.time())

    def _require_holder(self, entry: Dict[str, Any], worker_id: str) -> None:
        lease = entry.get("lease")
        if not lease:
            raise LeaseError(
                f"{entry['job_id']} 에 유효한 리스가 없다 (state={entry['state']}) — "
                f"{worker_id!r} 는 소유자가 아니다")
        if lease.get("worker_id") != worker_id:
            raise LeaseError(
                f"{entry['job_id']} 의 소유자는 {lease.get('worker_id')!r} 다 — "
                f"{worker_id!r} 의 요청을 거부한다")
        if not self._lease_is_live(entry):
            raise LeaseError(
                f"{entry['job_id']} 의 리스가 만료됐다 — {worker_id!r} 는 더 이상 소유자가 아니다")

    def _recover_locked(self, data: Dict[str, Any]) -> List[str]:
        """락 안에서 만료 리스를 회수한다. 회수/데드레터된 job_id 목록 반환."""
        now = time.time()
        recovered: List[str] = []
        for entry in data["jobs"]:
            if entry["state"] not in RECOVERABLE or self._lease_is_live(entry, now):
                continue
            lease = entry.get("lease") or {}
            entry["lease"] = None
            entry["last_error"] = (f"리스 만료 — 워커 {lease.get('worker_id')!r} 응답 없음")
            was = entry["state"]
            if was == STATE_PUBLISHING:
                # 발행 API 호출이 이미 성공했을 수 있다. 그 사실이 events.jsonl
                # 에만 남으면 발행 워커가 이벤트 로그를 파싱해야 알 수 있다.
                # status/show 에서 바로 보이도록 원장 자체에 못을 박는다.
                entry["recovered_from"] = STATE_PUBLISHING
                entry["publish_attempted_at"] = float(
                    lease.get("acquired_at") or entry.get("updated_at") or now)
            # 계약의 전이표를 따른다: 소유 중 -> retryable_failed -> queued|dead_letter
            self._set_state(entry, STATE_RETRYABLE_FAILED, reason="lease_expired",
                            worker_id=lease.get("worker_id"))
            if entry.get("attempts", 0) >= MAX_ATTEMPTS:
                self._set_state(entry, STATE_DEAD_LETTER,
                                reason="lease_expired_max_attempts",
                                attempts=entry.get("attempts", 0))
            else:
                self._set_state(entry, STATE_QUEUED, reason="lease_expired",
                                attempts=entry.get("attempts", 0))
            recovered.append(entry["job_id"])
        return recovered

    # -- 공개 API -----------------------------------------------------------

    def enqueue(self, job: VideoJob) -> Dict[str, Any]:
        """잡을 큐에 넣는다. 같은 멱등 키가 이미 있으면 **기존 잡을 그대로 돌려준다**.

        반환 dict 의 ``created`` 로 새로 만들었는지 구분한다.
        검증에 실패한 잡은 원장에 절대 남지 않는다.
        """
        job.validate()
        if job.state != STATE_QUEUED:
            raise StateError(f"enqueue 는 {STATE_QUEUED} 상태 잡만 받는다: {job.state!r}")
        key = idempotency_key(job, self.pipeline_version)

        with self._locked():
            data = self._read()
            existing_id = data["index"].get(key)
            if existing_id is not None:
                try:
                    entry = self._find(data, existing_id)
                except KeyError:
                    entry = None            # 인덱스가 떠 있다 — 아래에서 새로 만든다
                if entry is not None:
                    self._event(job_id=entry["job_id"], run_id=entry.get("run_id", ""),
                                event="enqueue_duplicate", idempotency_key=key,
                                requested_job_id=job.job_id, state=entry["state"])
                    return dict(entry, created=False)

            if any(e.get("job_id") == job.job_id for e in data["jobs"]):
                raise QueueError(f"job_id 중복: {job.job_id!r} (멱등 키는 다르다)")

            now = time.time()
            entry = {
                "job_id": job.job_id,
                "run_id": job.run_id,
                "product_id": job.product_id,
                "market": job.market,
                "state": STATE_QUEUED,
                "idempotency_key": key,
                "pipeline_version": self.pipeline_version,
                "attempts": 0,
                "lease": None,
                "last_error": None,
                "media_id": None,
                "created_at": now,
                "updated_at": now,
                "job": job.to_dict(),
            }
            data["jobs"].append(entry)
            data["index"][key] = job.job_id
            self._write(data)
            self._event(job_id=job.job_id, run_id=job.run_id, event="enqueue",
                        idempotency_key=key, market=job.market,
                        product_id=job.product_id)
            return dict(entry, created=True)

    def claim(self, worker_id: str,
              states: Iterable[str] = (STATE_QUEUED,),
              lease_seconds: float = DEFAULT_LEASE_SECONDS) -> Optional[Dict[str, Any]]:
        """가장 오래된 대기 잡 하나를 단독 소유로 가져온다. 없으면 None.

        락 안에서 만료 리스를 먼저 회수하므로, 운영자가 recover 를 잊어도
        다음 claim 이 스스로 좀비를 되살린다.
        """
        if not worker_id:
            raise QueueError("worker_id 는 비어 있을 수 없다")
        wanted = tuple(states)
        for st in wanted:
            if st not in CLAIMABLE:
                raise StateError(f"claim 할 수 없는 상태: {st!r} — 허용: {tuple(CLAIMABLE)}")

        with self._locked():
            data = self._read()
            if self._recover_locked(data):
                self._write(data)

            for entry in sorted(data["jobs"], key=lambda e: e.get("created_at", 0)):
                if entry["state"] not in wanted or self._lease_is_live(entry):
                    continue
                now = time.time()
                entry["attempts"] = entry.get("attempts", 0) + 1
                entry["lease"] = {
                    "worker_id": worker_id,
                    "acquired_at": now,
                    "expires_at": now + float(lease_seconds),
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                }
                self._set_state(entry, CLAIMABLE[entry["state"]],
                                worker_id=worker_id, attempts=entry["attempts"])
                self._write(data)
                return dict(entry)
            return None

    def heartbeat(self, job_id: str, worker_id: str,
                  lease_seconds: float = DEFAULT_LEASE_SECONDS) -> Dict[str, Any]:
        """리스를 연장한다. 소유자만 가능하며, 만료된 뒤에는 되살릴 수 없다."""
        with self._locked():
            data = self._read()
            entry = self._find(data, job_id)
            self._require_holder(entry, worker_id)
            entry["lease"]["expires_at"] = time.time() + float(lease_seconds)
            entry["updated_at"] = time.time()
            self._write(data)
            return dict(entry)

    def complete(self, job_id: str, worker_id: str,
                 manifest: Optional[vc.GenerationManifest] = None,
                 qa_report: Optional[vc.QAReport] = None,
                 handoff: Optional[vc.PublishingHandoff] = None,
                 packet: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """생성 완료. QA 통과면 ready_to_publish, 실패면 qa_failed 로 간다.

        저장되는 잡 문서는 ``VideoJob.validate()`` 를 다시 통과해야 하므로
        계보가 어긋난 산출물은 원장에 들어오지 못한다.

        ``packet`` 은 발행 핸드오프 패킷이다. **상태 확정과 같은 락·같은 쓰기**
        안에서 붙는다 — 두 번 나누면 그 사이에서 죽은 잡이 패킷 없는
        ``ready_to_publish`` 로 남아 영원히 발행되지 않는다.
        """
        with self._locked():
            data = self._read()
            entry = self._find(data, job_id)
            job = VideoJob.from_dict(entry["job"])
            passed = bool(qa_report is not None and qa_report.passed)
            target = STATE_READY_TO_PUBLISH if passed else STATE_QA_FAILED
            assert_transition(entry["state"], target)   # 계약 먼저
            self._require_holder(entry, worker_id)

            job.state = target
            job.manifest = manifest
            job.qa_report = qa_report
            job.handoff = handoff if passed else None
            job.validate()

            entry["job"] = job.to_dict()
            entry["lease"] = None
            if packet is not None and passed:
                entry["packet"] = packet
            self._set_state(entry, target, worker_id=worker_id,
                            qa_passed=passed,
                            packet_attached=bool(packet is not None and passed))
            self._write(data)
            return dict(entry)

    def publish_done(self, job_id: str, worker_id: str,
                     media_id: str) -> Dict[str, Any]:
        """발행 확정. published 는 종결 상태라 두 번째 호출은 StateError 로 죽는다."""
        if not media_id:
            raise QueueError("media_id 는 비어 있을 수 없다")
        with self._locked():
            data = self._read()
            entry = self._find(data, job_id)
            assert_transition(entry["state"], STATE_PUBLISHED)  # 재발행 차단
            self._require_holder(entry, worker_id)

            job = VideoJob.from_dict(entry["job"])
            job.state = STATE_PUBLISHED
            if job.handoff is not None:
                job.handoff.state = STATE_PUBLISHED
            job.validate()

            entry["job"] = job.to_dict()
            entry["media_id"] = media_id
            entry["lease"] = None
            self._set_state(entry, STATE_PUBLISHED, worker_id=worker_id,
                            media_id=media_id)
            self._write(data)
            return dict(entry)

    def retry(self, job_id: str, worker_id: Optional[str] = None,
              reason: str = "") -> Dict[str, Any]:
        """실패를 기록하고 다시 큐로. 시도 한계를 넘으면 데드레터로 보낸다."""
        with self._locked():
            data = self._read()
            entry = self._find(data, job_id)
            if worker_id is not None and entry.get("lease"):
                self._require_holder(entry, worker_id)

            exhausted = entry.get("attempts", 0) >= MAX_ATTEMPTS
            target = STATE_DEAD_LETTER if exhausted else STATE_QUEUED
            if entry["state"] != STATE_RETRYABLE_FAILED:
                assert_transition(entry["state"], STATE_RETRYABLE_FAILED)
                self._set_state(entry, STATE_RETRYABLE_FAILED, reason=reason)
            entry["lease"] = None
            entry["last_error"] = reason or entry.get("last_error")
            self._set_state(entry, target, reason=reason,
                            attempts=entry.get("attempts", 0))
            self._write(data)
            return dict(entry)

    def requeue(self, job_id: str, reason: str = "requeue") -> Dict[str, Any]:
        """qa_failed / retryable_failed 잡을 운영자가 다시 큐에 넣는다."""
        with self._locked():
            data = self._read()
            entry = self._find(data, job_id)
            entry["lease"] = None
            self._set_state(entry, STATE_QUEUED, reason=reason)
            self._write(data)
            return dict(entry)

    def dead_letter(self, job_id: str, reason: str = "") -> Dict[str, Any]:
        """사람 개입이 필요한 종결 상태로 보낸다."""
        with self._locked():
            data = self._read()
            entry = self._find(data, job_id)
            entry["lease"] = None
            entry["last_error"] = reason or entry.get("last_error")
            self._set_state(entry, STATE_DEAD_LETTER, reason=reason)
            self._write(data)
            return dict(entry)

    def recover_stale(self) -> List[str]:
        """만료 리스를 회수한다. 살아 있는 리스는 건드리지 않는다."""
        with self._locked():
            data = self._read()
            recovered = self._recover_locked(data)
            if recovered:
                self._write(data)
            return recovered

    # -- 조회 ---------------------------------------------------------------

    def get(self, job_id: str) -> Dict[str, Any]:
        return dict(self._find(self._read(), job_id))

    def list_jobs(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        jobs = sorted(self._read()["jobs"], key=lambda e: e.get("created_at", 0))
        if state is not None:
            vc.assert_state(state)
            jobs = [j for j in jobs if j["state"] == state]
        return [dict(j) for j in jobs]

    def stats(self) -> Dict[str, Any]:
        jobs = self._read()["jobs"]
        by_state = {s: 0 for s in vc.STATES}
        for entry in jobs:
            by_state[entry["state"]] = by_state.get(entry["state"], 0) + 1
        now = time.time()
        return {
            "total": len(jobs),
            "by_state": by_state,
            "leased": sum(1 for e in jobs if self._lease_is_live(e, now)),
            "stale_leases": sum(1 for e in jobs if e["state"] in RECOVERABLE
                                and not self._lease_is_live(e, now)),
            "root": self.root,
        }


# ---------------------------------------------------------------------------
# CLI — 테스트·크론 모니터·운영자용
# ---------------------------------------------------------------------------


def _summary_line(entry: Dict[str, Any]) -> str:
    lease = entry.get("lease") or {}
    holder = lease.get("worker_id", "-")
    line = (f"{entry['job_id']:<20} {entry['state']:<18} "
            f"attempts={entry.get('attempts', 0)} worker={holder} "
            f"market={entry.get('market', '-')} product={entry.get('product_id', '-')}")
    if entry.get("recovered_from"):
        line += f" recovered_from={entry['recovered_from']}"
    return line


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="video_queue.py", description="HeightCue 영상 잡 원장 CLI")
    parser.add_argument("--root", default=None,
                        help="원장 디렉터리 (기본: state/video)")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --json/--root 는 하위 명령 뒤에도 쓸 수 있어야 한다 (`status --json` 이 자연스럽다).
    # SUPPRESS 가 없으면 하위 파서의 기본값이 상위에서 이미 파싱한 --root 를 덮어쓴다.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS, help="JSON 으로 출력")

    p_status = sub.add_parser("status", parents=[common], help="상태별 집계")
    p_status.add_argument("--fail-on-dead-letter", action="store_true",
                          help="데드레터가 있으면 exit 1 (크론 모니터용)")
    p_list = sub.add_parser("list", parents=[common], help="잡 목록")
    p_list.add_argument("--state", default=None)
    sub.add_parser("recover", parents=[common], help="만료 리스 회수")
    p_show = sub.add_parser("show", parents=[common], help="잡 하나 조회")
    p_show.add_argument("job_id")
    p_dl = sub.add_parser("dead-letter", parents=[common], help="잡을 데드레터로")
    p_dl.add_argument("job_id")
    p_dl.add_argument("--reason", default="operator")
    p_rq = sub.add_parser("requeue", parents=[common], help="잡을 다시 큐로")
    p_rq.add_argument("job_id")

    args = parser.parse_args(argv)
    ledger = VideoLedger(args.root)

    try:
        if args.cmd == "status":
            stats = ledger.stats()
            if args.json:
                print(json.dumps(stats, ensure_ascii=False, indent=2))
            else:
                print(f"원장: {stats['root']}")
                print(f"총 {stats['total']}건 · 리스 보유 {stats['leased']}건 · "
                      f"만료 리스 {stats['stale_leases']}건")
                for state, count in stats["by_state"].items():
                    if count:
                        print(f"  {state:<18} {count}")
            if args.fail_on_dead_letter and stats["by_state"].get(STATE_DEAD_LETTER):
                print("데드레터가 있다 — 사람 확인 필요", file=sys.stderr)
                return 1
            return 0

        if args.cmd == "list":
            jobs = ledger.list_jobs(args.state)
            if args.json:
                print(json.dumps(jobs, ensure_ascii=False, indent=2))
            else:
                for entry in jobs:
                    print(_summary_line(entry))
                if not jobs:
                    print("(비어 있음)")
            return 0

        if args.cmd == "recover":
            recovered = ledger.recover_stale()
            if args.json:
                print(json.dumps({"recovered": recovered}, ensure_ascii=False))
            else:
                print(f"회수 {len(recovered)}건: {', '.join(recovered) or '(없음)'}")
            return 0

        if args.cmd == "show":
            entry = ledger.get(args.job_id)
            if args.json:
                print(json.dumps(entry, ensure_ascii=False, indent=2))
            else:
                print(_summary_line(entry))
                print(f"  멱등키: {entry.get('idempotency_key')}")
                print(f"  마지막 오류: {entry.get('last_error')}")
            return 0

        if args.cmd == "dead-letter":
            entry = ledger.dead_letter(args.job_id, reason=args.reason)
            print(_summary_line(entry))
            return 0

        # requeue — 하위 파서가 required=True 라 남은 경우는 이것뿐이다.
        entry = ledger.requeue(args.job_id)
        print(_summary_line(entry))
        return 0

    except KeyError as exc:
        print(f"없는 잡: {exc}", file=sys.stderr)
        return 2
    except ContractError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
