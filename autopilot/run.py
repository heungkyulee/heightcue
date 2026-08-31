#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""heightcue 오토파일럿 — 오케스트레이터.

사용법:
  python3 run.py daily      # 지표 수집 → 소싱 → KR 판매1+가치1 + US 판매(로테이션)+가치1 → 발행/보류
  python3 run.py post       # 가치글 1건 추가 (저녁 슬롯 — KR 가치 2/일 완성)
  python3 run.py comments   # 댓글 수집 → 분류 → 자동 답글/보류
  python3 run.py weekly     # 주간 지표 요약 → 플레이북 갱신 → 리포트 → Threads 토큰 자동 갱신
  python3 run.py rehearsal  # 원커맨드 리허설: validate → daily(실생성·발행 없음) → preview 출력
  python3 run.py status     # 현재 상태 요약 (큐·보류함·오류·발행 카운트)
  python3 run.py golive     # 가동 전환: dry_run=false + publish=true 저장 + crontab 안내 출력
  python3 run.py dryrun     # API 키 없이 전체 사이클 모의 실행
  python3 run.py context    # 소싱용 채널 컨텍스트 출력 (바이오·게시글·답글·받은댓글·큐 상태, JSON)
  python3 run.py video ...  # I2V UGC 영상 워크플로 (enqueue|process|status|rehearsal)
                            # 유료 생성은 config video.production_generation_enabled 기본 꺼짐

