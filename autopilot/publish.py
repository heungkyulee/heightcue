# -*- coding: utf-8 -*-
"""파이프라인 B: Threads 공식 API 발행 계층.

- 2단계 발행: 컨테이너 생성 → publish. 링크는 link_attachment로 첨부(본문 텍스트를 아끼는 방식).
- reply_to_id로 첫 답글(링크 답글 A/B)과 댓글 답글을 발행.
- 장기 토큰은 60일 유효 — refresh()를 주 1회 돌려 갱신하고 config에 반영하는 것은 운영 세션이 담당.
- dry_run이면 호출 없이 로그만 남긴다.
"""
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

import requests

import execution_contract
from common import append_jsonl, log, read_jsonl, redact_secrets, state_path

API = "https://graph.threads.net/v1.0"


def _threads_text_matches(expected, observed):
    """Exact match except Threads Graph's documented/observed hashtag marker removal."""
    def canonical(value):
        # Strip '#' only when it starts a lexical hashtag; keep numbers and embedded hashes exact.
        return re.sub(r"(?<!\w)#(?=[^\W\d_])", "", str(value or ""))
    return canonical(expected) == canonical(observed)


class PublicationVerificationError(RuntimeError):
    """API가 publish id를 반환했지만 실제 객체 read-back을 확정하지 못함."""

    def __init__(self, media_id, detail):
        self.media_id = str(media_id)
        super().__init__(f"Threads 발행 검증 보류({self.media_id}): {detail}")


class PublicationStateError(RuntimeError):
    """예약 원장의 소유권 또는 허용된 상태 전이가 위반됨."""


_PUBLICATION_EDGES = {
    "reserved": {"creating", "released"},
    "creating": {"created", "released", "verification_pending"},
    "created": {"publishing", "verification_pending"},
    "publishing": {"verification_pending", "verified"},
    "verification_pending": {"verified", "failed"},
    "released": set(),
    "verified": set(),
}


def _reservation_latest(rows):
    latest = {}
    for row in rows:
        if row.get("idempotency_key"):
            latest[row["idempotency_key"]] = row
    return latest


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _transition_publication(cfg, key, expected, status, owner_id, **fields):
    import fcntl
    ledger = state_path(cfg, "publication_reservations.jsonl")
    lock = state_path(cfg, "publication_reservations.lock")
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        current = _reservation_latest(read_jsonl(ledger)).get(key)
        if current is None:
            raise PublicationStateError("publication reservation does not exist")
        if current.get("status") != expected:
            raise PublicationStateError("publication reservation state changed")
        if not owner_id or current.get("owner_id") != owner_id:
            raise PublicationStateError("publication reservation owner mismatch")
        if status not in _PUBLICATION_EDGES or status not in _PUBLICATION_EDGES[expected]:
            raise PublicationStateError(f"illegal publication transition: {expected}->{status}")
        if status == "verified":
            raise PublicationStateError("verified requires coupled API readback")
        append_jsonl(ledger, {**current, **fields, "idempotency_key": key,
                              "status": status,
                              "transitioned_at": datetime.now(timezone.utc).isoformat()})


def _mark_verified_after_readback(cfg, key, owner_id, media_id, text, token):
    """Read the remote object and atomically append verified only on an exact match."""
    verify = requests.get(f"{API}/{media_id}",
                          params={"fields": "id,text", "access_token": token}, timeout=30)
    verify.raise_for_status()
    observed = verify.json()
    if (str(observed.get("id") or "") != str(media_id)
            or not _threads_text_matches(text, observed.get("text"))):
        raise PublicationVerificationError(media_id, "final coupled readback mismatch")
    import fcntl
    ledger = state_path(cfg, "publication_reservations.jsonl")
    lock = state_path(cfg, "publication_reservations.lock")
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        current = _reservation_latest(read_jsonl(ledger)).get(key)
        if current is None or current.get("status") != "publishing":
            raise PublicationStateError("publication reservation state changed")
        if current.get("owner_id") != owner_id:
            raise PublicationStateError("publication reservation owner mismatch")
        append_jsonl(ledger, {**current, "status": "verified", "media_id": media_id,
                              "transitioned_at": datetime.now(timezone.utc).isoformat()})


