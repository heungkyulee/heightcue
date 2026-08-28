#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V UGC — 생성과 발행 사이의 원자적 핸드오프 (Task 14).

한 줄 요약: **QA 리포트 없이는 발행 대기열에 들어올 수 없고, 한 번 발행된 것은
두 번 발행되지 않는다.**

이 모듈이 닫는 두 구멍
----------------------

1. **QA 게이트가 선택이었다.** 계약의 전이표(``TRANSITIONS``)에는
   ``generating -> ready_to_publish`` 직행 간선이 있다. 즉 QA 를 한 번도 돌리지
   않은 호출자가 그대로 발행까지 걸어갈 수 있었다. 계약을 좁히면 QA 실패 경로
   (``generating -> qa_failed``)와 정상 완료를 구분할 수 없어지므로, 간선은 그대로
   두고 **그 간선을 지나는 유일한 문**인 ``promote_to_ready`` 에 게이트를 못 박는다.
   ``assert_qa_gate`` 는 ``qa_report is not None`` 과 ``qa_report.passed`` 를
   둘 다 요구하며, 계보 불일치도 여기서 죽는다.

2. **크래시로 인한 중복 발행.** Threads 발행 API 호출이 성공한 직후·원장 확정
   직전에 워커가 죽으면, 큐는 리스 만료로 잡을 회수하면서
   ``recovered_from="publishing"`` 과 ``publish_attempted_at`` 을 찍는다.
   그 신호를 달고 돌아온 잡은 **이미 올라간 글이 있을 수도 있는 잡**이다.
   ``publish_video`` 는 그런 잡에 대해 ``existence_checker`` 없이는 발행 호출을
   아예 하지 않고 ``DuplicatePublishRisk`` 로 죽는다. 검사에서 글이 발견되면
   재발행 대신 그 media_id 를 채택한다(dedup).

경계 원칙
---------
* **영상은 로컬 파일로 넘긴다.** 업로드하지 않고 공개 호스팅을 도입하지 않는다.
  핸드오프 패킷은 절대 경로 + sha256 을 싣는다.
* **자격증명은 절대 싣지 않는다.** 패킷은 만들 때 ``assert_no_credentials`` 를
  통과해야 한다. 토큰은 발행 워커가 자기 설정에서 읽는다.
* **락과 상태 기계를 재구현하지 않는다.** 잠금은 ``video_queue.VideoLedger._locked``,
  전이는 ``video_contracts.assert_transition`` 이 유일한 권위다.

이 모듈은 네트워크를 호출하지 않는다. 실제 발행은 주입된 ``publisher`` 호출 가능
객체가 한다 — 테스트는 그 시임에 가짜를 물려 네트워크 없이 돈다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

import video_contracts as vc
import video_queue as vq
from video_contracts import (STATE_DEAD_LETTER, STATE_GENERATING,
                             STATE_PUBLISHED, STATE_PUBLISHING, STATE_QUEUED,
                             STATE_READY_TO_PUBLISH, PublishingHandoff,
                             QAReport, VideoJob, append_event)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

#: 발행 근거 로그 (원장 디렉터리 안). 텍스트 파이프라인의 published.jsonl 을
#: 건드리지 않는다 — 스키마가 다르고, 섞으면 기존 분석이 깨진다.
EVIDENCE_FILENAME = "publish_evidence.jsonl"

#: 발행 워커가 요구할 수 있는 리스 기본값. 업로드+발행이 넉넉히 끝나는 시간.
DEFAULT_PUBLISH_LEASE_SECONDS = 600.0

#: 패킷이 반드시 담아야 하는 것. 하나라도 비면 핸드오프를 만들지 않는다.
REQUIRED_PACKET_FIELDS = (
    "job_id", "run_id", "product_id", "market", "account",
    "video_path", "video_sha256", "duration_seconds", "aspect_ratio",
    "caption", "disclosure", "affiliate_link",
    "qa_report", "qa_report_path", "lineage",
    "idempotency_key", "created_at",
)

#: 이 조각이 키에 들어 있으면 자격증명으로 간주하고 거부한다.
CREDENTIAL_KEY_MARKERS = (
    "token", "secret", "password", "passwd", "api_key", "apikey",
    "cookie", "authorization", "credential", "private_key", "session_id",
)