발행 정책 (SSOT §8): 포맷 FAIL(500자 초과) → 1회 재생성, 재실패 시 보류함 /
리스크 메모 있음 → 보류함 / 깨끗함 → (publish=true일 때) 발행, 아니면 preview 기록.
각 단계는 오류 격리된다 — 한 단계가 죽어도 나머지는 계속 실행되고 errors.jsonl에 남는다.
"""
import hashlib
import json
import os
import random
import re
import subprocess
import sys

import analytics
import comments as comments_mod
import evidence
import execution_contract
import generate
import improve
import post_check
import publish
import sitegen
import sourcing
from common import (append_jsonl, is_real_publication, load_config, log,
                    recent_context, read_json, read_jsonl, record_error, set_mode_flags, state_path)


def _guess_pattern(hook):
    if any(q in hook for q in ["\"", "“", "'"]):
        return "리뷰 발굴형"
    if "?" in hook:
        return "공감 직격형"
    if any(w in hook for w in ["걸렀", "비교", "rejected", "compared"]):
        return "선별형"
    return "결과 관찰형"


def _sales_arm(cfg, country, key, default="ab"):
    """A/B 암 선택 — 발행된 해당 국가 판매글 수의 홁수로 결정적 교대. config로 고정 가능."""
    mode = cfg["mode"].get(key, default)
    if mode in ("direct", "site", "on", "off"):
        return mode
    n = sum(1 for p in read_jsonl(state_path(cfg, "published.jsonl"))
            if p.get("country") == country
            and not str(p.get("media_id") or "").startswith("DRY-")
            and (p.get("meta") or {}).get("post_type") == "sales")
    if key == "kr_link_mode":
        return "direct" if n % 2 == 0 else "site"
    return "on" if n % 2 == 0 else "off"


def _gate_and_publish(cfg, text, country, post_type, product=None, link=None, dry_run=False,
                      meta_extra=None, candidate=None):
    text = text or ""
    if not text.strip():
        return None, "format_fail"
    """검사 → 발행. 반환: (media_id|None, reason) — reason ∈ published/format_fail/risk_hold/manual_hold"""
    if country == "US" and re.search(r"[가-힣]", text):
        log("검사(US): 한글 감지 — 영어로 재생성")
        return None, "language_fail"
    if country == "KR" and not re.search(r"[가-힣]", text):
        log("검사(KR): 한국어 없음 — 한국어로 재생성")
        return None, "language_fail"
    if candidate is not None:
        try:
            validated = generate.validate_friction_candidate({**candidate, "text": text})
        except ValueError as exc:
            log(f"friction candidate gate: {exc}")
            return None, "candidate_fail"
        meta_extra = {**(meta_extra or {}), **{key: validated.get(key) for key in (
            "friction_id", "stage", "market", "source_pointers", "mechanism",
            "failure_mode", "skip_if", "attributable_route", "disclosure", "rehearsal_fixture")
            if validated.get(key) is not None}}
    check = post_check.check_post({
        "country": country, "post_type": post_type, "text": text,
        # 원문 리뷰·라벨·스펙과 대조하는 검사가 추가돼도 근거가 잘리지 않도록 전체 제품 증거를 넘긴다.
        "product": product or {},
    })
    hook = text.splitlines()[0][:70]
    log(f"검사({country}/{post_type}): 포맷 {check['format_score']}점, 리스크 메모 {len(check['risk_notes'])}건")
    for t in check["format_tips"]:
        log(f"  ▲ {t}")
    for n in check["risk_notes"]:
        log(f"  ~ {n}")

    if check["verdict"] == "FAIL":
        return None, "format_fail"
    if check["risk_notes"] and cfg["mode"].get("hold_flagged", True):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "risk_flagged", "country": country, "post_type": post_type,
                      "text": text, "notes": check["risk_notes"]})
        log("→ 리스크 메모가 있어 보류함(주간 리포트에서 확인)")
        return None, "risk_hold"
    if not cfg["mode"].get("auto_publish_clean", True):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "manual_mode", "country": country, "post_type": post_type, "text": text})
        return None, "manual_hold"
    media = publish.publish_text(cfg, country, text, link=link, dry_run=dry_run,
                                 meta={"post_type": post_type, "hook_pattern": _guess_pattern(hook),
                                       "format_score": check["format_score"], **(meta_extra or {})})
    if not media and (cfg.get("mode") or {}).get("_rehearsal"):
        record_error(cfg, f"publish_{country}_{post_type}", RuntimeError("preview publication gate failed"))
    return (media, "published") if media else (None, "publish_failed")


def _publish_with_retry(cfg, build_fn, country, post_type, product=None, link=None, dry_run=False,
                        meta_extra=None, candidate=None):
    """포맷·언어 실패 때만 1회 재생성. 리스크 보류는 재시도하지 않는다."""
    last_text = None
    last_reason = None
    for attempt in (1, 2):
        text = build_fn()
        last_text = text
        media, reason = _gate_and_publish(cfg, text, country, post_type,
                                          product=product, link=link, dry_run=dry_run,
                                          meta_extra=meta_extra, candidate=candidate)
        if reason not in ("format_fail", "language_fail"):
            return media, reason
        last_reason = reason
        label = "포맷" if reason == "format_fail" else "언어"
        log(f"{label} 실패 — {'재생성 1회 시도' if attempt == 1 else '재시도도 실패, 보류함으로'}")
    append_jsonl(state_path(cfg, "holdbox.jsonl"),
                 {"why": last_reason, "country": country, "text": last_text})
    return None, last_reason


def _publish_thread(cfg, parts, country, dry_run=False, meta_extra=None, candidate=None):
    """타래 발행 — 각 편을 검사기에 태우고 앞 편에 답글로 잇는다.

    설계 결정: **전량 사전 검사 후 발행.** 1편을 올린 뒤 2편이 검사에서
    걸리면 미완성 타래가 채널에 남는데, 이건 삭제 스코프 이슈까지 얽혀
    수습이 번거롭다. 그래서 한 편이라도 걸리면 아무것도 올리지 않는다.

    반환: (root_media_id|None, reason)
    """
    if not parts or len(parts) < 2:
        return None, "thread_too_short"

    # 1단계 — 전량 사전 검사 (발행 없음)
    for i, text in enumerate(parts, 1):
        text = (text or "").strip()
        if not text:
            log(f"타래 {i}편 비어 있음 — 전체 취소")
            return None, "format_fail"
        if country == "US" and re.search(r"[가-힣]", text):
            return None, "language_fail"
        if country == "KR" and not re.search(r"[가-힣]", text):
            return None, "language_fail"
        if candidate is not None:
            try:
                generate.validate_friction_candidate({**candidate, "text": text})
            except ValueError as exc:
                log(f"타래 {i}편 friction candidate gate: {exc}")
                return None, "candidate_fail"
        check = post_check.check_post({"country": country, "post_type": "value",
                                       "text": text, "product": {}})
        if check["verdict"] == "FAIL":
            log(f"타래 {i}편 포맷 FAIL — 전체 취소")
            return None, "format_fail"
        if check["risk_notes"] and cfg["mode"].get("hold_flagged", True):
            append_jsonl(state_path(cfg, "holdbox.jsonl"),
                         {"why": "risk_flagged", "country": country, "text": text,
                          "notes": check["risk_notes"], "thread_part": i})
            log(f"타래 {i}편 리스크 메모 — 전체 보류")
            return None, "risk_hold"
    if not cfg["mode"].get("auto_publish_clean", True):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "manual_mode", "country": country, "text": "\n---\n".join(parts)})
        return None, "manual_hold"

    # 2단계 — 순차 발행. 각 편은 직전 편에 답글로 붙는다.
    root, prev = None, None
    for i, text in enumerate(parts, 1):
        candidate_meta = {key: (candidate or {}).get(key) for key in (
            "friction_id", "stage", "market", "source_pointers")
            if (candidate or {}).get(key) is not None}
        meta = {"post_type": "value", "thread_part": i, "thread_total": len(parts),
                **candidate_meta, **(meta_extra or {})}
        if i == 1:
            meta["hook_pattern"] = _guess_pattern(text.splitlines()[0][:70])
        media = publish.publish_text(cfg, country, text.strip(), reply_to=prev,
                                     dry_run=dry_run, meta=meta)
        if not media:
            # 사전 검사를 통과했으므로 여기 오면 API 장애다. 남은 편을 보류함에 남겨
            # 사람이 이어붙일 수 있게 한다(추측 재시도로 중복 발행하지 않는다).
            log(f"타래 {i}편 발행 실패 — 남은 편 보류함 기록")
            append_jsonl(state_path(cfg, "holdbox.jsonl"),
                         {"why": "thread_broken", "country": country,
                          "published_root": root, "failed_at_part": i,
                          "remaining": parts[i - 1:]})
            return root, "thread_partial"
        root = root or media
        prev = media
    log(f"타래 발행 완료({country}): {len(parts)}편, root={root}")
    return root, "published"


def make_and_publish_value(cfg, dry_run=False, country="KR", stage="discovery"):
    recent = [p.get("text", "").splitlines()[0]
              for p in read_jsonl(state_path(cfg, "published.jsonl"))
              if is_real_publication(p)][-10:]
    kind = "info"

    import friction
    signal = friction.pick_signal(state_path(cfg, "friction_signals.jsonl"), country)
    if not signal and cfg["mode"].get("_rehearsal"):
        suffix = "" if country == "KR" else "-us"
        signal = {"friction_id": f"fr-rehearsal-storage{suffix}", "market": country,
                  "source_pointer": f"rehearsal:approved-friction{suffix}",
                  "verbatim": "stacked bins must be emptied to reach the lower toys"}
    if not signal:
        log("검증된 friction ledger 입력 없음 — 비상업 글 생성 건너뜀")
        return None, "no_validated_friction"
    topic = signal["verbatim"]

    candidate_meta = {"friction_id": signal["friction_id"], "stage": stage,
                      "market": country, "source_pointers": [signal["source_pointer"]]}
    meta_extra = dict(candidate_meta)

    # Thread publication remains supported, but ordinary friction stages use a single mobile screen.
    atom = None
    episode = None

    # 근거가 탄탄한 원자(strong/moderate)는 타래로 푼다. 사실·반론·실행이
    # 한 원자에 다 들어있어 480자 단편에 넣으면 정보가 뭉개진다.
    # 확신도 weak이나 story 글은 단편 유지.
    thread_ratio = cfg["mode"].get("value_thread_ratio", 0.5)
    if (atom and atom.get("confidence") in ("strong", "moderate")
            and random.random() < thread_ratio):
        parts_n = 4 if atom.get("confidence") == "strong" else 3
        try:
            result = generate.make_value_thread(
                cfg, topic, parts=parts_n, dry_run=dry_run, country=country,
                input_ids=[f"atom:{atom['atom_id']}"])
            parts = [p for p in (result.get("parts") or []) if (p or "").strip()]
            if len(parts) >= 2:
                provenance = result.get("_provenance")
                if isinstance(provenance, dict):
                    meta_extra.update(execution_contract.merge_provenance({}, provenance))
                if isinstance(result.get("_attestation"), dict):
                    meta_extra["generation_attestation"] = result["_attestation"]
                media, reason = _publish_thread(cfg, parts, country, dry_run=dry_run,
                                                meta_extra=meta_extra)
                if reason in ("published", "thread_partial"):
                    if media and not dry_run:
                        evidence.mark_used(cfg, atom["atom_id"], "threads", country, media)
                    return media, reason
                log(f"타래 실패({reason}) — 단편으로 폴백")
        except Exception as e:
            record_error(cfg, "value_thread", e)
            log("타래 생성 오류 — 단편으로 폴백")

    def build():
        input_ids = [f"friction:{signal['friction_id']}"]
        result = generate.make_value_post(
            cfg, kind, topic=topic, recent=recent, dry_run=dry_run, country=country,
            input_ids=input_ids, stage=stage)
        generate.validate_friction_candidate(result)
        # 토너먼트 산출물을 발행 meta에 실어야 나중에 "어떤 앵글·점수가 실제로
        # 조회수를 냈는지" 귀속할 수 있다. 안 실으면 토너먼트를 돌린 의미가 없다.
        meta_extra.update({k: result.get(k) for k in
                           ("angle_used", "writer_variant", "viral_score",
                            "critic_model", "tournament_fallback")
                           if result.get(k) is not None})
        if result.get("rehearsal_fixture"):
            meta_extra["rehearsal_fixture"] = True
        provenance = result.get("_provenance")
        if isinstance(provenance, dict):
            meta_extra.update(execution_contract.merge_provenance({}, provenance))
        if isinstance(result.get("_attestation"), dict):
            meta_extra["generation_attestation"] = result["_attestation"]
        return result["text"]

    media, reason = _publish_with_retry(cfg, build, country, "value", dry_run=dry_run,
                                        meta_extra=meta_extra, candidate=candidate_meta)
    return media, reason


def _kr_sales(cfg, hint, dry_run):
    product = sourcing.pick(cfg, dry_run=dry_run)
    if not product:
        return
    master = generate.make_master(cfg, product, playbook_hint=hint, dry_run=dry_run)

    # 링크 모드 A/B — direct: 글에 쿠팡링크+고지 / site: 자사 제품페이지 경유(고지는 페이지 상단)
    link_mode = _sales_arm(cfg, "KR", "kr_link_mode")
    link = product.get("link")
    if link_mode == "site":
        deploy = not (dry_run or cfg["mode"].get("_rehearsal") or not cfg["mode"].get("publish", False))
        page_url = None
        try:
            page_url = sitegen.kr_page(cfg, product, master, deploy=deploy)
        except Exception as e:
            record_error(cfg, "sitegen.kr_page", e)
        if page_url:
            link = page_url
        else:
            link_mode = "direct"  # 배포 실패 → 직링크+고지로 안전 폴백
            link = product.get("link")
    product = {**product, "link": link, "link_mode": link_mode}
    log(f"KR 판매글 링크 모드: {link_mode}")

    publication_meta = {
        "experiment_id": "kr_link_mode", "experiment_arm": link_mode,
        "link_mode": link_mode, "category": product.get("category"),
        "product_id": product.get("product_key"),
        "formfactor_id": product.get("formfactor_id"),
        "ux_grade": product.get("ux_grade"), "sub_id": product.get("sub_id"),
    }
    verdict_candidate = {}

    def build():
        result = generate.make_sales_post(cfg, master, product, playbook_hint=hint, dry_run=dry_run)
        verdict_candidate.update(result)
        publication_meta.update({key: result.get(key) for key in
                                 ("hook_family", "angle_id", "writer_variant",
                                  "viral_score", "critic_model", "rehearsal_fixture")
                                 if result.get(key) is not None})
        provenance = result.get("_provenance")
        if isinstance(provenance, dict):
            publication_meta.update(execution_contract.merge_provenance({}, provenance))
        if isinstance(result.get("_attestation"), dict):
            publication_meta["generation_attestation"] = result["_attestation"]
        return result["text"]

    _publish_with_retry(cfg, build, product.get("country", "KR"), "sales",
                        product=product, link=link, dry_run=dry_run,
                        meta_extra=publication_meta, candidate=verdict_candidate)


def _us_sales(cfg, hint, dry_run):
    product = sourcing.pick_us(cfg, dry_run=dry_run)
    if not product:
        return
    import companyos
    media = None
    built = {"text": ""}
    try:
        master = generate.make_master(cfg, product, playbook_hint=hint, dry_run=dry_run)

        # US 판매글은 자사 가이드 경유 여부와 무관하게 추천 자체의 경제적 이해관계를 첫 줄에 고지한다.
        # FTC의 clear/conspicuous·recommendation 근접 원칙상 #ad 유무는 성과 실험 대상이 아니다.
        ad_mode = "on"
        product = {**product, "ad_mode": ad_mode}
        log(f"US 판매글 #ad 모드: {ad_mode}")

        publication_meta = {
            "ad_mode": ad_mode,
            "product_id": product.get("product_key"),
            "formfactor_id": product.get("formfactor_id"),
            "ux_grade": product.get("ux_grade"),
            "category": product.get("category"),
            "sub_id": product.get("sub_id"),
            "workflow_id": (product.get("_workflow") or {}).get("workflow_id"),
            "evidence_revision": (product.get("_workflow") or {}).get("evidence_revision"),
        }
        verdict_candidate = {}

        def build():
            result = generate.make_sales_post(cfg, master, product, playbook_hint=hint, dry_run=dry_run)
            verdict_candidate.update(result)
            built["text"] = result["text"]
            publication_meta.update({key: result.get(key) for key in
                                     ("hook_family", "angle_id", "writer_variant",
                                      "viral_score", "critic_model", "rehearsal_fixture")
                                     if result.get(key) is not None})
            provenance = result.get("_provenance")
            if isinstance(provenance, dict):
                publication_meta.update(execution_contract.merge_provenance({}, provenance))
            if isinstance(result.get("_attestation"), dict):
                publication_meta["generation_attestation"] = result["_attestation"]
            return result["text"]

        publish_result = _publish_with_retry(cfg, build, "US", "sales",
                                             product=product, link=product.get("link"), dry_run=dry_run,
                                             meta_extra=publication_meta, candidate=verdict_candidate)
        if isinstance(publish_result, tuple) and len(publish_result) == 2:
            media, reason = publish_result
        else:
            media, reason = None, "publish_not_confirmed"
        mode_cfg = cfg.get("mode") or {}
        preview_only = bool(media) and (
            str(media).startswith("PREVIEW-")
            or ("publish" in mode_cfg and mode_cfg.get("publish") is False)
        )
        if not dry_run and media and reason == "published" and not preview_only:
            workflow = product.get("_workflow") or {}
            publication_url = publish.verified_publication_url(cfg, media)
            companyos.record_product_publication(
                product, media_id=media, publication_url=publication_url, text=built["text"],
                tracking_key=workflow.get("tracking_key") or product.get("sub_id") or "",
                sub_id=product.get("sub_id") or "", readback_verified=True)
        elif not dry_run and product.get("_workflow"):
            release_reason = "preview_only" if preview_only else (reason or "not_published")
            companyos.release_product_claim(product, release_reason,
                                            {"actor": "heightcue-autopilot"})
        return media, reason
    except Exception:
        # A remotely verified post must never be released back to the active pool merely
        # because the subsequent DB acknowledgement failed; that would permit duplication.
        if not dry_run and not media and product.get("_workflow"):
            try:
                companyos.release_product_claim(product, "generation_or_publish_failed",
                                                {"actor": "heightcue-autopilot"})
            except Exception as release_error:
                record_error(cfg, "us_product_claim_release", release_error)
        raise


def daily(cfg, dry_run=False):
    log("=== daily 시작 ===")
    
    # NEW: Run digest first to set ACP
    import digest
    try:
        digest.run_digest(cfg)
    except Exception as e:
        log(f"Digest failed (ignored): {e}")

    try:
        analytics.collect(cfg, dry_run=dry_run)
    except Exception as e:
        record_error(cfg, "analytics.collect", e)

    # 증거 원장 승격 — Aside 수집 워커가 쌓은 원본을 claim_gate에 태운다.
    # 가치글 생성보다 반드시 먼저 실행되어야 당일 원자가 공급된다.
    try:
        evidence.promote_pending(cfg)
    except Exception as e:
        record_error(cfg, "evidence.promote_pending", e)

    # 콘텐츠 팀용 소재 브리프 갱신 (state/content-brief.md).
    # 봇이 쓰는 원장과 같은 원자를 사람이 읽는 카드로 렌더링한다.
    try:
        import briefing
        path = state_path(cfg, "content-brief.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(briefing.build(cfg))
        log(f"콘텐츠 브리프 갱신: {path}")
    except Exception as e:
        record_error(cfg, "briefing.build", e)

    hint = improve.playbook_hint(cfg)

    # KR 판매 1
    try:
        _kr_sales(cfg, hint, dry_run)
    except Exception as e:
        record_error(cfg, "kr_sales", e)
    # KR 가치 1 (두 번째는 post 명령이 저녁 슬롯에서)
    try:
        make_and_publish_value(cfg, dry_run=dry_run, stage="discovery")
        make_and_publish_value(cfg, dry_run=dry_run, stage="bridge")
    except Exception as e:
        record_error(cfg, "kr_value", e)

    # US 트랙 (확정 스코프)
    us_ready = dry_run or cfg["threads"].get("us_access_token")
    if cfg["mode"].get("us_sales_posts", True) and us_ready:
        try:
            _us_sales(cfg, hint, dry_run)
        except Exception as e:
            record_error(cfg, "us_sales", e)
    if cfg["mode"].get("us_value_posts", True) and us_ready:
        try:
            make_and_publish_value(cfg, dry_run=dry_run, country="US", stage="discovery")
            make_and_publish_value(cfg, dry_run=dry_run, country="US", stage="bridge")
        except Exception as e:
            record_error(cfg, "us_value", e)

    # 소싱 버퍼 충전 — Aside 루틴이 다음 슬롯 전까지 결과를 채워 둔다
    if not dry_run:
        try:
            sourcing.top_up_requests(cfg)
        except Exception as e:
            record_error(cfg, "top_up_requests", e)
        if not cfg["mode"].get("publish", False):
            log("※ 리허설 모드: 생성 결과는 state/preview.jsonl 확인. 실발행은 golive 명령 또는 \"publish\": true")
    log("=== daily 종료 ===")


def status(cfg):
    reqs = read_json(state_path(cfg, "browser-queue/requests.json"), [])
    results = read_json(state_path(cfg, "browser-queue/results.json"), [])
    used = {h.get("product_key") for h in read_json(state_path(cfg, "sourced_history.json"), [])}
    ready = [r for r in results if r.get("status") == "done"
             and r.get("product_key") not in used and sourcing.is_audit_approved(r)]
    audit_hold = [r for r in results if r.get("status") == "done"
                  and r.get("product_key") not in used and not sourcing.is_audit_approved(r)]
    published = [p for p in read_jsonl(state_path(cfg, "published.jsonl"))
                 if is_real_publication(p) and p.get("meta", {}).get("kind") != "reply"]
    preview = read_jsonl(state_path(cfg, "preview.jsonl"))
    holds = read_jsonl(state_path(cfg, "holdbox.jsonl"))
    errors = read_jsonl(state_path(cfg, "errors.jsonl"))
    mode = {k: v for k, v in cfg["mode"].items() if not k.startswith("_")}
    print("heightcue 상태")
    print("=" * 44)
    print(f"모드            : {mode}")
    print(f"소싱 큐         : 대기 {sum(1 for q in reqs if q.get('status') == 'pending')}건 / 감사 통과 {len(ready)}건 / 감사 보류 {len(audit_hold)}건")
    print(f"US 판매 레지스트리: {len(read_json(state_path(cfg, 'us_products.json'), []))}건")
    print(f"발행 이력        : 실발행 {len(published)}건 / 리허설 preview {len(preview)}건")
    print(f"보류함          : {len(holds)}건 (주간 리포트에서 검토)")
    print(f"오류 로그        : {len(errors)}건" + (f" — 최근: {errors[-1]['where']}" if errors else ""))
    print("friction ledger : active prompt assembly does not read narrative archives")
    ux = sourcing.ux_store(cfg)["formfactors"]
    cand = sum(1 for f in ux if f.get("status") == "candidate")
    novel = sum(1 for f in ux if f.get("ux_grade") == "novel" and f.get("status") == "active")
    proven = sum(1 for f in ux if f.get("ux_grade") == "proven" and f.get("status") == "active")
    last_disc = max((f.get("discovered_ts") or 0) for f in ux)
    import time as _t
    disc_str = _t.strftime("%Y-%m-%d", _t.localtime(last_disc)) if last_disc else "없음(시드만)"
    print(f"UX 발굴 저장소    : 활성 proven {proven} / novel {novel} / 후보 {cand} / 마지막 발굴 {disc_str}")


def rehearsal(cfg):
    log("=== 리허설: 자격 검증 → 실제 생성(발행 없음) → 미리보기 ===")
    if cfg["mode"].get("publish"):
        log("경고: publish=true 상태 — 리허설은 발행 없이 돌리는 모드입니다. config에서 publish를 끄고 다시 실행하세요.")
        return 1
    if subprocess.call([sys.executable, "validate.py"]):
        log("자격 검증 실패 — 생성하지 않고 중단합니다.")
        return 1
    before_errors = len(read_jsonl(state_path(cfg, "errors.jsonl")))
    cfg["mode"]["_rehearsal"] = True
    try:
        daily(cfg, dry_run=False)
    finally:
        cfg["mode"].pop("_rehearsal", None)
    print("\n──── 생성된 미리보기 (state/preview.jsonl 최근 5건) ────")
    for rec in read_jsonl(state_path(cfg, "preview.jsonl"))[-5:]:
        print(f"\n[{rec.get('country')}] {'링크: ' + str(rec.get('link')) if rec.get('link') else '(링크 없음)'}")
        print(rec.get("text", ""))
    new_errors = read_jsonl(state_path(cfg, "errors.jsonl"))[before_errors:]
    if new_errors:
        log(f"리허설 실패: {len(new_errors)}개 단계 오류가 기록됨")
        return 1
    print("\n→ 이 내용을 Claude에게 공유해 톤·품질 점검을 받은 뒤, 만족스러우면 `python3 run.py golive`")
    return 0


# ---------------------------------------------------------------------------
# 영상(I2V UGC) 명령 — Task 16
#
# 설계 원칙 하나: **돈이 나가는 경로는 명시적으로 켜야만 열린다.**
# `video.production_generation_enabled` 기본값은 false 이고, 이게 꺼져 있으면
# `video process` 는 잡을 claim 조차 하지 않는다(리스를 잡았다 놓으면 attempts 만
# 축나고 원장이 지저분해진다). 리허설은 이 플래그와 무관하게 항상 무료다.
# ---------------------------------------------------------------------------

#: 영상 설정 기본값 — 전부 안전한 쪽(꺼짐/작음)으로 둔다.
VIDEO_DEFAULTS = {
    "enabled": False,
    "production_generation_enabled": False,
    "kill_switch": False,
    "markets": ["KR"],
    "daily_budget_usd": 2.0,
    "max_jobs_per_run": 1,
    "max_attempts": 3,
    "lease_seconds": 3600,
    "ledger_root": None,
}

#: QA 게이트(video_qa)는 fail-closed 다. 전사기가 없으면 spoken_content 검사가
#: 돌지 못하고, 돌지 못한 검사는 실패로 집계된다 → 모든 실영상이 QA 실패한다.
#: 유료 실행 중에 발견하면 안 되므로 리허설이 먼저 확인한다.
#:
#: **파일 존재는 증거가 아니다.** 라운드 1 은 transcriber.py 가 있으면 [충족]
#: 을 찍었는데, 이 머신에는 그 파일이 있고 백엔드(faster_whisper/whisperx)는
#: 아무 인터프리터에도 없다. 즉 전사기는 존재하지만 **실행 불가**다. 그 상태로
#: [충족] 을 찍는 것은 유료 실행을 승인해놓고 모든 영상이 QA 로 떨어지게 만드는
#: 거짓 초록이다 — 이 전제조건이 막으려던 사고 그 자체다.
#:
#: 그래서 프로브는 **실제로 import 를 시도한다.** `video_qa._openmontage_call`
#: 이 하는 것과 동일하게: OpenMontage 루트를 sys.path 에 넣고 cwd 를 거기로 두고
#: 그 호출이 쓰는 인터프리터로 백엔드를 import 해 본다. 하나도 import 되지
#: 않으면 미충족이다. 해석 못 한 출력·비정상 종료·타임아웃도 전부 미충족이다
#: (fail closed — 못 확인한 것을 충족으로 세지 않는다).
OPENMONTAGE_TRANSCRIBER_REL = os.path.join("tools", "analysis", "transcriber.py")

#: transcriber.py 가 실제로 import 하는 것들. faster_whisper 가 본선이고
#: whisperx 는 문서화된 대안(diarization 경로)이다.
TRANSCRIBER_BACKENDS = ("faster_whisper", "whisperx")

#: 백엔드가 없을 때 운영자가 그대로 복사해 실행할 명령.
TRANSCRIBER_INSTALL_HINT = (
    "cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pip install "
    "faster-whisper   # 반드시 OpenMontage 자기 venv 에 설치한다 "
    "(QA 전사는 이 인터프리터로만 돈다)")

#: 프로브 자체는 import 만 하므로 초 단위다. 모델은 절대 로드하지 않는다.
TRANSCRIBER_PROBE_TIMEOUT = 60


def _probe_openmontage_transcriber():
    """(충족?, 사람이 읽을 사유) — 전사가 **실제로 돌 수 있는지** 본다.

    네트워크를 쓰지 않고 모델도 받지 않는다. 백엔드 import 만 시도한다.
    """
    try:
        import video_qa
        root = video_qa.DEFAULT_OPENMONTAGE_ROOT
    except Exception as exc:  # video_qa 자체가 못 올라오면 QA 는 못 돈다
        return False, f"video_qa 를 불러올 수 없다: {type(exc).__name__}: {exc}"
    if not os.path.isdir(root):
        return False, (f"OpenMontage 루트가 없다: {root} "
                       "(OPENMONTAGE_ROOT 로 지정 가능)")
    entry = os.path.join(root, OPENMONTAGE_TRANSCRIBER_REL)
    if not os.path.isfile(entry):
        return False, f"transcriber 엔트리포인트가 없다: {entry}"

    # video_qa._openmontage_call 과 **같은 인터프리터·같은 cwd·같은 sys.path**.
    # 인터프리터 해석은 video_qa 의 헬퍼를 그대로 재사용한다 — 여기에 경로
    # 로직을 복제하면 프로브와 실제 호출부가 갈라지고, 그 드리프트가 바로
    # 거짓 초록(예전)과 거짓 빨강(이번)을 만든 원인이다.
    try:
        exe = video_qa._openmontage_python(root)
    except Exception as exc:
        return False, (f"{exc} · 설치: {TRANSCRIBER_INSTALL_HINT}")

    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {root!r})\n"
        "out = {}\n"
        f"for name in {list(TRANSCRIBER_BACKENDS)!r}:\n"
        "    try:\n"
        "        __import__(name)\n"
        "        out[name] = True\n"
        "    except Exception:\n"
        "        out[name] = False\n"
        "print(json.dumps({'backends': out}))\n"
    )
    try:
        proc = subprocess.run([exe, "-c", script],
                              capture_output=True, text=True,
                              timeout=TRANSCRIBER_PROBE_TIMEOUT, cwd=root)
    except Exception as exc:
        return False, (f"전사 백엔드 프로브를 실행하지 못했다: "
                       f"{type(exc).__name__}: {exc} · 설치: {TRANSCRIBER_INSTALL_HINT}")
    if proc.returncode != 0:
        return False, (f"전사 백엔드 프로브가 코드 {proc.returncode} 로 죽었다: "
                       f"{(proc.stderr or '').strip()[-200:]} · "
                       f"설치: {TRANSCRIBER_INSTALL_HINT}")
    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
        backends = payload["backends"]
        if not isinstance(backends, dict):
            raise ValueError("backends 가 dict 가 아니다")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return False, (f"전사 백엔드 프로브 출력을 해석할 수 없다: {exc} "
                       "(확인 못 한 것은 충족으로 세지 않는다)")
    live = sorted(n for n, ok in backends.items() if ok)
    if not live:
        missing = ", ".join(TRANSCRIBER_BACKENDS)
        return False, (
            f"transcriber.py 는 있으나 전사 백엔드가 없다 ({missing} 모두 "
            f"import 실패, 인터프리터 {exe}). 파일 존재는 실행 "
            f"가능을 뜻하지 않는다 — 지금 유료 실행하면 모든 영상이 QA 에서 "
            f"fail-closed 로 떨어진다. 설치: {TRANSCRIBER_INSTALL_HINT}")
    return True, (f"전사 백엔드 실행 가능: {', '.join(live)} "
                  f"(인터프리터 {exe}, 루트 {root})")


VIDEO_PREREQUISITES = (
    ("OpenMontage transcriber (video_qa 셸아웃 대상)",
     "_probe_openmontage_transcriber",
     "QA 전사 검사(spoken_content). 없으면 모든 실영상이 fail-closed 로 QA 실패한다"),
)


def _strict_flag(name, value, safe_value):
    """불리언 게이트를 엄격하게 읽는다 — bool 이 아니면 안전한 쪽으로 떨어뜨린다.

    `bool()` 강제는 fail-open 이었다: config 에 `"false"`/`"off"`/`"no"` 를 쓰면
    전부 True 가 되어 돈 게이트가 열렸다. 조용히 깎지 않고 반드시 알린다.
    """
    if isinstance(value, bool):
        return value
    print(f"경고: video.{name} 값 {value!r} 은 불리언이 아니다 — "
          f"안전한 쪽({safe_value})으로 처리한다. true/false 로 적어라.")
    return safe_value


def video_settings(cfg):
    """config 의 video 섹션에 기본값을 덮어 채운 설정 dict."""
    raw = (cfg.get("video") or {}) if isinstance(cfg, dict) else {}
    settings = dict(VIDEO_DEFAULTS)
    for key, value in raw.items():
        if not str(key).startswith("_"):
            settings[key] = value
    settings["production_generation_enabled"] = _strict_flag(
        "production_generation_enabled",
        settings.get("production_generation_enabled"), False)
    settings["enabled"] = _strict_flag("enabled", settings.get("enabled"), False)
    # kill_switch 만 방향이 반대다 — 애매하면 **걸린 것**으로 본다.
    settings["kill_switch"] = _strict_flag(
        "kill_switch", settings.get("kill_switch"), True)
    markets = settings.get("markets") or []
    if isinstance(markets, str):
        # "KR" 이 ['K','R'] 로 쪼개지면 모든 market 게이트가 조용히 막힌다.
        markets = [markets]
    settings["markets"] = [str(m).upper() for m in markets] or ["KR"]
    if not settings.get("ledger_root"):
        # None 을 그대로 VideoLedger 에 넘기면 video_queue.default_root() 로
        # 폴백한다(= load_config 재독). 어느 원장을 쓰는지 status 에 찍혀야
        # 하므로 여기서 확정해 둔다.
        if isinstance(cfg, dict) and cfg.get("paths", {}).get("state_dir"):
            settings["ledger_root"] = state_path(cfg, "video")
        else:
            import video_queue as vq
            settings["ledger_root"] = vq.default_root()
    return settings


def _video_ledger(settings):
    import video_queue as vq
    return vq.VideoLedger(settings["ledger_root"])


def _video_prereq_report():
    """(모두충족?, 줄 목록) — 리허설이 사람에게 보여줄 전제조건 표."""
    lines, all_ok = [], True
    for label, probe_name, why in VIDEO_PREREQUISITES:
        # 이름으로 늦게 찾는다 — 테스트가 프로브를 갈아끼울 수 있어야 한다.
        ok, detail = globals()[probe_name]()
        all_ok = all_ok and ok
        lines.append(f"  [{'충족' if ok else '미충족'}] {label} — {why}")
        lines.append(f"          {detail}")
    return all_ok, lines


def _video_enqueue(cfg, settings, args):
    import video_contracts as vc
    if settings["kill_switch"]:
        print("킬스위치가 켜져 있다 — 새 잡을 받지 않는다 (video.kill_switch=false 로 해제)")
        return 3
    if not args.job_file:
        print("--job-file 이 필요하다 (video_contracts.save_job 로 저장한 잡 문서)")
        return 2
    if not os.path.exists(args.job_file):
        print(f"잡 파일이 없다: {args.job_file}")
        return 2
    try:
        job = vc.load_job(args.job_file)
    except Exception as exc:
        print(f"잡 문서를 읽을 수 없다: {type(exc).__name__}: {exc}")
        return 3
    if job.market not in settings["markets"]:
        print(f"허용되지 않은 market={job.market} — 설정의 markets={settings['markets']}")
        return 3
    entry = _video_ledger(settings).enqueue(job)
    if entry.get("created"):
        print(f"큐에 넣었다: {entry['job_id']} (market={entry['market']}, "
              f"product={entry['product_id']})")
    else:
        print(f"기존 잡을 그대로 쓴다(멱등): {entry['job_id']} state={entry['state']}")
    return 0


def _video_fal_client(request, *, api_key=None, session=None, sleep=None,
                      max_polls=900):
    """``video_generate.generate_cuts(client=...)`` 용 fal 큐 전송 어댑터.

    모델·해상도·프롬프트 확장 정책은 이 함수가 만들지 않는다. 검증을 마친
    ``video_generate.build_cut_request`` 를 그대로 전송하고 provider 의 실제
    request_id 와 결과 바이트만 돌려준다.
    """
    import time

    import fal_upload
    import requests

    key = fal_upload.resolve_api_key(api_key)
    http = session or requests
    nap = sleep or time.sleep
    headers = {"Authorization": f"Key {key}"}

    def checked(response, what):
        status = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status < 300:
            body = fal_upload.redact(str(getattr(response, "text", ""))[:300], key)
            raise RuntimeError(f"{what} HTTP {status}: {body}")
        return response

    def payload(response, what):
        checked(response, what)
        try:
            value = response.json()
        except Exception as exc:
            raise RuntimeError(f"{what} 응답이 JSON 이 아니다") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"{what} 응답이 객체가 아니다: {type(value).__name__}")
        return value

    submitted = payload(http.post(
        request["url"], json=request["payload"], headers=headers, timeout=60),
        "fal submit")
    request_id = str(submitted.get("request_id") or "").strip()
    status_url = str(submitted.get("status_url") or "").strip()
    response_url = str(submitted.get("response_url") or "").strip()
    if not request_id or not status_url or not response_url:
        raise RuntimeError("fal submit 응답에 request_id/status_url/response_url 이 없다")

    for poll in range(int(max_polls)):
        status_payload = payload(
            http.get(status_url, headers=headers, timeout=60), "fal status")
        status = str(status_payload.get("status") or "").upper()
        if status == "COMPLETED":
            break
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(
                f"fal request {request_id} {status}: "
                f"{status_payload.get('error') or status_payload.get('detail') or ''}")
        if poll + 1 >= int(max_polls):
            raise TimeoutError(f"fal request {request_id} 가 완료 시간 안에 끝나지 않았다")
        nap(2)
    else:
        raise TimeoutError(f"fal request {request_id} 상태를 확인하지 못했다")

    result = payload(http.get(response_url, headers=headers, timeout=60),
                     "fal result")
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    video = data.get("video") if isinstance(data, dict) else None
    video_url = str((video or {}).get("url") or "").strip()
    if not video_url:
        raise RuntimeError(f"fal request {request_id} 결과에 video.url 이 없다")

    downloaded = checked(http.get(video_url, timeout=180), "fal video download")
    output = os.path.abspath(os.path.expanduser(str(request["output_path"])))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    partial = output + ".part"
    try:
        with open(partial, "wb") as fh:
            chunks = (downloaded.iter_content(chunk_size=1024 * 1024)
                      if callable(getattr(downloaded, "iter_content", None))
                      else (getattr(downloaded, "content", b""),))
            for chunk in chunks:
                if chunk:
                    fh.write(chunk)
        if not os.path.isfile(partial) or os.path.getsize(partial) <= 0:
            raise RuntimeError(f"fal request {request_id} 가 빈 영상을 돌려줬다")
        os.replace(partial, output)
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise
    return {"request_id": request_id, "output_path": output,
            "expanded_prompt": (data or {}).get("expanded_prompt")}


def _video_remotion_renderer(request, *, runner=None, composer_root=None):
    """HeightCue 전용 Remotion composition 을 실행하는 얇은 전송 어댑터."""
    import video_compose as vcomp

    root = os.path.abspath(os.path.expanduser(
        composer_root or os.path.join("~/OpenMontage", "remotion-composer")))
    if request.get("composition_id") != vcomp.COMPOSITION_ID:
        raise RuntimeError(
            f"등록된 composition 은 {vcomp.COMPOSITION_ID!r} 하나뿐이다")
    props = os.path.abspath(str(request.get("props_path") or ""))
    output = os.path.abspath(str(request.get("output_path") or ""))
    if not os.path.isfile(props):
        raise RuntimeError(f"Remotion props 파일이 없다: {props}")
    command = [
        "npx", "remotion", "render", os.path.join(root, "src", "index.tsx"),
        vcomp.COMPOSITION_ID, output, f"--props={props}", "--codec=h264",
    ]
    public_dir = os.path.join(root, "public")
    if os.path.isdir(public_dir):
        command.append(f"--public-dir={public_dir}")
    execute = runner or subprocess.run
    result = execute(command, cwd=root, capture_output=True, text=True,
                     timeout=1800)
    if int(getattr(result, "returncode", 0) or 0) != 0:
        raise RuntimeError(
            f"Remotion render 실패({result.returncode}): "
            f"{str(getattr(result, 'stderr', '') or '')[-500:]}")
    layers = (request.get("overlay_plan") or {}).get("text_layers") or []
    return {"output_path": output, "runtime": vcomp.RENDER_RUNTIME,
            "text_layers": [str(layer.get("text") or "")
                            for layer in layers if isinstance(layer, dict)]}


def _video_setting_for_job(settings, name, job):
    value = settings.get(name)
    if isinstance(value, dict):
        value = value.get(job.product_id, value.get(job.market))
    return value


def _video_process_deps(cfg, settings):
    """운영 의존성. 테스트는 ``_video_process(..., deps=...)`` 로 전부 교체한다."""
    import fal_upload
    import video_compose as vcomp
    import video_generate as vg
    import video_handoff as vh
    import video_qa as vqa
    import video_storyboard as vs

    # 키는 claim 전에 확인한다. 자격증명 누락만으로 attempts 를 태우지 않는다.
    fal_key = fal_upload.resolve_api_key()
    projects_root = os.path.abspath(os.path.expanduser(
        settings.get("projects_root") or vg.DEFAULT_PROJECTS_ROOT))

    def asset_dir(job):
        return os.path.join(state_path(cfg, "product_assets"), job.product_id)

    def load_asset_manifest(job):
        path = os.path.join(asset_dir(job), "product_assets.json")
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        if not isinstance(manifest, dict):
            raise ValueError(f"상품 자산 매니페스트가 객체가 아니다: {path}")
        return manifest

    def resolve_affiliate_link(job):
        value = _video_setting_for_job(settings, "affiliate_links", job)
        if not str(value or "").strip():
            raise ValueError(
                f"video.affiliate_links[{job.product_id!r}] 가 없다 — "
                "제휴 링크 없는 핸드오프는 만들지 않는다")
        return str(value).strip()

    def resolve_account(job):
        value = (cfg.get("threads") or {}).get(f"{job.market.lower()}_user_id")
        if not str(value or "").strip():
            raise ValueError(f"threads.{job.market.lower()}_user_id 가 비어 있다")
        return str(value).strip()

    def load_identity_signoff(job, master_path):
        configured = _video_setting_for_job(
            settings, "identity_signoff_path", job)
        path = (os.path.abspath(os.path.expanduser(str(configured)))
                if configured else os.path.join(
                    projects_root, f"heightcue_{job.run_id}", "qa",
                    f"{job.job_id}_identity_signoff.json"))
        try:
            with open(path, encoding="utf-8") as fh:
                value = json.load(fh)
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    return {
        "worker_id": f"heightcue-video-{os.getpid()}",
        "generate_storyboard": vs.generate_storyboard,
        "generate_first_frames": vg.generate_first_frames,
        "generate_cuts": vg.generate_cuts,
        "compose_master": vcomp.compose_master,
        "compose_subtitled": vcomp.compose_subtitled,
        "run_qa": vqa.run_qa,
        "promote_to_ready": vh.promote_to_ready,
        "load_asset_manifest": load_asset_manifest,
        "resolve_asset_sha256": lambda job, manifest: _video_setting_for_job(
            settings, "asset_sha256", job),
        "resolve_affiliate_link": resolve_affiliate_link,
        "resolve_account": resolve_account,
        "load_identity_signoff": load_identity_signoff,
        "product_asset_dir": asset_dir,
        "cut_client": lambda request: _video_fal_client(
            request, api_key=fal_key),
        "image_url_for": fal_upload.make_image_url_for(api_key=fal_key),
        "renderer": _video_remotion_renderer,
        "runtime_probe": None,
        "projects_root": projects_root,
    }


def _video_compose_lineage(frames_manifest, generated_lineage):
    """생성 모션 컷과 실물 Ken-Burns 사진을 합성 입력 한 줄로 합친다."""
    import video_contracts as vc
    import video_storyboard as vs

    lineage = [dict(c, cut_kind=c.get("cut_kind", vs.CUT_KIND_MOTION))
               for c in (generated_lineage or [])]
    for still in (frames_manifest or {}).get("still_cuts") or []:
        if still.get("generated") or still.get("paid"):
            raise ValueError("Ken-Burns 실물 사진이 생성/유료 컷으로 표시됐다")
        lineage.append({
            "cut_index": int(still.get("cut_index") or 0),
            "cut_kind": vs.CUT_KIND_STILL,
            "output_path": str(still.get("source_path") or ""),
            "output_sha256": str(still.get("source_sha256") or ""),
            "duration_seconds": vc.CUT_DURATION_SECONDS,
            "ken_burns_move": still.get("ken_burns_move")
                              or vs.DEFAULT_KEN_BURNS_MOVE,
            "generated": False,
            "paid": False,
        })
    return sorted(lineage, key=lambda c: int(c.get("cut_index") or 0))


def _video_process(cfg, settings, args, *, deps=None):
    """유료 생성 진입점. **기본은 거부한다.**

    거부할 때 잡을 claim 하지 않는 것이 중요하다 — 리스를 잡았다 놓으면 attempts 가
    축나고, 반복 거부만으로 멀쩡한 잡이 dead_letter 로 굴러떨어진다.
    """
    ledger = _video_ledger(settings)
    # 킬스위치를 **가장 먼저** 본다. README §5-2 는 kill_switch=true 가
    # enqueue·process 를 즉시 막는다고 약속한다 — --dry-run 도 process 다.
    if settings["kill_switch"]:
        print("킬스위치가 켜져 있다 — 생성하지 않는다 (video.kill_switch=false 로 해제)")
        return 3
    if args.dry_run:
        jobs = ledger.list_jobs(state="queued")[:settings["max_jobs_per_run"]]
        print(f"[dry-run] 유료 호출 없이 대상만 나열한다 ({len(jobs)}건)")
        for entry in jobs:
            print(f"  {entry['job_id']} market={entry['market']} "
                  f"product={entry['product_id']}")
        if not jobs:
            print("  (대기 중인 잡 없음)")
        return 0
    # 돈 게이트를 **먼저** 본다. enabled 를 먼저 검사하면 운영자가 받는 메시지가
    # "파이프라인이 꺼져 있다"가 되어, 정작 비용을 여는 스위치가 따로 있다는
    # 사실이 가려진다. 가장 비싼 실수를 가장 먼저 설명한다.
    if not settings["production_generation_enabled"]:
        print("거부: video.production_generation_enabled=false.\n"
              "  이 플래그는 실제 유료 호출(fal.ai MiniMax H3 Max, 5초 컷당 약 $0.20)을 여는\n"
              "  유일한 스위치이며 기본값은 꺼짐이다. 라이브 종단 게이트를 통과하기 전에는\n"
              "  켜지 마라. 무료 확인은 `run.py video rehearsal`.")
        return 3
    if not settings["enabled"]:
        print("video.enabled=false — 영상 파이프라인이 꺼져 있다. 켜기 전에 리허설부터.")
        return 3
    ok, lines = _video_prereq_report()
    if not ok:
        print("배포 전제조건 미충족 — 생성해도 전량 QA 실패한다:")
        for line in lines:
            print(line)
        return 3
    queued = ledger.list_jobs(state="queued")
    if not queued:
        print("대기 중인 영상 잡이 없다.")
        return 0

    import video_compose as vcomp
    import video_contracts as vc
    import video_generate as vg

    try:
        max_jobs = max(0, int(settings["max_jobs_per_run"]))
        lease_seconds = max(1.0, float(settings.get("lease_seconds", 3600)))
        daily_cap = min(float(settings["daily_budget_usd"]),
                        vg.MAX_DAILY_SPEND_USD)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"영상 실행 설정이 잘못됐다 — claim 하지 않는다: {exc}")
        return 3
    if max_jobs == 0:
        print("video.max_jobs_per_run=0 — claim 하지 않는다.")
        return 0
    if daily_cap <= 0:
        print("video.daily_budget_usd 가 0 이하라 claim 하지 않는다.")
        return 3

    # 운영 의존성(키 포함)은 claim 전에 만든다. 테스트는 이 dict 를 통째로
    # 주입해 네트워크·유료 호출·실렌더를 0건으로 고정한다.
    if deps is None:
        try:
            deps = _video_process_deps(cfg, settings)
        except Exception as exc:
            print(f"영상 실행 사전점검 실패 — claim 하지 않는다: "
                  f"{type(exc).__name__}: {exc}")
            return 3

    worker_id = str(deps.get("worker_id") or f"heightcue-video-{os.getpid()}")
    projects_root = os.path.abspath(os.path.expanduser(
        deps.get("projects_root") or vg.DEFAULT_PROJECTS_ROOT))
    edit_decisions = {
        "render_runtime": vcomp.RENDER_RUNTIME,
        "composition_mode": vcomp.COMPOSITION_MODE,
        "aspect_ratio": vc.VIDEO_ASPECT_RATIO,
        "resolution": vc.VIDEO_RESOLUTION,
    }
    failed = False
    processed = 0

    for _ in range(max_jobs):
        claimed = ledger.claim(worker_id, lease_seconds=lease_seconds)
        if claimed is None:
            break
        processed += 1
        job_id = claimed["job_id"]
        job = vc.VideoJob.from_dict(claimed["job"])
        project = os.path.join(projects_root, f"heightcue_{job.run_id}")
        renders = os.path.join(project, "renders")
        qa_dir = os.path.join(project, "qa")
        os.makedirs(renders, exist_ok=True)
        os.makedirs(qa_dir, exist_ok=True)
        qa_path = os.path.join(qa_dir, f"{job.job_id}_qa.json")

        try:
            # 링크/계정/자산은 모델·provider 지출 전에 확정한다.
            affiliate_link = deps["resolve_affiliate_link"](job)
            account = deps["resolve_account"](job)
            asset_manifest = deps["load_asset_manifest"](job)
            asset_sha256 = deps["resolve_asset_sha256"](job, asset_manifest)

            complexity = {1: "simple", 2: "standard", 3: "complex"}[
                len(job.storyboard.cuts)]
            storyboard = deps["generate_storyboard"](
                cfg, evidence=job.evidence, market=job.market,
                run_id=job.run_id,
                content_draft_id=job.storyboard.content_draft_id,
                viral_pattern_ids=list(job.storyboard.viral_pattern_ids),
                complexity=complexity,
                storyboard_id=job.storyboard.storyboard_id)
            ledger.heartbeat(job_id, worker_id, lease_seconds)

            frames = deps["generate_first_frames"](
                storyboard, asset_manifest, projects_root=projects_root,
                bridge=deps.get("image_bridge"),
                preflight_runner=deps.get("image_preflight"),
                asset_sha256=asset_sha256)
            ledger.heartbeat(job_id, worker_id, lease_seconds)

            generated = deps["generate_cuts"](
                storyboard, frames, client=deps["cut_client"],
                job_id=job_id,
                ledger_path=os.path.join(ledger.root, "spend_ledger.json"),
                projects_root=projects_root,
                run_cap_usd=min(daily_cap, vg.MAX_RUN_SPEND_USD),
                daily_cap_usd=daily_cap,
                image_url_for=deps.get("image_url_for"),
                sleep=deps.get("sleep"))
            ledger.heartbeat(job_id, worker_id, lease_seconds)

            generation_state = generated.get("state")
            if generation_state != vc.STATE_READY_TO_PUBLISH:
                reason = str(generated.get("failure") or
                             f"video_generate state={generation_state}")
                if generation_state == vc.STATE_QA_FAILED:
                    report = vc.QAReport(
                        job_id=job_id, run_id=job.run_id, passed=False,
                        checks={"generation": {"passed": False}},
                        failures=[f"generation: {reason}"]).validate()
                    vc.atomic_write_json(qa_path, report.to_dict())
                    ledger.complete(job_id, worker_id, qa_report=report)
                else:
                    ledger.retry(job_id, worker_id, reason=reason)
                print(f"영상 잡 실패: {job_id} — {reason}")
                failed = True
                break

            raw_manifest = generated.get("manifest")
            manifest = (raw_manifest if isinstance(raw_manifest,
                                                   vc.GenerationManifest)
                        else vc.GenerationManifest.from_dict(
                            raw_manifest or {})).validate()
            cut_lineage = _video_compose_lineage(
                frames, generated.get("cut_lineage"))
            storyboard_dict = storyboard.to_dict()

            master_path = os.path.join(
                renders, f"{job_id}_clean-master.mp4")
            master = deps["compose_master"](
                storyboard=storyboard_dict, cut_lineage=cut_lineage,
                edit_decisions=edit_decisions, job_id=job_id,
                output_path=master_path, renderer=deps["renderer"],
                runtime_probe=deps.get("runtime_probe"))
            ledger.heartbeat(job_id, worker_id, lease_seconds)

            deliverable_path = os.path.join(
                renders, f"{job_id}_subtitled.mp4")
            deliverable = deps["compose_subtitled"](
                master=master, storyboard=storyboard_dict,
                edit_decisions=edit_decisions, job_id=job_id,
                output_path=deliverable_path, renderer=deps["renderer"],
                runtime_probe=deps.get("runtime_probe"))
            ledger.heartbeat(job_id, worker_id, lease_seconds)

            disclosure = vcomp.extract_disclosure(storyboard_dict, job.market)
            spoken = vcomp.extract_captions(storyboard_dict)
            caption = (("\n".join(spoken) + "\n\n") if spoken else "") + disclosure
            stills = frames.get("still_cuts") or []
            motion_frames = frames.get("frames") or []
            product_image = (str(stills[0].get("source_path") or "")
                             if stills else str(
                                 (motion_frames[0] if motion_frames else {}).get(
                                     "source_path") or ""))
            identity_signoff = deps["load_identity_signoff"](
                job, master["output_path"])
            product_asset_dir = (deps["product_asset_dir"](job)
                                 if deps.get("product_asset_dir")
                                 else os.path.dirname(product_image))
            report = deps["run_qa"](
                job_id=job_id, run_id=job.run_id,
                video_path=deliverable["output_path"],
                storyboard=storyboard_dict, caption=caption,
                overlay_texts=deliverable.get("rendered_text_layers") or [],
                product_image_path=product_image,
                identity_signoff=identity_signoff,
                product_asset_dir=product_asset_dir,
                fidelity_checker=deps.get("fidelity_checker"),
                master_path=master["output_path"],
                master_caption=disclosure,
                master_overlay_texts=master.get("rendered_text_layers") or [],
                frame_sampler=deps.get("frame_sampler"),
                transcriber=deps.get("transcriber"),
                audio_probe=deps.get("audio_probe"),
                workdir=os.path.join(qa_dir, "work"))
            report.validate()
            vc.atomic_write_json(qa_path, report.to_dict())
            ledger.heartbeat(job_id, worker_id, lease_seconds)

            if not report.passed:
                ledger.complete(job_id, worker_id, manifest=manifest,
                                qa_report=report)
                print(f"영상 QA 실패: {job_id} — "
                      f"{'; '.join(report.failures)}")
                failed = True
                break

            deps["promote_to_ready"](
                ledger, job_id=job_id, worker_id=worker_id,
                manifest=manifest, qa_report=report,
                video_path=deliverable["output_path"], caption=caption,
                disclosure=disclosure, affiliate_link=affiliate_link,
                qa_report_path=qa_path, account=account)
            print(f"영상 준비 완료: {job_id} → ready_to_publish "
                  "(이 프로세스는 발행하지 않음)")
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            try:
                ledger.retry(job_id, worker_id, reason=reason)
            except Exception as recovery_exc:
                print(f"영상 잡 복구 위임: {job_id} — 리스 소유권이 바뀌어 "
                      f"현재 원장을 덮어쓰지 않는다 ({recovery_exc})")
            print(f"영상 잡 재시도 대기: {job_id} — {reason}")
            failed = True
            break

    if processed == 0:
        print("claim 가능한 영상 잡이 없다.")
    return 1 if failed else 0


def _video_status(cfg, settings, args):
    stats = _video_ledger(settings).stats()
    if args.json:
        print(json.dumps({"settings": settings, "ledger": stats,
                          "orchestrator": {"wired": True,
                                           "publishes": False}},
                         ensure_ascii=False, indent=2))
        return 0
    flag = "켜짐" if settings["production_generation_enabled"] else "꺼짐"
    print("heightcue 영상 상태")
    print("=" * 44)
    print(f"원장            : {stats['root']}")
    print(f"총 {stats['total']}건 · 리스 보유 {stats['leased']}건 · "
          f"만료 리스 {stats['stale_leases']}건")
    for state, count in stats["by_state"].items():
        if count:
            print(f"  {state:<18} {count}")
    print(f"enabled                        : {settings['enabled']}")
    print(f"production_generation_enabled  : {flag}")
    print(f"kill_switch                    : {settings['kill_switch']}")
    print(f"markets / 일예산 / 회당최대     : {settings['markets']} / "
          f"${settings['daily_budget_usd']} / {settings['max_jobs_per_run']}건")
    print("종단 오케스트레이터             : 배선됨 · ready_to_publish까지만 · 발행하지 않음")
    return 0


def _video_rehearsal(cfg, settings, args):
    """무료 드라이런. 유료 호출 0건 · 발행 0건을 **구조적으로** 보장한다 —
    provider 호출 경로에 아예 진입하지 않는다."""
    print("=== 영상 리허설: 유료 호출 없음 · 발행 없음 ===")
    market = (args.market or settings["markets"][0]).upper()
    print(f"대상 market: {market}")
    if market not in settings["markets"]:
        print(f"경고: {market} 는 설정의 markets={settings['markets']} 에 없다")

    observations = None
    if args.fixture:
        if not os.path.exists(args.fixture):
            print(f"픽스처 파일이 없다: {args.fixture}")
            return 2
        observations = [ln for ln in read_jsonl(args.fixture)
                        if ln.get("observation_id")]
        matching = [o for o in observations if o.get("market") == market]
        print(f"픽스처 관측 {len(observations)}건 (market={market} {len(matching)}건)")

    stats = _video_ledger(settings).stats()
    print(f"원장: 총 {stats['total']}건 "
          f"(대기 {stats['by_state'].get('queued', 0)}건)")

    print("\n배포 전제조건")
    ok, lines = _video_prereq_report()
    for line in lines:
        print(line)

    print("\n게이트 상태")
    print(f"  video.enabled                       : {settings['enabled']}")
    print(f"  video.production_generation_enabled : "
          f"{settings['production_generation_enabled']} "
          f"({'켜짐' if settings['production_generation_enabled'] else '꺼짐'})")
    print(f"  video.kill_switch                   : {settings['kill_switch']}")

    print("\n리허설 결과: 유료 호출 0건 · 발행 0건")
    print("종단 오케스트레이터 배선: storyboard → 실물/첫프레임 → H3 Max 컷 → "
          "클린 마스터+자막본+SRT → 양쪽 QA → ready_to_publish")
    print("이 process 경로 자체에는 publish 호출이 없다.")
    if not ok:
        print("→ 전제조건 미충족. 위 [미충족] 항목을 설치하기 전에는 실행하지 마라 —")
        print("  QA 는 fail-closed 라 생성한 영상이 전량 탈락하고 비용만 나간다.")
        return 1
    print("→ 전제조건 충족. 실행하려면 config 의 video.enabled 와")
    print("  video.production_generation_enabled 를 명시적으로 켜라(기본은 꺼짐).")
    return 0


def video_command(cfg, argv):
    """`run.py video <sub>` 진입점. 반환값이 그대로 종료 코드가 된다."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="run.py video", description="HeightCue I2V UGC 영상 워크플로",
        add_help=False)
    sub = parser.add_subparsers(dest="sub")
    p_enq = sub.add_parser("enqueue", add_help=False, help="잡을 원장에 넣는다")
    p_enq.add_argument("--job-file", default=None)
    p_proc = sub.add_parser("process", add_help=False, help="대기 잡을 생성한다(유료)")
    p_proc.add_argument("--dry-run", action="store_true")
    p_stat = sub.add_parser("status", add_help=False, help="원장·게이트 상태")
    p_stat.add_argument("--json", action="store_true")
    p_reh = sub.add_parser("rehearsal", add_help=False, help="무료 드라이런")
    p_reh.add_argument("--market", default=None)
    p_reh.add_argument("--fixture", default=None)

    if not argv or argv[0] not in ("enqueue", "process", "status", "rehearsal"):
        print("사용법: run.py video <enqueue|process|status|rehearsal>")
        print("  enqueue   --job-file <path>      잡을 원장에 넣는다(멱등)")
        print("  process   [--dry-run]            대기 잡 생성 — 기본 거부(유료)")
        print("  status    [--json]               원장 집계 + 게이트 상태")
        print("  rehearsal [--market KR] [--fixture <path>]  무료 드라이런")
        return 2
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    settings = video_settings(cfg)
    handlers = {"enqueue": _video_enqueue, "process": _video_process,
                "status": _video_status, "rehearsal": _video_rehearsal}
    try:
        return handlers[args.sub](cfg, settings, args)
    except Exception as exc:
        print(f"영상 명령 실패: {type(exc).__name__}: {exc}")
        record_error(cfg, f"video_{args.sub}", exc)
        return 4