def recover_publication_reservations(cfg):
    """Dead pre-submit owners release; possible submissions remain pending."""
    import fcntl
    ledger = state_path(cfg, "publication_reservations.jsonl")
    lock = state_path(cfg, "publication_reservations.lock")
    recovered = []
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        for key, row in _reservation_latest(read_jsonl(ledger)).items():
            if row.get("status") not in ("reserved", "submitting", "creating", "created", "publishing") or _pid_alive(row.get("owner_pid")):
                continue
            status = "released" if row["status"] == "reserved" else "verification_pending"
            append_jsonl(ledger, {**row, "status": status,
                                  "recovered_at": datetime.now(timezone.utc).isoformat()})
            recovered.append((key, status))
    return recovered


def reconcile_pending(cfg):
    """Resolve uncertain submissions by exact remote readback; never repost."""
    import fcntl
    ledger = state_path(cfg, "publication_reservations.jsonl")
    lock = state_path(cfg, "publication_reservations.lock")
    counts = {"verified": 0, "failed": 0, "unchanged": 0, "backfilled": 0}
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        pending = [row for row in _reservation_latest(read_jsonl(ledger)).values()
                   if row.get("status") == "verification_pending"]
        for row in pending:
            media_id = row.get("media_id")
            expected = row.get("expected_text", row.get("text"))
            _, token = _account(cfg, row.get("country", "KR"))
            if not media_id or expected is None or not token:
                counts["unchanged"] += 1
                continue
            try:
                response = requests.get(f"{API}/{media_id}", params={
                    "fields": "id,text", "access_token": token}, timeout=30)
                response.raise_for_status()
                observed = response.json()
                status = ("verified" if str(observed.get("id")) == str(media_id)
                          and _threads_text_matches(expected, observed.get("text")) else "failed")
                reconciled_at = datetime.now(timezone.utc).isoformat()
                append_jsonl(ledger, {**row, "status": status,
                                     "reconciled_at": reconciled_at})
                if status == "verified":
                    published_path = state_path(cfg, "published.jsonl")
                    candidates = [item for item in read_jsonl(published_path)
                                  if str(item.get("media_id") or "") == str(media_id)
                                  and (item.get("meta") or {}).get("publish_status") == "verification_pending"]
                    if candidates:
                        original = candidates[-1]
                        meta = {**(original.get("meta") or {}), "publish_status": "verified",
                                "reconciled_from": "verification_pending",
                                "reconciled_at": reconciled_at}
                        append_jsonl(published_path, {**original, "meta": meta})
                counts[status] += 1
            except Exception as exc:
                counts["unchanged"] += 1
                append_jsonl(ledger, {**row, "reconcile_error": redact_secrets(str(exc)),
                                     "reconciled_at": datetime.now(timezone.utc).isoformat()})

        # Repair partial application: reservation may already be verified while the
        # append-only published ledger still ends at verification_pending.
        reservations = _reservation_latest(read_jsonl(ledger))
        published_path = state_path(cfg, "published.jsonl")
        published_rows = read_jsonl(published_path)
        verified_media = {str(item.get("media_id")) for item in published_rows
                          if (item.get("meta") or {}).get("publish_status") == "verified"}
        for original in published_rows:
            meta = original.get("meta") or {}
            media_id = str(original.get("media_id") or "")
            key = meta.get("idempotency_key")
            reservation = reservations.get(key) if key else None
            if (meta.get("publish_status") != "verification_pending" or not media_id
                    or media_id in verified_media or not reservation
                    or reservation.get("status") != "verified"
                    or str(reservation.get("media_id") or "") != media_id):
                continue
            reconciled_at = datetime.now(timezone.utc).isoformat()
            corrected_meta = {**meta, "publish_status": "verified",
                              "reconciled_from": "verification_pending",
                              "reconciled_at": reconciled_at}
            append_jsonl(published_path, {**original, "meta": corrected_meta})
            verified_media.add(media_id)
            counts["backfilled"] += 1
    return counts