# ---------------------------------------------------------------------------
# 예외 — 전부 ContractError 하위 (CLI 가 한 번에 잡아 조용히 보고한다)
# ---------------------------------------------------------------------------


class HandoffError(vc.ContractError):
    """핸드오프 계층 오류 공통 베이스."""


class QAGateError(HandoffError):
    """QA 리포트가 없거나 통과하지 않았다 — 발행 핸드오프 불가."""


class DuplicatePublishRisk(HandoffError):
    """이미 발행됐을 수 있는 잡을 존재 확인 없이 다시 발행하려 했다."""


class CredentialLeak(HandoffError):
    """핸드오프 패킷에 자격증명이 실렸다."""


# ---------------------------------------------------------------------------
# 프리미티브
# ---------------------------------------------------------------------------


def sha256_file(path: str, chunk: int = 1024 * 1024) -> str:
    """로컬 파일의 sha256. 발행 워커가 받은 파일이 QA 를 통과한 그 파일인지
    스스로 확인할 수 있게 패킷에 싣는다."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def assert_local_video(path: Any) -> str:
    """영상은 **로컬 파일**로만 넘어간다. URL 은 거부한다.

    공개 호스팅에 올리는 순간 (a) 승인 안 된 소재가 인터넷에 남고 (b) 발행
    실패 시 회수할 방법이 없다. 이 파이프라인은 업로드하지 않는다.
    """
    if not isinstance(path, str) or not path.strip():
        raise HandoffError(f"video_path 가 비어 있다: {path!r}")
    if "://" in path:
        raise HandoffError(
            f"영상은 로컬 파일로만 넘긴다 — 공개 호스팅 URL 금지: {path!r}")
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        raise HandoffError(f"영상 파일이 없다: {absolute}")
    if os.path.getsize(absolute) <= 0:
        raise HandoffError(f"영상 파일이 비어 있다: {absolute}")
    return absolute


def assert_no_credentials(value: Any, _path: str = "packet") -> Any:
    """dict/list 를 재귀적으로 훑어 자격증명스러운 키가 있으면 죽는다.

    조용히 마스킹하지 않는다 — 마스킹하면 '왜 발행 워커가 토큰을 넘겨받으려
    했는가'라는 진짜 질문이 묻힌다.
    """
    if isinstance(value, dict):
        for key, sub in value.items():
            lowered = str(key).lower()
            for marker in CREDENTIAL_KEY_MARKERS:
                if marker in lowered:
                    raise CredentialLeak(
                        f"{_path}.{key} 는 자격증명으로 보인다 — 핸드오프 패킷에 "
                        f"자격증명을 실을 수 없다")
            assert_no_credentials(sub, f"{_path}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            assert_no_credentials(sub, f"{_path}[{i}]")
    return value


def _require_post_url(url: Any) -> str:
    if not isinstance(url, str) or not url.startswith("http"):
        raise HandoffError(f"post_url 은 http(s) URL 이어야 한다: {url!r}")
    return url


def _require_media_id(media_id: Any) -> str:
    if not isinstance(media_id, str) or not media_id.strip():
        raise HandoffError(f"media_id 는 비어 있을 수 없다: {media_id!r}")
    return media_id


# ---------------------------------------------------------------------------
# QA 게이트 — 이 태스크의 핵심
# ---------------------------------------------------------------------------


def assert_qa_gate(job: VideoJob) -> QAReport:
    """``generating -> ready_to_publish`` 간선의 구조적 전제조건.

    계약의 전이표는 이 간선을 허용하지만, 이 함수를 통과하지 않고서는 원장의
    잡이 그 간선을 지날 수 없다. QA 리포트가 없거나, 통과하지 않았거나,
    다른 잡의 것이면 여기서 죽는다.
    """
    report = job.qa_report
    if report is None:
        raise QAGateError(
            f"{job.job_id}: qa_report 가 없다 — QA 없이 발행 핸드오프 금지 "
            f"(generating -> {STATE_READY_TO_PUBLISH} 간선의 전제조건)")
    report.validate()
    if report.job_id != job.job_id or report.run_id != job.run_id:
        raise vc.LineageError(
            f"QA 리포트 계보 불일치: {report.job_id}/{report.run_id} != "
            f"{job.job_id}/{job.run_id}")
    if not report.passed:
        raise QAGateError(
            f"{job.job_id}: QA 가 통과하지 않았다 — 발행 핸드오프 금지: "
            f"{report.failures}")
    return report


def needs_existence_check(entry: Dict[str, Any]) -> bool:
    """이 잡이 '이미 발행됐을 수도 있는' 잡인가.

    큐는 publishing 중 죽은 잡을 회수하면서 ``recovered_from`` 과
    ``publish_attempted_at`` 을 원장에 못 박는다. 그 신호가 살아 있고 아직
    확정된 media_id 가 없다면, 발행 API 가 이미 성공했을 가능성이 남아 있다.
    """
    if entry.get("media_id"):
        return False        # 이미 확정됐다 — 재발행 자체가 막힌다
    return bool(entry.get("recovered_from") == STATE_PUBLISHING
                or entry.get("publish_attempted_at"))


# ---------------------------------------------------------------------------
# 패킷 조립
# ---------------------------------------------------------------------------


def build_packet(*, job: VideoJob, entry: Dict[str, Any], video_path: str,
                 video_sha256: str, caption: str, disclosure: str,
                 affiliate_link: str, qa_report_path: str,
                 account: str) -> Dict[str, Any]:
    """발행 워커가 필요한 것 전부 — 그리고 그 이상은 아무것도.

    계보는 소스 자산까지 되짚을 수 있어야 한다: 어떤 상품의 어떤 원문에서,
    어떤 바이럴 패턴과 초안으로, 어떤 스토리보드를 거쳐, 어떤 프로바이더
    요청으로 만들어졌는가.
    """
    report = job.qa_report
    manifest = job.manifest
    if manifest is None:
        raise HandoffError(f"{job.job_id}: manifest 가 없다 — 계보를 실을 수 없다")

    packet = {
        "job_id": job.job_id,
        "run_id": job.run_id,
        "product_id": job.product_id,
        "market": job.market,
        "account": account,
        "video_path": video_path,
        "video_sha256": video_sha256,
        "duration_seconds": manifest.total_duration_seconds(),
        "aspect_ratio": manifest.aspect_ratio,
        "caption": caption,
        "disclosure": disclosure,
        "affiliate_link": affiliate_link,
        "qa_report": report.to_dict() if report else None,
        "qa_report_path": os.path.abspath(qa_report_path),
        "idempotency_key": entry.get("idempotency_key"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "lineage": {
            "run_id": job.run_id,
            "product_id": job.product_id,
            "market": job.market,
            "storyboard_id": job.storyboard.storyboard_id,
            "content_draft_id": job.storyboard.content_draft_id,
            "viral_pattern_ids": list(job.storyboard.viral_pattern_ids),
            "source_urls": list(job.evidence.source_urls),
            "source_sha256": list(job.evidence.source_sha256),
            "evidence_captured_at": job.evidence.captured_at,
            "rights_basis": job.evidence.rights.get("basis"),
            "provider_request_ids": [c.provider_request_id
                                     for c in manifest.cuts],
            "cut_sha256": [c.output_sha256 for c in manifest.cuts],
            "pipeline_version": entry.get("pipeline_version"),
        },
    }

    missing = [f for f in REQUIRED_PACKET_FIELDS
               if packet.get(f) in (None, "", [], {})]
    if missing:
        raise HandoffError(f"핸드오프 패킷에 빠진 항목: {missing}")
    assert_no_credentials(packet)
    return packet


# ---------------------------------------------------------------------------
# 원장 연동
# ---------------------------------------------------------------------------


def _attach_packet(ledger: vq.VideoLedger, job_id: str,
                   packet: Dict[str, Any]) -> Dict[str, Any]:
    """패킷을 원장 엔트리에 붙인다 (원장의 지정 락 아래에서)."""
    with ledger._locked():                       # noqa: SLF001 — 지정 락 구현
        data = ledger._read()                    # noqa: SLF001
        entry = ledger._find(data, job_id)       # noqa: SLF001
        entry["packet"] = packet
        entry["updated_at"] = time.time()
        ledger._write(data)                      # noqa: SLF001
        return dict(entry)


def promote_to_ready(ledger: vq.VideoLedger, *, job_id: str, worker_id: str,
                     manifest: vc.GenerationManifest,
                     qa_report: Optional[QAReport],
                     video_path: str, caption: str, disclosure: str,
                     affiliate_link: str, qa_report_path: str,
                     account: str) -> Dict[str, Any]:
    """생성 워커가 잡을 발행 대기열로 올리는 **유일한** 문.

    QA 게이트를 먼저 통과시키고, 그다음 패킷을 조립하고, 마지막에 원장을
    옮긴다. 앞 두 단계 중 하나라도 실패하면 원장은 손대지 않는다 — 잡은
    ``generating`` 에 남고 발행 대기열에는 아무것도 나타나지 않는다.
    """
    entry = ledger.get(job_id)
    job = VideoJob.from_dict(entry["job"])
    job.manifest = manifest
    job.qa_report = qa_report

    assert_qa_gate(job)                          # QA 없으면 여기서 끝

    if not str(disclosure or "").strip():
        raise vc.RightsError(
            "제휴 고지가 비어 있다 — 고지 없는 발행 핸드오프는 금지 (SSOT 불변 규칙 2)")
    if disclosure.strip() not in (caption or ""):
        raise vc.RightsError("캡션에 제휴 고지 문구가 포함돼 있지 않다")
    if not str(affiliate_link or "").strip():
        raise HandoffError("affiliate_link 가 비어 있다")

    absolute = assert_local_video(video_path)
    digest = sha256_file(absolute)

    handoff = PublishingHandoff(
        job_id=job.job_id, run_id=job.run_id, product_id=job.product_id,
        market=job.market, state=STATE_READY_TO_PUBLISH,
        content_draft_id=job.storyboard.content_draft_id,
        video_path=absolute, video_sha256=digest,
        duration_seconds=manifest.total_duration_seconds(),
        aspect_ratio=manifest.aspect_ratio,
        caption=caption, disclosure_included=True,
    ).validate()

    job.handoff = handoff
    packet = build_packet(job=job, entry=entry, video_path=absolute,
                          video_sha256=digest, caption=caption,
                          disclosure=disclosure, affiliate_link=affiliate_link,
                          qa_report_path=qa_report_path, account=account)

    ledger.complete(job_id, worker_id, manifest=manifest,
                    qa_report=qa_report, handoff=handoff)
    return _attach_packet(ledger, job_id, packet)


def list_ready(ledger: vq.VideoLedger) -> List[Dict[str, Any]]:
    """발행 준비된 잡의 패킷 목록. QA 를 통과한 것만 여기 존재할 수 있다."""
    out: List[Dict[str, Any]] = []
    for entry in ledger.list_jobs(STATE_READY_TO_PUBLISH):
        packet = entry.get("packet")
        if not packet:
            continue
        out.append(dict(packet, requires_existence_check=needs_existence_check(entry)))
    return out


def claim(ledger: vq.VideoLedger, worker_id: str,
          lease_seconds: float = DEFAULT_PUBLISH_LEASE_SECONDS
          ) -> Optional[Dict[str, Any]]:
    """발행할 영상 하나를 단독 소유로 가져온다. 없으면 None.

    원자성은 원장의 락이 보장한다 — 여기서 두 번째 잠금 구현을 만들지 않는다.
    """
    entry = ledger.claim(worker_id, states=(STATE_READY_TO_PUBLISH,),
                         lease_seconds=lease_seconds)
    if entry is None:
        return None
    packet = entry.get("packet")
    if not packet:
        # QA 게이트를 우회해 원장에 직접 꽂힌 잡. 발행하지 않고 되돌린다.
        ledger.retry(entry["job_id"], worker_id, reason="packet_missing")
        raise HandoffError(
            f"{entry['job_id']}: 핸드오프 패킷이 없다 — 발행 대기열에 들어올 수 없는 잡이다")
    return {
        "job_id": entry["job_id"],
        "state": entry["state"],
        "worker_id": worker_id,
        "packet": packet,
        "requires_existence_check": needs_existence_check(entry),
        "recovered_from": entry.get("recovered_from"),
        "publish_attempted_at": entry.get("publish_attempted_at"),
    }


def _record_evidence(ledger: vq.VideoLedger, entry: Dict[str, Any],
                     packet: Dict[str, Any], media_id: str, post_url: str,
                     deduplicated: bool) -> Dict[str, Any]:
    """발행 근거를 추가 전용 로그에 남긴다.

    텍스트 파이프라인의 ``published.jsonl`` 과 분리한 파일을 쓴다 — 스키마가
    다르므로 섞으면 기존 analytics 수집이 깨진다. analytics 쪽에는 영상 행을
    **알아보는 법**만 더한다(기존 텍스트 경로는 그대로).
    """
    import analytics

    row = {
        "media_id": media_id,
        "post_url": post_url,
        "post_type": analytics.VIDEO_POST_TYPE,
        "country": packet.get("market"),
        "product_id": packet.get("product_id"),
        "video_job_id": packet.get("job_id"),
        "video_run_id": packet.get("run_id"),
        "qa_report_ref": packet.get("qa_report_path"),
        "video_sha256": packet.get("video_sha256"),
        "duration_seconds": packet.get("duration_seconds"),
        "idempotency_key": packet.get("idempotency_key"),
        "deduplicated": deduplicated,
        "lineage": packet.get("lineage"),
    }
    assert_no_credentials(row, "evidence")
    return append_event(os.path.join(ledger.root, EVIDENCE_FILENAME), row)


def mark_published(ledger: vq.VideoLedger, job_id: str, worker_id: str, *,
                   media_id: str, post_url: str,
                   deduplicated: bool = False) -> Dict[str, Any]:
    """발행 확정. 같은 media_id 로 다시 부르면 멱등하게 성공한다.

    다른 media_id 로 두 번째 확정을 시도하면 **글이 두 개 올라갔다는 뜻**이므로
    조용히 덮어쓰지 않고 ``DuplicatePublishRisk`` 로 죽는다.
    """
    _require_media_id(media_id)
    _require_post_url(post_url)

    entry = ledger.get(job_id)
    if entry["state"] == STATE_PUBLISHED:
        existing = entry.get("media_id")
        if existing == media_id:
            return dict(entry, idempotent=True, deduplicated=deduplicated)
        raise DuplicatePublishRisk(
            f"{job_id}: 이미 {existing!r} 로 발행 확정됐는데 {media_id!r} 로 다시 "
            f"확정하려 한다 — 글이 중복 발행됐을 수 있다. 사람이 확인해야 한다")

    entry = ledger.publish_done(job_id, worker_id, media_id)
    packet = entry.get("packet") or {}
    _record_evidence(ledger, entry, packet, media_id, post_url, deduplicated)
    ledger._event(job_id=job_id, run_id=entry.get("run_id", ""),       # noqa: SLF001
                  event="publish_evidence", media_id=media_id,
                  post_url=post_url, deduplicated=deduplicated)
    return dict(entry, idempotent=False, deduplicated=deduplicated)


def publish_video(ledger: vq.VideoLedger, job_id: str, worker_id: str, *,
                  publisher: Callable[[Dict[str, Any]], Dict[str, Any]],
                  existence_checker: Optional[
                      Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None
                  ) -> Dict[str, Any]:
    """실제 발행 한 번. 중복 발행 위험이 있으면 **호출 자체를 하지 않는다**.

    ``publisher`` 는 패킷을 받아 ``{"media_id", "post_url"}`` 을 돌려주는
    호출 가능 객체다. 이 모듈은 네트워크를 모른다.
    """
    entry = ledger.get(job_id)
    if entry["state"] == STATE_PUBLISHED:
        raise HandoffError(
            f"{job_id}: 이미 발행 확정된 잡이다 (media_id={entry.get('media_id')!r}) "
            f"— 재발행하지 않는다")
    if entry["state"] != STATE_PUBLISHING:
        raise HandoffError(
            f"{job_id}: 발행하려면 {STATE_PUBLISHING} 상태여야 한다: {entry['state']!r}")
    packet = entry.get("packet")
    if not packet:
        raise HandoffError(f"{job_id}: 핸드오프 패킷이 없다 — 발행 불가")

    if needs_existence_check(entry):
        if existence_checker is None:
            raise DuplicatePublishRisk(
                f"{job_id}: publishing 에서 회수된 잡이다 "
                f"(recovered_from={entry.get('recovered_from')!r}, "
                f"publish_attempted_at={entry.get('publish_attempted_at')!r}). "
                f"발행 API 가 이미 성공했을 수 있으므로 existence_checker 없이는 "
                f"재발행하지 않는다")
        found = existence_checker(packet)
        if found:
            return mark_published(ledger, job_id, worker_id,
                                  media_id=_require_media_id(found["media_id"]),
                                  post_url=_require_post_url(found["post_url"]),
                                  deduplicated=True)

    result = publisher(packet) or {}
    return mark_published(ledger, job_id, worker_id,
                          media_id=_require_media_id(result.get("media_id")),
                          post_url=_require_post_url(result.get("post_url")),
                          deduplicated=False)


def mark_failed(ledger: vq.VideoLedger, job_id: str,
                worker_id: Optional[str] = None, *, reason: str = "",
                dead_letter: bool = False) -> Dict[str, Any]:
    """발행 실패를 기록한다. 계약의 전이표에 있는 간선만 쓴다.

    기본 경로는 ``publishing -> retryable_failed -> queued|dead_letter`` 이며,
    전부 ``video_queue`` 가 계약을 통과시켜 수행한다. 종결 상태(published /
    dead_letter)에서 부르면 계약이 StateError 로 거절한다.
    """
    if dead_letter:
        return ledger.dead_letter(job_id, reason=reason or "publish_failed")
    return ledger.retry(job_id, worker_id, reason=reason or "publish_failed")


# ---------------------------------------------------------------------------
# CLI — 발행 워커·운영자용
# ---------------------------------------------------------------------------


def _print(payload: Any, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(human)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="video_handoff.py", description="HeightCue 영상 발행 핸드오프 CLI")
    parser.add_argument("--root", default=None, help="원장 디렉터리 (기본: state/video)")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="JSON 으로 출력")

    sub.add_parser("list-ready", parents=[common], help="발행 준비된 패킷 목록")

    p_claim = sub.add_parser("claim", parents=[common], help="영상 하나를 단독 소유")
    p_claim.add_argument("--worker", required=True)
    p_claim.add_argument("--lease-seconds", type=float,
                         default=DEFAULT_PUBLISH_LEASE_SECONDS)

    p_pub = sub.add_parser("mark-published", parents=[common], help="발행 확정")
    p_pub.add_argument("job_id")
    p_pub.add_argument("--worker", required=True)
    p_pub.add_argument("--media-id", required=True)
    p_pub.add_argument("--post-url", required=True)

    p_fail = sub.add_parser("mark-failed", parents=[common], help="발행 실패 기록")
    p_fail.add_argument("job_id")
    p_fail.add_argument("--worker", default=None)
    p_fail.add_argument("--reason", default="publish_failed")
    p_fail.add_argument("--dead-letter", action="store_true")

    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    ledger = vq.VideoLedger(args.root)

    try:
        if args.cmd == "list-ready":
            packets = list_ready(ledger)
            _print(packets, as_json,
                   "\n".join(f"{p['job_id']:<20} {p['market']} "
                             f"{p['product_id']} {p['video_path']}"
                             for p in packets) or "(비어 있음)")
            return 0

        if args.cmd == "claim":
            claimed = claim(ledger, args.worker, args.lease_seconds)
            payload = {"claimed": claimed}
            _print(payload, as_json,
                   f"{claimed['job_id']} 소유 획득" if claimed else "(가져올 잡 없음)")
            return 0

        if args.cmd == "mark-published":
            entry = mark_published(ledger, args.job_id, args.worker,
                                   media_id=args.media_id,
                                   post_url=args.post_url)
            payload = {"job_id": entry["job_id"], "state": entry["state"],
                       "media_id": entry.get("media_id"),
                       "idempotent": entry.get("idempotent", False)}
            _print(payload, as_json, f"{args.job_id} 발행 확정 ({args.media_id})")
            return 0

        # mark-failed — 하위 파서가 required=True 라 남은 경우는 이것뿐이다.
        entry = mark_failed(ledger, args.job_id, args.worker,
                            reason=args.reason, dead_letter=args.dead_letter)
        payload = {"job_id": entry["job_id"], "state": entry["state"],
                   "attempts": entry.get("attempts", 0)}
        _print(payload, as_json, f"{args.job_id} → {entry['state']}")
        return 0

    except KeyError as exc:
        print(f"없는 잡: {exc}", file=sys.stderr)
        return 2
    except vc.ContractError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