def golive(cfg):
    mode = set_mode_flags(dry_run=False, publish=True)
    log(f"가동 전환 저장 완료: {mode}")
    print("""
다음 실행부터 실제 발행됩니다. OS crontab에 아래 6개 작업을 등록하세요(이 레포의 `crontab.txt`와 동일):

30 9  * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py daily >> state/cron.log 2>&1
30 12 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR value >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1
0  14 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py comments >> state/cron.log 2>&1
0  16 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR sales >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1
30 19 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR value >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1 && ../.venv/bin/python run.py comments >> state/cron.log 2>&1
0  21 * * 0 cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py weekly >> state/cron.log 2>&1

정지(킬스위치): crontab에서 6개 작업을 제거, 또는 config.json의 "publish"를 false로.""")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dryrun"
    cfg = load_config()
    dry = cfg["mode"].get("dry_run", True) or cmd == "dryrun"
    if cmd in ("daily", "dryrun", "post", "comments", "weekly", "rehearsal", "golive"):
        # 선언만 존재하는 계약은 실행 통제가 아니다. 콘텐츠/댓글 경로는
        # dispatch 전에 모델·source·task·country 전체를 조립할 수 있어야 한다.
        execution_contract.validate_runtime(cfg)
    if cmd in ("daily", "dryrun"):
        daily(cfg, dry_run=dry)
        if cmd == "dryrun":
            comments_mod.run(cfg, dry_run=True)
            improve.run(cfg, dry_run=True)
    elif cmd == "post":
        # 사용법: run.py post [KR|US] [value|sales] — 기본 KR value
        country = (sys.argv[2] if len(sys.argv) > 2 else "KR").upper()
        ptype = (sys.argv[3] if len(sys.argv) > 3 else "value").lower()
        try:
            if ptype == "sales":
                hint = improve.playbook_hint(cfg)
                if country == "KR":
                    _kr_sales(cfg, hint, dry)
                else:
                    _us_sales(cfg, hint, dry)
            else:
                make_and_publish_value(cfg, dry_run=dry, country=country)
        except Exception as e:
            record_error(cfg, f"post_{country.lower()}_{ptype}", e)
    elif cmd == "comments":
        # 매시간 실행이라 앞 실행이 LLM 대기로 길어지면 겹칠 수 있다.
        # 겹치면 같은 댓글을 두 프로세스가 각각 '미응답'으로 보고 두 번 답할 위험이 있어
        # 파일 락으로 직렬화한다. 이미 돌고 있으면 조용히 건너뛴다.
        import fcntl
        lock_path = state_path(cfg, "comments.lock")
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # 3분마다 도는 작업이라 조용히 넘어간다(로그를 남기면 cron.log가 커진다).
            return
        try:
            comments_mod.run(cfg, dry_run=dry)
        except Exception as e:
            record_error(cfg, "comments", e)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    elif cmd == "weekly":
        improve.run(cfg, dry_run=dry)
    elif cmd == "rehearsal":
        raise SystemExit(rehearsal(cfg))
    elif cmd == "status":
        status(cfg)
    elif cmd == "video":
        # 신규 명령 — 기존 4개(daily/post/comments/weekly) 분기 뒤에 붙는다.
        raise SystemExit(video_command(cfg, sys.argv[2:]))
    elif cmd == "golive":
        golive(cfg)
    elif cmd == "context":
        # 소싱 루틴(Aside)이 읽는 읽기전용 채널 컨텍스트 — 발행/답글 없음
        out = {}
        for country in ("KR", "US"):
            try:
                out[country] = recent_context(cfg, country)
            except Exception as e:
                out[country] = {"error": f"{type(e).__name__}: {e}"}
        try:
            from sourcing import _queue_paths
            from common import read_json
            req_p, res_p, _ = _queue_paths(cfg)
            out["queue"] = {
                "pending_requests": [q for q in read_json(req_p, []) if q.get("status") == "pending"],
                "done_results": [{"category": r.get("category"), "product_name": r.get("product_name")}
                                  for r in read_json(res_p, []) if r.get("status") == "done"],
            }
        except Exception as e:
            out["queue"] = {"error": str(e)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