def _reserve_publication(cfg, record, provenance, text):
    """프로세스 간 동일 게시를 JSONL 예약 원장으로 원자적으로 차단한다."""
    import fcntl
    material = {
        "task": provenance["task"], "country": record["country"],
        "input_ids": provenance["input_ids"],
        "content_digest": hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
    }
    key = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    record["meta"]["idempotency_key"] = key
    ledger = state_path(cfg, "publication_reservations.jsonl")
    lock = state_path(cfg, "publication_reservations.lock")
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        current = _reservation_latest(read_jsonl(ledger)).get(key, {})
        occupied = current.get("status") in (
            "reserved", "submitting", "creating", "created", "publishing", "verified",
            "verification_pending")
        if occupied:
            return False
        owner_id = secrets.token_urlsafe(32)
        append_jsonl(ledger, {**material, "idempotency_key": key,
                              "status": "reserved", "owner_pid": os.getpid(),
                              "expected_text": str(text), "country": record["country"],
                              "owner_id": owner_id,
                              "reserved_at": datetime.now(timezone.utc).isoformat()})
    record["meta"]["reservation_owner_id"] = owner_id
    return owner_id


def _account(cfg, country):
    p = "kr" if country == "KR" else "us"
    return cfg["threads"].get(f"{p}_user_id"), cfg["threads"].get(f"{p}_access_token")


def verified_publication_url(cfg, media_id):
    """Freshly read back the canonical Threads permalink for a published US object."""
    _, token = _account(cfg, "US")
    if not token:
        raise PublicationVerificationError(media_id, "US access token missing")
    response = requests.get(
        f"{API}/{media_id}",
        params={"fields": "id,permalink", "access_token": token}, timeout=30)
    response.raise_for_status()
    observed = response.json()
    permalink = str(observed.get("permalink") or "")
    if str(observed.get("id") or "") != str(media_id) or not re.match(r"^https://(www\.)?threads\.(?:net|com)/", permalink):
        raise PublicationVerificationError(media_id, "canonical permalink readback mismatch")
    return permalink


def publish_text(cfg, country, text, link=None, reply_to=None, dry_run=False, meta=None):
    """텍스트 포스트(또는 답글) 발행. 반환: media_id 또는 None."""
    record = {"country": country, "text": text, "link": link, "reply_to": reply_to, "meta": meta or {}}
    meta_dict = meta or {}
    post_type = meta_dict.get("post_type")

    # 판매글 고지 불변문구 hard-fail 가드
    if post_type == "sales":
        if country == "KR":
            kr_req = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
            if kr_req not in (text or ""):
                append_jsonl(state_path(cfg, "holdbox.jsonl"),
                             {"why": "disclosure_missing_hard_fail", "stage": "publish_boundary", **record})
                log("발행 차단(KR 판매글): 쿠팡 파트너스 고지 불변문구 누락 — hard-fail")
                return None
        elif country == "US":
            first_line = (text or "").splitlines()[0] if text else ""
            if "#ad" not in first_line:
                append_jsonl(state_path(cfg, "holdbox.jsonl"),
                             {"why": "disclosure_missing_hard_fail", "stage": "publish_boundary", **record})
                log("발행 차단(US 판매글): 첫 줄 #ad 고지 누락 — hard-fail")
                return None

    # 모든 발행 경로(메인 글·첫 답글·댓글 답글)의 최종 언어 게이트.
    # run._gate_and_publish는 메인 글을 검사하지만 comments.run은 이 계층을
    # 직접 호출하므로, API 경계에서도 US 한글 혼입을 차단한다.
    if country == "US" and re.search(r"[가-힣]", text or ""):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "language_fail", "stage": "publish_boundary", **record})
        log("발행 차단(US): 한글 혼입 — 보류함 기록")
        return None
    if country == "KR" and not re.search(r"[가-힣]", text or ""):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "language_fail", "stage": "publish_boundary", **record})
        log("발행 차단(KR): 한국어 없음 — 보류함 기록")
        return None
    if dry_run:
        record["meta"]["publish_status"] = "dry_run"
        record["meta"]["publisher"] = "publish.publish_text"
        record["media_id"] = f"DRY-{int(time.time())}"
        append_jsonl(state_path(cfg, "published.jsonl"), record)
        log(f"발행(dry, {country}): {text.splitlines()[0][:48]}...")
        return record["media_id"]

    # 실생성 결과는 생성 경로·모델·프롬프트 정본을 증명해야 한다. 이 경계에서
    # 강제하지 않으면 세션이나 수동 스크립트가 통합 계약을 우회해도 게시된다.
    provenance = record["meta"].get("execution_contract")
    attestation = record["meta"].get("generation_attestation")
    rehearsal_fixture = (bool(record["meta"].get("rehearsal_fixture"))
                         and bool((cfg.get("mode") or {}).get("_rehearsal"))
                         and (cfg.get("mode") or {}).get("publish") is False)
    try:
        if rehearsal_fixture:
            provenance = {"execution_scope": "rehearsal_fixture", "critic_status": "deterministic"}
        elif attestation is not None:
            part_n = record["meta"].get("thread_part")
            part_total = record["meta"].get("thread_total")
            expected_payload = (
                {"parts": [text]}
                if (part_n is not None and part_total is not None and part_total > 1
                    and len(attestation.get("payload", {}).get("output_digests", [])) == 1)
                else ({"thread_part": part_n, "text": text} if (part_n is not None and part_total is not None and part_total > 1) else {"text": text})
            )
            if not execution_contract.verify_attestation(
                    attestation, expected_payload, project_root=execution_contract.PROJECT_ROOT,
                    expected_country=country,
                    expected_rehearsal=bool((cfg.get("mode") or {}).get("_rehearsal"))):
                raise execution_contract.ContractError("generation attestation mismatch")
            provenance = attestation["payload"]
        else:
            execution_contract.validate_provenance(
                cfg, provenance, country=country, text=text)
    except execution_contract.ContractError as exc:
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "execution_contract_invalid", "stage": "publish_boundary",
                      "contract_error": str(exc), **record})
        log(f"발행 차단({country}): 실행 계약 provenance 불일치")
        return None

    # 리허설 모드: 실제 생성·검사는 다 하되 발행만 안 함. config에 "publish": true 를 넣어야 실발행.
    if not cfg["mode"].get("publish", False):
        record["meta"]["publish_status"] = "preview"
        record["meta"]["publisher"] = "publish.publish_text"
        record["media_id"] = f"PREVIEW-{int(time.time())}"
        append_jsonl(state_path(cfg, "preview.jsonl"), record)
        log(f"리허설(발행 안 함, {country}): {text.splitlines()[0][:48]}... → state/preview.jsonl")
        return record["media_id"]

    user_id, token = _account(cfg, country)
    if not (user_id and token):
        log(f"발행 불가({country}): Threads 토큰 없음 → 보류함으로")
        append_jsonl(state_path(cfg, "holdbox.jsonl"), {"why": "no_token", **record})
        return None

    if not _reserve_publication(cfg, record, provenance, text):
        log(f"발행 차단({country}): 동일 입력·본문의 live 예약이 이미 존재")
        return None

    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if link:
        params["link_attachment"] = link
    if reply_to:
        params["reply_to_id"] = reply_to
    key = record["meta"]["idempotency_key"]
    owner = record["meta"]["reservation_owner_id"]
    _transition_publication(cfg, key, "reserved", "creating", owner)
    try:
        r = requests.post(f"{API}/{user_id}/threads", data=params, timeout=30)
        r.raise_for_status()
    except requests.HTTPError as exc:
        _transition_publication(cfg, key, "creating", "released", owner,
                                create_error=redact_secrets(str(exc)))
        raise
    except Exception as exc:
        _transition_publication(cfg, key, "creating", "verification_pending", owner,
                                create_error=redact_secrets(str(exc)))
        raise
    try:
        creation_id = r.json()["id"]
        _transition_publication(cfg, key, "creating", "created", owner,
                                creation_id=creation_id)
        time.sleep(2)
        _transition_publication(cfg, key, "created", "publishing", owner,
                                creation_id=creation_id)
        r2 = requests.post(f"{API}/{user_id}/threads_publish",
                           data={"creation_id": creation_id, "access_token": token}, timeout=30)
        r2.raise_for_status()
        media_id = r2.json()["id"]
    except Exception as exc:
        current_status = _reservation_latest(read_jsonl(
            state_path(cfg, "publication_reservations.jsonl")))[key]["status"]
        _transition_publication(cfg, key, current_status, "verification_pending", owner,
                                submission_error=redact_secrets(str(exc)))
        raise

    # API publish 응답은 접수 확인일 뿐이다. 실제 객체를 다시 읽어 ID와 본문을
    # 대조한 뒤에만 성공 기록을 남긴다. 게시 직후 eventual consistency를 위해
    # 짧게 재시도하되, 끝내 일치하지 않으면 성공으로 기록하지 않는다.
    observed = {}
    verify_error = None
    for attempt in range(3):
        try:
            verify = requests.get(
                f"{API}/{media_id}",
                params={"fields": "id,text", "access_token": token}, timeout=30)
            verify.raise_for_status()
            observed = verify.json()
            text_match = _threads_text_matches(text, observed.get("text"))
            if (str(observed.get("id") or "") == str(media_id) and text_match):
                verify_error = None
                break
            verify_error = RuntimeError(
                f"Threads 발행 검증 불일치: id={observed.get('id')!r}, "
                f"text_match={text_match}")
        except Exception as exc:
            verify_error = exc
        if attempt < 2:
            time.sleep(2 ** attempt)
    if verify_error is not None:
        detail = redact_secrets(str(verify_error))
        # publish API가 media_id를 반환한 뒤에는 재게시보다 보류가 안전하다.
        # 검증 불확실 레코드를 durable하게 남겨 다음 실행이 같은 콘텐츠를
        # 신규 게시로 오인하지 않게 한다. verified 성공으로는 절대 기록하지 않는다.
        record["meta"]["publish_status"] = "verification_pending"
        record["meta"]["publisher"] = "publish.publish_text"
        record["meta"]["published_media_id"] = media_id
        record["meta"]["verification_error"] = detail
        record["meta"]["verification_attempted_at"] = datetime.now(timezone.utc).isoformat()
        record["media_id"] = media_id
        append_jsonl(state_path(cfg, "published.jsonl"), record)
        _transition_publication(cfg, key, "publishing", "verification_pending", owner,
                                media_id=media_id)
        raise PublicationVerificationError(media_id, detail) from verify_error

    _mark_verified_after_readback(cfg, key, owner, media_id, text, token)
    record["meta"]["publish_status"] = "verified"
    record["meta"]["publisher"] = "publish.publish_text"
    record["meta"]["published_media_id"] = media_id
    record["meta"]["published_at"] = datetime.now(timezone.utc).isoformat()
    record["media_id"] = media_id
    append_jsonl(state_path(cfg, "published.jsonl"), record)
    log(f"발행 검증 완료({country}): media_id={media_id}")
    return media_id


class DeletePermissionError(RuntimeError):
    """앱에 threads_delete 스코프가 없어 삭제가 거부됨. 토큰 재발급(재인증)이 필요하다."""


def has_delete_scope(cfg, country, dry_run=False):
    """현재 토큰이 삭제 가능한지 스코프로 판정. 실호출 전에 확인용."""
    if dry_run:
        return True
    _, token = _account(cfg, country)
    if not token:
        return False
    try:
        r = requests.get(f"{API.replace('/v1.0','')}/debug_token",
                         params={"input_token": token, "access_token": token}, timeout=20)
        r.raise_for_status()
        return "threads_delete" in (r.json().get("data", {}).get("scopes") or [])
    except Exception as e:
        log(f"스코프 확인 실패({country}): {e}")
        return False


def delete_media(cfg, country, media_id, dry_run=False):
    """기존 Threads 게시물 삭제. 삭제 후 상태 검증은 호출자가 수행한다.

    앱에 threads_delete 권한이 없으면 API가 code 10으로 거부한다 —
    이때는 DeletePermissionError를 올려 '삭제됐다'는 오해를 막는다.
    """
    if dry_run:
        log(f"삭제(dry, {country}): media_id={media_id}")
        return True
    user_id, token = _account(cfg, country)
    if not (user_id and token):
        raise RuntimeError(f"삭제 불가({country}): Threads 토큰 없음")
    r = requests.delete(f"{API}/{media_id}", params={"access_token": token}, timeout=30)
    if not r.ok:
        try:
            err = r.json().get("error", {})
        except Exception:
            err = {}
        msg = str(err.get("message", ""))
        if err.get("code") == 10 or "does not have permission" in msg:
            raise DeletePermissionError(
                f"삭제 권한 없음({country}): 앱에 threads_delete 스코프가 없다. "
                f"Meta 앱에 권한 추가 후 재인증(토큰 재발급)이 필요하다. 원문: {msg}")
        r.raise_for_status()
    log(f"삭제({country}): media_id={media_id}")
    return True


def refresh_token(cfg, country):
    """장기 토큰 갱신(60일 만료 전 주기 실행). 새 토큰을 반환 — config 반영은 호출자가."""
    _, token = _account(cfg, country)
    if not token:
        return None
    r = requests.get(f"{API.replace('/v1.0','')}/refresh_access_token",
                     params={"grant_type": "th_refresh_token", "access_token": token}, timeout=30)
    r.raise_for_status()
    return r.json().get("access_token")


_REPLY_FIELDS = "id,text,username,timestamp,replied_to,is_reply_owned_by_me"

_USERNAME_CACHE = {}


def fetch_username(cfg, country, dry_run=False):
    """계정 자기 핸들. 자기 댓글에 자기가 답글 다는 루프를 막는 데 쓴다."""
    if dry_run:
        return "dry_self"
    if country in _USERNAME_CACHE:
        return _USERNAME_CACHE[country]
    user_id, token = _account(cfg, country)
    if not (user_id and token):
        return None
    try:
        r = requests.get(f"{API}/{user_id}",
                         params={"fields": "username", "access_token": token}, timeout=30)
        r.raise_for_status()
        name = r.json().get("username")
    except Exception as e:  # 조회 실패해도 파이프라인은 계속 — 다른 자기필터가 있다
        log(f"username 조회 실패({country}): {e}")
        return None
    _USERNAME_CACHE[country] = name
    return name


def fetch_replies(cfg, country, media_id, dry_run=False):
    """게시물의 직속 답글만. 대댓글까지 필요하면 fetch_conversation을 쓸 것."""
    if dry_run:
        return [{"id": f"DRYC-{media_id}", "text": "저도 반에서 제일 작았는데 이 글 너무 공감돼요",
                 "username": "dry_user", "replied_to": {"id": media_id}}]
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{media_id}/replies",
                     params={"fields": _REPLY_FIELDS, "access_token": token}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_conversation(cfg, country, media_id, dry_run=False):
    """게시물 스레드 전체(대댓글 포함). 실패 시 호출자가 fetch_replies로 폴백한다."""
    if dry_run:
        return fetch_replies(cfg, country, media_id, dry_run=True)
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{media_id}/conversation",
                     params={"fields": _REPLY_FIELDS, "reverse": "false",
                             "access_token": token}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_insights(cfg, country, media_id, dry_run=False):
    if dry_run:
        return {"views": 1200, "likes": 34, "replies": 3, "reposts": 2, "quotes": 0, "shares": 1}
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{media_id}/insights",
                     params={"metric": "views,likes,replies,reposts,quotes,shares",
                             "access_token": token}, timeout=30)
    r.raise_for_status()
    out = {}
    for item in r.json().get("data", []):
        vals = item.get("values") or [{}]
        out[item.get("name")] = vals[0].get("value")
    return out


def fetch_link_clicks(cfg, country, dry_run=False):
    """계정 단위 clicks 지표 — URL별 분해값. 게시물별 고유 링크로 게시물 클릭을 추적한다."""
    if dry_run:
        return {"https://link.coupang.com/DRYRUN": 18}
    user_id, token = _account(cfg, country)
    r = requests.get(f"{API}/{user_id}/threads_insights",
                     params={"metric": "clicks", "access_token": token}, timeout=30)
    r.raise_for_status()
    out = {}
    for item in r.json().get("data", []):
        for lv in item.get("link_total_values", []):
            if "link_url" in lv:
                out[lv["link_url"]] = lv.get("value", 0)
        for v in item.get("values", []):
            out[str(v.get("dimension_values", v.get("end_time", "total")))] = v.get("value")
    return out
